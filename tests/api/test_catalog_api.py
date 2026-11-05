"""The source catalog's API (#225), as the Sources page uses it: a new table found, profiled, mapped, previewed,
approved, scheduled and loaded with no repository change; checks that hold a load; mapping drafts with impact,
approval and rollback; rules; health and the alerts it raises to owners; lineage of a golden value."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from fuzer.core import db


@pytest.fixture(scope="module")
def client(offshore_data):
    from fastapi.testclient import TestClient

    data = Path(tempfile.mkdtemp(prefix="fzcatapi"))
    shutil.copytree(offshore_data, data, dirs_exist_ok=True)
    mp = pytest.MonkeyPatch()
    mp.delenv("FUZER_DATABASE_URL", raising=False)
    mp.setenv("FUZER_PGDATA", os.environ.get("FUZER_PGDATA") or tempfile.mkdtemp(prefix="fzpg"))
    mp.setenv("FUZER_DATA_DIR", str(data))
    with db.connect() as c:
        db.reset(c)
    from fuzer import api

    with TestClient(api.app) as cl:
        cl.data = data
        yield cl
    mp.undo()


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_a_new_source_from_the_api_alone(client):
    cat = ok(client.get("/sources/catalog"))
    sap = next(s for s in cat["systems"] if s["name"] == "sap")
    files = next(c for c in sap["connections"] if c["type"] == "folder")
    assert {d["name"] for d in sap["datasets"]} >= {"sap_equipment", "sap_floc"}
    assert "pi_web_api" in cat["types"]["connections"] and "not_null" in cat["check_kinds"]

    (client.data / "vendor_pumps.csv").write_text("pump_id,tag,make,model,rated_kw\nVP1,20-P-1001A,Acme,AX-1,55\n"
                                                  "VP2,20-P-1001B,Acme,AX-1,-5\n")
    assert "vendor_pumps" in ok(client.get(f"/sources/connections/{files['id']}/discover"))["uncovered"]
    test = ok(client.post(f"/sources/connections/{files['id']}/test"))
    assert test["ok"] and [s["step"] for s in test["steps"]] == ["reach", "list", "read"]

    sug = ok(client.post("/sources/vendor_pumps/suggest", json={"claude": False}))
    loaded = ok(client.post("/sources/vendor_pumps/load", json={"yaml": sug["yaml"], "by": "ana"}))
    assert loaded["loaded"] == ["vendor_pumps"] and loaded["inserted"] == 2

    d = ok(client.get("/sources/datasets/vendor_pumps"))
    assert d["dataset"]["status"] == "active" and len(d["runs"]) == 1 and d["profiles"][0]["rows"] == 2
    assert d["mappings"][0]["current"] and d["mappings"][0]["author"] == "ana"

    assert client.patch("/sources/datasets/vendor_pumps", json={"values": {"schedule": "hourly"}, "by": "ana"}).status_code == 400
    ok(client.patch("/sources/datasets/vendor_pumps", json={"values": {"schedule": "0 * * * *", "owner": "ana"},
                                                             "by": "ana", "reason": "the vendor sends it hourly"}))
    hist = ok(client.get("/sources/datasets/vendor_pumps"))["history"]
    assert hist[0]["new"] == {"schedule": "0 * * * *", "owner": "ana"} and hist[0]["reason"] == "the vendor sends it hourly"

    # a blocking range check holds the next load
    ok(client.post("/sources/datasets/vendor_pumps/checks",
                   json={"kind": "range", "column": "rated_kw", "params": {"min": 0}, "blocking": True, "by": "ana"}))
    held = ok(client.post("/sources/datasets/vendor_pumps/run", params={"full": "true"}))
    assert held["held"] == ["vendor_pumps"]
    chk = ok(client.get("/sources/datasets/vendor_pumps"))["checks"][0]
    assert chk["last_ok"] is False and chk["last_value"] == 1.0

    # a draft mapping: impact, approve, roll back
    text = d["mappings"][0]["yaml"]
    first = d["mappings"][0]["id"]
    draft = ok(client.post("/sources/mappings", json={"yaml": text + "\n# reviewed\n", "by": "ben", "note": "reviewed"}))
    assert draft["status"] == "draft"
    imp = ok(client.post("/sources/impact", json={"version_id": draft["id"]}))
    assert imp["records"] == 2 and imp["fields"]["changed"] == 0
    ok(client.post(f"/sources/mappings/{draft['id']}/approve", json={"by": "ben"}))
    ok(client.post(f"/sources/mappings/{first}/rollback", json={"by": "ben", "reason": "back"}))
    vers = ok(client.get("/sources/datasets/vendor_pumps"))["mappings"]
    assert vers[0]["current"] and vers[0]["parent_id"] == first

    ok(client.post("/sources/datasets/vendor_pumps/pause", json={"by": "ana"}))
    assert ok(client.get("/sources/datasets/vendor_pumps"))["dataset"]["status"] == "paused"
    ok(client.post("/sources/datasets/vendor_pumps/resume", json={"by": "ana"}))
    assert ok(client.get("/sources/audit", params={"object": "check"}))


def test_rules_health_alerts_and_lineage(client):
    from fuzer.core.pack import load_pack
    from fuzer.ops import pipeline
    from fuzer.sources import alerts, ingest

    with db.connect() as conn:
        ingest.ingest(conn, client.data)
        pipeline.rebuild(conn, load_pack("offshore_demo"))

    r = ok(client.get("/sources/rules"))
    assert any(x["name"] == "sap" and x["rank"] for x in r["ranks"]) and any(x["scope"] == "match" for x in r["rules"])
    ok(client.put("/sources/rules/field/description", json={"config": {"priority": ["review", "maximo", "sap"]}, "by": "ana"}))
    assert client.put("/sources/rules/match/equipment", json={"config": {"auto_accept": 2}, "by": "ana"}).status_code == 400
    ok(client.put("/sources/systems/maximo/rank", json={"rank": 5, "by": "ana"}))

    h = {x["dataset"]: x for x in ok(client.get("/sources/health"))}
    assert h["sap_equipment"]["state"] == "ok" and h["sap_equipment"]["coverage_pct"] is not None
    assert ok(client.get("/sources/matching", params={"source": "sap"}))[0]["stats"]["records"] > 0

    ok(client.patch("/sources/datasets/sap_equipment", json={"values": {"expected_hours": 1}, "by": "ana"}))
    with db.connect() as conn:
        conn.execute("update source_dataset set loaded_at = now() - interval '2 hours' where name = 'sap_equipment'")
        out = alerts.check(conn, ["sap_equipment"])
        conn.commit()
        assert out["opened"] == ["sap_equipment: stale"]
        item = conn.execute("select * from work_item where type = 'source_issue' and state = 'open' "
                            "and payload->>'dataset' = 'sap_equipment'").fetchone()
        held = conn.execute("select assignee from work_item where type = 'source_issue' and payload->>'dataset' = "
                            "'vendor_pumps'").fetchone()
        assert held["assignee"] == "ana"  # the held load in the test before went to the dataset's owner
        assert item["role"] == "role:data_steward" and item["payload"]["dataset"] == "sap_equipment"
        assert alerts.check(conn, ["sap_equipment"])["opened"] == []  # once per problem
    ok(client.patch("/sources/datasets/sap_equipment", json={"values": {"expected_hours": None}, "by": "ana"}))
    with db.connect() as conn:
        assert alerts.check(conn, ["sap_equipment"])["closed"] == ["sap_equipment: stale"]

    with db.connect() as conn:
        fid = conn.execute("select fuzer_id from golden where field = 'manufacturer' and source = 'sap' limit 1"
                           ).fetchone()["fuzer_id"]
    w = ok(client.get(f"/sources/why/{fid}/manufacturer"))
    assert w["source"] == "sap" and any(a["wins"] and a["run"] for a in w["assertions"])
    assert client.get(f"/sources/why/{fid}/no_such_field").status_code == 404

    from fuzer.agents.capabilities import shared

    assert any(x["dataset"] == "sap_equipment" for x in shared.source_health())
    assert shared.why_value(fid, "manufacturer")["source"] == "sap"


def test_add_a_source_from_another_folder(client, okan_data, tmp_path):
    """The Add a source flow: a new system, a folder connection to a lake outside the data folder, its tables, a
    preview with a suggested mapping for the system and a date-format hint, then save and load in one step."""
    from fakeplant import schedules

    schedules.write(okan_data, tmp_path / "lake")
    ok(client.post("/sources/systems", json={"name": "schedule", "values": {"kind": "planning"}, "by": "ana"}))
    c = ok(client.post("/sources/systems/schedule/connections",
                       json={"name": "lake", "type": "folder", "config": {"path": str(tmp_path / "lake")}, "by": "ana"}))
    t = ok(client.get(f"/sources/connections/{c['id']}/tables"))
    assert t["tables"][0] == {"table": "project_schedules", "dataset": None}

    p = ok(client.get(f"/sources/connections/{c['id']}/tables/project_schedules"))
    assert p["connector"] == "delta" and len(p["sample"]) == 8 and p["covered_by"] is None
    assert p["date_hint"]["date_format"] == "%d/%m/%Y"
    assert p["suggested"]["key"] == "activity_id" and "source: schedule" in p["suggested"]["yaml"]
    ok(client.patch("/sources/systems/schedule", json={"values": {"date_format": "%d/%m/%Y"}, "by": "ana"}))
    assert ok(client.get(f"/sources/connections/{c['id']}/tables/project_schedules"))["date_hint"] is None

    bad = client.post(f"/sources/connections/{c['id']}/tables/project_schedules",
                      json={"yaml": p["suggested"]["yaml"].replace("source: schedule", "source: other"), "by": "ana"})
    assert bad.status_code == 400 and "system's name" in bad.json()["detail"]
    out = ok(client.post(f"/sources/connections/{c['id']}/tables/project_schedules",
                         json={"yaml": p["suggested"]["yaml"], "values": {"owner": "ana", "schedule": "0 6 * * *"},
                               "by": "ana"}))
    assert out["load"]["held"] == ["project_schedules"]  # the export repeats one activity id
    assert out["run"]["detail"]["checks"][0]["kind"] == "duplicate keys" and out["health"]["state"] == "held"
    d = ok(client.get("/sources/datasets/project_schedules"))["dataset"]
    assert d["connection_id"] == c["id"] and d["owner"] == "ana" and d["source"] == "schedule"
    assert ok(client.get(f"/sources/connections/{c['id']}/tables"))["tables"][0]["dataset"] == "project_schedules"
