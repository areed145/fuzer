"""Crew load (#361): the backlog in crew-weeks per work center from planned hours and the crew's calendar, and the
days the work scheduled ahead asks a crew for more people than it has. On Okan the 2026 turnaround's five orders all
start on 6 April and need more of the mechanical crew than its six people: the conflict names the crew, the dates and
the orders."""

import pytest

from fuzer.analysis import crew_load as cl


@pytest.mark.unit
def test_working_days_skip_the_weekend_and_spans_join_across_it():
    from datetime import date

    days = cl._workdays(date(2026, 4, 9), 3, 5)  # Thursday
    assert [d.isoformat() for d in days] == ["2026-04-09", "2026-04-10", "2026-04-13"]
    assert cl._spans(days, 5) == [(date(2026, 4, 9), date(2026, 4, 13))]


def test_a_crew_conflict_names_its_crew_dates_and_orders(okan_db):
    conn = okan_db.conn
    got = cl.load(conn, as_of="2025-12-31", weeks=26)
    mech = next(c for c in got["centers"] if c["center"] == "MECH")
    assert mech["crew"] == 6 and mech["calendar_given"] and mech["open_orders"] >= 30 and mech["planned_hours"] > 0
    assert mech["crew_weeks"] == pytest.approx(mech["planned_hours"] / (6 * 8 * 5), abs=0.05)
    assert mech["past_start"] > 0 and mech["scheduled_ahead"] == 5  # the turnaround's orders; the rest is backlog
    c = got["conflicts"][0]
    assert got["verdict"] == "conflicts" and c["center"] == "MECH" and c["from"] == "2026-04-06" and c["needed"] > 6
    assert len(c["orders"]) == 5 and all(o["revision"] == "OK-TAR-2026" for o in c["orders"])
    assert "has 6 people" in c["say"] and all(o["order"] in c["say"] for o in c["orders"])
    assert any(w["over"] and w["week"] == "2026-04-06" for w in mech["weekly"])
    assert "approve schedules" in got["note"]


def test_the_tool_and_chart_carry_the_load(okan_db):
    from fuzer.agents.capabilities import maintenance
    from fuzer.plots import general

    got = maintenance.crew_load(as_of="2025-12-31")
    assert got["conflicts"] and got["summary"]["crew_weeks"]["MECH"] > 0
    fig = general.crew_load(okan_db.conn, as_of="2025-12-31")
    assert fig.summary["verdict"] == "conflicts" and fig.data[0]["name"] == "MECH planned hours"
