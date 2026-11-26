"""Re-plan when something breaks (P5.21, ops/replan.py): a trip, a late part or a lapsed permit on planned work is a
plan change for the planner, with each move, the constraint behind it and the KPIs before and after."""

from pathlib import Path

import pytest
import yaml

from fuzer.analysis import trips
from fuzer.core.exceptions import clear_exception, raise_exception
from fuzer.core.findings import DETERMINISTIC, Finding, fact
from fuzer.core.plan import PlanError
from fuzer.ops import materials, plan, replan, schedule
from fuzer.ops.plan import _when

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "fakeplant" / "bundles" / "okan" / "schedules" / "OK-TAR-2026.yaml"


@pytest.fixture(scope="module")
def c(okan_db):
    conn = okan_db.conn
    plan.sync_work_orders(conn)
    plan.sync_maintenance_plans(conn)
    schedule.import_template(conn, yaml.safe_load(TEMPLATE.read_text()), "tests")
    conn.commit()
    return conn


def _tag(c, label: str) -> str:
    return c.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def _trip(c, label: str, at: str) -> dict:
    """A trip as the trip scan raises it (trips.TYPE, its time in `tripped_at`), seeded on `label`."""
    found = Finding("trips.diagnosis", DETERMINISTIC, start=at, data={"tripped_at": at},
                    items=(fact("Train", f"{label}'s train"),))
    x = raise_exception(c, trips.TYPE, f"tests:{label}@{at}", f"{label} tripped {at}: seeded for the plan change",
                        "tests", source="trips", refs=[_tag(c, label)], finding=found)
    c.commit()
    return x


def _late_part(c, order: str, need: str, days_late: int) -> dict:
    """A part on order too late for `order`'s job, as the parts check raises it."""
    found = Finding("materials.parts_not_ready", DETERMINISTIC, start=need, data={"work_order": order, "lines": [
        {"material": "MAT-TEST", "verdict": "late", "need_date": need, "days_late": days_late,
         "earliest_if_ordered_now": None}]}, items=(fact("Work order", order),))
    oid = c.execute("select fuzer_id from entity_key where kind = 'work_order' and key = %s", (order,)).fetchone()
    x = raise_exception(c, materials.TYPE, order, f"{order}: 1 part on order too late", "tests", source="sap",
                        refs=[oid["fuzer_id"]] if oid else [], finding=found)
    c.commit()
    return x


def _change(c, cause: dict) -> dict:
    pc = c.execute("select exception_id from plan_change where key = %s", (f"{cause['type']}:{cause['key']}",)).fetchone()
    return replan.change(c, pc["exception_id"])


def test_a_trip_on_planned_work_is_a_plan_change_with_each_move_and_why(c):
    """20-K-2200B trips the day before its preventive maintenance: the job needs it running, so it moves to after the
    hours a trip is allowed for, and the plan change says so, routed to the planner."""
    trip = _trip(c, "20-K-2200B", "2025-10-24T12:00")
    out = replan.watch(c)
    assert not out["errors"], out
    p = _change(c, trip)
    assert p["say"] == "20-K-2200B tripped" and p["constraint"].startswith("20-K-2200B tripped: down until Sat 25 Oct 12:00")
    [m] = [m for m in p["proposal"]["moves"] if m["order"] == "50000303"]
    assert _when(m["after"]["start"]) == _when("2025-10-25T12:00") and m["hours"] == 12
    assert m["because"] == p["constraint"]
    k = p["proposal"]["kpis"]
    assert set(k["before"]) == {"finish", "deferred_oil", "deferred_unit", "overloads"}
    assert _when(k["after"]["finish"]) > _when(k["before"]["finish"])
    e = c.execute("select * from exception_record where id = %s", (p["exception_id"],)).fetchone()
    assert e["type"] == "plan_change" and e["state"] == "open" and e["title"].startswith("20-K-2200B tripped: ")
    case = c.execute("select role, type from work_item where id = %s", (e["item_id"],)).fetchone()
    assert case["role"] == "maintenance_planner"
    # nothing moved yet: the proposal was read in a savepoint
    assert _when(c.execute("select start_at from plan_item where source = 'sap' and source_key = '50000303'")
                 .fetchone()["start_at"]) == _when("2025-10-25T00:00")
    # once the trip's case is closed, nothing holds the job: the plan change clears
    clear_exception(c, trips.TYPE, trip["key"], "tests", reason="restarted")
    c.commit()
    replan.watch(c)
    assert c.execute("select state from exception_record where id = %s", (p["exception_id"],)).fetchone()["state"] != "open"


def test_work_that_needs_the_machine_down_is_worth_pulling_forward(c):
    tag = _tag(c, "20-K-2200B")
    job = plan.propose(c, "maintenance", "Replace the 20-K-2200B coupling", "tests", "2025-11-03T06:00",
                       "2025-11-03T18:00", refs=[tag], needs_shutdown=True, source="tests", source_key="pull-forward")
    trip = _trip(c, "20-K-2200B", "2025-10-30T06:00")
    d = replan.disruption(c, c.execute("select * from exception_record where id = %s", (trip["id"],)).fetchone())
    assert [x["item_id"] for x in replan.pull_forward(c, d)] == [job["id"]]
    assert not [h for h in replan.hits(c, d) if h["id"] == job["id"]], "work that needs it down isn't held by a trip"
    clear_exception(c, trips.TYPE, trip["key"], "tests", reason="restarted")
    plan.update(c, job["id"], "tests", status="cancelled")
    c.commit()


def test_a_late_part_on_a_turnaround_order_levels_the_schedule_again(c):
    """The 20-K-2200B overhaul's bearings arrive three days after the turnaround needs them: its activities wait for
    them, the schedule is levelled again around them, and each move says why."""
    part = _late_part(c, "50000306", "2026-04-08", 3)
    replan.watch(c)
    p = _change(c, part)["proposal"]
    by = {m["code"]: m for m in p["moves"] if m["code"]}
    assert _when(by["A0100"]["after"]["start"]) >= _when("2026-04-11T00:00")
    assert by["A0100"]["because"] == "parts for 50000306 are late: due Sat 11 Apr"
    # what follows them moves after them, or waits for the crew they now hold
    assert by["A0130"]["because"].startswith(("follows ", "waits for Mechanical fitters"))
    assert "OK-TAR-2026" in p["schedules"]
    assert any(m["order"] == "50000306" and m["because"] == "spans its operations" for m in p["moves"])
    assert _when(p["kpis"]["after"]["finish"]) > _when(p["kpis"]["before"]["finish"])
    clear_exception(c, materials.TYPE, part["key"], "tests", reason="covered")
    replan.watch(c)
    c.commit()


def test_a_day_is_read_as_a_planner_types_it():
    ref = _when("2026-04-08T00:00")
    assert replan._day("12 Apr", ref) == replan._day("Apr 12", ref) == replan._day("2026-04-12", ref) == _when("2026-04-12T00:00")
    assert replan._day("12 April 06:00", ref) == _when("2026-04-12T06:00")
    assert replan._day("2 Jan", ref) == _when("2027-01-02T00:00")  # no year: the next one


def test_a_constraint_typed_in_words_changes_the_proposal(c):
    part = _late_part(c, "50000306", "2026-04-08", 3)
    replan.watch(c)
    pid = c.execute("select exception_id from plan_change where key = %s", (f"{part['type']}:{part['key']}",)).fetchone()["exception_id"]
    first = replan.change(c, pid)
    assert first["constraints"] == [] and len(first["forms"]) == 3

    def start(p, code):
        return _when(next(m for m in p["proposal"]["moves"] if m["code"] == code)["after"]["start"])

    # keep an activity on a day: it starts then, and what follows it moves with it
    p = replan.add_constraint(c, pid, "keep A0200 on 13 Apr", "tests")
    assert start(p, "A0200") == _when("2026-04-13T00:00")
    assert next(m for m in p["proposal"]["moves"] if m["code"] == "A0200")["because"] == "keep A0200 on 13 Apr"
    # no crane work before a day: every lift waits for it, and says so
    p = replan.add_constraint(c, pid, "no crane work before 15 Apr", "tests")
    lifts = [m for m in p["proposal"]["moves"] if m["code"] in ("A0100", "A0130", "A0200", "A0220", "A0500")]
    assert lifts and all(_when(m["after"]["start"]) >= _when("2026-04-15T00:00") for m in lifts)
    assert any(m["because"] == "no crane work before 15 Apr" for m in lifts)
    # a crew held to fewer people: the schedule runs longer
    later = replan.add_constraint(c, pid, "crew MECH max 4", "tests")
    assert _when(later["proposal"]["kpis"]["after"]["finish"]) >= _when(p["proposal"]["kpis"]["after"]["finish"])
    assert [x["text"] for x in later["constraints"]] == ["keep A0200 on 13 Apr", "no crane work before 15 Apr",
                                                         "crew MECH max 4"]
    c.commit()
    # a phrase it can't read says which it can
    with pytest.raises(PlanError, match="keep A0200 on 12 Apr; no crane work before 10 Apr; crew MECH max 4"):
        replan.add_constraint(c, pid, "move everything to May", "tests")
    c.rollback()
    with pytest.raises(PlanError, match="nothing on the plan is called A9999"):
        replan.add_constraint(c, pid, "keep A9999 on 13 Apr", "tests")
    c.rollback()
    # taken off again, the proposal is the first one; and none of it touched the plan
    for _ in range(3):
        back = replan.remove_constraint(c, pid, 0, "tests")
    assert back["constraints"] == [] and back["proposal"]["moves"] == first["proposal"]["moves"]
    assert not c.execute("select 1 from plan_resource_capacity where reason like 'Plan change:%'").fetchone()
    clear_exception(c, materials.TYPE, part["key"], "tests", reason="covered")
    replan.watch(c)
    c.commit()


def test_approving_moves_the_plan_queues_sap_for_the_planner_and_drafts_notices(c):
    """The planner approves 20-K-2200B's plan change: the job moves for good and says why, SAP's new dates wait in
    the write-back queue on that planner's own work, and a notice is drafted for the field operator booked on it; a
    person edits it and sends it, and it lands on the moved job's case. The tests run in any order, so the job is put
    back as it was at the end."""
    from fuzer.core import work

    work.add_person(c, "pat", ["maintenance_planner"], "test")
    work.add_person(c, "sam", ["operator"], "test")
    job = c.execute("select * from plan_item where source = 'sap' and source_key = '50000303'").fetchone()
    case = work.open_case(c, "maintenance", job["title"], "tests", assignee="sam")
    c.execute("update plan_item set crew = 'sam', item_id = %s where id = %s", (case["id"], job["id"]))
    c.commit()
    trip = _trip(c, "20-K-2200B", "2025-10-24T12:00")
    try:
        _approve(c, trip, job, case)
    finally:
        c.rollback()
        clear_exception(c, trips.TYPE, trip["key"], "tests", reason="restarted")
        c.execute("update plan_item set start_at = %s, end_at = %s, crew = %s, item_id = %s, moved_by = %s, "
                  "moved_because = null where id = %s",
                  (job["start_at"], job["end_at"], job["crew"], job["item_id"], job["moved_by"], job["id"]))
        replan.watch(c)
        c.commit()


def _approve(c, trip, job, case):
    from fuzer.ops.exceptions import field_jobs

    replan.watch(c)
    pid = _change(c, trip)["exception_id"]
    with pytest.raises(PlanError, match="isn't a maintenance planner"):
        replan.approve(c, pid, "sam")
    c.rollback()

    p = replan.approve(c, pid, "pat")
    c.commit()
    assert p["state"] == "approved" and p["approved_by"] == "pat" and not p["open"]
    row = c.execute("select * from plan_item where id = %s", (job["id"],)).fetchone()
    assert _when(row["start_at"]) == _when("2025-10-25T12:00") and row["moved_because"] == "20-K-2200B tripped"
    # what a field operator reads on the job, on Coming up and on their field jobs
    [shown] = [i for i in plan.items(c, _when("2025-10-20T00:00"), _when("2025-11-01T00:00")) if i["id"] == job["id"]]
    assert shown["moved_because"] == "20-K-2200B tripped"
    field_jobs(c, "sam")
    # SAP's schedule change waits for this planner, on their own work
    [wb] = p["writebacks"]
    assert wb["record"] == "50000303" and wb["state"] == "awaiting_approval" and wb["approver_role"] == "maintenance_planner"
    w = c.execute("select payload from writeback where id = %s", (wb["id"],)).fetchone()["payload"]
    assert w["action"] == "reschedule" and _when(w["start"]) == _when("2025-10-25T12:00")
    assert c.execute("select assignee from work_item where id = %s", (wb["item_id"],)).fetchone()["assignee"] == "pat"
    # the field operator's notice, edited and sent by a person, goes on the job's case
    [n] = [x for x in p["notices"] if x["to"] == "sam"]
    assert n["audience"] == "field" and n["text"].startswith("20-K-2200B tripped. Your job moved: ")
    with pytest.raises(PlanError, match="named person"):
        replan.send_notice(c, pid, n["n"], "agent:planner")
    c.rollback()
    sent = replan.send_notice(c, pid, n["n"], "pat", "20-K-2200B tripped: your PM moves to Saturday noon.")
    c.commit()
    assert sent["notices"][n["n"]]["sent_by"] == "pat"
    said = c.execute("select data from work_event where item_id = %s and kind = 'commented' order by id desc limit 1",
                     (case["id"],)).fetchone()
    assert said and "Saturday noon" in str(said["data"])
    # the plan change's case is done, and it is not raised again: nothing is held any more
    e = c.execute("select state from exception_record where id = %s", (pid,)).fetchone()
    assert e["state"] != "open"
    replan.watch(c)
    assert c.execute("select state from exception_record where id = %s", (pid,)).fetchone()["state"] != "open"
