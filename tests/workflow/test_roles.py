"""Roles, routing and escalation as data: roles edited and checked, a role's default SLA for steps without one,
auto-assignment (round-robin, least loaded) that skips people who are away, delegation while away, escalation
along the role chain (with a hop limit), case actions, and the statistics and instance lists the pages show."""

import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fuzer.core import db
from fuzer.workflow import bpmn, engine, roles

pytestmark = pytest.mark.sequence

T0 = datetime(2026, 1, 5, 8, tzinfo=UTC)


def proc(key: str, role: str, sla: str | None = None) -> str:
    body = (f'<bpmn:startEvent id="s"/><bpmn:userTask id="u" name="Look" fuzer:role="{role}"'
            f'{f" fuzer:sla={chr(34)}{sla}{chr(34)}" if sla else ""}/><bpmn:endEvent id="e"/>'
            '<bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="u"/><bpmn:sequenceFlow id="f2" sourceRef="u" targetRef="e"/>')
    return (f'<bpmn:definitions xmlns:bpmn="{bpmn.BPMN}" xmlns:fuzer="{bpmn.FUZER}" id="d">'
            f'<bpmn:process id="{key}" name="{key}" isExecutable="true">{body}</bpmn:process></bpmn:definitions>')


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        engine._parsed.clear()
        roles._seen = None
        for name, rs in (("alice", ["engineer"]), ("bob", ["engineer"]), ("olga", ["ops_lead", "workflow_admin"])):
            engine.set_person(c, name, rs)
        yield c


def publish(conn, key, role, sla=None):
    rows = engine.import_bpmn(conn, proc(key, role, sla), "olga")
    engine.publish(conn, rows[0]["id"], "olga")


def task_of(conn, item_id):
    return conn.execute("select * from work_token where item_id = %s and kind = 'user' and state = 'waiting'",
                        (item_id,)).fetchone()


def test_roles_are_data(conn):
    names = {r["name"] for r in roles.listing(conn, T0)}
    assert {"engineer", "ops_lead", "sre"} <= names
    roles.set_role(conn, "integrity_engineer", "olga", kind="person", description="CMLs and IOWs", default_sla="P2D",
                   escalate_to="ops_lead")
    conn.commit()
    engine.set_person(conn, "ivy", ["integrity_engineer"])  # usable straight away
    with pytest.raises(roles.RoleError, match="ISO 8601"):
        roles.set_role(conn, "integrity_engineer", "olga", default_sla="two days")
    with pytest.raises(roles.RoleError, match="person role"):
        roles.set_role(conn, "integrity_engineer", "olga", escalate_to="sre")
    with pytest.raises(roles.RoleError, match="in use"):
        roles.delete_role(conn, "integrity_engineer", "olga")
    ie = next(r for r in roles.listing(conn, T0) if r["name"] == "integrity_engineer")
    assert ie["members"][0]["name"] == "ivy" and ie["default_sla"] == "P2D"


def test_existing_databases_gain_the_domain_agents(conn):
    """Roles are seeded once, so a database from before the domain agents gets them from the work engine's schema.sql, without
    resurrecting anything else; an empty table stays empty so the first seed still runs."""
    statement = re.search(r"insert into work_role .*?on conflict do nothing;",
                          (Path(db.__file__).parents[1] / "workflow" / "schema.sql").read_text(), re.DOTALL).group(0)
    domains = ("production-surveillance", "hse-incident-management")
    conn.execute("delete from work_role where name = any(%s)", (list(domains),))
    conn.execute(statement)
    names = {r["name"] for r in conn.execute("select name from work_role")}
    assert set(domains) <= names and "troubleshooter" in names and "engineer" in names

    conn.execute("savepoint empty")
    conn.execute("delete from work_role")
    conn.execute(statement)
    assert conn.execute("select count(*) as n from work_role").fetchone()["n"] == 0
    conn.execute("rollback to savepoint empty")
    conn.commit()


def test_a_roles_default_sla_and_escalation_chain(conn):
    roles.set_role(conn, "operator", "olga", default_sla="PT2H", escalate_to="engineer")
    roles.set_role(conn, "engineer", "olga", default_sla="PT4H", escalate_to="ops_lead")
    conn.commit()
    publish(conn, "chain", "operator")  # no SLA on the step: the role's PT2H covers it (publish would refuse otherwise)
    item = engine.start(conn, "chain", {}, "olga", now=T0)["item_id"]
    t = task_of(conn, item)
    assert t["due_at"] == T0 + timedelta(hours=2)
    engine.tick(conn, now=T0 + timedelta(hours=3))
    t = task_of(conn, item)
    assert t["role"] == "engineer" and t["due_at"] == T0 + timedelta(hours=7)  # engineer's own SLA from the hop
    engine.tick(conn, now=T0 + timedelta(hours=8))
    t = task_of(conn, item)
    assert t["role"] == "ops_lead"
    hops = conn.execute("select data->>'to' as to_ from work_event where token_id = %s and kind = 'escalated' order by id",
                        (t["id"],)).fetchall()
    assert [h["to_"] for h in hops] == ["role:engineer", "role:ops_lead"]
    engine.tick(conn, now=T0 + timedelta(days=5))  # ops_lead escalates nowhere: it stays, marked escalated
    assert task_of(conn, item)["role"] == "ops_lead"


def test_auto_assignment_and_delegation(conn):
    roles.set_role(conn, "engineer", "olga", assignment="round_robin")
    conn.commit()
    publish(conn, "rr", "engineer", "PT1H")
    got = [task_of(conn, engine.start(conn, "rr", {}, "olga", now=T0)["item_id"])["assignee"] for _ in range(3)]
    assert sorted(set(got)) == ["alice", "bob"] and got[0] != got[1]

    roles.set_away(conn, "bob", "bob", T0 + timedelta(days=7), delegate="alice", note="leave")
    conn.commit()
    got = [task_of(conn, engine.start(conn, "rr", {}, "olga", now=T0)["item_id"])["assignee"] for _ in range(2)]
    assert got == ["alice", "alice"]  # bob is away: auto-assignment skips him
    open_bob = conn.execute("select item_id from work_token where assignee = 'bob' and state = 'waiting' limit 1").fetchone()
    mine = engine.my_work(conn, "alice", now=T0 + timedelta(minutes=5))
    assert mine["covering"] == ["bob"] and any(r["covering_for"] == "bob" for r in mine["mine"]) and open_bob

    t = task_of(conn, engine.start(conn, "rr", {}, "olga", now=T0)["item_id"])
    engine.hand_off(conn, t["id"], "olga", "bob", now=T0)  # handed to bob while he's away: alice gets it
    assert task_of(conn, t["item_id"])["assignee"] == "alice"

    roles.set_role(conn, "engineer", "olga", assignment="least_loaded")
    roles.set_away(conn, "bob", "bob", None)
    conn.commit()
    t = task_of(conn, engine.start(conn, "rr", {}, "olga", now=T0)["item_id"])
    assert t["assignee"] == "bob"  # back, with fewer steps than alice
    roles.set_role(conn, "engineer", "olga", assignment="queue")
    conn.commit()


def test_case_actions_stats_and_instances(conn):
    publish(conn, "case", "engineer", "PT1H")
    item = engine.start(conn, "case", {}, "olga", now=T0)["item_id"]
    engine.set_item(conn, item, "olga", "urgent", priority=0.9, due=T0 + timedelta(hours=10))
    assert task_of(conn, item)["due_at"] == T0 + timedelta(hours=10)
    assert conn.execute("select priority from work_item where id = %s", (item,)).fetchone()["priority"] == 0.9
    engine.reassign_item(conn, item, "olga", "role:ops_lead", "needs a decision")
    t = task_of(conn, item)
    assert t["role"] == "ops_lead" and t["assignee"] is None
    with pytest.raises(engine.WorkError, match="nothing to change"):
        engine.set_item(conn, item, "olga")

    stats = {s["process_key"]: s for s in roles.process_stats(conn, T0 + timedelta(hours=1))}
    assert stats["case"]["running"] == 1 and stats["chain"]["breached"] >= 1
    inst = roles.instances(conn, "case", T0 + timedelta(hours=2))
    assert inst[0]["item_id"] == item and inst[0]["open_steps"][0]["role"] == "ops_lead"
    w = roles.workload(conn, T0)
    assert any(p["name"] == "alice" and p["assigned"] >= 1 for p in w["people"])


def test_away_moves_what_they_hold(conn):
    publish(conn, "held", "engineer", "PT1H")
    item = engine.start(conn, "held", {}, "olga", now=T0)["item_id"]
    t = task_of(conn, item)
    engine.claim(conn, t["id"], "alice", now=T0)
    roles.set_away(conn, "alice", "alice", datetime.now(UTC) + timedelta(days=3), delegate="bob")
    conn.commit()
    assert task_of(conn, item)["assignee"] == "bob"
    roles.set_away(conn, "bob", "bob", datetime.now(UTC) + timedelta(days=3))  # bob away too, no one covers
    conn.commit()
    assert task_of(conn, item)["assignee"] is None  # back in the engineer queue
    roles.set_away(conn, "alice", "alice", None)
    roles.set_away(conn, "bob", "bob", None)
    conn.commit()
