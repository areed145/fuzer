"""Connectors: Parquet / Delta lake tables load like CSV exports, unchanged datasets are skipped,
schema changes hold a dataset for review, and watermarked tables read only new rows."""

import datetime as dt
import os
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from fuzer import connectors, db, fakedata, ingest


@pytest.fixture(scope="module")
def exports():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzdata"))
    fakedata.generate(data)
    return data


@pytest.fixture
def conn():
    with db.connect() as c:
        db.reset(c)
        yield c


def as_lake(exports: Path) -> Path:
    """A copy of the exports with every table as a folder of Parquet part files."""
    lake = Path(tempfile.mkdtemp(prefix="fzlake"))
    shutil.copytree(exports, lake, dirs_exist_ok=True)
    for name in ingest.TABLES:
        if (lake / f"{name}.csv").exists():
            connectors.write_parquet(lake / f"{name}.csv", lake / name, rows_per_file=25)
            (lake / f"{name}.csv").unlink()
    return lake


def counts(conn):
    return {(r["source"], r["record_type"]): r["n"] for r in conn.execute(
        "select source, record_type, count(*) as n from source_record group by 1, 2")}


def test_parquet_loads_the_same_records_as_csv(exports, conn):
    ingest.ingest(conn, exports)
    from_csv = counts(conn)
    lake = as_lake(exports)
    assert connectors.find(lake, "sap_equipment").kind == "parquet"
    assert len(list((lake / "pi_points").glob("*.parquet"))) > 1  # several part files

    stats = ingest.ingest(conn, lake)
    assert set(stats["loaded"]) >= {n for n in ingest.TABLES if (lake / n).is_dir()}
    assert stats["inserted"] == stats["removed"] == 0
    # Identical payloads; only manifest records holding absolute file paths change with the folder.
    assert {r["source"] for r in conn.execute("select distinct source from source_record_history")} <= {"edms", "pid"}
    assert counts(conn) == from_csv


def test_unchanged_datasets_are_skipped(exports, conn):
    lake = as_lake(exports)
    first = ingest.ingest(conn, lake)
    again = ingest.ingest(conn, lake)
    assert again["records"] == 0 and again["loaded"] == []
    assert set(again["unchanged"]) == set(first["loaded"])
    assert ingest.ingest(conn, lake, full=True)["records"] == first["records"]


def test_renamed_column_holds_the_dataset_until_accepted(exports, conn, monkeypatch):
    lake = as_lake(exports)
    ingest.ingest(conn, lake)
    before = counts(conn)[("sap", "equipment")]
    t = pq.read_table(lake / "sap_equipment").rename_columns(
        ["maker" if c == "manufacturer" else c for c in pq.read_table(lake / "sap_equipment").column_names])
    shutil.rmtree(lake / "sap_equipment")
    (lake / "sap_equipment").mkdir()
    pq.write_table(t, lake / "sap_equipment" / "part-00000.parquet")

    stats = ingest.ingest(conn, lake)
    assert stats["held"] == ["sap_equipment"]
    item = conn.execute("select * from review_item where kind = 'schema' and status = 'open'").fetchone()
    assert item["payload"]["removed"] == ["manufacturer"] and item["payload"]["added"] == ["maker"]
    assert counts(conn)[("sap", "equipment")] == before
    assert conn.execute("select count(*) as n from source_record where record_type = 'equipment' "
                        "and payload ? 'maker'").fetchone()["n"] == 0

    from fuzer import api

    client = TestClient(api.app)
    assert client.post(f"/review/{item['id']}", json={"decision": "reject", "by": "test"}).status_code == 200
    assert ingest.ingest(conn, lake)["held"] == ["sap_equipment"]  # still held, not asked again
    assert conn.execute("select status from review_item where id = %s", (item["id"],)).fetchone()["status"] == "decided"

    conn.execute("update review_item set status = 'open' where id = %s", (item["id"],))
    conn.commit()
    assert client.post(f"/review/{item['id']}", json={"decision": "accept", "by": "test"}).status_code == 200
    stats = ingest.ingest(conn, lake)
    assert stats["loaded"] == ["sap_equipment"] and stats["updated"] == before


def test_added_column_loads_without_review(exports, conn):
    lake = as_lake(exports)
    ingest.ingest(conn, lake)
    t = pq.read_table(lake / "sap_floc")
    t = t.append_column("plant_section", pa.array(["A"] * t.num_rows))
    shutil.rmtree(lake / "sap_floc")
    (lake / "sap_floc").mkdir()
    pq.write_table(t, lake / "sap_floc" / "part-00000.parquet")
    stats = ingest.ingest(conn, lake)
    assert stats["loaded"] == ["sap_floc"] and stats["held"] == []
    assert conn.execute("select count(*) as n from review_item where kind = 'schema'").fetchone()["n"] == 0


def test_watermark_reads_only_new_log_rows(exports, conn):
    lake = as_lake(exports)
    ingest.ingest(conn, lake)
    logs = pq.read_table(lake / "oplogs")
    top = max(logs.column("timestamp").to_pylist())
    new = pa.Table.from_pylist([{**logs.slice(0, 1).to_pylist()[0], "entry_id": "LOG-NEW-1", "timestamp": "2099-01-01T00:00:00"}],
                               logs.schema)
    pq.write_table(new, lake / "oplogs" / "part-99999.parquet")
    stats = ingest.ingest(conn, lake)
    tied = sum(1 for v in logs.column("timestamp").to_pylist() if v == top)
    assert stats["loaded"] == ["oplogs"] and stats["records"] == tied + 1  # rows at or after the old watermark
    assert stats["inserted"] == 1 and stats["removed"] == 0
    pos = conn.execute("select position from dataset_state where dataset = 'oplogs'").fetchone()["position"]
    assert pos == "2099-01-01T00:00:00"


def test_delta_table(exports, conn):
    from deltalake import write_deltalake

    lake = Path(tempfile.mkdtemp(prefix="fzdelta"))
    t = pq.read_table(as_lake(exports) / "sap_materials")
    write_deltalake(str(lake / "sap_materials"), t)
    c = connectors.find(lake, "sap_materials")
    assert c.kind == "delta" and c.fingerprint() == "delta:0"
    assert ingest.ingest(conn, lake)["records"] == t.num_rows
    write_deltalake(str(lake / "sap_materials"), t.slice(0, 1).set_column(
        0, "matnr", pa.array(["M-NEW"])), mode="append")
    stats = ingest.ingest(conn, lake)
    assert stats["loaded"] == ["sap_materials"] and stats["inserted"] == 1


def test_typed_columns_become_json(tmp_path):
    pq.write_table(pa.table({"id": ["a"], "day": [dt.date(2024, 5, 1)], "at": [dt.datetime(2024, 5, 1, 7, 30)],
                             "cost": pa.array([Decimal("12.50")], pa.decimal128(10, 2)), "n": [3]}),
                   tmp_path / "t.parquet")
    c = connectors.find(tmp_path, "t")
    assert next(c.rows()) == {"id": "a", "day": "2024-05-01", "at": "2024-05-01T07:30:00", "cost": 12.5, "n": 3}
    assert dict(map(tuple, c.schema()))["cost"] == "decimal128(10, 2)"
