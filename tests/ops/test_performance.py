"""How the work is running (P7.2a, #660): this week against the last four, per queue and type, from the history."""

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from fuzer.core import db
from fuzer.ops import performance

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)  # a Thursday
MONDAY = datetime(2026, 11, 9, tzinfo=UTC)


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        c.commit()
        yield c


def case(conn, role, opened, closed=None, type_="upset"):
    return conn.execute("insert into work_item (type, title, state, role, created_by, created_at, closed_at) "
                        "values (%s, 'x', %s, %s, 'ioc-ann', %s, %s) returning id",
                        (type_, "done" if closed else "open", role, opened, closed)).fetchone()["id"]


def event(conn, item_id, kind, actor, at, **data):
    from psycopg.types.json import Jsonb
    conn.execute("insert into work_event (item_id, kind, actor, at, data) values (%s, %s, %s, %s, %s)",
                 (item_id, kind, actor, at, Jsonb(data)))


def test_a_queue_falling_behind_this_week_shows_against_the_last_four_with_its_cases(conn):
    # the operator queue: one case a week for four weeks, each closed in two days; this week three arrive, none close
    old = []
    for k in range(4, 0, -1):
        opened = MONDAY - timedelta(weeks=k) + timedelta(days=1)
        old.append(case(conn, "operator", opened, opened + timedelta(days=2)))
    new = [case(conn, "operator", MONDAY + timedelta(hours=h)) for h in (2, 10, 30)]
    event(conn, new[0], "commented", "fred", MONDAY + timedelta(hours=3))           # triaged an hour in
    event(conn, new[0], "handed_off", "ines", MONDAY + timedelta(hours=6), to="fred")  # dispatched four hours in
    event(conn, new[0], "handed_off", "fred", MONDAY + timedelta(hours=8), to="ines")  # and handed back
    event(conn, new[1], "escalated", "fuzer", MONDAY + timedelta(hours=20))
    # the planners keep up: one in, one out
    p = case(conn, "role:maintenance_planner", MONDAY + timedelta(hours=1), MONDAY + timedelta(hours=5), type_="pm")
    conn.commit()

    out = performance.measures(conn, NOW)
    assert [w["start"][:10] for w in out["weeks"]] == ["2026-10-12", "2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09"]
    ops = next(q for q in out["queues"] if q["queue"] == "operator")
    this = ops["weeks"][-1]
    assert [w["arrivals"]["n"] for w in ops["weeks"]] == [1, 1, 1, 1, 3]
    assert [w["closed"]["n"] for w in ops["weeks"]] == [1, 1, 1, 1, 0]
    assert this["backlog"] == {"n": 3, "ids": new}
    assert (this["triage_h"], this["dispatch_h"]) == (1.0, 4.0)
    assert this["hand_backs"] == {"n": 1, "ids": [new[0]]} and this["escalations"]["ids"] == [new[1]]
    assert ops["weeks"][0]["close_h"] == 48.0
    assert ops["keeping_up"] == "falling_behind" and ops["backlog_avg4"] == 0.0
    planners = next(q for q in out["queues"] if q["queue"] == "maintenance_planner")
    assert planners["keeping_up"] == "keeping_up" and planners["weeks"][-1]["closed"]["ids"] == [p]
    assert {t["type"] for t in out["types"]} == {"upset", "pm"}
    assert out["compliance"] is None  # no maintenance plans in this project
