"""How the plant is running (P7.3a, #353): one scorecard across production, maintenance and reliability, this period
against the one before, on the Okan field. Every KPI cites its window, unit and source; one the records can't say is
unknown, never zero."""

import pytest
from fastapi.testclient import TestClient

from fuzer.agents import mcp_server
from fuzer.analysis import kpis
from fuzer.plots import general


def test_each_kpi_cites_its_window_unit_and_source(okan_db):
    conn = okan_db.conn
    s = kpis.scorecard(conn)  # the 30 days to Okan's latest daily report, against the 30 before
    assert s["now"] == {"since": "2025-12-01", "through": "2025-12-30"}
    assert s["before"] == {"since": "2025-11-01", "through": "2025-11-30"}
    assert [k["key"] for k in s["kpis"]] == ["production_efficiency", "deferred_oil", "flare", "corrective_backlog",
                                             "preventive_on_time", "failures", "repeat_failures"]
    for k in s["kpis"]:
        assert k["unit"] and k["source"] and k["definition"], k["key"]
        assert k["now"]["since"] == "2025-12-01" and k["before"]["through"] == "2025-11-30"
        assert k["now"]["value"] is not None and k["before"]["value"] is not None, k["key"]  # Okan has them all
        assert k["verdict"] in ("better", "worse", "same")
        assert len(k["now"]["refs"]) == min(k["now"]["records"], kpis.REFS)
    by = {k["key"]: k for k in s["kpis"]}
    assert by["production_efficiency"]["unit"] == "%" and by["production_efficiency"]["now"]["records"] == 30
    # fewer failures is better, more deferred oil is worse
    f = by["failures"]
    assert f["now"]["value"] < f["before"]["value"] and f["verdict"] == "better"
    d = by["deferred_oil"]
    assert d["now"]["value"] > d["before"]["value"] and d["verdict"] == "worse"
    assert d["now"]["records"] and all(r.startswith("fz-") for r in d["now"]["refs"])
    # the late preventive orders are what the number points at
    p = by["preventive_on_time"]
    assert p["now"]["value"] < 100 and p["now"]["refs"] and "on time" in p["now"]["note"]
    conn.rollback()


def test_a_kpi_with_missing_data_is_unknown_not_zero(okan_db):
    conn = okan_db.conn
    s = kpis.scorecard(conn, until="2023-06-30")  # before Okan's daily reports start; its notifications go back further
    by = {k["key"]: k for k in s["kpis"]}
    for key in ("production_efficiency", "deferred_oil", "flare"):
        k = by[key]
        assert k["now"]["value"] is None and k["before"]["value"] is None, key
        assert k["now"]["note"].startswith("no daily report") and k["verdict"] == "unknown" and k["change"] is None
    assert {"Production efficiency", "Deferred oil", "Flare"} <= set(s["unknown"])
    assert by["failures"]["now"]["value"] is not None  # notifications reach back, so failures can be said
    with pytest.raises(ValueError):
        kpis.scorecard(conn, days=0)
    conn.rollback()


def test_the_scorecard_reaches_people_and_agents(okan_db):
    from fuzer import api
    client = TestClient(api.app)
    r = client.get("/plant/scorecard", params={"until": "2025-06-30", "days": 30})
    assert r.status_code == 200, r.text
    pe = next(k for k in r.json()["kpis"] if k["key"] == "production_efficiency")
    assert pe["now"]["value"] < pe["before"]["value"] and pe["verdict"] == "worse"  # June's planned shutdown
    assert client.get("/plant/scorecard", params={"days": 0}).status_code == 400
    got = mcp_server.kpi_scorecard()
    assert got["now"]["through"] == "2025-12-30" and len(got["kpis"]) == 7
    fig = general.scorecard(okan_db.conn, "2023-06-30")
    assert fig.summary["unknown"] and "unknown" in fig.data[0]["text"]
    okan_db.conn.rollback()
