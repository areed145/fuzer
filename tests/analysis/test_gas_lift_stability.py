"""Gas lift casing heading and instability (#341), on the Okan field: the fake plant's heading well (P3.1c) cycles
from 15 to 29 December 2025, its casing and wellhead pressures swinging together about every 75 minutes. The rest are
steady: their noise, however it wanders, isn't a regular cycle. Values are synthetic and the screen says so."""

from fastapi.testclient import TestClient

from fuzer.agents import mcp_server
from fuzer.analysis import gas_lift
from fuzer.analysis import gas_lift_stability as gls
from fuzer.plots import production

DURING = "2025-12-20T00:00"


def test_the_heading_well_is_found_and_the_rest_are_steady(okan_db):
    out = gls.screen(okan_db.conn, until=DURING)
    s = out["summary"]
    assert s["heading"] == 1 and s["unstable"] == 0 and s["wells"] > 100
    assert s["heading"] + s["unstable"] + s["steady"] + s["too_little_data"] == s["wells"]
    top = out["wells"][0]
    assert top["verdict"] == "heading" and 65 <= top["period_min"] <= 85
    assert top["casing_swing_pct"] >= gls.SWING_PCT
    assert top["cycling_together"] == ["casing_pressure", "wellhead_pressure"]  # lift gas held steady
    assert all(w["casing_swing_pct"] < gls.SWING_PCT for w in out["wells"][1:])
    # the same well the detector raises
    assert [r["well"] for r in gas_lift.unstable(okan_db.conn, until=DURING)] == [top["well_id"]]
    assert out["synthetic"] and "synthetic" in out["note"]
    assert "no gas or choke change is proposed" in out["note"]


def test_before_and_after_the_well_was_steady(okan_db):
    conn = okan_db.conn
    wid = gls.screen(conn, until=DURING)["wells"][0]["well_id"]
    assert gls.well(conn, wid, 3, "2025-12-10T00:00")["verdict"] == "steady"
    now = gls.well(conn, wid, 3, DURING)
    assert now["verdict"] == "heading" and len(now["series"]["casing_pressure"]) >= gls.MIN_READINGS
    assert gls.screen(conn, until="2026-01-01T12:00")["summary"]["heading"] == 0  # settled once it stops


def test_a_well_without_lift_says_so(okan_db):
    conn = okan_db.conn
    nf = conn.execute("select fuzer_id from entity_label where label = 'OK-NWP-G1'").fetchone()["fuzer_id"]
    try:
        gls.well(conn, nf)
        raise AssertionError("a gas producer has no annulus to watch")
    except gls.CalcError as e:
        assert "isn't gas lifted" in str(e)


def test_heading_reaches_people_and_agents(okan_db):
    from fuzer import api
    r = TestClient(api.app).get("/plant/gas-lift-stability")
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["wells"] > 100
    wid = gls.screen(okan_db.conn, until=DURING)["wells"][0]["well_id"]
    label = okan_db.conn.execute("select label from entity_label where fuzer_id = %s", (wid,)).fetchone()["label"]
    one = mcp_server.gas_lift_stability(label)
    assert one["verdict"] in ("heading", "steady") and "series" not in one
    fig = production.gas_lift_heading(okan_db.conn, wid, 3, DURING)
    assert len(fig.data) == 3 and fig.summary["verdict"] == "heading"
    ranked = production.gas_lift_screen(okan_db.conn, 3, DURING)
    assert ranked.data[0]["y"][-1] == label and ranked.summary["heading"] == [label]
    okan_db.conn.rollback()
