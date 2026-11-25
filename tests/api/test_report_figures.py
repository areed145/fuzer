"""Report figures that mean what they say (#1349): a figure nobody recorded is unknown, not 0; a window before the
first case has no history; every stat says what it is read from and the day it is for; a trend's last point is the
stat beside it; the AI note never repeats a live list's count or compares against a window with no history."""

import pytest
from fastapi.testclient import TestClient

from fuzer.agents import report_summary

pytestmark = pytest.mark.sequence


def _panel(client, key, pid, **params):
    got = client.get(f"/reports/{key}/panels/{pid}", params=params)
    assert got.status_code == 200, got.text
    return got.json()


def test_oil_deferred_on_a_day_with_no_downtime_record_is_unknown(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        quiet = _panel(client, "operations", "deferred", at="2025-12-30")
        assert quiet["value"] is None and quiet["unknown"] is True
        assert quiet["note"] == "no downtime recorded in the window"
        lost = _panel(client, "operations", "deferred", at="2025-12-27")  # a trip that day lost 1,887 bbl
        assert lost["value"] == 1887 and not lost["unknown"]
        # the day before the trip had no record either: no history the period before, never "0 bbl the period before"
        assert lost["previous"] is None and lost["previous_unknown"] is True


def test_a_window_before_the_first_case_has_no_history(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        backlog = _panel(client, "work", "backlog")
        assert backlog["value"] and backlog["previous"] is None and backlog["previous_unknown"] is True
        # the sparkline keeps only windows with a value: none is drawn as 0 before the cases began
        assert all(p["value"] for p in backlog["history"]), backlog["history"]
        old = client.get("/metrics/work.backlog", params={"window": "7d", "at": "2025-12-01"}).json()
        assert old["value"] is None and old["unknown"] and old["note"].startswith("no history")


def test_every_stat_says_what_it_is_read_from_and_the_day_it_is_for(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        oil = _panel(client, "operations", "oil")
        assert oil["source"] == "production days" and str(oil["as_of"])[:10] == oil["window"]["at"]
        assert _panel(client, "work", "backlog")["source"] == "cases"


def test_a_trends_last_point_is_the_stat_beside_it(okan_db):
    from fuzer import api
    from fuzer.core import metrics
    from fuzer.core.pack import current_pack

    with TestClient(api.app) as client:
        s = _panel(client, "work", "backlog")
        series = _panel(client, "work", "open-trend")["spec"]["series"]
        assert next(x for x in series if x["point"] == "work.backlog")["last"] == s["value"]
        # what adds up over days draws each day's own part, and says so, so it isn't read as the stat's 7 days
        flow = _panel(client, "work", "flow")["spec"]["series"]
        assert next(x for x in flow if x["point"] == "work.arrivals")["label"] == "Cases opened per day"
    # a share draws the window ending each day: the last point is the window's value, not that day's reading
    conn, pack = okan_db.conn, current_pack()
    fac = _panel(TestClient(api.app), "operations", "oil")["scope"]["id"]
    got = metrics.series(conn, pack, "production.efficiency", fac, "30d", "2025-12-30")
    assert got["points"][-1]["value"] == metrics.value(conn, pack, "production.efficiency", fac, "30d", "2025-12-30")["value"]


def test_the_ai_note_gets_no_live_count_and_may_say_a_dates_day(okan_db):
    from datetime import date

    from fuzer.core.pack import current_pack

    f = report_summary.facts(okan_db.conn, current_pack(), "work", None, "7d", date.today(), asked=False)
    lists = [p for p in f["panels"] if p["type"] == "cases"]
    assert lists and not any({"total", "overdue"} & set(p["result"]) for p in lists)
    backlog = next(p for p in f["panels"] if p["id"] == "backlog")["result"]
    assert backlog.get("previous_unknown") is True and "previous" not in backlog
    # a day named by its date's parts ("on 27 Dec" for 2025-12-27, a point in a sparkline) is on the page
    ops = report_summary.facts(okan_db.conn, current_pack(), "operations", None, "1d", date(2025, 12, 30))
    said = {"headline": "Oil deferred 1,887 bbl on 27 Dec.", "points": [{"text": "x", "cites": ["production.deferred"]}]}
    assert report_summary.check(said, ops)["unknown_numbers"] == []
