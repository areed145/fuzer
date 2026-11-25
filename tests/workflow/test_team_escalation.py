"""Overdue work stays in its own team's queue, marked escalated (#1346, #1369): no built-in role or shipped process
escalates into another team's queue, and work that escalated there before goes back to its own queue, once, when the
schema is applied. A step a person took stays theirs, and a site that sets a target keeps it."""

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from fuzer.core import db
from fuzer.workflow import engine, roles

pytestmark = pytest.mark.sequence

T0 = datetime(2026, 11, 20, 8, tzinfo=UTC)
TEAMS = ("data_steward", "production_engineer", "engineer", "maintenance_planner", "operator", "hse_specialist")


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        engine._parsed.clear()
        roles._seen = None
        engine.load_defaults(c)
        engine.set_person(c, "casey", ["data_steward"])
        engine.set_person(c, "jordan", ["production_engineer"])
        engine.set_person(c, "riley", ["ops_lead"])
        c.commit()
        yield c


def question(conn, role: str = "data_steward", process: str = "queue_item") -> int:
    n = conn.execute("select count(*) as n from work_item").fetchone()["n"]
    data = {"role": role, "case_type": "curve_to_confirm" if role == "data_steward" else "task",
            "exception_id": n + 1, "task_key": f"k{n + 1}"}
    return engine.start(conn, process, data, "stewardship", title=f"Work for the {role} queue", now=T0)["item_id"]


def step(conn, item_id):
    return conn.execute("select t.*, i.role as item_role, i.assignee as item_assignee from work_token t "
                        "join work_item i on i.id = t.item_id "
                        "where t.item_id = %s and t.kind = 'user' and t.state = 'waiting'", (item_id,)).fetchone()


def apply_schema(conn):
    conn.execute(db.schema_sql())  # what a deploy with this change runs
    conn.commit()


def test_no_built_in_role_escalates_into_another_teams_queue(conn):
    assert {r: roles.role(conn, r)["escalate_to"] for r in TEAMS} == dict.fromkeys(TEAMS)
    item = question(conn)
    assert step(conn, item)["due_at"] == T0 + timedelta(days=1)  # the steward role's P1D
    engine.tick(conn, now=T0 + timedelta(days=2))
    t = step(conn, item)
    assert (t["role"], t["item_role"]) == ("data_steward", "data_steward") and t["escalated_at"] is not None
    task = question(conn, "production_engineer", "task")  # N18: production engineer task 9 escalated to the ops lead
    engine.tick(conn, now=T0 + timedelta(days=3))
    assert step(conn, task)["role"] == "production_engineer" and step(conn, task)["escalated_at"] is not None


def test_a_shipped_process_names_no_step_target_and_an_old_version_follows_the_published_one(conn):
    """The shipped upset, IOW and field check steps once escalated to the ops lead themselves; a case still running on
    such a version escalates as the process is published now."""
    from fuzer.workflow import definitions

    xml = (definitions.DEFINITIONS_DIR / "task.bpmn").read_text()
    assert 'escalateTo="' not in "".join(f.read_text() for f in definitions.DEFINITIONS_DIR.glob("*.bpmn"))
    old = xml.replace('<bpmn:userTask id="work" name="Do the task">',
                      '<bpmn:userTask id="work" name="Do the task" fuzer:escalateTo="ops_lead">')
    engine.publish(conn, engine.import_bpmn(conn, old, "system")[0]["id"], "system")
    running = question(conn, "production_engineer", "task")
    engine.publish(conn, engine.import_bpmn(conn, xml, "system")[0]["id"], "system")
    conn.commit()
    engine.tick(conn, now=T0 + timedelta(days=2))
    assert step(conn, running)["role"] == "production_engineer"


def test_work_escalated_into_another_team_goes_back_once_and_what_a_person_took_stays(conn):
    # as a deploy before #1369 left it: the seeded roles escalating to the ops lead, and work moved there
    conn.execute("update work_role set escalate_to = 'ops_lead', updated_by = 'seed' "
                 "where name in ('data_steward', 'production_engineer')")
    conn.commit()
    left, redated, taken = question(conn), question(conn), question(conn)
    task = question(conn, "production_engineer", "task")
    engine.tick(conn, now=T0 + timedelta(days=2))
    assert {step(conn, i)["role"] for i in (left, redated, taken, task)} == {"ops_lead"}
    # the live miss: the ops lead re-dated one, and it escalated again in place, so its last event was not the move
    engine.set_item(conn, redated, "riley", due=T0 + timedelta(days=2, hours=1), reason="queued behind the work in hand",
                    now=T0 + timedelta(days=2))
    engine.tick(conn, now=T0 + timedelta(days=3))
    assert [e["to"] for e in conn.execute(
        "select data->>'to' as to from work_event where token_id = %s and kind = 'escalated' order by id",
        (step(conn, redated)["id"],))] == ["role:ops_lead", "the ops_lead queue"]
    engine.claim(conn, step(conn, taken)["id"], "riley")  # riley took this one
    conn.commit()

    apply_schema(conn)
    assert roles.role(conn, "data_steward")["escalate_to"] is None
    assert roles.role(conn, "production_engineer")["escalate_to"] is None
    for item, role in ((left, "data_steward"), (redated, "data_steward"), (task, "production_engineer")):
        t = step(conn, item)
        assert (t["role"], t["item_role"], t["assignee"], t["item_assignee"]) == (role, role, None, None), item
        assert t["escalated_at"] is not None  # still marked escalated
    assert (step(conn, taken)["role"], step(conn, taken)["assignee"]) == ("ops_lead", "riley")  # theirs
    said = conn.execute("select data from work_event where token_id = %s and kind = 'handed_off'",
                        (step(conn, left)["id"],)).fetchall()
    assert [d["data"]["to"] for d in said] == ["role:data_steward"]
    assert said[0]["data"]["reason"].startswith("Back to its own queue")
    apply_schema(conn)  # once: a second apply moves nothing and says nothing more
    assert conn.execute("select count(*) as n from work_event where token_id = %s and kind = 'handed_off'",
                        (step(conn, left)["id"],)).fetchone()["n"] == 1
    engine.tick(conn, now=T0 + timedelta(days=6))
    assert step(conn, left)["role"] == "data_steward"


def test_work_a_person_held_goes_back_to_them(conn):
    conn.execute("update work_role set escalate_to = 'ops_lead', updated_by = 'seed' where name = 'production_engineer'")
    conn.commit()
    item = question(conn, "production_engineer", "task")
    engine.hand_off(conn, step(conn, item)["id"], "riley", "jordan", "yours", now=T0)
    engine.tick(conn, now=T0 + timedelta(days=2))
    assert (step(conn, item)["role"], step(conn, item)["assignee"]) == ("ops_lead", None)
    conn.commit()
    apply_schema(conn)
    t = step(conn, item)
    assert (t["role"], t["assignee"]) == ("production_engineer", "jordan")


def test_a_site_that_chose_a_target_keeps_it_and_the_ops_lead_still_leads(conn):
    from fuzer.api.security import _leads, _may_work

    roles.set_role(conn, "data_steward", "olga", escalate_to="ops_lead")
    conn.commit()
    item = question(conn)
    engine.tick(conn, now=T0 + timedelta(days=2))
    apply_schema(conn)
    assert roles.role(conn, "data_steward")["escalate_to"] == "ops_lead"
    assert step(conn, item)["role"] == "ops_lead"  # the site's own target: stays where it sent it
    # leading is not escalation: the IOC lead may still act on any queue's work, though none escalates to it
    assert _may_work(conn, "riley", "operator") and _may_work(conn, "riley", "production_engineer")
    assert _leads(conn, "riley", "ops_lead") and not _may_work(conn, "jordan", "data_steward")
