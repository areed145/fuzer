"""Extract -> resolve -> golden -> graph. Safe to rerun at any time."""

import threading
import time
from datetime import datetime, timezone

from . import db, golden, graph, llm_links
from .extract import extract
from .pack import Pack
from .resolve import resolve


REBUILD_LOCK = 0x46555A  # "FUZ"


def rebuild(conn, pack: Pack) -> dict:
    timings = {}
    t = time.perf_counter()

    def lap(step):
        nonlocal t
        now = time.perf_counter()
        timings[step] = round(now - t, 2)
        t = now

    # One rebuild at a time across every process (API, CLI, scheduled jobs); waits for a running one.
    conn.execute("select pg_advisory_xact_lock(%s)", (REBUILD_LOCK,))
    lap("wait_for_lock")

    cands, extra, log_entries = [], [], {}
    for rec in conn.execute("select source, record_type, source_key, payload from source_record"):
        c, e = extract(rec, pack)
        cands += c
        extra += e
        if rec["source"] == "oplog":
            log_entries[rec["source_key"]] = rec["payload"]
    # Claude's cached reads of the logs (events, extra links); never calls the model here.
    c, e = llm_links.cached_items(conn, log_entries)
    cands += c
    extra += e
    lap("extract")

    assigned, key_index, stats = resolve(conn, cands, pack)
    lap("resolve")
    stats |= golden.build(conn, cands, assigned, pack)
    lap("golden")
    stats |= graph.build(conn, cands, extra, assigned, key_index)
    lap("graph")
    conn.commit()
    lap("commit")
    stats["seconds"] = timings
    return stats


class BackgroundRebuild:
    """Runs rebuilds on a worker thread. Requests made while one is running are folded into a
    single follow-up run, so a reviewer clicking through many answers triggers few rebuilds."""

    def __init__(self, pack: Pack):
        self.pack = pack
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pending = False
        self._running = False  # changed only under _lock, so a request can't slip past a finishing worker
        self.last: dict | None = None

    def request(self) -> dict:
        with self._lock:
            self._pending = True
            if not self._running:
                self._running = True
                self._thread = threading.Thread(target=self._run, name="fuzer-rebuild", daemon=True)
                self._thread.start()
        return self.status()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._running = False
                    return
                self._pending = False
            started = datetime.now(timezone.utc)
            try:
                with db.connect() as conn:
                    self.last = {"started": started.isoformat(), "stats": rebuild(conn, self.pack)}
            except Exception as e:  # keep the worker alive; report the failure in status
                self.last = {"started": started.isoformat(), "error": f"{type(e).__name__}: {e}"}
            self.last["finished"] = datetime.now(timezone.utc).isoformat()

    def status(self) -> dict:
        return {"running": self._running, "pending": self._pending, "last": self.last}

    def wait(self, timeout: float = 120) -> None:
        if self._thread:
            self._thread.join(timeout)
