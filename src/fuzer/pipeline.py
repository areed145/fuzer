"""Extract -> resolve -> golden -> graph. Safe to rerun at any time."""

import os
import threading
import time
from datetime import datetime, timezone

from . import db, golden, graph, llm_docs, llm_links, llm_pids
from .extract import extract, link_part_numbers
from .pack import Pack
from psycopg.types.json import Jsonb

from . import incidents, knowledge, lessons, queries, taxonomy
from .resolve import resolve, resolve_keyed, retire_unused, sync_labels


REBUILD_LOCK = 0x46555A  # "FUZ"
WORK_MEM = os.environ.get("FUZER_WORK_MEM", "256MB")


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
    # Set-based syncs join hundreds of thousands of rows; give this transaction enough memory to hash
    # them instead of sorting on disk (the server default is a few MB).
    conn.execute(f"set local work_mem = '{WORK_MEM}'")
    lap("wait_for_lock")

    cands, extra, pid_sheets = [], [], {}
    text_kinds = {(rsrc, rtype): name for name, (rsrc, rtype, _, _) in llm_links.TEXT_SOURCES.items()}
    texts: dict[str, dict[str, dict]] = {name: {} for name in llm_links.TEXT_SOURCES}
    for rec in conn.execute("select source, record_type, source_key, payload from source_record"):
        c, e = extract(rec, pack)
        cands += c
        extra += e
        if name := text_kinds.get((rec["source"], rec["record_type"])):
            texts[name][rec["source_key"]] = rec["payload"]
        elif rec["source"] == "pid":
            pid_sheets[rec["source_key"]] = rec["payload"]
    # Claude's cached reads of logs and scanned drawings; never calls the model here.
    c, e = llm_links.cached_items(conn, texts)
    cands += c
    extra += e + llm_pids.cached_edges(conn, pid_sheets) + llm_docs.apply_cached(conn, cands)
    extra += link_part_numbers(cands)
    lap("extract")

    assigned, key_index, stats = resolve(conn, cands, pack)
    lap("resolve")
    golden_stats, values, golden_rows = golden.build(conn, cands, assigned, pack)
    stats |= golden_stats
    lap("golden")

    # Classes and models come from golden values, so they're derived after survivorship.
    kinds = {assigned[c.ref]: c.kind for c in cands}
    tag_keys = {next(iter(fids)): key for (kind, key), fids in key_index.items() if kind == "tag" and len(fids) == 1}
    equipment_tags: dict[str, set[str]] = {}
    for c in cands:
        if c.kind == "equipment" and c.block and len(key_index.get(("tag", c.block), ())) == 1:
            equipment_tags.setdefault(assigned[c.ref], set()).add(next(iter(key_index[("tag", c.block)])))
    d_cands, d_edges, d_fields = taxonomy.derive(pack, kinds, values, tag_keys,
                                                 {k: list(v) for k, v in equipment_tags.items()})
    d_assigned, d_keys = resolve_keyed(conn, d_cands)
    sync = golden.sync(conn, golden_rows + golden.derived_rows(d_cands, d_assigned, d_fields))
    stats["golden_changes"] = {k: v for k, v in sync.items() if k != "rows"}
    for k, v in d_keys.items():
        key_index.setdefault(k, set()).update(v)
    sync_labels(conn, key_index)  # incidents and later steps read labels
    stats["classes"] = sum(1 for c in d_cands if c.kind == "equipment_class")
    stats["models"] = sum(1 for c in d_cands if c.kind == "equipment_model")
    lap("taxonomy")

    stats |= graph.build(conn, cands + d_cands, extra + d_edges, {**assigned, **d_assigned}, key_index)
    stats["maintenance_event_links"] = graph.link_maintenance_to_events(conn)
    lap("graph")

    # Incidents group signals that are only linked once the graph exists.
    i_cands, i_edges = incidents.derive(conn)
    i_assigned, i_keys = resolve_keyed(conn, i_cands)
    for k, v in i_keys.items():
        key_index.setdefault(k, set()).update(v)
    sync_labels(conn, key_index)
    golden.append(conn, i_cands, i_assigned, [])
    incidents.write_edges(conn, i_edges, i_assigned)
    stats["incidents"] = len(i_cands)
    retire_unused(conn, set(assigned.values()) | set(d_assigned.values()) | set(i_assigned.values()))
    lap("incidents")
    stats |= lessons.suggest(conn, pack.classification.get("taxonomy", "iso14224"))
    stats["knowledge_changes"] = {k: v for k, v in knowledge.build(conn).items() if k != "rows"}
    conn.execute(
        """
        insert into register_summary (name, value, updated_at) values ('ontology', %s, now())
        on conflict (name) do update set value = excluded.value, updated_at = now()
        """,
        (Jsonb(queries.ontology(conn, cached=False)),),
    )
    lap("summary")
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
