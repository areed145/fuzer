"""Schedule compliance and repeat repair rate (#373), on the Okan field: the fake plant runs about one closed order in
six past its basic finish and plants eight repeat repairs, each a corrective order on the same equipment within a
month of the last, all listed in answer_keys/work.json. Measures the records, not people or crews."""

import json

from fastapi.testclient import TestClient

from fuzer.agents import mcp_server
from fuzer.analysis import work_execution as we
from fuzer.plots import maintenance


def _key(okan_db):
    return json.loads((okan_db.data / "answer_keys" / "work.json").read_text())


def _labels(conn, ids):
    return {r["label"] for r in conn.execute("select label from entity_label where fuzer_id = any(%s)", (list(ids),))}


def test_late_finishes_match_the_answer_key(okan_db):
    out = we.trend(okan_db.conn)
    key = {k["order"] for k in _key(okan_db)["late"] if out["since"] <= k["finish"] < out["until"]}
    late = _labels(okan_db.conn, [r["id"] for p in out["periods"] for r in p["late"]])
    assert key and late == key  # every planted late finish, and nothing else
    s = out["summary"]
    assert s["late"] == len(key) and s["on_time"] + s["late"] == s["finished_with_due_date"]
    assert 70 < s["schedule_compliance_pct"] < 100
    assert all(r["days_late"] > 0 for p in out["periods"] for r in p["late"])


def test_planted_repeat_repairs_are_found(okan_db):
    out = we.trend(okan_db.conn)
    planted = {k["order"] for k in _key(okan_db)["repeat_repairs"] if out["since"] <= k["start"] < out["until"]}
    found = _labels(okan_db.conn, [r["id"] for p in out["periods"] for r in p["repeats"]])
    assert planted and planted <= found and len(found - planted) <= 3  # a few repeats were already in the records
    for p in out["periods"]:
        for r in p["repeats"]:
            assert r["previous_finish"] <= r["start"]
    assert out["summary"]["repeat_rate_pct"] == round(100 * out["summary"]["repeats"] / out["summary"]["corrective"], 1)


def test_a_small_period_says_too_little_data(okan_db):
    out = we.trend(okan_db.conn, by="month")
    small = [p for p in out["periods"] if p["corrective"] < we.MIN_ORDERS]
    assert small and all(p["repeat_rate_pct"] is None for p in small)
    assert all(p["schedule_compliance_pct"] is None for p in out["periods"]
               if p["finished"] - p["no_due_date"] < we.MIN_ORDERS)
    assert "not people or crews" in out["note"]


def test_work_execution_reaches_people_and_agents(okan_db):
    from fuzer import api
    r = TestClient(api.app).get("/plant/work-execution")
    assert r.status_code == 200, r.text
    assert r.json()["by"] == "quarter"
    assert mcp_server.work_execution(by="month")["by"] == "month"
    fig = maintenance.work_execution(okan_db.conn)
    assert fig.data[0]["name"] == "schedule compliance (%)" and fig.summary["summary"]["repeats"] >= 8
    okan_db.conn.rollback()
