"""Schedules on the integrated plan (P5.18, ops/schedule.py): critical path, levelling and the planner's edits."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from fuzer.core.plan import PlanError
from fuzer.ops import plan, schedule

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "fakeplant" / "bundles" / "okan" / "schedules" / "OK-TAR-2026.yaml"


# ---- the pure engine --------------------------------------------------------------------------------------------

def test_the_critical_path_is_the_longest_chain_and_the_rest_has_float():
    d = {"shut": 10, "pump": 20, "valve": 5, "start": 4}
    links = [("shut", "pump", "FS", 0), ("shut", "valve", "FS", 0), ("pump", "start", "FS", 0), ("valve", "start", "FS", 0)]
    cp = schedule.critical_path(d, links)
    assert cp["finish"] == 34
    assert cp["chain"] == ["shut", "pump", "start"]
    assert cp["float"]["valve"] == 15 and cp["float"]["pump"] == 0


def test_start_to_start_and_finish_to_finish_links_with_lags():
    cp = schedule.critical_path({"a": 10, "b": 4, "c": 6}, [("a", "b", "SS", 2), ("a", "c", "FF", 3)])
    assert (cp["es"]["b"], cp["ef"]["c"]) == (2, 13)  # b starts 2 h after a starts; c finishes 3 h after a finishes


def test_a_loop_is_refused_naming_it():
    with pytest.raises(PlanError, match="loops back"):
        schedule.critical_path({"a": 1, "b": 1}, [("a", "b", "FS", 0), ("b", "a", "FS", 0)])


def test_levelling_moves_work_until_the_crew_has_room_and_says_who_held_it():
    d = {"overhaul": 40, "retube": 30, "pump": 10}
    need = {"overhaul": {"MECH": 4}, "retube": {"MECH": 3}, "pump": {"MECH": 2}}
    lv = schedule.level(d, [], need, lambda r, h: 6)
    # 4 + 3 > 6: the second job waits for the first, and the pump (2) fits beside whichever is running
    assert lv["end"] == 70
    held = [k for k, w in lv["waited"].items() if w]
    assert held == ["retube"] and lv["why"]["retube"] == {"resource": "MECH", "held_by": ["overhaul"]}
    assert lv["chain"][-1] == {"activity": "retube", "waited_for": "MECH"}


def test_other_planned_work_on_the_crew_counts_against_it():
    lv = schedule.level({"job": 10}, [], {"job": {"MECH": 4}}, lambda r, h: 6, background={"MECH": [(0, 24, 3)]})
    assert lv["start"]["job"] == 24


def test_overloads_name_the_resource_the_need_and_the_jobs():
    d = {"a": 10, "b": 10}
    over = schedule.overloads(d, {"a": 0, "b": 5}, {"a": {"MECH": 4}, "b": {"MECH": 4}}, lambda r, h: 6)
    assert over == [{"resource": "MECH", "from": 5, "to": 10, "peak": 8, "capacity": 6, "activities": ["a", "b"],
                     "other_work": False}]


def test_a_day_shift_calendar_turns_working_hours_into_dates():
    cal = schedule.Calendar(datetime(2027, 2, 22, 6), hours_per_day=12, days_per_week=5)
    assert cal.at(12) == datetime(2027, 2, 22, 18)
    assert cal.at(13) == datetime(2027, 2, 23, 7)
    assert cal.at(60) == datetime(2027, 2, 26, 18)  # Friday's close
    assert cal.at(61) == datetime(2027, 3, 1, 7)  # the weekend skipped
    assert cal.hours(datetime(2027, 3, 1, 7)) == 61


# ---- Okan's 2026 turnaround on the plan ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tar(okan_db):
    c = okan_db.conn
    plan.sync_work_orders(c)
    out = schedule.import_template(c, yaml.safe_load(TEMPLATE.read_text()), "tests")
    c.commit()
    return c, out


def test_the_template_lands_on_the_plan_under_the_sap_orders(tar):
    c, out = tar
    assert out["status"] == "imported" and out["activities"] == 25 and out["orders"] == 5
    again = schedule.import_template(c, yaml.safe_load(TEMPLATE.read_text()), "tests")
    assert again["status"] == "kept"
    rows = c.execute("select o.source_key as order_no, count(*) as ops from plan_item a join plan_item o on o.id = a.parent_id "
                     "where a.project = 'OK-TAR-2026' group by o.source_key order by 1").fetchall()
    assert [r["ops"] for r in rows] == [4, 3, 4, 3, 1]
    c.commit()


def test_the_critical_path_runs_through_the_compressor_overhaul_and_the_crew_sets_the_levelled_finish(tar):
    c, _ = tar
    out = schedule.analyse(c, "OK-TAR-2026")
    cp = out["critical_path"]
    assert cp[0] == "A0010" and cp[-1] == "A0900"
    assert {"A0100", "A0110", "A0130"} <= set(cp), cp
    assert out["levelled_finish"] >= out["unconstrained_finish"]
    mech = [o for o in out["overloads_if_unlevelled"] if o["resource"] == "MECH"]
    assert mech and mech[0]["need"] > 6 and mech[0]["capacity"] == 6
    assert any(a["held_back_by"] and a["held_back_by"]["resource"] == "MECH" for a in out["activities"])
    assert not [o for o in out["overloads_levelled"] if not o["other_work"]], "levelling leaves no overload of its own"
    c.commit()


def test_a_planner_shortens_an_activity_and_the_schedule_follows(tar):
    c, _ = tar
    before = schedule.analyse(c, "OK-TAR-2026")
    a0110 = next(a for a in before["activities"] if a["code"] == "A0110")
    schedule.change_activity(c, a0110["id"], "tests", duration_hours=24)
    after = schedule.analyse(c, "OK-TAR-2026")
    assert after["unconstrained_finish"] < before["unconstrained_finish"]
    schedule.change_activity(c, a0110["id"], "tests", duration_hours=48)
    c.commit()


def test_a_link_that_would_loop_is_refused(tar):
    c, _ = tar
    ids = {r["code"]: r["id"] for r in c.execute("select code, id from plan_item where project = 'OK-TAR-2026' and code is not null")}
    with pytest.raises(PlanError, match="loop"):
        schedule.link(c, ids["A0900"], ids["A0010"], "tests")
    c.rollback()


def test_apply_puts_the_levelled_dates_on_the_integrated_plan(tar):
    c, _ = tar
    out = schedule.apply(c, "OK-TAR-2026", "tests")
    c.commit()
    items = plan.items(c, out["finish"] - timedelta(days=40), out["finish"] + timedelta(days=1))
    restart = next(i for i in items if i.get("code") == "A0900")
    assert restart["end_at"] == out["finish"]
    summary = next(i for i in items if i["source"] == "sap" and i.get("project") == "OK-TAR-2026")
    assert summary["start_at"] <= summary["end_at"]



def test_the_specialist_reads_the_critical_path_and_draws_a_gantt(tar):
    from fuzer.agents.capabilities import maintenance as tools
    from fuzer.plots import maintenance as charts

    c, _ = tar
    out = tools.critical_path("OK-TAR-2026")
    assert out["critical_path"][0] == "A0010" and out["levelled_finish"] >= out["unconstrained_finish"]
    assert "error" in tools.critical_path("NO-SUCH-TAR")
    fig = charts.gantt(c, "OK-TAR-2026")
    assert fig.kind == "gantt" and {t["name"] for t in fig.data} == {"critical path", "other work"}
    bars = sum(len(t["y"]) for t in fig.data)
    assert bars == 25 and fig.layout["xaxis"]["type"] == "date"
    drawn = tools.plot_gantt("OK-TAR-2026")
    assert "error" not in drawn, drawn
    c.commit()





def test_a_resource_histogram_shows_each_day_s_peak_against_capacity():
    from fuzer.ops.schedule import profile
    out = profile({"a": 30, "b": 10}, {"a": 0, "b": 10}, {"a": {"MECH": 4}, "b": {"MECH": 3}},
                  lambda r, h: 6, {"MECH": [(24, 48, 2)]}, end=48)
    assert [(d["schedule"], d["other"], d["capacity"]) for d in out["MECH"]] == [(7, 0, 6), (4, 2, 6)]


def test_a_resource_owner_sees_demand_by_source_against_capacity_and_the_move_that_clears_an_overload(tar):
    c, _ = tar
    start = datetime(2026, 4, 6, tzinfo=UTC)
    load = schedule.resource_load(c, "MECH", start, 21)
    [mech] = load["resources"]
    assert len(mech["days"]) == 21 and "OK-TAR-2026" in mech["sources"]
    busy = [d for d in mech["days"] if d["demand"]]
    assert busy and all(round(sum(d["by_source"].values()), 2) == d["demand"] for d in busy)
    assert not [o for o in mech["overloads"] if o["from"] < datetime(2026, 4, 20, tzinfo=UTC)], "levelled: no overload"
    # two fitters off for three days: the owner sees the overload, the jobs in it and the cheapest move
    w = schedule.set_capacity_window(c, "MECH", datetime(2026, 4, 9, tzinfo=UTC), datetime(2026, 4, 12, tzinfo=UTC), 4,
                                     "tests", "leave")
    mech = schedule.resource_load(c, "MECH", start, 21)["resources"][0]
    [run, *_] = mech["overloads"]
    assert run["capacity"] == 4 and run["peak"] > 4 and run["jobs"] and run["moves"]
    best = run["moves"][0]
    assert best["slip_hours"] > 0 and best["new_start"] == run["to"]
    assert mech["capacity_windows"][0]["reason"] == "leave"
    assert any(d["capacity"] == 4 for d in mech["days"])
    schedule.remove_capacity_window(c, w["id"], "tests")
    c.rollback()


def test_the_resources_page_s_day_with_no_zone_loads(tar):
    """The page sends `start=2026-04-06`, which arrives without a zone, and the default is the plant's wall clock: both
    load, read as the plant's time, rather than failing on an aware-minus-naive subtraction."""
    c, _ = tar
    load = schedule.resource_load(c, "MECH", datetime(2026, 4, 6), 21)
    assert len(load["resources"][0]["days"]) == 21
    assert schedule.resource_load(c, "MECH", None, 7)["resources"][0]["days"]
    c.rollback()


def test_a_planner_holds_an_activity_to_start_no_earlier_and_lets_it_go(tar):
    c, _ = tar
    a = next(x for x in schedule.analyse(c, "OK-TAR-2026")["activities"] if x["code"] == "A0500")
    later = a["start"] + timedelta(days=2)
    schedule.change_activity(c, a["id"], "tests", not_before=later)
    held = next(x for x in schedule.analyse(c, "OK-TAR-2026")["activities"] if x["code"] == "A0500")
    assert held["start"] >= later and held["early_start"] >= later and held["not_before"] == later
    schedule.change_activity(c, a["id"], "tests", not_before=None)
    assert next(x for x in schedule.analyse(c, "OK-TAR-2026")["activities"] if x["code"] == "A0500")["start"] == a["start"]
    c.rollback()


def test_bed_space_counts_the_visiting_crews_and_holds_work_when_beds_run_short(tar):
    c, _ = tar
    out = schedule.analyse(c, "OK-TAR-2026")
    beds = next(r for r in out["resources"] if r["code"] == "BEDS")
    assert beds["counts"] == ["MECH", "INST", "SCAF", "NDT"]
    a100 = next(a for a in out["activities"] if a["code"] == "A0100")
    assert a100["needs"] == {"MECH": 4, "CRANE": 1, "BEDS": 4}  # four fitters, four beds; the crane sleeps nowhere
    a10 = next(a for a in out["activities"] if a["code"] == "A0010")
    assert "BEDS" not in a10["needs"]  # the operators are core crew
    days = schedule.resource_load(c, "BEDS", datetime(2026, 4, 6, tzinfo=UTC), 21)["resources"][0]["days"]
    assert 0 < max(d["demand"] for d in days) <= 14
    # eight beds lost to another campaign for the whole window: work waits for beds and the turnaround runs longer
    schedule.set_capacity_window(c, "BEDS", datetime(2026, 4, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC), 6, "tests", "drilling crew aboard")
    short = schedule.analyse(c, "OK-TAR-2026")
    assert short["levelled_finish"] > out["levelled_finish"]
    assert any(a["held_back_by"] and a["held_back_by"]["resource"] == "BEDS" for a in short["activities"])
    with pytest.raises(PlanError, match="counts none itself"):
        schedule.set_resource(c, "SEATS", "tests", kind="seats", capacity=12, counts=["BEDS"])
    c.rollback()


def test_operator_rounds_and_checks_hold_operators_whatever_else_is_planned(tar):
    c, _ = tar
    from fuzer.ops import rounds

    checks = len(rounds.points(c))
    assert checks, "Okan has operator rounds"
    out = schedule.analyse(c, "OK-TAR-2026")
    ops = next(r for r in out["resources"] if r["code"] == "OPS")
    assert ops["routine"]["source"] == "Rounds and checks" and f"{checks} round checks a shift" in ops["routine"]["basis"]
    assert 0 < ops["routine"]["quantity"] <= 1
    load = schedule.resource_load(c, "OPS", datetime(2026, 4, 6, tzinfo=UTC), 21)["resources"][0]
    assert "Rounds and checks" in load["sources"]
    assert all(d["by_source"]["Rounds and checks"] == ops["routine"]["quantity"] for d in load["days"])  # every day, busy or not
    assert not load["overloads"], "four operators cover three on isolations and the rounds"
    # with only three operators the isolations no longer fit beside the rounds
    schedule.set_resource(c, "OPS", "tests", capacity=3)
    assert schedule.resource_load(c, "OPS", datetime(2026, 4, 6, tzinfo=UTC), 21)["resources"][0]["overloads"]
    schedule.set_resource(c, "OPS", "tests", capacity=3, routine="")
    assert not schedule.resource_load(c, "OPS", datetime(2026, 4, 6, tzinfo=UTC), 21)["resources"][0]["overloads"]
    c.rollback()


def test_seats_carry_each_crew_out_for_its_first_job_and_back_after_its_last(tar):
    c, _ = tar
    out = schedule.analyse(c, "OK-TAR-2026")
    assert all("SEATS" not in a["needs"] for a in out["activities"]), "seats follow crew changes, not each job"
    a200 = next(a for a in out["activities"] if a["code"] == "A0200")
    assert a200["needs"]["VESSEL"] == 1
    start = datetime(2026, 4, 1, tzinfo=UTC)
    seats = schedule.resource_load(c, "SEATS", start, 30)["resources"][0]
    busy = {d["day"]: d["demand"] for d in seats["days"] if d["demand"]}
    scaf = [a for a in out["activities"] if "SCAF" in a["needs"]]
    first, last = min(a["start"] for a in scaf).date().isoformat(), max(a["finish"] for a in scaf).date().isoformat()
    assert busy[first] >= 4 and busy[last] >= 4  # four scaffolders out, four back
    assert sum(busy.values()) == 2 * (6 + 2 + 4 + 2)  # every crew at its peak size, out and back
    assert seats["sources"] == ["OK-TAR-2026"] and not seats["overloads"]
    # five seats a day: a day the fitters (6) fly is over, and the owner sees who is on it
    schedule.set_resource(c, "SEATS", "tests", capacity=5)
    [run, *_] = schedule.resource_load(c, "SEATS", start, 30)["resources"][0]["overloads"]
    assert run["peak"] > 5 and any("flies" in j["title"] for j in run["jobs"]) and run["moves"] == []
    c.rollback()


def test_production_capacity_caps_what_is_online_less_what_jobs_have_down_plus_what_jobs_bring_on(tar):
    """Adam: "the cap on how many can be online at once. You don't want to plan to bring on more production than
    the system can handle." """
    c, _ = tar
    from fuzer.core.units import boe
    from fuzer.ops import at_risk

    _, nodes = at_risk._flow(c)
    wells = [r["fuzer_id"] for r in c.execute("select fuzer_id from entity where kind = 'well' and fuzer_id = any(%s)", (list(nodes),))]
    field = sum(boe(sum(q[k]["value"] for k in ("oil_rate", "condensate_rate") if k in q), (q.get("gas_rate") or {}).get("value"))
                for q in at_risk._rates(c, wells).values())
    schedule.set_resource(c, "PROD", "tests", name="Production the system can handle", kind="capacity", unit="BOE/d",
                          capacity=field + 500)
    start = datetime(2026, 4, 1, tzinfo=UTC)
    prod = schedule.resource_load(c, "PROD", start, 40)["resources"][0]
    by_day = {d["day"]: d for d in prod["days"]}
    # before the turnaround every tested well is on; during it the wells behind the units it has down are off
    assert abs(by_day["2026-04-02"]["demand"] - field) < 1 and by_day["2026-04-02"]["by_source"] == {schedule.PRODUCING: by_day["2026-04-02"]["demand"]}
    assert min(d["demand"] for d in prod["days"]) < field - 1 and not prod["overloads"]
    # a job that brings 400 BOE/d on fits under the cap; one that brings 900 on is over it from the day it finishes
    restart = next(a for a in schedule.analyse(c, "OK-TAR-2026")["activities"] if a["code"] == "A0220")
    schedule.change_activity(c, restart["id"], "tests", adds_boed=400)
    assert not schedule.resource_load(c, "PROD", start, 40)["resources"][0]["overloads"]
    schedule.change_activity(c, restart["id"], "tests", adds_boed=900)
    prod = schedule.resource_load(c, "PROD", start, 40)["resources"][0]
    [run] = prod["overloads"]
    assert abs(run["peak"] - (field + 900)) < 1 and [j["title"] for j in run["jobs"]] == [restart["name"]]
    assert "OK-TAR-2026" in prod["sources"] and run["from"].date() >= restart["planned_finish"].date()
    with pytest.raises(schedule.PlanError):
        schedule.change_activity(c, restart["id"], "tests", adds_boed=-1)
    c.rollback()


def test_logistics_reads_each_day_s_crews_travel_seats_beds_and_lifts_from_the_plan(tar):
    """Dispatch and logistics from the schedule: who goes where each day, who travels, and what they take."""
    c, _ = tar
    out = schedule.analyse(c, "OK-TAR-2026")
    scaf = [a for a in out["activities"] if "SCAF" in a["needs"]]
    first = min(a["start"] for a in scaf)
    start = first.replace(hour=0, minute=0, second=0, microsecond=0)
    plan_ = schedule.logistics(c, start - timedelta(days=1), 40)
    days = {d["day"]: d for d in plan_["by_day"]}
    day = days[first.date().isoformat()]
    crew = next(x for x in day["crews"] if x["resource"] == "SCAF")
    assert {j["code"] for j in crew["jobs"]} >= {a["code"] for a in scaf if a["start"] == first}
    assert all(j["schedule"] == "OK-TAR-2026" and j["assets"] for j in crew["jobs"])
    [trip] = [t for t in day["travel"] if t["crew"] == "SCAF"]
    assert trip["way"] == "out" and trip["people"] == 4 and trip["by"], "the scaffolders fly out the day they start"
    seats = next(x for x in day["limits"] if x["kind"] == "seats")
    assert seats["need"] >= 4 and seats["capacity"] == 12 and not seats["over"]
    back = days[max(a["finish"] for a in scaf).date().isoformat()]
    assert any(t["crew"] == "SCAF" and t["way"] == "back" for t in back["travel"])
    lifts = [d for d in plan_["by_day"] if any(e["kind"] == "crane" for e in d["equipment"])]
    assert lifts and all(e["jobs"] for d in lifts for e in d["equipment"])
    # the seats the day needs are the Resources page's own figure, not a second count
    load = schedule.resource_load(c, "SEATS", start - timedelta(days=1), 40)["resources"][0]
    assert {d["day"]: d["demand"] for d in load["days"] if d["demand"]} == {
        d["day"]: x["need"] for d in plan_["by_day"] for x in d["limits"] if x["kind"] == "seats"}
    c.rollback()


def test_the_specialist_reads_a_day_s_logistics(tar):
    c, _ = tar
    from fuzer.agents.capabilities import maintenance

    first = min(a["start"] for a in schedule.analyse(c, "OK-TAR-2026")["activities"])
    out = maintenance.logistics_plan(first.date().isoformat(), 3)
    assert out["by_day"][0]["day"] == first.date().isoformat() and out["by_day"][0]["crews"]
    assert "error" in maintenance.logistics_plan("next week")
