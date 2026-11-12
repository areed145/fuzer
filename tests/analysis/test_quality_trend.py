"""Separator and export quality trend (#342), on the Okan field: every lab analyte at every sample point against
itself, gaps in sampling made visible, downtime around a series, and never an on or off specification claim, since no
limit is loaded. On Okan the HP separator's oil in water doubles over 2025."""

from fastapi.testclient import TestClient

from fuzer.agents import mcp_server
from fuzer.analysis import quality_trend as qt
from fuzer.plots import troubleshooting

HP_WATER = ("HP separator water outlet", "oil_in_water")


def test_every_analyte_against_itself_with_no_limit_claimed(okan_db):
    out = qt.trends(okan_db.conn)
    s = out["summary"]
    assert s["series"] >= 10 and s["sample_points"] >= 5 and s["limits_loaded"] == 0
    assert out["limits"] is None and "no on or off specification claim" in out["note"]
    assert {r["analyte"] for r in out["series"]} >= {"oil_in_water", "bsw", "salt"}
    assert "wall_thickness" not in {r["analyte"] for r in out["series"]}  # inspection readings aren't lab samples
    hp = next(r for r in out["series"] if (r["sample_point"], r["analyte"]) == HP_WATER)
    assert hp["change_pct"] > 50 and hp["latest_3_median"] > hp["first_half_median"]
    assert out["series"][0] is hp or out["series"][0]["drift"]  # the largest move first, after any drift
    for r in out["series"]:
        assert r["samples"] > 0 and r["first"] >= out["since"] and r["low"] <= r["latest"] <= r["high"]


def test_a_gap_in_sampling_is_shown(okan_db):
    conn = okan_db.conn
    subject = qt.find(conn, *HP_WATER)
    conn.execute("delete from measurement where fuzer_id = %s and location = %s and quantity = %s "
                 "and at >= '2025-06-01' and at < '2025-08-15'", (subject, *HP_WATER))
    one = qt.series(conn, subject, *HP_WATER)
    assert len(one["gaps"]) == 1 and one["gaps"][0]["days"] > 60
    assert one["gaps"][0]["from"] < "2025-06-01" and one["gaps"][0]["to"] >= "2025-08-15"
    assert qt.trends(conn)["summary"]["with_gaps"] == 1
    fig = troubleshooting.quality_trend(conn, subject, *HP_WATER)
    assert any(sh["fillcolor"].startswith("rgba(150,150,150") for sh in fig.layout["shapes"])
    conn.rollback()


def test_one_series_carries_the_downtime_around_it(okan_db):
    conn = okan_db.conn
    one = qt.series(conn, qt.find(conn, *HP_WATER), *HP_WATER)
    assert one["events"] and all(one["since"] <= e["start"] < one["until"] for e in one["events"])
    assert any(e["field_wide"] for e in one["events"])
    assert len(one["points"]) == one["samples"] and one["limits"] is None


def test_quality_reaches_people_and_agents(okan_db):
    from fuzer import api
    r = TestClient(api.app).get("/plant/quality")
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["limits_loaded"] == 0
    one = mcp_server.quality_trends(*HP_WATER)
    assert one["analyte"] == "oil_in_water" and len(one["points"]) <= 30
    assert "error" in mcp_server.quality_trends("nowhere", "oil_in_water")
    fig = troubleshooting.quality_trend(okan_db.conn, qt.find(okan_db.conn, *HP_WATER), *HP_WATER)
    assert fig.summary["limits"] is None and "limit" in fig.summary["limit_basis"]
    okan_db.conn.rollback()
