"""The integrated plan (core/plan.py, ops/plan.py) on an empty database with a small plant."""

import os
import tempfile
from datetime import UTC

import pytest
from psycopg.types.json import Jsonb

from fuzer.core import db, events
from fuzer.core.plan import PlanError, propose, update
from fuzer.ops import plan, readiness

T = "2026-11-10T08:00:00+00:00"


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        c.execute("insert into entity (fuzer_id, kind) values ('fz-area-north', 'area'), ('fz-pump-1', 'equipment'), "
                  "('fz-pump-2', 'equipment'), ('fz-wo-1', 'work_order'), ('fz-wo-2', 'work_order'), "
                  "('fz-wo-3', 'work_order')")
        c.execute("insert into entity_label (fuzer_id, label) values ('fz-area-north', 'North'), "
                  "('fz-pump-1', 'P-101') on conflict do nothing")
        for src in ("fz-pump-1", "fz-pump-2"):
            c.execute("insert into edge (src, rel, dst, sources, confidence) values (%s, 'part_of', 'fz-area-north', "
                      "'{t}', 1)", (src,))
        c.execute("insert into entity_key (kind, key, fuzer_id) values ('tag', 'P-101', 'fz-pump-1'), "
                  "('work_order', '4000001', 'fz-wo-1'), ('work_order', '4000002', 'fz-wo-2'), "
                  "('work_order', '4000003', 'fz-wo-3')")
        c.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-wo-1', 'concerns', 'fz-pump-1', "
                  "'{t}', 1), ('fz-wo-2', 'concerns', 'fz-pump-2', '{t}', 1)")
        golden = {"fz-wo-1": {"text": "Replace P-101 seal", "start": "2026-11-12", "finish": "2026-11-13", "status": "REL"},
                  "fz-wo-2": {"text": "North train overhaul", "start": "2026-11-20", "finish": "2026-11-25",
                              "status": "CRTD", "revision": "TA26"},
                  "fz-wo-3": {"text": "Old order", "basic_finish": "2026-11-01", "status": "TECO"}}
        for fid, fields in golden.items():
            for k, v in fields.items():
                c.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, %s, %s, 'sap', 't')",
                          (fid, k, Jsonb(v)))
        c.commit()
        yield c


@pytest.fixture(autouse=True)
def fresh(conn):
    """Each test starts with no open exception and no open bar left by another. The workflow engine commits every
    operation it runs (a dispatch, a role lookup), so a test's `conn.rollback()` does not take back what it raised
    once a case was opened; a later test raising the same type and key then joined that one, with its case and the
    field's reports, and Release sent nothing to its crew."""
    yield
    conn.rollback()
    conn.execute("update plan_item set status = 'cancelled' where status in ('proposed', 'scheduled', 'in_progress') "
                 "and exception_id in (select id from exception_record where state = 'open')")
    conn.execute("update exception_record set state = 'closed', closed_at = now() where state = 'open'")
    conn.commit()


def test_items_are_matched_to_assets_and_their_area(conn):
    p = propose(conn, "inspection", "Inspect P-101", "ann", T, "2026-11-10T12:00:00+00:00", refs=["tag:P-101"])
    assert p["subjects"] == ["fz-pump-1"] and p["area_id"] == "fz-area-north" and p["status"] == "proposed"
    assert any(e["ref"] == f"plan_item:{p['id']}" for e in events.pending(conn, "plan.changed"))
    with pytest.raises(PlanError, match="end before"):
        propose(conn, "inspection", "Backwards", "ann", "2026-11-10", "2026-11-09")
    with pytest.raises(PlanError, match="kind must be"):
        propose(conn, "picnic", "No", "ann", T, T)
    conn.commit()


def test_sap_work_orders_become_plan_items_and_clashes_and_bundles_show(conn):
    out = plan.sync_work_orders(conn)
    assert out["added"] == 2 and out["skipped"] == 0
    again = plan.sync_work_orders(conn)
    assert again["added"] == 0 and again["updated"] == 2  # idempotent by order number
    rows = {r["title"]: r for r in plan.items(conn, "2026-11-01", "2026-12-31")}
    seal, overhaul = rows["Replace P-101 seal"], rows["North train overhaul"]
    assert seal["kind"] == "maintenance" and seal["status"] == "scheduled" and seal["source_key"] == "4000001"
    assert overhaul["kind"] == "turnaround" and overhaul["needs_shutdown"] and overhaul["area_id"] == "fz-area-north"
    assert set(seal["subjects"]) == {"fz-wo-1", "fz-pump-1"} and seal["area_label"] == "North"
    assert any(c["kind"] == "bundle" and c["with"] == overhaul["id"] for c in seal["clashes"])  # 8 days before it
    assert "Old order" not in rows

    clash = propose(conn, "maintenance", "Lube P-101", "bob", "2026-11-12T10:00:00+00:00",
                    "2026-11-12T11:00:00+00:00", refs=["fz-pump-1"], crew="fred")
    update(conn, seal["id"], "ann", crew="fred")
    rows = {r["id"]: r for r in plan.items(conn, "2026-11-01", "2026-12-31")}
    kinds = {c["kind"] for c in rows[clash["id"]]["clashes"]}
    assert {"same_asset", "crew"} <= kinds
    # one sentence after its "clash" badge, the person first by name (tour D16: "clash sam is also on … then")
    said = [c["say"] for c in rows[clash["id"]]["clashes"] if c["kind"] == "crew"]
    assert said == ["Fred is also booked on “Replace P-101 seal” then"], said
    update(conn, clash["id"], "ann", status="cancelled")
    rows = {r["id"]: r for r in plan.items(conn, "2026-11-01", "2026-12-31")}
    assert not [c for c in rows[seal["id"]]["clashes"] if c["kind"] != "bundle"]
    conn.commit()


def test_a_field_operator_sees_what_they_are_planned_onto(conn):
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    soon = propose(conn, "maintenance", "Grease P-101", "ann", now + timedelta(days=2), now + timedelta(days=2, hours=2),
                   refs=["fz-pump-1"], crew="gina", status="scheduled")
    propose(conn, "maintenance", "Maybe later", "ann", now + timedelta(days=3), now + timedelta(days=3, hours=1),
            crew="gina")  # a proposal isn't a commitment yet
    propose(conn, "maintenance", "Next month", "ann", now + timedelta(days=40), now + timedelta(days=41),
            crew="gina", status="scheduled")
    crew = propose(conn, "inspection", "Operator rounds", "ann", now + timedelta(days=1), now + timedelta(days=1, hours=1),
                   crew="role:operator", status="scheduled")
    conn.execute("insert into work_person (name, roles) values ('gina', '{operator}') on conflict (name) do update "
                 "set roles = excluded.roles")
    assert [i["id"] for i in plan.coming_up(conn, "gina")] == [crew["id"], soon["id"]]  # her own and her role's
    assert plan.coming_up(conn, "nobody") == []
    conn.rollback()


def test_a_closed_order_leaves_the_plan_and_people_choices_stick(conn):
    plan.sync_work_orders(conn)  # on the plan already if the sync test ran first; either way, now
    seal = conn.execute("select * from plan_item where source_key = '4000001'").fetchone()
    update(conn, seal["id"], "ann", crew="fred")  # a person's choice
    plan.sync_work_orders(conn)
    assert conn.execute("select crew from plan_item where id = %s", (seal["id"],)).fetchone()["crew"] == "fred"
    conn.execute("update golden set value = '\"TECO\"' where fuzer_id = 'fz-wo-1' and field = 'status'")
    assert plan.sync_work_orders(conn)["closed"] == 1
    assert conn.execute("select status from plan_item where id = %s", (seal["id"],)).fetchone()["status"] == "done"
    conn.rollback()


def test_an_exception_can_be_planned_instead_of_worked_now(conn, monkeypatch):
    from fuzer.core.exceptions import TYPES, ExceptionType, raise_exception

    monkeypatch.setitem(TYPES, "minor_leak", ExceptionType("minor_leak", "Minor leak", joins=False))
    e = raise_exception(conn, "minor_leak", "P-101 drip", "P-101 drip", "olga", refs=["tag:P-101"], value_usd=300)
    item = plan.plan_exception(conn, e["id"], "ann", "2026-11-21", "2026-11-21T04:00:00+00:00")
    assert item["exception_id"] == e["id"] and item["subjects"] == ["fz-pump-1"] and item["status"] == "scheduled"
    assert item["deferment_usd"] == 300
    from fuzer.ops import board, exceptions
    listed = next(x for x in exceptions.listing(conn) if x["id"] == e["id"])
    assert listed["planned"]["id"] == item["id"]  # the IOC sees it is planned, not waiting to be dispatched
    card = next(c for c in board._exception_cards(conn) if c["key"] == f"exception:{e['id']}")
    assert "planned for 2026-11-21" in card["reason"]
    with pytest.raises(PlanError, match="no exception"):
        plan.plan_exception(conn, 99999, "ann", T, T)
    conn.rollback()


def test_maintenance_plans_put_their_next_due_task_on_the_plan_and_roll_forward(conn):
    conn.execute("insert into entity (fuzer_id, kind) values ('fz-mp-1', 'maintenance_plan'), "
                 "('fz-mp-2', 'maintenance_plan') on conflict do nothing")
    conn.execute("insert into entity_key (kind, key, fuzer_id) values ('maintenance_plan', 'MP-7', 'fz-mp-1'), "
                 "('maintenance_plan', 'MP-8', 'fz-mp-2') on conflict do nothing")
    conn.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-mp-1', 'concerns', 'fz-pump-1', "
                 "'{t}', 1) on conflict do nothing")
    for fid, fields in {"fz-mp-1": {"kind": "INSP", "task": "PSV-101 statutory test", "next_due": "2026-11-18",
                                    "interval_days": 365.0, "needs_shutdown": "X"},
                        "fz-mp-2": {"kind": "PM", "task": "No date yet"}}.items():
        for k, v in fields.items():
            conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, %s, %s, 'sap', 't')",
                         (fid, k, Jsonb(v)))
    out = plan.sync_maintenance_plans(conn)
    assert out["added"] == 1 and out["skipped"] == 1
    psv = conn.execute("select * from plan_item where source = 'sap_plan'").fetchone()
    assert psv["kind"] == "inspection" and psv["needs_shutdown"] and psv["status"] == "proposed"
    assert psv["subjects"] == ["fz-pump-1"] and psv["source_key"] == "MP-7:2026-11-18" and "every 365 days" in psv["notes"]
    assert plan.sync_maintenance_plans(conn)["added"] == 0  # idempotent

    conn.execute("update golden set value = '\"2027-11-18\"' where fuzer_id = 'fz-mp-1' and field = 'next_due'")
    out = plan.sync_maintenance_plans(conn)
    assert out["added"] == 1 and out["rolled_forward"] == 1
    assert conn.execute("select status from plan_item where id = %s", (psv["id"],)).fetchone()["status"] == "done"

    # A load and a sync either side of midnight write the same due date a day apart: one item, open, not three closed
    nxt = conn.execute("select id from plan_item where source_key = 'MP-7:2027-11-18'").fetchone()["id"]
    conn.execute("update golden set value = '\"2027-11-19\"' where fuzer_id = 'fz-mp-1' and field = 'next_due'")
    out = plan.sync_maintenance_plans(conn)
    assert out["added"] == 0 and out["rolled_forward"] == 0
    conn.execute("update plan_item set status = 'done' where id = %s", (nxt,))  # an earlier sync closed it as rolled
    propose(conn, "inspection", "PSV-101 statutory test", "sap", "2027-11-17", "2027-11-18", source="sap_plan",
            source_key="MP-7:2027-11-17")  # and wrote a copy a day off
    plan.sync_maintenance_plans(conn)
    rows = conn.execute("select id, status from plan_item where source = 'sap_plan' and start_at > '2027-01-01'").fetchall()
    assert [(r["id"], r["status"]) for r in rows] == [(nxt, "proposed")]
    conn.rollback()


def test_work_starting_soon_shows_the_parts_its_own_order_is_short_of_never_the_bom(conn):
    """Tour E, D3: Plan flagged "parts short" with the vessel's gasket set (a BOM spare at zero stock) on a fogged level
    glass that has no order, while its Spares check read ✓. Parts short is the Spares check's parts: those reserved on
    the job's own work order that stock does not cover."""
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    conn.execute("insert into entity (fuzer_id, kind) values ('fz-mat-seal', 'material'), ('fz-mat-brg', 'material'), "
                 "('fz-mat-oring', 'material'), ('fz-mat-gasket', 'material'), ('fz-wo-reseal', 'work_order'), "
                 "('fz-wo-later', 'work_order') on conflict do nothing")
    conn.execute("insert into entity_label (fuzer_id, label) values ('fz-mat-seal', '10001 seal kit') "
                 "on conflict do nothing")
    # the pump's BOM: a gasket set at zero stock that no order reserves
    conn.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-pump-1', 'uses_part', "
                 "'fz-mat-gasket', '{t}', 1)")
    for m, stock in (("fz-mat-seal", 0), ("fz-mat-brg", 4), ("fz-mat-gasket", 0)):  # o-ring: no stock record, unknown
        conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, 'stock', %s, 'sap', 't')",
                     (m, Jsonb(stock)))
    for wo in ("fz-wo-reseal", "fz-wo-later"):
        for m in ("fz-mat-seal", "fz-mat-brg", "fz-mat-oring"):
            conn.execute("insert into edge (src, rel, dst, sources, confidence, props) values (%s, 'reserves', %s, "
                         "'{sap}', 1, %s)", (wo, m, Jsonb({"quantity": 1, "status": "open"})))
    soon = propose(conn, "maintenance", "Reseal P-101", "sap", now + timedelta(days=5), now + timedelta(days=6),
                   refs=["fz-pump-1"], source="sap", source_key="fz-wo-reseal")
    later = propose(conn, "maintenance", "Reseal P-101 again", "sap", now + timedelta(days=45), now + timedelta(days=46),
                    refs=["fz-pump-1"], source="sap", source_key="fz-wo-later")
    look = propose(conn, "exception", "P-101 sight glass fogged", "ann", now + timedelta(days=1),
                   now + timedelta(days=1, hours=4), refs=["fz-pump-1"])
    rows = {r["id"]: r for r in plan.items(conn, now, now + timedelta(days=60))}
    assert rows[soon["id"]]["parts_short"] == [{"id": "fz-mat-seal", "label": "10001 seal kit", "description": None}]
    assert rows[later["id"]]["parts_short"] == []  # too far out to chase yet
    assert rows[look["id"]]["parts_short"] == []  # no order: no parts, whatever the pump's BOM holds at zero
    # and it agrees with the job's Spares check, the one place its parts are read
    spares = next(p for p in plan.prerequisites(conn, soon["id"])["prerequisites"] if p["kind"] == "spares")
    assert not spares["satisfied"] and spares["detail"][0].startswith("10001 seal kit: 0 in stock, 1 needed")
    conn.rollback()


def test_a_bar_closes_with_its_work_and_taking_one_off_leaves_the_work_open(conn, monkeypatch):
    from fuzer.core.exceptions import TYPES, ExceptionType, raise_exception
    from fuzer.ops import exceptions

    monkeypatch.setitem(TYPES, "minor_leak", ExceptionType("minor_leak", "Minor leak", joins=False))
    cleared = raise_exception(conn, "minor_leak", "P-101 drip", "P-101 drip", "olga", refs=["tag:P-101"])
    bar = plan.plan_exception(conn, cleared["id"], "ann", "2026-11-21", "2026-11-21T04:00:00+00:00")
    exceptions.clear(conn, "minor_leak", "P-101 drip", "olga", reason="tightened")
    assert conn.execute("select status from plan_item where id = %s", (bar["id"],)).fetchone()["status"] == "done"

    # a bar left behind before this rule existed is swept up by the work tick
    other = raise_exception(conn, "minor_leak", "P-102 drip", "P-102 drip", "olga", refs=["tag:P-101"])
    old = plan.plan_exception(conn, other["id"], "ann", "2026-11-22", "2026-11-22T04:00:00+00:00")
    conn.execute("update exception_record set state = 'closed', closed_at = now() where id = %s", (other["id"],))
    assert exceptions.reconcile(conn)["plan_done"] >= 1
    assert conn.execute("select status from plan_item where id = %s", (old["id"],)).fetchone()["status"] == "done"

    # taking a bar off the plan cancels the bar only: the work stays open and is waiting for a decision again
    still = raise_exception(conn, "minor_leak", "P-103 drip", "P-103 drip", "olga", refs=["tag:P-101"])
    off = plan.plan_exception(conn, still["id"], "ann", "2026-11-23", "2026-11-23T04:00:00+00:00")
    update(conn, off["id"], "ann", status="cancelled")
    listed = next(x for x in exceptions.listing(conn) if x["id"] == still["id"])
    assert listed["state"] == "open" and listed["planned"] is None and listed["stage"] == "dispatch"
    conn.rollback()


def test_a_bar_becomes_its_cases_job_and_closes_with_the_case(conn, monkeypatch):
    from fuzer.core import work
    from fuzer.core.exceptions import TYPES, ExceptionType, raise_exception
    from fuzer.ops import exceptions

    monkeypatch.setitem(TYPES, "minor_leak", ExceptionType("minor_leak", "Minor leak", joins=False))
    e = raise_exception(conn, "minor_leak", "P-104 drip", "P-104 drip", "olga", refs=["tag:P-101"])
    bar = plan.plan_exception(conn, e["id"], "ann", "2026-11-24", "2026-11-24T04:00:00+00:00")
    assert bar["item_id"] == e["item_id"] and bar["place"] == "field"  # its case opened when it arrived (step 4)
    sent = exceptions.dispatch(conn, e["id"], "ann", "fred")
    linked = conn.execute("select item_id from plan_item where id = %s", (bar["id"],)).fetchone()["item_id"]
    assert linked == sent["item_id"] == e["item_id"]  # the bar is that case's job, sent or not
    update(conn, bar["id"], "ann", place="desk")
    with pytest.raises(PlanError, match="place must be"):
        update(conn, bar["id"], "ann", place="moon")
    work.close_case(conn, sent["item_id"], "cancelled", "ann", reason="not needed")
    plan.finish_closed_work(conn)
    assert conn.execute("select status from plan_item where id = %s", (bar["id"],)).fetchone()["status"] == "cancelled"
    conn.rollback()


def test_plans_day_lists_only_the_work_still_to_do(conn, monkeypatch):
    """Tour E (E11): the day view listed a closed case's bar and a field job the field had said Done as today's work.
    A Done from the field finishes its bar, a closed case's bar is done, and `active` keeps the rest."""
    from fuzer.core import work
    from fuzer.core.exceptions import TYPES, ExceptionType, raise_exception
    from fuzer.ops import exceptions

    monkeypatch.setitem(TYPES, "minor_leak", ExceptionType("minor_leak", "Minor leak", joins=False))
    day = ("2026-11-26", "2026-11-27")
    went = raise_exception(conn, "minor_leak", "P-106 drip", "P-106 drip", "olga", refs=["tag:P-101"])
    exceptions.dispatch(conn, went["id"], "ann", "role:operator")
    closed = raise_exception(conn, "minor_leak", "P-107 drip", "P-107 drip", "olga", refs=["tag:P-101"])
    shut = plan.plan_exception(conn, closed["id"], "ann", "2026-11-26T08:00:00+00:00", "2026-11-26T12:00:00+00:00")
    still = raise_exception(conn, "minor_leak", "P-108 drip", "P-108 drip", "olga", refs=["tag:P-101"])
    todo = plan.plan_exception(conn, still["id"], "ann", "2026-11-26T09:00:00+00:00", "2026-11-26T13:00:00+00:00")
    bar = conn.execute("select * from plan_item where exception_id = %s", (went["id"],)).fetchone()
    conn.execute("update plan_item set start_at = '2026-11-26T07:00:00+00:00', end_at = '2026-11-26T11:00:00+00:00' "
                 "where id = %s", (bar["id"],))
    exceptions.report(conn, went["id"], "fred", "on_site")
    assert conn.execute("select status from plan_item where id = %s", (bar["id"],)).fetchone()["status"] == "in_progress"
    exceptions.report(conn, went["id"], "fred", "done", "nothing leaking")
    assert conn.execute("select status from plan_item where id = %s", (bar["id"],)).fetchone()["status"] == "done"
    work.close_case(conn, closed["item_id"], "done", "ann", reason="fixed")
    exceptions.reconcile(conn)
    everything = {i["id"] for i in plan.items(conn, *day)}
    assert {bar["id"], shut["id"], todo["id"]} <= everything  # the plan itself keeps its history
    assert {i["id"] for i in plan.items(conn, *day, active=True)} & {bar["id"], shut["id"], todo["id"]} == {todo["id"]}
    # sent to the field again, the bar is work to do again
    exceptions.dispatch(conn, went["id"], "ann", "role:operator")
    assert conn.execute("select status from plan_item where id = %s", (bar["id"],)).fetchone()["status"] == "in_progress"
    conn.rollback()


def _crew(conn, capacity=1):
    conn.execute("insert into entity (fuzer_id, kind) values ('fz-wc-mech', 'work_center') on conflict do nothing")
    conn.execute("insert into entity_label (fuzer_id, label) values ('fz-wc-mech', 'MECH-WC') on conflict do nothing")
    conn.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-wo-1', 'done_by', 'fz-wc-mech', "
                 "'{t}', 1) on conflict do nothing")
    conn.execute("insert into plan_resource (code, name, kind, capacity, work_center, created_by, updated_by) "
                 "values ('MECH', 'Mechanical crew', 'crew', %s, 'MECH-WC', 't', 't') "
                 "on conflict (code) do update set capacity = excluded.capacity", (capacity,))
    conn.execute("delete from plan_demand where resource = 'MECH'")  # what an earlier test left committed


def test_booking_a_crew_is_checked_against_its_capacity(conn):
    from fuzer.core.plan import PlanOverload

    _crew(conn)
    a = propose(conn, "maintenance", "Seal job", "ann", "2026-12-01T08:00:00+00:00", "2026-12-01T16:00:00+00:00")
    b = propose(conn, "maintenance", "Filter job", "ann", "2026-12-01T10:00:00+00:00", "2026-12-01T14:00:00+00:00")
    booked = plan.book(conn, a["id"], "ann", "MECH", 1, person="fred")
    assert booked["status"] == "scheduled" and booked["crew"] == "fred" and not booked["over_capacity"]
    with pytest.raises(PlanOverload, match="Mechanical crew would be over capacity.*Seal job"):
        plan.book(conn, b["id"], "ann", "MECH", 1)
    over = plan.book(conn, b["id"], "ann", "MECH", 1, reason="vendor tech joins the crew")
    assert over["over_capacity"] and "vendor tech joins the crew" in over["notes"]
    conn.rollback()


def test_work_sent_to_a_field_operator_is_on_the_plan_and_holds_no_maintenance_crew(conn, monkeypatch):
    """Tour E, E2: every look sent to a field operator booked one of the fitters (MECH 8 of 6 booked today), so the
    next simple look read not ready and Escalate was recommended. A look is the operator's work: it is a bar booked to
    them and holds none of the crew; a field operator's send is never checked against it."""
    from datetime import datetime, timedelta

    from fuzer.core import clock
    from fuzer.core.exceptions import TYPES, ExceptionType, raise_exception
    from fuzer.ops import exceptions, readiness

    _crew(conn)
    assert readiness.crew(conn, ["fz-pump-1"])["ready"]  # nobody booked on the crew today
    monkeypatch.setitem(TYPES, "minor_leak", ExceptionType("minor_leak", "Minor leak", joins=False))
    e = raise_exception(conn, "minor_leak", "P-105 drip", "P-105 drip", "olga", refs=["tag:P-101"])
    sent = exceptions.dispatch(conn, e["id"], "ann", "role:operator")
    bar = conn.execute("select * from plan_item where exception_id = %s", (e["id"],)).fetchone()
    assert bar["status"] == "in_progress" and bar["item_id"] == sent["item_id"] and bar["start_at"] <= clock.aware(clock.now())
    assert bar["crew"] == "role:operator"
    assert conn.execute("select 1 from plan_demand where item_id = %s", (bar["id"],)).fetchone() is None
    assert readiness.crew(conn, ["fz-pump-1"])["ready"]  # the look holds none of the fitters
    # planned work booked on the crew fills it, and the ready check reads the plan's one ledger
    now = clock.aware(clock.now())
    start = datetime.combine(now.date(), datetime.min.time(), tzinfo=now.tzinfo) + timedelta(hours=1)
    job = propose(conn, "maintenance", "Seal change", "ann", start, start + timedelta(hours=20), refs=["fz-pump-1"])
    plan.book(conn, job["id"], "ann", "MECH", 1)
    check = readiness.crew(conn, ["fz-pump-1"])
    assert not check["ready"] and check["blockers"][0].endswith("1 of 1 booked today")
    assert [j["title"] for j in check["centers"][0]["jobs"]] == ["Seal change"]  # on the crew, not in the blocker
    # a second look sent to a field operator is not held on the full crew, whoever in the field it goes to
    from fuzer.core import work

    monkeypatch.setattr(work, "roles_of", lambda conn, p: ["operator"] if p == "olive" else [])
    second = raise_exception(conn, "minor_leak", "P-106 drip", "P-106 drip", "olga", refs=["tag:P-101"])
    for to in ("role:operator", "olive"):
        ready = readiness.check(conn, ["fz-pump-1"], second["id"], to=to)
        assert "crew" not in [c["check"] for c in ready["checks"]] and not any(b.startswith("Crew") for b in ready["blockers"])
    # sent to someone who is no field operator, the crew is checked
    assert any(b.startswith("Crew: ") for b in readiness.check(conn, ["fz-pump-1"], second["id"], to="fred")["blockers"])
    conn.rollback()


def test_a_job_is_released_to_the_field_when_its_prerequisites_are_met_or_with_a_reason(conn, monkeypatch):
    """Step 5 of the work model: prerequisites decide Ready, and Release sends the job's waiting case to its crew."""
    from fuzer.core.exceptions import TYPES, ExceptionType, raise_exception
    from fuzer.core.plan import PlanNotReady
    from fuzer.ops import exceptions, routing

    monkeypatch.setitem(TYPES, "minor_leak", ExceptionType("minor_leak", "Minor leak", joins=False))
    e = raise_exception(conn, "minor_leak", "P-106 drip", "P-106 drip", "olga", refs=["tag:P-101"])
    monkeypatch.setattr(readiness, "CHECKS", (readiness.permits,))  # the plan's own prerequisites, and one check
    bar = plan.plan_exception(conn, e["id"], "ann", "2026-12-08", "2026-12-08T04:00:00+00:00")
    update(conn, bar["id"], "ann", status="proposed")
    pre = plan.prerequisites(conn, bar["id"])
    assert not pre["ready"] and [p["kind"] for p in pre["prerequisites"] if not p["satisfied"]] == ["scheduled"]
    with pytest.raises(PlanNotReady, match="Not ready: Scheduled: not booked on a crew yet"):
        plan.release(conn, bar["id"], "ann")
    update(conn, bar["id"], "ann", status="scheduled", crew="fred")
    assert plan.prerequisites(conn, bar["id"])["ready"]
    out = plan.release(conn, bar["id"], "ann")
    assert out["released_by"] == "ann" and out["release_reason"] is None and plan.job_stage(out) == "released"
    assert next(i for i in plan.items(conn, "2026-12-01", "2026-12-15") if i["id"] == bar["id"])["stage"] == "released"
    assert out["start_at"].isoformat().startswith("2026-12-08")  # released ahead, its dates and booking stay
    row = exceptions._row(conn, e["id"])
    assert not routing.waiting_case(conn, row["item_id"]) and row["item_id"] == e["item_id"]
    assert conn.execute("select assignee from work_item where id = %s", (row["item_id"],)).fetchone()["assignee"] == "fred"
    with pytest.raises(PlanError, match="was released"):
        plan.release(conn, bar["id"], "ann")
    with pytest.raises(PlanError, match="say why it moves"):
        plan.reschedule(conn, bar["id"], "ann", "2026-12-09T00:00:00+00:00", "2026-12-09T04:00:00+00:00", " ")
    moved = plan.reschedule(conn, bar["id"], "ann", "2026-12-09T00:00:00+00:00", "2026-12-09T04:00:00+00:00",
                            "scaffold not up")
    assert moved["released_at"] is None and moved["start_at"].day == 9
    note = conn.execute("select data->>'text' as t from work_event where item_id = %s and kind = 'commented' "
                        "order by id desc limit 1", (row["item_id"],)).fetchone()["t"]
    assert "Why: scaffold not up" in note and "release it again" in note
    conn.rollback()


def test_a_job_not_ready_goes_only_with_a_reason_kept_on_its_notes(conn, monkeypatch):
    from fuzer.core.plan import PlanNotReady

    monkeypatch.setattr(readiness, "CHECKS", ())

    job = propose(conn, "maintenance", "Valve job", "ann", "2026-12-10T08:00:00+00:00", "2026-12-10T16:00:00+00:00",
                  needs_shutdown=True)
    pre = plan.prerequisites(conn, job["id"])
    assert {p["kind"] for p in pre["prerequisites"] if not p["satisfied"]} == {"scheduled", "unit_down"}
    with pytest.raises(PlanNotReady, match="Unit down"):
        plan.release(conn, job["id"], "ann")
    out = plan.release(conn, job["id"], "ann", reason="the unit is down for the compressor swap anyway")
    assert out["release_reason"].startswith("the unit is down") and out["status"] == "scheduled"
    assert "Why: the unit is down" in conn.execute("select notes from plan_item where id = %s",
                                                   (job["id"],)).fetchone()["notes"]
    desk = propose(conn, "maintenance", "Paperwork", "ann", "2026-12-10T08:00:00+00:00", "2026-12-10T09:00:00+00:00")
    update(conn, desk["id"], "ann", place="desk")
    with pytest.raises(PlanError, match="done at a desk"):
        plan.release(conn, desk["id"], "ann")
    conn.rollback()
