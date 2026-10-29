"""Land source datasets into source_record, unchanged.

Tables (ERP, EC, PI point lists, logs) come through a connector (connectors.py): a CSV export,
a folder of Parquet part files or a Delta table, found by name in the data folder. Nested
exports (PI AF tree, EC network, P&ID sheets, document manifests) have their own readers.
Each load is remembered in dataset_state (fingerprint, schema, watermark): unchanged datasets
are skipped without reading a row, and a removed or retyped column holds the dataset until a
reviewer accepts the new schema.
"""

import hashlib
import json
from pathlib import Path

from psycopg.types.json import Jsonb

from . import connectors

# table name -> (source, record_type, key column, or columns joined by ">")
TABLES = {
    "sap_floc": ("sap", "floc", "floc"),
    "sap_equipment": ("sap", "equipment", "equnr"),
    "jde_assets": ("jde", "asset", "asset_number"),
    "maximo_assets": ("maximo", "asset", "assetnum"),
    "pi_points": ("pi", "point", "tag"),
    "oplogs": ("oplog", "entry", "entry_id"),
    "sap_notifications": ("sap", "notification", "qmnum"),
    "sap_work_orders": ("sap", "work_order", "aufnr"),
    "ec_downtime": ("ec", "downtime", "event_id"),
    "sap_materials": ("sap", "material", "matnr"),
    "ec_day001": ("ec", "daily_report", "report_date"),
    "sap_bom": ("sap", "bom", ("equnr", "matnr")),
}

# Append-only tables: lake connectors read only rows at or after the last value of this column
# (CSV exports are always read whole). Rows already loaded are never removed by such a load.
WATERMARKS = {"oplogs": "timestamp"}


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


# nested export -> (file name, field naming files it references, reader). Fingerprints cover those files too.
FILES = {
    "pi_af": ("pi_af.json", None, _pi_af),
    "ec_network": ("ec_network.json", None, _ec_network),
    "pid_sheets": ("pid_sheets.json", "image", _pid_sheets),
    "documents": ("documents.json", "file", _documents),
}

EXPORTS = [*TABLES, *FILES]


class _Manifest:
    """A nested JSON export read by its own loader; no column schema."""

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
        return None


def dataset(data_dir: Path, name: str):
    """The connector for one export in data_dir, or None when it isn't there."""
    if name in FILES:
        file, ref, load = FILES[name]
        return _Manifest(data_dir / file, ref, load) if (data_dir / file).is_file() else None
    return connectors.find(data_dir, name)


def _records(name: str, conn_, since=None):
    if name in FILES:
        yield from conn_.load(conn_.path)
        return
    source, record_type, key = TABLES[name]
    cols = key if isinstance(key, tuple) else (key,)
    for row in conn_.rows(since):
        yield source, record_type, ">".join(str(row[c]) for c in cols), row


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


def ingest(conn, data_dir, exports: list[str] | None = None, snapshot: bool = True, full: bool = False) -> dict:
    """Load datasets from data_dir (all of them, or the named ones). With snapshot (the default),
    each dataset is a full export: records of that source and type no longer in it are removed,
    except for watermarked lake tables, which only add and update. Every update or removal keeps
    the previous version in source_record_history. Unchanged datasets are skipped; `full` reads
    everything again and restarts watermarks."""
    from . import db

    data_dir = Path(data_dir)
    state = {r["dataset"]: r for r in conn.execute("select * from dataset_state")}
    report = {"loaded": [], "unchanged": [], "held": []}
    loaded_state = []

    plan = []  # decide every dataset before COPY starts: nothing else may run on conn during it
    for name in exports or EXPORTS:
        if (c := dataset(data_dir, name)) is None:
            continue
        prev = state.get(name) or {}
        fp = c.fingerprint()
        if not full and prev.get("fingerprint") == fp:
            report["unchanged"].append(name)
            continue
        schema = c.schema()
        drift = connectors.schema_drift(prev.get("schema"), schema) if schema else None
        if drift and (drift["removed"] or drift["retyped"]):
            _hold(conn, name, c, prev.get("schema"), schema, drift)
            report["held"].append(name)
            continue
        plan.append((name, c, fp, schema, prev.get("position")))

    def rows():
        for name, c, fp, schema, position in plan:
            wm = WATERMARKS.get(name) if c.watermarks else None
            since = (wm, position) if wm and position is not None and not full else None
            top = since[1] if since else None
            n = 0
            for source, record_type, key, payload in _records(name, c, since):
                if wm and (v := payload.get(wm)) is not None and (top is None or str(v) > str(top)):
                    top = str(v)
                n += 1
                yield source, record_type, key, Jsonb(payload), snapshot and since is None
            report["loaded"].append(name)
            loaded_state.append((name, c.kind, fp, Jsonb(schema), Jsonb(top), n))

    conn.execute("drop table if exists source_record_stage")
    conn.execute("create temp table source_record_stage (source text, record_type text, source_key text, "
                 "payload jsonb, snap boolean) on commit drop")
    n = db.copy_rows(conn, "source_record_stage", ["source", "record_type", "source_key", "payload", "snap"], rows())
    conn.execute("analyze source_record_stage")
    match = "r.source = s.source and r.record_type = s.record_type and r.source_key = s.source_key"
    loaded = "(r.source, r.record_type) in (select distinct source, record_type from source_record_stage where snap)"
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
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into dataset_state (dataset, connector, fingerprint, schema, position, rows, loaded_at)
            values (%s, %s, %s, %s, %s, %s, now())
            on conflict (dataset) do update
            set connector = excluded.connector, fingerprint = excluded.fingerprint, schema = excluded.schema,
                position = excluded.position, rows = excluded.rows, loaded_at = now()
            """,
            loaded_state,
        )
    conn.commit()
    return {"records": n, "inserted": inserted, "updated": updated, "removed": removed, **report}
