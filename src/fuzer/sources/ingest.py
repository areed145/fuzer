"""Land source datasets into source_record, unchanged.

What to load comes from the source catalog (catalog.py, #225): each active dataset, read through its
connection (connections.py): a table in a folder (CSV, Parquet, Delta, Excel, JSON, GeoJSON), a nested export
with its own reader (PI AF tree, EC network, P&ID sheets, document manifests), or a live system (PI Web API,
ADX). Each dataset loads as its load_mode says: snapshot (records no longer there are removed), incremental
(add and update only) or append (rows at or after the watermark). An unchanged dataset is skipped without
reading a row; a changed schema holds it for review as its drift_policy says. Each load that changed something
is a source_run row, and the dataset keeps its state (fingerprint, schema, watermark position).
"""

import hashlib
import json
from pathlib import Path

import time
from typing import Callable, NamedTuple

from psycopg.types.json import Jsonb

from . import catalog, connections, connectors, mappings, quality


class DatasetNotFound(ValueError):
    """An explicitly requested dataset is not in the catalog."""


class DatasetUnavailable(ValueError):
    """An explicitly requested active dataset cannot be read through its connection."""


def tables(conn=None) -> dict:
    """Tabular datasets by name, from the mapping files and approved mappings (mappings.py)."""
    return mappings.tables(conn)


def unmapped(data_dir, conn=None) -> list[str]:
    """Tables in data_dir (any connector: CSV, Parquet, lake folders, Delta, GeoJSON, Excel sheets, JSON)
    that no mapping covers."""
    data_dir, known = Path(data_dir), set(tables(conn)) | set(READERS)
    if not data_dir.is_dir():
        return []
    if conn is not None:
        known |= set(catalog.datasets(conn))
    return [n for n in connectors.datasets(data_dir) if n not in known]


def _pi_af(path: Path):
    for el in json.loads(path.read_text()):
        yield "pi_af", "element", el["path"], el


def _ec_network(path: Path):
    net = json.loads(path.read_text())
    nodes = {n["code"]: n for n in net["nodes"]}
    for n in net["nodes"]:
        yield "ec", "node", n["code"], n
    for e in net["edges"]:
        key = f"{e['from']}>{e['to']}@{e.get('valid_from') or ''}"
        yield "ec", "edge", key, {**e, "from": nodes[e["from"]], "to": nodes[e["to"]]}


def _pid_sheets(path: Path):
    for s in json.loads(path.read_text()):
        if image := s.get("image"):
            # Keep where the scan is and a fingerprint of its content; the bytes stay on disk
            # (later: blob storage), and the fingerprint tells us when a drawing was re-scanned.
            file = (path.parent / image).resolve()
            s = {**s, "image_path": str(file),
                 "image_sha256": hashlib.sha256(file.read_bytes()).hexdigest() if file.exists() else None}
        yield "pid", "sheet", s["sheet"], s


def _documents(path: Path):
    """EDMS-style manifest: one record per document revision, plus one per section of its file."""
    from .documents import sections

    for d in json.loads(path.read_text()):
        key = f"{d['doc_number']} rev {d['revision']}"
        file = (path.parent / d["file"]).resolve() if d.get("file") else None
        secs, facts = sections(file) if file and file.exists() else ([], {"missing_file": True})
        yield "edms", "document", key, {**d, **facts, "file_path": str(file) if file else None,
                                        "file_sha256": hashlib.sha256(file.read_bytes()).hexdigest()
                                        if file and file.exists() else None, "sections": len(secs)}
        for i, sec in enumerate(secs, 1):
            yield "edms", "section", f"{key}#{i:03d}", {**sec, "document": key, "index": i,
                                                       "status": d.get("status", "current")}


class Reader(NamedTuple):
    """A nested export read by its own loader: its file, the field naming files it references (fingerprints
    cover those too), the loader, and the source and record types its records land as."""
    file: str
    ref: str | None
    load: Callable
    source: str
    record_types: tuple[str, ...]


READERS = {
    "pi_af": Reader("pi_af.json", None, _pi_af, "pi_af", ("element",)),
    "ec_network": Reader("ec_network.json", None, _ec_network, "ec", ("node", "edge")),
    "pid_sheets": Reader("pid_sheets.json", "image", _pid_sheets, "pid", ("sheet",)),
    "documents": Reader("documents.json", "file", _documents, "edms", ("document", "section")),
}
FILES = READERS  # the name before #225


def export_names(conn=None) -> list[str]:
    """Every dataset: the catalog's active ones with a connection, else (no database) the seed files'."""
    if conn is not None:
        catalog.ensure(conn)
        return list(catalog.datasets(conn, ("active",)))
    return [*tables(conn), *READERS]


class _Manifest:
    """A nested JSON export read by its own loader. Its schema is the shape of the JSON (field paths and
    types), so a renamed or retyped field holds it for review like a table's column would."""

    kind = "json"
    watermarks = False

    def __init__(self, path: Path, ref_field: str | None, load):
        self.path, self.ref_field, self.load = path, ref_field, load

    def fingerprint(self) -> str:
        files = [self.path]
        if self.ref_field:
            items = json.loads(self.path.read_text())
            files += sorted({f for item in items if (ref := item.get(self.ref_field))
                             and (f := (self.path.parent / ref).resolve()).is_file()})
        parts = [f"{f}:{f.stat().st_size}:{f.stat().st_mtime_ns}" for f in files]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()

    def schema(self):
        return connectors.json_shape(json.loads(self.path.read_text())) or None

    def records(self):
        return self.load(self.path)


def dataset(data_dir: Path, name: str, conn=None):
    """The reader for one dataset, None when it isn't there. With a connection, through the dataset's catalog
    connection (a folder, PI Web API, ADX); without one, as the seed files describe it (a live system when the
    old settings name one)."""
    if conn is not None:
        catalog.ensure(conn)
        if (d := catalog.dataset(conn, name)) is None:
            return None
        return connections.connector(d, data_dir)
    if (live := connectors.live(name)) is not None:
        return live
    if name in READERS:
        return connections.connector({"name": name, "reader": name}, data_dir)
    m = tables().get(name)
    return connections.connector({"name": name, "location": m.location if m else None,
                                  "connector": m.connector if m else None}, data_dir)


def _records(name: str, c, d: dict, since=None):
    """(source, record_type, key, payload) per record, with the system's number and date formats read
    (formats.py) before the key is taken."""
    from .formats import Formats

    fmt = Formats.of(d)
    if d.get("reader"):
        for source, rtype, key, payload in c.records():
            yield source, rtype, key, fmt.row(payload)
        return
    cols = d["key"] or ()
    for row in c.rows(since):
        row = fmt.row(row)
        yield d["source"], d["record_type"], ">".join(str(row[k]) for k in cols), row


def _hold(conn, name: str, c, old_schema, new_schema, drift: dict) -> None:
    """Open (or keep) a schema review item. A reviewer who rejected this exact schema is not asked again."""
    conn.execute(
        """
        insert into review_item (kind, subject, payload) values ('schema', %s, %s)
        on conflict (kind, subject) do update
        set payload = excluded.payload,
            status = case when review_item.payload->'new' = excluded.payload->'new' then review_item.status
                          else 'open' end
        """,
        (name, Jsonb({"dataset": name, "connector": c.kind, "path": str(getattr(c, "path", "")),
                      "old": old_schema, "new": new_schema, **drift})),
    )


def _drift_holds(policy: str, drift: dict | None) -> bool:
    if not drift or policy == "ignore":
        return False
    if policy == "hold_any":
        return bool(drift["removed"] or drift["retyped"] or drift["added"])
    return bool(drift["removed"] or drift["retyped"])


def ingest(conn, data_dir, exports: list[str] | None = None, snapshot: bool = True, full: bool = False,
           trigger: str = "manual", *, allow_missing: bool = False) -> dict:
    """Load datasets from their connections (every active one, or the named ones; data_dir is the project's
    data folder, where folder connections without a path look). Each loads as its load_mode says;
    `snapshot=False` loads them all incrementally. Every update or removal keeps the previous version in
    source_record_history. Unchanged datasets are skipped; `full` reads everything again and restarts
    watermarks. Paused and retired datasets are skipped (reported as `paused`). Explicit names must exist
    and active ones must be available; bulk discovery and scheduler discovery (`allow_missing`) report
    absent exports as `missing`. Repeated names load once."""
    from ..core import db

    catalog.ensure(conn)
    data_dir = Path(data_dir)
    ds = catalog.datasets(conn)
    names = list(dict.fromkeys(exports)) if exports else [
        n for n, d in ds.items() if d["status"] in ("active", "paused")]
    for name in names:
        if name not in ds:
            raise DatasetNotFound(f"no dataset {name!r}: inspect Sources before requesting a load")
    used = {f"{m.source}/{m.record_type}": m.version for m in mappings.load(conn).values()}
    report = {"loaded": [], "unchanged": [], "held": [], "paused": [], "missing": [],
              "unmapped": unmapped(data_dir, conn) if not exports else []}
    plan, runs = [], []  # decide every dataset before COPY starts: nothing else may run on conn during it
    for name in names:
        d = ds[name]
        if d["status"] != "active":
            report["paused"].append(name)
            continue
        if (c := connections.connector(d, data_dir)) is None:
            if exports and not allow_missing:
                raise DatasetUnavailable(
                    f"dataset {name!r} is unavailable through its connection: inspect Sources connection "
                    "and recorded runs before deliberately retrying")
            report["missing"].append(name)
            continue
        fp = c.fingerprint()
        if not full and d["fingerprint"] == fp:
            report["unchanged"].append(name)
            conn.execute("update source_dataset set checked_at = now() where id = %s", (d["id"],))
            continue
        schema = c.schema()
        drift = connectors.schema_drift(d["schema"], schema) if schema else None
        if _drift_holds(d["drift_policy"], drift):
            _hold(conn, name, c, d["schema"], schema, drift)
            report["held"].append(name)
            runs.append({"d": d, "status": "held", "connector": c.kind, "fingerprint": fp, "schema": schema,
                         "detail": drift})
            continue
        plan.append((d, c, fp, schema))

    counts: dict[str, dict] = {}

    def rows():
        for d, c, fp, schema in plan:
            t0 = time.perf_counter()
            wm = d["watermark"] if c.watermarks and d["load_mode"] == "append" else None
            since = (wm, d["position"]) if wm and d["position"] is not None and not full else None
            top = since[1] if since else None
            # a full read is a snapshot (append too, until its connector reads from a watermark position)
            snap = snapshot and d["load_mode"] != "incremental" and since is None
            n, prof = 0, quality.Profiler()
            for source, record_type, key, payload in _records(d["name"], c, d, since):
                if wm and (v := payload.get(wm)) is not None and (top is None or str(v) > str(top)):
                    top = str(v)
                n += 1
                prof.add(payload)
                yield d["name"], source, record_type, key, Jsonb(payload), snap
            counts[d["name"]] = {"d": d, "status": "done", "connector": c.kind, "fingerprint": fp, "schema": schema,
                                 "position": top, "rows_read": n, "seconds": time.perf_counter() - t0,
                                 "profile": prof.result()}

    try:
        conn.execute("drop table if exists source_record_stage")
        conn.execute("create temp table source_record_stage (dataset text, source text, record_type text, "
                     "source_key text, payload jsonb, snap boolean) on commit drop")
        n = db.copy_rows(conn, "source_record_stage", ["dataset", "source", "record_type", "source_key", "payload", "snap"],
                         rows())
        conn.execute("analyze source_record_stage")
        _check(conn, counts, report)
        totals = _merge(conn, snapshot, {k: v for k, v in counts.items() if v["status"] == "done"})
        report["loaded"] = [k for k, v in counts.items() if v["status"] == "done"]
    except Exception as e:
        conn.rollback()
        for d, c, fp, schema in plan:
            _run(conn, {"d": d, "status": "failed", "connector": c.kind, "fingerprint": fp, "error": f"{type(e).__name__}: {e}"},
                 used, trigger)
        conn.commit()
        _alert(conn, [d["name"] for d, *_ in plan])
        raise
    for r in [*runs, *counts.values()]:
        run_id = _run(conn, r, used, trigger)
        if "profile" in r:
            p = quality.save_profile(conn, r["d"], run_id, r["profile"], r.get("key"))
            if p["drift"]:
                report.setdefault("profile_drift", {})[r["d"]["name"]] = p["drift"]
        quality.store_results(conn, r.get("checks") or [], run_id)
        if r["status"] == "done":
            quality.clear_check_hold(conn, r["d"]["name"])
    conn.commit()
    _alert(conn, [r["d"]["name"] for r in [*runs, *counts.values()]])
    return {"records": n, **totals, **report}


def _alert(conn, names: list[str]) -> None:
    """Tell the owners of the datasets just loaded about new problems (and close cleared ones). Alerting never
    fails a load."""
    import logging

    from . import alerts

    if not names:
        return
    try:
        alerts.check(conn, names)
        conn.commit()
    except Exception:  # noqa: BLE001
        conn.rollback()
        logging.getLogger(__name__).exception("source alerts failed")


def _check(conn, counts: dict[str, dict], report: dict) -> None:
    """Load checks on the staged rows. A dataset with duplicate keys, or failing a blocking check, is held: its
    staged rows are dropped (its records stay as they were) and a `check` review item says why."""
    keys = quality.key_stats(conn, list(counts))
    for name, r in counts.items():
        r["key"] = keys.get(name)
        r["checks"] = quality.run_load_checks(conn, r["d"], r["rows_read"], r["key"])
        failed = [x for x in r["checks"] if not x["ok"] and x["blocking"]]
        if r["key"]["duplicates"]:
            failed.insert(0, {"kind": "duplicate keys", "value": r["key"]["duplicates"],
                              "detail": {"examples": r["key"]["examples"]}})
        if failed:
            conn.execute("delete from source_record_stage where dataset = %s", (name,))
            quality.hold_for_checks(conn, name, failed)
            r |= {"status": "held", "detail": {"checks": failed}}
            report["held"].append(name)


def _merge(conn, snapshot: bool, counts: dict[str, dict]) -> dict:
    """Apply the staged rows to source_record (history first), counting per dataset."""
    match = "r.source = s.source and r.record_type = s.record_type and r.source_key = s.source_key"
    for r in conn.execute(f"""
        select s.dataset, count(*) filter (where r.source_key is null) as inserted,
               count(*) filter (where r.source_key is not null and r.payload is distinct from s.payload) as updated,
               count(*) filter (where r.source_key is not null and r.payload is not distinct from s.payload) as unchanged
        from source_record_stage s left join source_record r on {match} group by s.dataset
    """):
        counts[r["dataset"]] |= {k: r[k] for k in ("inserted", "updated", "unchanged")}
    loaded = "(r.source, r.record_type) in (select distinct source, record_type from source_record_stage where snap)"
    removed = 0
    if snapshot:
        conn.execute(f"""
            insert into source_record_history (source, record_type, source_key, payload, first_seen, change)
            select r.source, r.record_type, r.source_key, r.payload, r.first_seen, 'removed'
            from source_record r
            where {loaded} and not exists (select 1 from source_record_stage s where {match})
        """)
        owner = {(r["source"], r["record_type"]): r["dataset"] for r in conn.execute(
            "select distinct dataset, source, record_type from source_record_stage where snap")}
        for r in conn.execute(f"""
            with gone as (delete from source_record r
                          where {loaded} and not exists (select 1 from source_record_stage s where {match})
                          returning r.source, r.record_type)
            select source, record_type, count(*) as n from gone group by 1, 2
        """):
            removed += r["n"]
            if (name := owner.get((r["source"], r["record_type"]))) in counts:
                counts[name]["removed"] = counts[name].get("removed", 0) + r["n"]
    updated = conn.execute(f"""
        insert into source_record_history (source, record_type, source_key, payload, first_seen, change)
        select r.source, r.record_type, r.source_key, r.payload, r.first_seen, 'updated'
        from source_record r join source_record_stage s on {match}
        where r.payload is distinct from s.payload
    """).rowcount
    conn.execute(f"""
        update source_record r set payload = s.payload, last_seen = now()
        from source_record_stage s where {match} and r.payload is distinct from s.payload
    """)
    inserted = conn.execute(f"""
        insert into source_record (source, record_type, source_key, payload)
        select s.source, s.record_type, s.source_key, s.payload from source_record_stage s
        where not exists (select 1 from source_record r where {match})
    """).rowcount
    return {"inserted": inserted, "updated": updated, "removed": removed}


def _run(conn, r: dict, used: dict, trigger: str) -> int:
    """One source_run row; a load that went through also moves the dataset's state on."""
    d = r["d"]
    mine = {f"{s}/{t}": used.get(f"{s}/{t}") for s, t in catalog.produces(d)}
    row = conn.execute(
        """
        insert into source_run (dataset_id, dataset, trigger, status, finished_at, connector, fingerprint, schema,
                                position, mappings, rows_read, inserted, updated, unchanged, removed, error, detail, seconds)
        values (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id
        """, (d["id"], d["name"], trigger, r["status"], r.get("connector"), r.get("fingerprint"), Jsonb(r.get("schema")),
              Jsonb(r.get("position")), Jsonb(mine), r.get("rows_read"), r.get("inserted", 0 if r["status"] == "done" else None),
              r.get("updated", 0 if r["status"] == "done" else None), r.get("unchanged", 0 if r["status"] == "done" else None),
              r.get("removed", 0 if r["status"] == "done" else None), r.get("error"), Jsonb(r.get("detail")),
              round(r["seconds"], 3) if r.get("seconds") is not None else None)).fetchone()
    run_id = row["id"]
    if r["status"] == "done":
        conn.execute(
            """
            update source_dataset set loaded_with = %s, fingerprint = %s, schema = %s, position = %s, rows = %s,
                   loaded_at = now(), checked_at = now(), last_run_id = %s where id = %s
            """, (r["connector"], r["fingerprint"], Jsonb(r["schema"]), Jsonb(r["position"]), r["rows_read"], row["id"],
                  d["id"]))
    else:
        conn.execute("update source_dataset set checked_at = now(), last_run_id = %s where id = %s", (row["id"], d["id"]))
    return run_id
