"""Backlog health (#474) for the work management specialist on Okan: ageing, urgent overdue work and unplanned
notifications on bad actors are flagged with their thresholds, and the open orders are ranked by value / risk."""

import pytest

from fuzer.analysis import backlog
from fuzer.core.pack import load_pack

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    yield okan_db.conn


def test_backlog_health_flags_and_ranks(conn):
    h = backlog.health(conn, PACK, "2025-12-30")
    a = h["ageing"]
    assert a["corrective_open"] > 0 and a["share_older_than_90_days"] > backlog.AGEING_SHARE
    assert {"ageing", "urgent_overdue", "unplanned_risk"} <= set(h["flags"])
    assert h["urgent_overdue"] == sum(n for p, n in h["overdue_by_priority"].items() if p in ("1", "2")) > 0
    # the compressor train's unplanned notifications sit on bad actors
    assert any(n["tag"].startswith("20-K-") for n in h["unplanned_on_bad_actors"])
    r = h["ranked"]
    assert r and h["currency"] == "USD" and all(x["reason"] for x in r)
    assert [x["value_usd"] for x in r] == sorted((x["value_usd"] for x in r), reverse=True)
    assert "90 days" in h["basis"] and "25 %" in h["basis"]


def test_capability_returns_health(okan_db):
    from fuzer.agents.capabilities import maintenance
    h = maintenance.backlog_health("2025-12-30", limit=3)
    assert len(h["ranked"]) <= 3 and "flags" in h
