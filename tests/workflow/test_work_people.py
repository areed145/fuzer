"""People and roles (#202): add, change and remove people through the API, the role summary the
Settings page and the Work tab show, and what happens to a removed person's claimed work."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from fuzer.core import db
from fuzer.workflow import engine


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        engine._parsed.clear()
        engine.load_defaults(c)
        yield c


@pytest.fixture(scope="module")
def client(conn):
    from fuzer.api import app

    return TestClient(app)


def upset(conn, incident: str) -> int:
    return engine.start_or_correlate(conn, "upset", {"incident_id": incident, "title": f"Upset {incident}",
                                                     "subjects": ["fz-unit-1"]}, "agent:sre")["item_id"]


def triage(conn, item_id: int) -> int:
    return conn.execute("select id from work_token where item_id = %s and element = 'triage' and state = 'waiting'",
                        (item_id,)).fetchone()["id"]


def role(client, name: str) -> dict:
    return next(r for r in client.get("/work/roles/summary").json() if r["role"] == name)


def test_add_change_and_remove_a_person(client):
    assert client.put("/work/people/bea", json={"roles": ["engineer"], "by": "olga"}).json() == \
        {"name": "bea", "roles": ["engineer"]}
    assert {"name": "bea", "roles": ["engineer"]} in [{k: p[k] for k in ("name", "roles")} for p in client.get("/work/people").json()]
    client.put("/work/people/bea", json={"roles": ["engineer", "ops_lead"], "by": "olga"})
    assert client.get("/work/inbox", params={"person": "bea"}).json()["roles"] == ["engineer", "ops_lead"]
    assert client.put("/work/people/bea", json={"roles": ["wizard"], "by": "olga"}).status_code == 400
    assert client.put("/work/people/%20", json={"roles": [], "by": "olga"}).status_code == 400

    out = client.delete("/work/people/bea", params={"by": "olga"}).json()
    assert out == {"name": "bea", "released_tasks": [], "released_items": []}
    assert all(p["name"] != "bea" for p in client.get("/work/people").json())
    assert client.delete("/work/people/bea", params={"by": "olga"}).status_code == 404


def test_roles_say_which_processes_use_them_and_what_waits(conn, client):
    before = role(client, "engineer")
    assert "upset" in before["processes"]  # the upset's triage step is an engineer's
    assert role(client, "workflow_admin")["processes"] == []
    upset(conn, "inc-roles")
    client.put("/work/people/cy", json={"roles": ["engineer"], "by": "olga"})
    after = role(client, "engineer")
    assert after["waiting"] == before["waiting"] + 1 and "cy" in after["people"]
    assert all(r["role"] in {"engineer", "ops_lead", "production_engineer", "data_steward",
                             "maintenance_planner", "operator", "hse_specialist", "workflow_admin"} for r in client.get("/work/roles/summary").json())


def test_removing_a_person_puts_their_claimed_work_back_in_its_queue(conn, client):
    client.put("/work/people/dee", json={"roles": ["engineer"], "by": "olga"})
    task = triage(conn, upset(conn, "inc-removed"))
    engine.claim(conn, task, "dee")
    item = engine.create_item(conn, "action", "Check the PSV", "olga", assignee="dee", role="engineer")["id"]
    waiting = role(client, "engineer")["waiting"]
    assert {r["task_id"] for r in engine.my_work(conn, "dee")["mine"]} >= {task}

    out = client.delete("/work/people/dee", params={"by": "olga"}).json()
    assert out["released_tasks"] == [task] and out["released_items"] == [item]
    t = conn.execute("select assignee, state from work_token where id = %s", (task,)).fetchone()
    assert t == {"assignee": None, "state": "waiting"}  # still open, back with the role
    assert role(client, "engineer")["waiting"] == waiting + 2
    queue = engine.my_work(conn, "someone", ["engineer"])["queue"]
    assert task in {r["task_id"] for r in queue} and item in {r["item_id"] for r in queue}
    kinds = [r["kind"] for r in conn.execute("select kind from work_event where actor = 'olga' order by id desc limit 3")]
    assert kinds[0] == "person_removed" and "released" in kinds


def test_roles_routing_and_cases_api(client):
    """The Teams & routing and Cases pages' API: roles catalog and edits, away with a delegate, workload,
    process statistics and instances, and case actions."""
    cat = client.get("/work/roles/catalog").json()
    eng = next(r for r in cat if r["name"] == "engineer")
    assert eng["kind"] == "person" and "members" in eng and "uncovered" in eng
    r = client.put("/work/roles/hse_advisor", json={"by": "olga", "kind": "person", "description": "HSE",
                                                    "default_sla": "PT12H", "escalate_to": "ops_lead"})
    assert r.status_code == 200 and r.json()["default_sla"] == "PT12H"
    assert client.put("/work/roles/hse_advisor", json={"by": "olga", "default_sla": "soon"}).status_code == 400
    assert client.delete("/work/roles/hse_advisor", params={"by": "olga"}).status_code == 200
    client.put("/work/people/zed", json={"roles": ["engineer"], "by": "olga"})
    client.put("/work/people/yan", json={"roles": ["engineer"], "by": "olga"})
    away = client.put("/work/people/zed/away", json={"by": "zed", "until": "2099-01-01T00:00:00Z", "delegate": "yan"})
    assert away.status_code == 200 and away.json()["delegate"] == "yan"
    w = client.get("/work/workload").json()
    assert any(p["name"] == "zed" and p["away"] for p in w["people"]) and "agents" in w
    stats = client.get("/work/stats").json()
    assert stats and {"running", "median_h", "sla_breach_pct"} <= set(stats[0])
    key = stats[0]["process_key"]
    assert isinstance(client.get("/work/instances", params={"process_key": key, "state": "all"}).json(), list)


def test_source_problems_reach_a_data_steward(conn, client):
    """`source_issue` cases route to role:data_steward (sources/alerts.py), so it is a person role someone can hold,
    and a database whose roles were seeded before it existed gets it too (schema.sql)."""
    assert client.put("/work/people/casey", json={"roles": ["data_steward"], "by": "olga"}).status_code == 200
    assert "casey" in role(client, "data_steward")["people"]
    conn.execute("delete from work_role where name = 'data_steward'")
    conn.execute(db.schema_sql())  # what a deploy with this schema runs on a database seeded before it
    row = conn.execute("select kind, escalate_to from work_role where name = 'data_steward'").fetchone()
    conn.commit()
    assert row == {"kind": "person", "escalate_to": "ops_lead"}
