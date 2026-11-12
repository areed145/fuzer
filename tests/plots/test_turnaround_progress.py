"""A turnaround's S-curve (P5.9b, #360): planned and forecast percent done by week from the schedule's baseline and
current dates, the percent earned today, the forecast slip, and rows whose figures can't be used left out and named
instead of plotted."""

from fuzer.graph.operations import schedule_curve
from fuzer.plots import check
from fuzer.plots.analytics import turnaround_progress


def act(aid, bs, bf, pct, *, s=None, f=None, parent="P", project=False, given=True):
    return {"id": aid, "activity": aid, "description": f"Job {aid}", "parent": None if project else parent,
            "project": project, "baseline_start": bs, "baseline_finish": bf, "start": s or bs, "finish": f or bf,
            "percent_complete": pct, "percent_given": given, "slipped_days": 0}


SCHEDULE = [
    act("P", "2026-01-05", "2026-02-02", 30, project=True),
    act("A", "2026-01-05", "2026-01-19", 100),  # two weeks, done
    act("B", "2026-01-19", "2026-02-02", 0, f="2026-02-16"),  # two weeks, not started, finish moved two weeks
    act("C", "2026-01-05", "2026-01-12", 112),  # a planner's typo: not counted
    act("D", None, None, 50),  # no baseline: not counted
]


def test_planned_earned_and_slip_from_the_lowest_level_activities():
    c = schedule_curve(SCHEDULE, "2026-01-19")
    assert c["used"] == 2  # A and B; the project row's own 30% is not counted again
    assert c["earned"] == 50.0 and c["planned_today"] == 50.0 and c["variance"] == 0.0
    pts = {p["day"]: p for p in c["points"]}
    assert pts["2026-01-05"]["planned"] == 0 and pts["2026-01-12"]["planned"] == 25.0 and pts["2026-02-02"]["planned"] == 100
    assert pts["2026-02-02"]["forecast"] == 75.0 and pts["2026-02-16"]["forecast"] == 100  # B runs four weeks now
    assert (c["baseline_finish"], c["forecast_finish"], c["slip_days"]) == ("2026-02-02", "2026-02-16", 14)
    assert c["left_out"] == [{"activity": "C", "why": "a percent complete of 112, outside 0 to 100"},
                             {"activity": "D", "why": "no baseline dates"}]


def test_behind_the_plan_and_nothing_usable():
    c = schedule_curve(SCHEDULE, "2026-01-26")
    assert c["planned_today"] == 75.0 and c["earned"] == 50.0 and c["variance"] == -25.0
    unread = schedule_curve([act("Y", "30/04/2026", "05/05/2026", 10)], "2026-01-06")
    assert unread["left_out"] == [{"activity": "Y", "why": "dates that can't be read (30/04/2026): set the source's date format"}]
    none = schedule_curve([act("X", "2026-01-05", "2026-01-12", 0, given=False)], "2026-01-06")
    assert none["points"] == [] and none["left_out"] == [{"activity": "X", "why": "no percent complete given"}]


def test_the_figure_says_where_it_stands_and_what_it_left_out():
    sched = {"as_of": "2026-01-26", "source": "the planning system's schedule (activities)",
             "activities": [{**a, "slipped_days": 14 if a["id"] == "B" else 0} for a in SCHEDULE],
             "curve": schedule_curve(SCHEDULE, "2026-01-26")}
    fig = turnaround_progress(sched, "TA26", "Fake data.")
    check(fig)
    s = fig.summary
    assert s["headline"] == "50% earned against 75% planned: 25 points behind the plan, forecast to finish 14 days after its baseline."
    assert [d["name"] for d in fig.data] == ["Planned (baseline)", "Forecast (today's dates)", "Earned"]
    assert s["warnings"] == ["Not counted: C, a percent complete of 112, outside 0 to 100.", "Not counted: D, no baseline dates."]
    assert s["tables"][0]["rows"] == [["B", "Job B", "2 Feb 2026", "16 Feb 2026", "14", "0"]]
    empty = turnaround_progress(None, "TA26", "Fake data.")
    assert empty.data == [] and empty.summary["headline"] == "No planning system's schedule names TA26."
