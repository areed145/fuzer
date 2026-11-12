"""Repeat incidents and corrective work ageing (#350), on the Okan field: train A's 2nd-stage compressor (20-K-2300A)
trips five times over two years, twice after the corrective work on an earlier trip was recorded closed. Linked work is
a proxy, not an action register."""

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from fuzer.agents import mcp_server
from fuzer.analysis import incident_followup as fu
from fuzer.plots import troubleshooting


def test_repeats_per_unit_and_work_that_closed_before_a_repeat(okan_db):
    out = fu.repeats(okan_db.conn)  # two years to Okan's latest daily report
    top = out["units"][0]
    assert top["unit"] == "20-K-2300A" and top["repeat"] and top["incidents"] == 5
    assert len(top["days_between"]) == 4 and all(d > 0 for d in top["days_between"])
    back = top["came_back_after_closed_work"]
    assert back and all(c["closed_orders"] for c in back)
    closed = {w["id"]: w for w in out["closed_work"]}
    for c in back:  # the work it came back after was recorded closed before the repeat began
        assert all(closed[o]["finish"] <= c["start"] for o in c["closed_orders"])
    assert out["summary"]["repeat_units"] == sum(1 for u in out["units"] if u["incidents"] > 1)
    assert out["summary"]["incidents"] == sum(u["incidents"] for u in out["units"])
    assert {w["link"] for w in out["closed_work"]} <= {"via_notification", "on_unit_after"}
    assert "proxy, not an action register" in out["note"]
    # a unit with one incident is listed, not a repeat
    assert any(not u["repeat"] for u in out["units"])


def test_open_linked_work_is_aged_at_the_latest_day(okan_db):
    conn = okan_db.conn
    out = fu.repeats(conn)
    assert out["summary"]["open_orders"] == 0  # Okan's linked orders are all closed
    wo = out["closed_work"][0]["id"]
    conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, 'status', %s, 'test', 'test') "
                 "on conflict (fuzer_id, field) do update set value = excluded.value", (wo, Jsonb("REL")))
    reopened = fu.repeats(conn)
    w = next(x for x in reopened["open_work"] if x["id"] == wo)
    assert w["open"] and w["age_days"] > 180 and w["age_band"] == "over 180 days" and w["finish"] is None
    assert reopened["summary"]["open_orders"] == 1 and reopened["summary"]["open_by_age"]["over 180 days"] == 1
    # an open order isn't work that closed, so nothing came back after it
    assert all(wo not in c["closed_orders"] for u in reopened["units"] for c in u["came_back_after_closed_work"])
    conn.rollback()


def test_repeats_reach_people_and_agents(okan_db):
    from fuzer import api
    r = TestClient(api.app).get("/incidents/repeats")
    assert r.status_code == 200, r.text
    assert r.json()["units"][0]["unit"] == "20-K-2300A"
    assert mcp_server.incident_repeats()["summary"]["repeat_units"] >= 1
    fig = troubleshooting.incident_repeats(okan_db.conn)
    assert fig.data[0]["name"] == "incidents" and fig.summary["came_back_after_closed_work"]
    okan_db.conn.rollback()
