"""Production forecast against actual (P7.3b, #336), on the Okan field: each well's tests against its own decline, so a
new loss stands apart from natural decline. OK-J03-02's water breakthrough in June 2025 is the known change; a well
with too few tests before the window is listed, never fitted."""

from fastapi.testclient import TestClient

from fuzer.agents import mcp_server
from fuzer.analysis import forecast_actual as fa
from fuzer.plots import production


def _w(out: dict, name: str) -> dict:
    return next(w for w in out["wells"] if w["well"] == name)


def test_a_known_decline_change_shows_in_the_gap(okan_db):
    conn = okan_db.conn
    out = fa.forecast_vs_actual(conn)  # the year to Okan's latest daily report
    assert (out["since"], out["until"]) == ("2024-12-31", "2025-12-31")
    j = _w(out, "OK-J03-02")
    assert j["verdict"] == "below_forecast" and j["below_since"] == "2025-06-08"  # the breakthrough, not before it
    assert j["gap_sm3d"] < -25 and j["fit"]["to"] < out["since"]  # fitted only on tests before the window
    before = [p for p in j["points"] if p["at"] < "2025-06-01"]
    assert before and all(p["outside"] is None for p in before)  # natural decline until then
    assert out["wells"][0]["well"] == "OK-J03-02"  # the largest new loss leads
    # a gas lift valve that failed open and was fixed: under its forecast while it was down, back on it after
    v = _w(out, "OK-J07-05")
    assert v["verdict"] == "on_forecast" and v["tests_below"] == 3
    # OK-J07-03's 2024 valve failure sits in its fit window: the dip is left out, so the fit is its decline
    assert _w(out, "OK-J07-03")["fit"]["left_out"]
    f = out["field"]
    assert f["wells_compared"] == len(out["wells"]) and "OK-J03-02" in f["below_forecast"]
    assert round(f["actual_sm3d"] - f["forecast_sm3d"], 1) == f["gap_sm3d"]
    assert out["level"] == 0.9 and "well tests" in out["source"] and "never a plan" in out["note"]
    conn.rollback()


def test_a_well_with_too_few_tests_is_listed_not_fitted(okan_db):
    conn = okan_db.conn
    out = fa.forecast_vs_actual(conn, since="2024-04-01", until="2025-04-01")  # three months of tests before it
    assert out["wells"] == [] and out["not_fitted"]
    nf = out["not_fitted"][0]
    assert nf["tests_before"] < fa.MIN_TESTS and "fewer than the 6 it needs" in nf["reason"]
    assert "forecast_sm3d" not in nf
    assert out["field"]["wells_compared"] == 0
    conn.rollback()


def test_forecast_against_actual_reaches_people_and_agents(okan_db):
    from fuzer import api
    client = TestClient(api.app)
    r = client.get("/plant/forecast-vs-actual")
    assert r.status_code == 200, r.text
    assert r.json()["wells"][0]["well"] == "OK-J03-02"
    got = mcp_server.forecast_vs_actual()
    assert all(w["verdict"] != "on_forecast" for w in got["wells"])  # the leads only
    assert "OK-J03-02" in got["field"]["below_forecast"]
    one = mcp_server.forecast_vs_actual(well="OK-J03-02")
    assert one["wells"][0]["points"]
    conn = okan_db.conn
    fig = production.forecast_vs_actual(conn, one["wells"][0]["id"])
    assert fig.summary["verdict"] == "below_forecast" and fig.summary["below_since"] == "2025-06-08"
    assert production.forecast_vs_actual(conn).data[0]["y"][0] == "OK-J03-02"
    conn.rollback()
