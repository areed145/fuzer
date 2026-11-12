"""Extract -> resolve -> golden -> graph. Safe to rerun at any time."""

import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from ..analysis import capacity_bounds, limits
from ..core import db, settings, taxonomy
from ..core.pack import Pack
from ..graph import (
    edges,
    golden,
    incidents,
    knowledge,
    lessons,
    llm_links,
    measurements,
    queries,
    roles,
    spatial,
)
from ..graph.resolve import replace_open_reviews, resolve, resolve_keyed, retire_unused, sync_labels
from ..sources import curves, learned, llm_docs, llm_pids, mappings, quality, rules
from ..sources.extract import extract, link_part_numbers

REBUILD_LOCK = 0x46555A  # "FUZ"


def readings_newer(conn) -> bool:
    """Claude has stored readings since the last rebuild (or nothing has been built yet).

    The readers rebuild after storing, but a read that was stopped, failed part way or ran with
    --no-rebuild leaves its readings cached and unused; later starts see nothing new to read and
    no changed exports, so without this check they would never rebuild with them."""
    row = conn.execute(
        """
        select (select max(created_at) from llm_extraction) as read,
               (select updated_at from register_summary where name = 'ontology') as built
        """
    ).fetchone()
    return row["read"] is not None and (row["built"] is None or row["read"] > row["built"])


def read_pending(conn, pack: Pack, report: Callable[[str], None]) -> None:
    """Read uncached source content once, then apply new or previously unapplied readings to the register."""
    from ..core import llm
    from ..core.pricing import cost
    from ..sources import llm_curves

    readers = [("operator log entries", "entries", llm_links.run), ("drawings", "sheets", llm_pids.run),
               ("document sections", "sections", llm_docs.run), ("datasheets", "datasheets", llm_curves.run)]
    todo = [(label, key, run, n) for label, key, run in readers if (n := run(conn, pack, dry_run=True)[key])]
    if todo:
        what = ", ".join(f"{n:,} {label}" for label, _, _, n in todo)
        if not llm.configured():
            report(f"Not read by {llm.provider()}: {what}. Configure the provider API key and restart.")
        else:
            report(f"{llm.provider()} ({llm.model()}) is reading {what}. This can take a few minutes ...")
            stored, spent = 0, 0.0
            for label, _, run, _ in todo:
                out = run(conn, pack)
                stored += out.get("stored", 0)
                spent += cost(out.get("model", llm_links.DEFAULT_MODEL), out.get("input_tokens", 0),
                              out.get("output_tokens", 0), out.get("cache_read_tokens", 0), 0, llm.provider())[0]
                for err in out.get("errors", [])[:3]:
                    report(f"Could not read some {label}: {err}. Inspect recorded readings before a deliberate retry.")
            report(f"{llm.provider()} read {stored:,} item(s) for about ${spent:.2f}")
    llm_curves.queue_reviews(conn, pack)
    if readings_newer(conn):
        report("Rebuilding the register with cached LLM readings ...")
        rebuild(conn, pack)


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
    conn.execute(f"set local work_mem = '{settings.get(conn, 'db.work_mem')}'")
    lap("wait_for_lock")
    # rules learned from stewards answer what the last rebuild left unresolved, before the aliases are read (P6.5)
    learned_rules = learned.sync(conn)
    pack = rules.apply(conn, pack)  # survivorship and matching rules from the source catalog (#225)

    cands, extra, pid_sheets = [], [], {}
    text_kinds = {(rsrc, rtype): name for name, (rsrc, rtype, _, _) in llm_links.TEXT_SOURCES.items()}
    texts: dict[str, dict[str, dict]] = {name: {} for name in llm_links.TEXT_SOURCES}
    maps = mappings.load(conn)
    for m in maps.values():
        m.unresolved.clear()  # counted again by this rebuild's extraction
    references = {}
    reference_maps = {key: m for key, m in maps.items() if m.shape == "reference"}
    if reference_maps:
        pairs = list(reference_maps)
        for rec in conn.execute(
            "select source, record_type, source_key, payload from source_record "
            "where (source, record_type) in (select * from unnest(%s::text[], %s::text[]))",
            ([s for s, _ in pairs], [t for _, t in pairs]),
        ):
            m = reference_maps[(rec["source"], rec["record_type"])]
            if entry := m.reference_entry(rec["source_key"], rec["payload"], pack):
                references.setdefault(entry["list"], {})[entry["code"]] = entry
    if references:
        pack = replace(pack, reference_data=references)
    for rec in conn.execute("select source, record_type, source_key, payload from source_record"):
        c, e = extract(rec, pack, maps)
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
    stats["learned_rules"] = learned_rules
    lap("resolve")
    # Off-page lines only one drawing shows are questions for a person, not links (#475).
    replace_open_reviews(conn, "pid_connection", llm_pids.reviews(conn, pid_sheets))
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

    stats |= edges.build(conn, cands + d_cands, extra + d_edges, {**assigned, **d_assigned}, key_index)
    coverage = stats.pop("coverage")
    stats["maintenance_event_links"] = edges.link_maintenance_to_events(conn)
    from ..graph import placement

    stats["placement"] = placement.apply(conn, pack)  # where new items sit (P2.1d)
    lap("graph")
    endpoint = edges.resolver({**assigned, **d_assigned}, key_index)
    # the drawings' equipment data and alarm and trip tables (#475), from the cached readings
    from . import rounds  # readings operators took on rounds stay on their asset (P5.4b)

    derive = lambda c, p, rows: capacity_bounds.derive(c, p, rows) | rounds.measurements(c)
    stats |= measurements.build(conn, pack, maps, endpoint, derive,
                                extra=llm_pids.design_measurements(conn, pid_sheets, pack))
    stats |= curves.build(conn, pack, maps, endpoint)
    stats |= limits.build(conn, pack, maps, endpoint, extra=llm_pids.design_limits(conn, pid_sheets, pack))
    # where a drawing and the DCS, AF or SAP disagree: questions for a person, never a change to either (#475)
    replace_open_reviews(conn, "pid_mismatch", llm_pids.mismatches(conn, pid_sheets, limits.drawing_issues(conn, pack)))
    stats |= roles.build(conn, pack)  # what each historian point means for its asset (P1.4)
    lap("measurements")

    stats["located"] = spatial.index(conn)
    if pack.spatial_links:
        stats["spatial_links"] = spatial.link(conn, pack.spatial_links)
    lap("spatial")

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
    misses: dict[str, dict] = {}  # link values that named nothing the register knows, per source and link type
    for m in maps.values():
        for rel, values in m.unresolved.items():
            misses.setdefault(m.source, {}).setdefault(rel, Counter()).update(values)
    stats["sources"] = len(quality.after_rebuild(conn, coverage, misses))  # coverage and match statistics per source
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
    from ..graph import criticality, data_work, review

    stats["standing_answers"] = review.apply_standing(conn)  # like questions a steward already answered (P2.3c)
    stats["steward_work"] = data_work.sync(conn)  # every open question is a case in the steward's queue (P2.3)
    stats["criticality_work"] = criticality.sync(conn)  # an asset with no criticality is a steward case
    from . import corrections

    stats["corrections"] = corrections.sync(conn)  # what answers said was wrong at a source, and what came back (P2.4)
    from ..analysis import completeness

    stats["context_gaps"] = completeness.sync(conn)  # the assets whose missing context holds back most (P2.5)
    from ..graph import contextualized

    stats["unmatched_records"] = contextualized.sync(conn)  # what matched nothing, one case per source (P2.1)
    stats["unresolved_links"] = contextualized.sync_links(conn)  # a parent or location that isn't in the register
    from ..sources import scorecard

    stats["source_quality"] = scorecard.sync(conn)  # each dataset's four measures; a drop is its owner's case (P2.6)
    lap("steward work")
    conn.commit()
    lap("commit")
    stats["seconds"] = timings
    return stats


class _RebuildQueue:
    """One project's rebuild worker: requests made while one is running fold into a single follow-up run."""

    def __init__(self, owner: "BackgroundRebuild", scope: db.Scope):
        self.owner, self.scope = owner, scope
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
            started = datetime.now(UTC)
            try:
                with db.use(self.scope), db.connect() as conn:  # the requesting project's register, on this thread
                    p = self.owner.pack
                    pack = p if isinstance(p, Pack) else p(conn)
                    self.last = {"started": started.isoformat(), "stats": rebuild(conn, pack)}
            except Exception as e:  # keep the worker alive; report the failure in status
                self.last = {"started": started.isoformat(), "error": f"{type(e).__name__}: {e}"}
            self.last["finished"] = datetime.now(UTC).isoformat()

    def status(self) -> dict:
        return {"running": self._running, "pending": self._pending, "last": self.last}

    def wait(self, timeout: float = 120) -> None:
        if self._thread:
            self._thread.join(timeout)


class BackgroundRebuild:
    """Runs rebuilds on worker threads, one queue per project: a reviewer clicking through many answers triggers
    few rebuilds, and one project's requests never rebuild (or wait behind) another's."""

    def __init__(self, pack: "Pack | Callable[[Any], Pack]"):
        self.pack = pack  # a Pack, or a function of the connection that resolves one when each rebuild runs (#376)
        self._queues: dict[db.Scope, _RebuildQueue] = {}
        self._lock = threading.Lock()

    def _queue(self) -> _RebuildQueue:
        s = db.scope()
        with self._lock:
            if s not in self._queues:
                self._queues[s] = _RebuildQueue(self, s)
            return self._queues[s]

    def request(self) -> dict:
        return self._queue().request()

    def status(self) -> dict:
        return self._queue().status()

    def wait(self, timeout: float = 120) -> None:
        self._queue().wait(timeout)

    @property
    def last(self) -> dict | None:
        return self._queue().last


def follow_register(conn, pack: Pack) -> dict:
    """What follows a register build wherever it runs (the scheduled `register` asset, `fuzer up`): the work board,
    the integrated plan's SAP work orders and maintenance plans, and the IOC's intake of open SAP notifications,
    so work that other systems hold reaches the IOC as soon as the register does."""
    from . import board, intake, plan

    planned = plan.sync_work_orders(conn)
    planned["maintenance_plans"] = plan.sync_maintenance_plans(conn)
    notified = intake.sync_notifications(conn)
    board.refresh(conn, pack)  # last, so the exceptions just raised are on it
    return {"plan": planned, "notifications": notified}



def first_scan(conn, pack: Pack, days: int = 7) -> dict | None:
    """On a deployment no SRE scan has run yet, do what the 6-hourly `sre_scan` job and an engineer's first
    `limits learn` / `limits scan` would: learn normal ranges (when none are learned), check limits over the data's
    last 6 hours (IOW breaches reach the IOC), scan the last `days` for upsets (the scheduled job's first run covers
    only one day, which on a quiet day holds nothing of note) and hand them off, without diagnosis
    (that costs a model call, so the scheduled job does it under its cap). None when a scan has already run, so a
    second start changes nothing. The caller loads the work engine's process definitions first, as the
    `sre_scan` job does."""
    from datetime import timedelta

    from ..graph import operations
    from . import board, sre, sre_handoff

    if sre.watermark(conn) is not None or not conn.execute("select 1 from entity where kind = 'timeseries' limit 1").fetchone():
        return None
    end = datetime.fromisoformat(f"{operations.as_of(conn)}T23:59")
    out: dict[str, Any] = {}
    if not conn.execute("select 1 from point_normal limit 1").fetchone():
        learned = limits.learn(conn, pack, (end - timedelta(days=90)).isoformat(timespec="minutes"),
                               end.isoformat(timespec="minutes"), 60)
        out["learned"] = {k: v for k, v in learned.items() if k != "errors"}
        conn.commit()
    checked = limits.scan(conn, pack, (end - timedelta(hours=6)).isoformat(timespec="minutes"),
                          end.isoformat(timespec="minutes"), 10)
    out["limits"] = {"excursions": checked["excursions"], "iow_breaches": len(checked.get("work") or [])}
    conn.commit()
    out["sre"] = sre.scan(conn, pack, since=(end - timedelta(days=days)).isoformat(timespec="minutes"),
                          max_days=days)
    handed = sre_handoff.handoff(conn, pack, max_diagnoses=0)
    out["handoff"] = {k: v for k, v in handed.items() if k != "items"}
    from ..core import detectors  # compressor trips join their upsets' cases; flagged machines reach the IOC

    def ran(name: str, keep=(), **kw) -> dict:  # a detector's result, or why it did not run here (off, not applicable)
        res = detectors.run(conn, pack, name, **kw)
        return res if "status" in res else {**{k: res[k] for k in keep}, "raised": len(res["raised"])}

    out["trips"] = ran("compressor_trips", ("trips",), since=out["sre"]["since"], until=out["sre"]["until"])
    out["machine_health"] = ran("machine_health", ("checked",))
    out["records_gaps"] = ran("records_gaps")  # downtime with no log or notification, and logged stops with none (#344)
    board.refresh(conn, pack)
    conn.commit()
    return out
