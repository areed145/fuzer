"""Water cut, GOR and test staleness (#339), on the Okan field: OK-J03-02's water cut steps from about 25 % to 72 % in
June 2025 (a breakthrough suggestion, at a cumulative oil estimated from the tests and labelled so), and every producing
well's latest test is aged at the data's latest day. Patterns are suggestions for the engineer, not mechanisms."""

from fastapi.testclient import TestClient

from fuzer.agents import mcp_server
from fuzer.analysis import well_diagnostics as wd
from fuzer.plots import production


def _id(conn, label):
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def test_water_cut_step_is_found_with_estimated_cumulative(okan_db):
    conn = okan_db.conn
    d = wd.diagnose(conn, _id(conn, "OK-J03-02"))
    assert d["verdict"] == "water_cut_step"
    s = d["water_cut_step"]
    assert s["at"] == "2025-06-08"
    assert s["water_cut_after_pct"] - s["water_cut_before_pct"] >= wd.WC_STEP
    assert 0 < s["cum_oil_sm3"] < d["cumulative"]["oil_sm3"]
    assert "estimated from well tests" in d["cumulative"]["basis"] and "estimated" in d["note"]
    cums = [p["cum_oil_sm3"] for p in d["points"]]
    assert cums == sorted(cums) and cums[0] == 0  # cumulative never falls
    assert "not a mechanism" in d["note"]


def test_a_well_with_too_few_tests_says_so(okan_db):
    conn = okan_db.conn
    d = wd.diagnose(conn, _id(conn, "OK-NWP-G1"))  # a gas producer metered another way
    assert d["verdict"] == "too_little_data" and "fewer than four" in d["reason"]


def test_staleness_counts_add_up_and_gas_wells_have_no_test(okan_db):
    conn = okan_db.conn
    out = wd.staleness(conn)
    s = out["summary"]
    assert s["current"] + s["stale"] + s["no_test_on_file"] == s["wells"] == len(out["wells"])
    assert s["stale"] == 0 and out["as_of"] == "2025-12-30"
    none = {w["well"] for w in out["wells"] if w["status"] == "no_test_on_file"}
    assert {"OK-NWP-G1", "OK-NWP-G2", "OK-NWP-G3"} <= none
    assert "not counted as current" in out["note"]
    # tighter age: the wells tested longest ago now read stale, listed first, oldest first
    tight = wd.staleness(conn, max_days=10)
    stale = [w for w in tight["wells"] if w["status"] == "stale"]
    assert stale and all(w["age_days"] > 10 for w in stale)
    assert tight["wells"][:len(stale) + 3] == sorted(tight["wells"][:len(stale) + 3],
                                                     key=lambda w: -(w["age_days"] or 10**6))
    assert tight["summary"]["stale_share_pct"] == round(100 * len(stale) / len(tight["wells"]), 1)


def test_screen_lists_patterns_with_the_water_cut_step_first(okan_db):
    out = wd.screen(okan_db.conn)
    assert out["wells_screened"] > 100
    assert out["patterns"][0]["verdict"] == "water_cut_step"
    assert "OK-J03-02" in {p["well"] for p in out["patterns"] if p["verdict"] == "water_cut_step"}
    assert all(p["gor_climb"]["ratio"] >= wd.GOR_RISE for p in out["patterns"] if p["verdict"] == "gor_climb")
    assert out["staleness"]["summary"]["wells"] >= out["wells_screened"]


def test_screen_reaches_people_and_agents(okan_db):
    from fuzer import api
    r = TestClient(api.app).get("/plant/well-screen")
    assert r.status_code == 200, r.text
    assert r.json()["thresholds"]["stale_days"] == wd.STALE_DAYS
    one = mcp_server.well_diagnostics("OK-J03-02")
    assert one["verdict"] == "water_cut_step"
    assert mcp_server.test_staleness(max_days=10)["summary"]["stale"] > 0
    climbing = next(p["well_id"] for p in wd.screen(okan_db.conn)["patterns"] if p["verdict"] == "gor_climb")
    fig = production.gor_trend(okan_db.conn, climbing)
    assert fig.data[0]["name"] == "GOR (Sm3/Sm3)" and len(fig.data) == 3 and fig.summary["gor_climb"]
    water = production.water_diagnostic(okan_db.conn, _id(okan_db.conn, "OK-J03-02"))
    assert "estimated" in water.summary["cumulative_basis"]
    okan_db.conn.rollback()
