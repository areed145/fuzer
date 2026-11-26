"""The business plan against actual (step 9 of docs/work-model.md): intent lines with estimates under a plan
container, matched to the real cases that do the work."""

import os
import tempfile
from datetime import UTC, date, datetime

import pytest

from fuzer.core import db, work
from fuzer.ops import business_plan, containers, plan


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        c.execute("insert into entity (fuzer_id, kind) values ('fz-pump-1', 'equipment'), ('fz-pump-2', 'equipment')")
        c.commit()
        yield c


def _job(conn, title: str, subject: str, day: int, hours: float, kind: str = "maintenance") -> tuple[dict, int]:
    start = datetime(2027, 3, day, 6, tzinfo=UTC)
    bar = plan.propose(conn, kind, title, "ann", start, start.replace(hour=6 + int(hours)), refs=[subject],
                       crew_hours=hours)
    case = work.open_case(conn, "task", title, "ann", subjects=[subject])
    conn.execute("update plan_item set item_id = %s where id = %s", (case["id"], bar["id"]))
    return bar, case["id"]


def test_lines_are_matched_to_real_cases_and_read_against_actual_with_the_unplanned(conn):
    bp = containers.create(conn, "business_plan", "2027 business plan", "olga", governed=False)
    ta = containers.create(conn, "turnaround", "Spring outage", "olga", parent=bp["id"], governed=False)
    line = business_plan.add_line(conn, bp["id"], "Overhaul pump 1", "olga", kind="maintenance",
                                  subjects=["fz-pump-1"], period_start=date(2027, 3, 1), period_end=date(2027, 3, 31),
                                  estimate_hours=10, estimate_cost=5000)
    idle = business_plan.add_line(conn, bp["id"], "Paint the deck", "olga", estimate_hours=40)
    with pytest.raises(business_plan.PlanLineError, match="ends after"):
        business_plan.add_line(conn, bp["id"], "x", "olga", period_start=date(2027, 4, 1), period_end=date(2027, 3, 1))

    a, a_case = _job(conn, "Strip pump 1", "fz-pump-1", 3, 6)
    b, b_case = _job(conn, "Rebuild pump 1", "fz-pump-1", 10, 8)
    _job(conn, "Pump 1 next year", "fz-pump-1", 10, 4)  # outside the period: moved to 2028 below
    conn.execute("update plan_item set start_at = start_at + interval '1 year', end_at = end_at + interval '1 year' "
                 "where title = 'Pump 1 next year'")
    c, c_case = _job(conn, "Seal on pump 2", "fz-pump-2", 12, 3)
    conn.execute("update plan_item set container_id = %s where id = any(%s)", (ta["id"], [a["id"], b["id"], c["id"]]))

    assert business_plan.auto_match(conn, bp["id"], "fuzer")["matched"] == 2
    assert business_plan.auto_match(conn, bp["id"], "fuzer")["matched"] == 0  # once
    plan.update(conn, a["id"], "fred", status="done")
    work.close_case(conn, a_case, "done", "fred", reason="stripped")

    out = business_plan.against_actual(conn, bp["id"])
    [got, empty] = out["lines"]
    assert got["id"] == line["id"] and sorted(got["case_ids"]) == sorted([a_case, b_case])
    assert got["state"] == "in progress" and got["actual"]["hours_done"] == 6 and got["actual"]["hours_booked"] == 8
    assert got["hours_variance"] == 4  # 14 hours against 10
    assert empty["id"] == idle["id"] and empty["state"] == "not started"
    assert [u["id"] for u in out["unplanned"]] == [c_case]  # in the outage, on no line
    assert out["totals"]["estimate_hours"] == 50 and out["totals"]["unplanned_cases"] == 1

    business_plan.match(conn, idle["id"], c_case, "olga")  # a person says it was this line's work
    business_plan.update_line(conn, idle["id"], "olga", status="dropped")
    out = business_plan.against_actual(conn, bp["id"])
    assert out["unplanned"] == [] and out["lines"][1]["state"] == "dropped" and out["totals"]["lines"] == 1
    assert business_plan.unmatch(conn, c_case, "olga")
    assert [u["id"] for u in business_plan.against_actual(conn, bp["id"])["unplanned"]] == [c_case]
    conn.commit()


def test_planned_hours_to_date_count_the_part_of_each_line_in_the_window():
    """#1386 (tour C, A9): the quarter to date showed each line's whole-quarter estimate (740 h) and the window before
    it the lines of two quarters it touched (1,408 h). Each estimate counts for the days of its period in the window."""
    from fuzer.core import metrics, windows
    from fuzer.ops.metrics import _in_window

    q = metrics.Query(None, windows.span("qtd", date(2026, 11, 25)))
    q4 = {"period_start": date(2026, 10, 1), "period_end": date(2026, 12, 31)}
    assert _in_window(q4, q) == pytest.approx(56 / 92)
    before = q.at(windows.before(q.span))  # 6 Aug to 30 Sep
    q3 = {"period_start": date(2026, 7, 1), "period_end": date(2026, 9, 30)}
    assert _in_window(q3, before) == pytest.approx(56 / 92) and _in_window(q4, before) == 0
    assert _in_window({"period_start": None, "period_end": None}, q) == 1.0
