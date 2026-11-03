"""Sources before they load (#132): the folder's tables with their state, and a preview of one."""

import csv
import shutil

import pytest

from fuzer.core.pack import load_pack
from fuzer.sources import preview

PACK = load_pack()


@pytest.fixture(scope="module")
def conn(offshore_db):
    return offshore_db.conn


def test_listing_shows_each_table_and_its_state(conn, offshore_db, tmp_path):
    rows = {r["dataset"]: r for r in preview.listing(conn, offshore_db.data)}
    assert rows["sap_equipment"]["state"] == "unchanged" and rows["sap_equipment"]["mapping"] == "sap/equipment"
    assert rows["sap_equipment"]["connector"] == "csv" and rows["sap_equipment"]["rows"] > 0
    assert rows["pi_af"]["connector"] == "json"  # a nested manifest with its own loader

    data = tmp_path / "data"
    shutil.copytree(offshore_db.data, data, ignore=shutil.ignore_patterns("pid", "documents"))
    with (data / "sap_equipment.csv").open("a") as f:
        f.write("\n")  # touched: changed since the last load
    (data / "vendor_list.csv").write_text("vendor,country\nSulzer,CH\n")
    rows = {r["dataset"]: r for r in preview.listing(conn, data)}
    assert rows["sap_equipment"]["state"] == "changed" and rows["vendor_list"]["state"] == "unmapped"


def test_preview_of_a_mapped_table_shows_what_it_makes(conn, offshore_db):
    p = preview.preview(conn, PACK, offshore_db.data, "sap_notifications", rows=3)
    assert p["mapping"] == "sap/notification" and len(p["sample"]) == 3
    assert p["drift"] == {"added": [], "removed": [], "retyped": []} and not p["held"]
    made = p["makes"][0]
    assert made["entities"][0]["kind"] == "notification" and any(x["rel"] == "concerns" for x in made["links"])


def test_preview_of_a_new_table_drafts_a_mapping(conn, tmp_path):
    with (tmp_path / "instrument_index.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["tag_no", "service"], ["20-PT-1001B", "Pump discharge pressure"],
                                 ["20-PT-1002B", "Pump suction pressure"]])
    p = preview.preview(conn, PACK, tmp_path, "instrument_index")
    assert p["connector"] == "csv" and p["suggested"]["key"] == "tag_no" and "dataset: instrument_index" in p["suggested"]["yaml"]
    with pytest.raises(ValueError, match="no table"):
        preview.preview(conn, PACK, tmp_path, "nothing_here")


def test_api(conn):
    from fastapi.testclient import TestClient

    from fuzer import api

    client = TestClient(api.app)
    listed = client.get("/sources").json()
    assert any(s["dataset"] == "sap_notifications" and s["state"] == "unchanged" for s in listed["sources"])
    one = client.get("/sources/sap_notifications", params={"rows": 2}).json()
    assert len(one["sample"]) == 2 and one["makes"][0]["entities"]
    assert client.get("/sources/nothing_here").status_code == 404


def test_add_a_table_in_one_flow(conn, tmp_path, monkeypatch):
    """Sources page (#132): the draft mapping, edited, saved and loaded without the review queue."""
    from fastapi.testclient import TestClient

    from fuzer import api

    with (tmp_path / "loop_index.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["tag_no", "service"], ["20-PT-1001B", "Pump discharge pressure"],
                                 ["20-PT-1002B", "Pump suction pressure"]])
    monkeypatch.setenv("FUZER_DATA_DIR", str(tmp_path))
    client = TestClient(api.app)
    assert client.post("/sources/loop_index/load", json={}).status_code == 400  # no mapping yet
    draft = client.post("/sources/loop_index/suggest", json={}).json()
    assert draft["key"] == "tag_no" and draft["claude"] is None
    bad = client.post("/sources/loop_index/load", json={"yaml": draft["yaml"].replace(
        "dataset: loop_index", "dataset: other"), "by": "test"})
    assert bad.status_code == 400 and "keep dataset" in bad.json()["detail"]

    r = client.post("/sources/loop_index/load", json={"yaml": draft["yaml"], "by": "test"}).json()
    assert r["loaded"] == ["loop_index"] and r["inserted"] == 2
    assert conn.execute("select approved_by from mapping_version where dataset = 'loop_index'"
                        ).fetchone()["approved_by"] == "test"
    again = client.post("/sources/loop_index/load", json={}).json()
    assert again["unchanged"] == ["loop_index"]
    assert {s["dataset"]: s["state"] for s in client.get("/sources").json()["sources"]}["loop_index"] == "unchanged"
