"""Before and after intervention gain (P7.5a, #340), on the Okan field: each recorded well intervention's oil rate after
against the decline the well was on before. Okan's interventions are background shut-ins that left the wells on
their decline, so they read "no clear change"; one at the start of the tests has too few before it and is unknown."""

from fastapi.testclient import TestClient

from fuzer.agents import mcp_server
from fuzer.analysis import interventions as iv
from fuzer.plots import production


def test_the_gain_is_net_of_the_decline_before(okan_db):
    conn = okan_db.conn
    out = iv.gains(conn)  # two years to Okan's latest daily report
    assert out["summary"]["interventions"] == len(out["interventions"]) >= 10
    said = [i for i in out["interventions"] if i["verdict"] != "unknown"]
    for i in said:
        # the gain is the rate after against where the decline put the well, not after less before
        assert abs(i["gain_sm3d"] - (i["oil_after_sm3d"] - i["oil_expected_sm3d"])) <= 0.11  # each rounded to 0.1
        assert i["oil_range_sm3d"][0] <= i["oil_expected_sm3d"] <= i["oil_range_sm3d"][1]
    # OK-J20-01 was on a steep decline: after less before is +6 Sm3/d, net of its decline the gain is far larger
    j = next(i for i in said if i["well"] == "OK-J20-01")
    assert j["gain_sm3d"] > j["oil_after_sm3d"] - j["oil_before_sm3d"] + 10
    # the background shut-ins left their wells on their decline
    assert out["summary"]["no_clear_change"] >= len(said) - 2
    assert all(i["water_cut_change_pct"] is not None for i in said)
    conn.rollback()


def test_an_intervention_with_no_test_on_one_side_is_unknown_not_zero(okan_db):
    conn = okan_db.conn
    out = iv.gains(conn)
    unk = next(i for i in out["interventions"] if i["well"] == "OK-J14-05")  # a week after the tests begin
    assert unk["verdict"] == "unknown" and "too few tests before" in unk["why"] and "gain_sm3d" not in unk
    assert out["summary"]["unknown"] >= 1
    assert out["summary"]["net_gain_sm3d"] == round(sum(i["gain_sm3d"] for i in out["interventions"]
                                                        if i["verdict"] != "unknown"), 1)
    late = iv.gains(conn, since="2025-12-01")  # the latest has one test after it at most
    for i in late["interventions"]:
        assert i["verdict"] != "unknown" or i["tests_after"] == 0 or i["tests_before"] < iv.MIN_BEFORE
    conn.rollback()


def test_intervention_gains_reach_people_and_agents(okan_db):
    from fuzer import api
    r = TestClient(api.app).get("/plant/intervention-gains")
    assert r.status_code == 200 and r.json()["interventions"]
    got = mcp_server.intervention_gains(well="OK-J20-01")
    assert [i["well"] for i in got["interventions"]] == ["OK-J20-01"]
    fig = production.intervention_gains(okan_db.conn)
    assert fig.summary["unknown"] and all("OK-J14-05" not in x for x in fig.data[0]["x"])  # unknown isn't drawn as 0
    okan_db.conn.rollback()
