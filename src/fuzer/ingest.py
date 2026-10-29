"""Land source exports into source_record, unchanged.

For now sources are local files. Later each loader is replaced by a connector
(SAP OData, PI Web API, ADX, EC views) that yields the same tuples.
"""

import csv
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
}


def read_records(data_dir: Path):
    for name, (source, record_type, key_col) in CSV_FILES.items():
        path = data_dir / name
        if path.exists():
            with path.open(newline="") as f:
                for row in csv.DictReader(f):
                    yield source, record_type, row[key_col], row

    if (path := data_dir / "pi_af.json").exists():
        for el in json.loads(path.read_text()):
            yield "pi_af", "element", el["path"], el

    if (path := data_dir / "ec_network.json").exists():
        net = json.loads(path.read_text())
        nodes = {n["code"]: n for n in net["nodes"]}
        for n in net["nodes"]:
            yield "ec", "node", n["code"], n
        for e in net["edges"]:
            key = f"{e['from']}>{e['to']}@{e.get('valid_from') or ''}"
            yield "ec", "edge", key, {**e, "from": nodes[e["from"]], "to": nodes[e["to"]]}

    if (path := data_dir / "pid_sheets.json").exists():
        for s in json.loads(path.read_text()):
            yield "pid", "sheet", s["sheet"], s


def ingest(conn, data_dir) -> int:
    rows = [(s, rt, k, Jsonb(p)) for s, rt, k, p in read_records(Path(data_dir))]
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into source_record (source, record_type, source_key, payload)
            values (%s, %s, %s, %s)
            on conflict (source, record_type, source_key)
            do update set payload = excluded.payload, last_seen = now()
            """,
            rows,
        )
    conn.commit()
    return len(rows)
