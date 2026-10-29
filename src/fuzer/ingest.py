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


# export name -> (file name, loader). One export = one file = one full snapshot of that source.
EXPORTS = {
    **{name.removesuffix(".csv"): (name, _csv(name)) for name in CSV_FILES},
    "pi_af": ("pi_af.json", _pi_af),
    "ec_network": ("ec_network.json", _ec_network),
    "pid_sheets": ("pid_sheets.json", _pid_sheets),
}


def read_records(data_dir: Path, exports: list[str] | None = None):
    for name in exports or EXPORTS:
        file, load = EXPORTS[name]
        if (path := data_dir / file).exists():
            yield from load(path)


def ingest(conn, data_dir, exports: list[str] | None = None, snapshot: bool = True) -> dict:
    """Load exports from data_dir (all of them, or the named ones). With snapshot (the default),
    each file is a full export: records of that source and type that are no longer in it are
    removed, and the next rebuild retires their entities."""
    rows = [(s, rt, k, Jsonb(p)) for s, rt, k, p in read_records(Path(data_dir), exports)]
    removed = 0
    if snapshot:
        loaded: dict[tuple[str, str], list[str]] = {}
        for s, rt, k, _ in rows:
            loaded.setdefault((s, rt), []).append(k)
        for (s, rt), keys in loaded.items():
            removed += conn.execute("delete from source_record where source = %s and record_type = %s "
                                    "and source_key <> all(%s)", (s, rt, keys)).rowcount
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into source_record (source, record_type, source_key, payload)
            values (%s, %s, %s, %s)
            on conflict (source, record_type, source_key)
            do update set payload = excluded.payload, last_seen = now()
            where source_record.payload is distinct from excluded.payload
            """,
            rows,
        )
    conn.commit()
    return {"records": len(rows), "removed": removed}
