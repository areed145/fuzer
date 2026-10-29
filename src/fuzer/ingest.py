"""Land source exports into source_record, unchanged.

For now sources are local files, one per export. Later each loader is replaced by a connector
(SAP OData, PI Web API, ADX, EC views) that yields the same tuples.
"""

import csv
import hashlib
import json
from pathlib import Path

from psycopg.types.json import Jsonb

# file name -> (source, record_type, key column)
CSV_FILES = {
    "sap_floc.csv": ("sap", "floc", "floc"),
    "sap_equipment.csv": ("sap", "equipment", "equnr"),
    "jde_assets.csv": ("jde", "asset", "asset_number"),
    "maximo_assets.csv": ("maximo", "asset", "assetnum"),
    "pi_points.csv": ("pi", "point", "tag"),
    "oplogs.csv": ("oplog", "entry", "entry_id"),
    "sap_notifications.csv": ("sap", "notification", "qmnum"),
    "sap_work_orders.csv": ("sap", "work_order", "aufnr"),
    "ec_downtime.csv": ("ec", "downtime", "event_id"),
    "ec_day001.csv": ("ec", "daily_report", "report_date"),
}


def _csv(name):
    source, record_type, key_col = CSV_FILES[name]

    def load(path: Path):
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                yield source, record_type, row[key_col], row
    return load


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


# export name -> (file name, loader). One export = one file = one full snapshot of that source.
EXPORTS = {
    **{name.removesuffix(".csv"): (name, _csv(name)) for name in CSV_FILES},
    "pi_af": ("pi_af.json", _pi_af),
    "ec_network": ("ec_network.json", _ec_network),
    "pid_sheets": ("pid_sheets.json", _pid_sheets),
    "documents": ("documents.json", _documents),
}


def read_records(data_dir: Path, exports: list[str] | None = None):
    for name in exports or EXPORTS:
        file, load = EXPORTS[name]
        if (path := data_dir / file).exists():
            yield from load(path)


def ingest(conn, data_dir, exports: list[str] | None = None, snapshot: bool = True) -> dict:
    """Load exports from data_dir (all of them, or the named ones). With snapshot (the default),
    each file is a full export: records of that source and type that are no longer in it are
    removed. Every update or removal keeps the previous version in source_record_history."""
    from . import db

    conn.execute("drop table if exists source_record_stage")
    conn.execute("create temp table source_record_stage (source text, record_type text, source_key text, "
                 "payload jsonb) on commit drop")
    n = db.copy_rows(conn, "source_record_stage", ["source", "record_type", "source_key", "payload"],
                     ((s, rt, k, Jsonb(p)) for s, rt, k, p in read_records(Path(data_dir), exports)))
    conn.execute("analyze source_record_stage")
    match = "r.source = s.source and r.record_type = s.record_type and r.source_key = s.source_key"
    loaded = "(r.source, r.record_type) in (select distinct source, record_type from source_record_stage)"
    removed = 0
    if snapshot:
        conn.execute(f"""
            insert into source_record_history (source, record_type, source_key, payload, first_seen, change)
            select r.source, r.record_type, r.source_key, r.payload, r.first_seen, 'removed'
            from source_record r
            where {loaded} and not exists (select 1 from source_record_stage s where {match})
        """)
        removed = conn.execute(f"""
            delete from source_record r
            where {loaded} and not exists (select 1 from source_record_stage s where {match})
        """).rowcount
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
    conn.commit()
    return {"records": n, "inserted": inserted, "updated": updated, "removed": removed}
