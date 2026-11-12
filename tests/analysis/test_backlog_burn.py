"""Is the backlog under control (#348), on the Okan field: orders created against closed each month, the open backlog
at each month's end, and the notification-to-order lag. Okan's backlog grows through late 2025; most of its orders were
raised without a notification, and those are counted and left out of the lag."""

from datetime import date

from fastapi.testclient import TestClient

from fuzer.agents import mcp_server
from fuzer.analysis import backlog
from fuzer.graph import operations
from fuzer.plots import maintenance


def test_created_closed_and_open_each_month(okan_db):
    conn = okan_db.conn
    out = backlog.burn(conn)  # the whole months in the year to Okan's latest daily report
    months = out["months"]
    assert out["since"].endswith("-01") and months[0]["since"] == out["since"]
    assert out["summary"]["created"] == sum(m["created"] for m in months)
    assert out["summary"]["trend"] == "growing" and months[-1]["open_at_end"] > out["summary"]["open_at_start"]
    # the open count at a month's end is the same as the corrective-backlog helper's when narrowed to PM01
    last = months[-1]
    pm01 = operations.corrective_backlog_at(conn, last["until"])
    assert last["by_type"]["PM01"]["open_at_end"] == len(pm01["open"])
    for m in months:
        assert m["created"] == sum(t["created"] for t in m["by_type"].values())
        assert len(m["refs"]["created"]) == min(m["created"], 50)


def test_lag_leaves_out_orders_with_no_notification(okan_db):
    lag = backlog.burn(okan_db.conn)["lag"]
    assert lag["without_notification"] > 0  # counted, not dropped
    assert lag["with_notification"] + lag["without_notification"] == lag["orders"]
    assert lag["median_days"] is not None and 0 <= lag["median_days"] <= lag["p90_days"] <= lag["max_days"]
    top = lag["longest"][0]
    assert top["lag_days"] == lag["max_days"]
    assert (date.fromisoformat(top["ordered"]) - date.fromisoformat(top["notified"])).days == top["lag_days"]


def test_burn_reaches_people_and_agents(okan_db):
    from fuzer import api
    r = TestClient(api.app).get("/plant/backlog-burn")
    assert r.status_code == 200, r.text
    assert r.json()["months"] and r.json()["lag"]["orders"]
    assert mcp_server.backlog_burn()["summary"]["trend"] in ("growing", "steady", "shrinking")
    fig = maintenance.backlog_burn(okan_db.conn)
    assert [t["name"] for t in fig.data] == ["created", "closed", "open at month end"]
