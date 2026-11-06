"""Work engine (#97) surfaces: task forms, the task view the inbox shows, bulk actions, and the MCP
work tools agents use (same engine paths, same guardrails)."""

import asyncio
import os
import tempfile

import pytest
from mcp import Client

from fuzer.agents import worker
from fuzer.core import db
from fuzer.workflow import engine


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        engine._parsed.clear()
        engine.set_person(c, "alice", ["engineer"])
        engine.load_defaults(c)
        yield c


def mcp(name, **args):
    from fuzer.agents.mcp_server import server

    async def _call():
        async with Client(server, raise_exceptions=True) as client:
            result = await client.call_tool(name, args)
            assert not result.is_error, result.content
            return result.structured_content
    return asyncio.run(_call())


def upset(conn, incident: str) -> int:
    return engine.start_or_correlate(conn, "upset", {"incident_id": incident, "title": f"Upset {incident}",
                                                     "subjects": ["fz-unit-1"]}, "agent:sre")["item_id"]


def step(conn, item_id: int, element: str) -> int:
    return conn.execute("select id from work_token where item_id = %s and element = %s and state = 'waiting'",
                        (item_id, element)).fetchone()["id"]


def test_starting_forms_load_once_and_new_versions_stack(conn):
    keys = {f["key"] for f in engine.forms(conn)}
    assert {"upset-triage", "upset-approve", "upset-notification", "upset-confirm", "daily-report-review"} <= keys
    engine.load_defaults(conn)
    assert engine.form(conn, "upset-triage")["version"] == 1
    schema = dict(engine.form(conn, "upset-triage")["schema"], components=[])
    assert engine.import_form(conn, schema, "olga")["version"] == 2
    engine.load_defaults(conn)  # the file differs from the latest again, so it comes back as version 3
    assert engine.form(conn, "upset-triage")["version"] == 3
    with pytest.raises(engine.WorkError):
        engine.import_form(conn, {"components": []}, "olga")


def test_task_view_has_form_bulk_actions_and_evidence(conn):
    item = upset(conn, "inc-view")
    view = engine.task(conn, step(conn, item, "triage"))
    assert view["task"]["name"] == "Triage" and view["task"]["role"] == "engineer" and view["item"]["id"] == item
    assert view["form"]["key"] == "upset-triage" and "fuzer" not in view["form"]["schema"]
    assert any(c.get("key") == "triage" for c in view["form"]["schema"]["components"])
    assert view["bulk"][0]["label"] == "Close as false alarm"
    assert view["data"]["incident_id"] == "inc-view" and view["evidence"]["subjects"][0]["fuzer_id"] == "fz-unit-1"
    engine.complete(conn, view["task"]["task_id"], "alice", {"triage": "real"})
    assert worker.run_once(conn, {"field-operations-facilities": lambda call: {"proposed_action": "recalibrate LT-1001"}}, "w")
    engine.send_message(conn, "evidence", "inc-view", {"trend": "level still drifting"}, "agent:sre")
    approve = engine.task(conn, step(conn, item, "approve"))
    assert approve["task"]["requires_human"] and approve["bulk"] == []
    assert approve["evidence"]["agents"][0]["data"]["data"] == {"proposed_action": "recalibrate LT-1001"}
    assert approve["evidence"]["messages"][0]["data"]["payload"] == {"trend": "level still drifting"}
    with pytest.raises(engine.NotFound):
        engine.task(conn, 10**9)


def test_bulk_closes_false_alarms_but_not_decisions_that_need_a_person(conn):
    a, b, c = upset(conn, "inc-b1"), upset(conn, "inc-b2"), upset(conn, "inc-b3")
    engine.complete(conn, step(conn, c, "triage"), "alice", {"triage": "real"})
    worker.run_once(conn, {"field-operations-facilities": lambda call: {"proposed_action": "x"}}, "w")
    out = engine.bulk_complete(conn, [step(conn, a, "triage"), step(conn, b, "triage"), step(conn, c, "approve"), 10**9],
                               "alice", "Close as false alarm", "spurious transmitter spike")
    assert len(out["done"]) == 2 and len(out["refused"]) == 2
    assert "needs a decision of its own" in out["refused"][0]["error"]
    for item in (a, b):
        detail = engine.item(conn, item)
        assert detail["state"] == "done" and detail["outcome"] == "False alarm"
        assert detail["processes"][0]["data"]["triage_note"] == "spurious transmitter spike"
    assert engine.item(conn, c)["state"] == "open"
    d = upset(conn, "inc-b4")
    assert engine.bulk_complete(conn, [step(conn, d, "triage")], "alice", "Approve all")["done"] == []


def test_agent_works_its_step_through_mcp(conn):
    item = upset(conn, "inc-mcp")
    engine.complete(conn, step(conn, item, "triage"), "alice", {"triage": "real"})
    diagnose = step(conn, item, "diagnose")
    mine = mcp("my_work", agent="field-operations-facilities")
    assert diagnose in [s["task_id"] for s in mine["steps"]]
    assert "error" in mcp("claim_work", task_id=diagnose, agent="maintenance-integrity")
    claimed = mcp("claim_work", task_id=diagnose, agent="field-operations-facilities")
    assert claimed["task"]["claimed_by"] == "mcp:field-operations-facilities"
    assert worker.claim(conn, "w2", ["field-operations-facilities"]) is None  # a worker can't take it now
    assert "error" in mcp("complete_work", task_id=diagnose, agent="maintenance-integrity", result={"x": 1})
    out = mcp("complete_work", task_id=diagnose, agent="field-operations-facilities",
              result={"diagnosis": "LT-1001 drift", "proposed_action": "recalibrate LT-1001"})
    assert out["process_state"] == "running"
    approve = step(conn, item, "approve")
    refused = mcp("complete_work", task_id=approve, agent="field-operations-facilities", result={"approval": "approved"})
    assert "needs a person" in refused["error"]
    assert "error" in mcp("hand_off", task_id=approve, agent="field-operations-facilities", to="agent:maintenance-integrity", reason="x")
    sent = mcp("send_evidence", agent="sre", evidence={"trend": "level back in band"}, item_id=item)
    assert sent["recorded"] and not sent["delivered"]
    events = [(e["kind"], e["actor"]) for e in engine.item(conn, item)["events"]]
    assert ("agent_result", "agent:field-operations-facilities") in events and ("message", "agent:sre") in events


def test_agent_hands_a_step_to_a_person(conn):
    item = upset(conn, "inc-hand")
    engine.complete(conn, step(conn, item, "triage"), "alice", {"triage": "real"})
    diagnose = step(conn, item, "diagnose")
    out = mcp("hand_off", task_id=diagnose, agent="field-operations-facilities", to="role:engineer",
              reason="no PI data for this unit")
    assert out["to"] == "role:engineer"
    queue = engine.my_work(conn, "alice")["queue"]
    assert diagnose in [t["task_id"] for t in queue]
    triage = step(conn, upset(conn, "inc-hand-2"), "triage")
    mcp("claim_work", task_id=triage, agent="sre")
    assert triage in [t["task_id"] for t in mcp("my_work", agent="sre")["tasks"]]


def test_inbox_api(conn):
    from fastapi.testclient import TestClient

    from fuzer.api import app
    client = TestClient(app)
    item = upset(conn, "inc-api")
    triage = step(conn, item, "triage")
    view = client.get(f"/work/tasks/{triage}").json()
    assert view["form"]["key"] == "upset-triage"
    assert client.get("/work/forms/upset-approve").json()["schema"]["id"] == "upset-approve"
    assert client.get("/work/forms/nope").status_code == 404
    out = client.post("/work/tasks/bulk-complete", json={"by": "alice", "task_ids": [triage],
                                                         "action": "Close as false alarm"}).json()
    assert out == {"done": [triage], "refused": []}
    assert client.get(f"/work/items/{item}").json()["outcome"] == "False alarm"


def test_inbox_rows_say_which_bulk_actions_apply(conn):
    item = upset(conn, "inc-rows")
    rows = {r["task_id"]: r for r in engine.my_work(conn, "alice")["queue"]}
    assert rows[step(conn, item, "triage")]["bulk"] == ["Close as false alarm"]
