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
from deltalake import write_deltalake
from fakeplant.lake import write_parquet
from fastapi.testclient import TestClient

from fakeplant import offshore
from fuzer.core import db
from fuzer.sources import connectors, ingest


@pytest.fixture(scope="module")
def exports():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzdata"))
    offshore.generate(data)
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
    for name in ingest.tables():
        if (lake / f"{name}.csv").exists():
            write_parquet(lake / f"{name}.csv", lake / name, rows_per_file=25)
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
    assert set(stats["loaded"]) >= {n for n in ingest.tables() if (lake / n).is_dir()}
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



def test_renamed_manifest_field_holds_it_and_new_map_keys_dont(exports, conn):
    import json

    lake = Path(tempfile.mkdtemp(prefix="fzman"))
    shutil.copytree(exports, lake, dirs_exist_ok=True)
    ingest.ingest(conn, lake)
    docs = json.loads((lake / "documents.json").read_text())
    (lake / "documents.json").write_text(json.dumps([{("doc_title" if k == "title" else k): v for k, v in d.items()}
                                                     for d in docs]))
    stats = ingest.ingest(conn, lake, exports=["documents"])
    assert stats["held"] == ["documents"]
    item = conn.execute("select payload from review_item where kind = 'schema' and subject = 'documents'").fetchone()
    assert item["payload"]["removed"] == ["[].title"] and item["payload"]["added"] == ["[].doc_title"]

    sheets = json.loads((lake / "pid_sheets.json").read_text())
    sheets[0].setdefault("positions", {})["99-ZZ-9999"] = [1, 2]  # a new tag on a drawing: data, not a field
    (lake / "pid_sheets.json").write_text(json.dumps(sheets))
    assert ingest.ingest(conn, lake, exports=["pid_sheets"])["loaded"] == ["pid_sheets"]


def test_json_shape():
    shape = dict(map(tuple, connectors.json_shape(
        {"nodes": [{"code": "A", "rate": 1}, {"code": "B", "rate": 2.5, "tags": []}],
         "positions": {"10-V-1": [1, 2], "10-V-2": [3, 4]}, "flag": True})))
    assert shape == {"nodes[].code": "string", "nodes[].rate": "number", "positions{}": "array", "flag": "bool"}

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


def test_excel_sheets_and_json_records(tmp_path):
    """#132: an Excel sheet (header found under title rows, one dataset per sheet) and JSON records (a list,
    a wrapper object, JSON Lines; nested objects flattened) read like any other table."""
    import json

    from openpyxl import Workbook

    wb = Workbook()
    a = wb.active
    a.title = "pumps"
    a.append(["Pump list"])
    a.append(["tag", "rated_flow", "installed"])
    a.append(["10-P-1001A", 120.5, dt.date(2020, 5, 1)])
    a.append([None, None, None])
    wb.create_sheet("motors").append(["tag", "kw"])
    wb["motors"].append(["10-PM-1001A", 75])
    wb.save(tmp_path / "rotating.xlsx")
    (tmp_path / "events.jsonl").write_text('{"id": 1, "where": {"area": "10"}}\n{"id": 2, "where": {"area": "20"}}\n')
    (tmp_path / "wrapped.json").write_text(json.dumps({"records": [{"k": "a"}], "meta": {"v": 1}}))
    (tmp_path / "model.json").write_text(json.dumps({"units": {"a": 1}}))  # a document, not a table

    pumps = connectors.find(tmp_path, "rotating.pumps")
    assert list(pumps.rows()) == [{"tag": "10-P-1001A", "rated_flow": 120.5, "installed": "2020-05-01T00:00:00"}]
    assert pumps.schema() == [["tag", "string"], ["rated_flow", "float"], ["installed", "string"]]
    assert list(connectors.find(tmp_path, "rotating.motors").rows()) == [{"tag": "10-PM-1001A", "kw": 75}]
    assert [r["where.area"] for r in connectors.find(tmp_path, "events").rows()] == ["10", "20"]
    assert list(connectors.find(tmp_path, "wrapped").rows()) == [{"k": "a"}]
    assert connectors.find(tmp_path, "model") is None
    assert connectors.datasets(tmp_path) == ["events", "rotating.motors", "rotating.pumps"]  # .json: only when mapped


def test_mapping_location_and_connector(conn, tmp_path):
    """#132: a mapping can say where its table is (outside the data folder, a workbook sheet) and which
    reader to use; an approved one counts as soon as it's saved."""
    from openpyxl import Workbook

    from fuzer.sources import mappings

    other = tmp_path / "elsewhere"
    other.mkdir()
    wb = Workbook()
    wb.active.title = "list"
    wb.active.append(["tag", "service"])
    wb.active.append(["10-P-1001A", "export"])
    wb.save(other / "pumps book.xlsx")
    data = tmp_path / "data"
    data.mkdir()
    text = ("source: vendor\nrecord_type: pump\nshape: records\ndataset: vendor_pumps\nkey: tag\n"
            "location: '../elsewhere/pumps book.xlsx#list'\nconnector: excel\n")
    assert ingest.dataset(data, "vendor_pumps", conn) is None  # no mapping yet
    conn.execute("insert into mapping_version (source, record_type, dataset, yaml, approved_by) "
                 "values ('vendor', 'pump', 'vendor_pumps', %s, 'test')", (text,))
    conn.commit()
    c = ingest.dataset(data, "vendor_pumps", conn)
    assert c.kind == "excel" and list(c.rows()) == [{"tag": "10-P-1001A", "service": "export"}]
    assert ingest.ingest(conn, data, exports=["vendor_pumps"])["inserted"] == 1

    with pytest.raises(mappings.MappingError, match="unknown connector"):
        mappings.Mapping({"source": "a", "record_type": "b", "shape": "records", "dataset": "d", "connector": "xml"})
    with pytest.raises(mappings.MappingError, match="for tables"):
        mappings.Mapping({"source": "a", "record_type": "b", "shape": "records", "location": "x.csv"})
