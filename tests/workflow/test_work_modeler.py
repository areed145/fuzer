"""Work engine (#97) modeler surfaces: editing drafts, comparing versions, the monitor counts and the roles
a process may name."""

import os
import tempfile
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from fuzer.core import db
from fuzer.workflow import bpmn, engine


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        engine._parsed.clear()
        engine.set_person(c, "alice", ["engineer"])
        engine.set_person(c, "olga", ["ops_lead", "workflow_admin"])
        engine.load_defaults(c)
        yield c


@pytest.fixture(scope="module")
def client(conn):
    from fuzer.api import app
    return TestClient(app)


UPSET = (engine.DEFINITIONS_DIR / "upset.bpmn").read_text()


def test_diff_ignores_layout_and_names_what_changed():
    assert bpmn.diff(UPSET, UPSET, "upset") == []
    moved = UPSET.replace('<dc:Bounds x="', '<dc:Bounds x="1', 1)
    assert moved != UPSET and bpmn.diff(UPSET, moved, "upset") == []
    changed = UPSET.replace('fuzer:sla="PT4H"', 'fuzer:sla="PT2H"').replace(
        'triage == "false_alarm"', 'triage == "noise"')
    rows = {r["element"]: r for r in bpmn.diff(UPSET, changed, "upset")}
    assert rows["triage"]["change"] == "changed"
    assert rows["triage"]["fields"] == [{"field": "fuzer:sla", "old": "PT4H", "new": "PT2H"}]
    assert rows["f_false"]["fields"][0]["field"] == "condition"
    added = {r["element"]: r["change"] for r in bpmn.diff(None, UPSET, "upset")}
    assert added["upset"] == added["triage"] == "added"
    gone = {r["element"]: r["change"] for r in bpmn.diff(UPSET, changed.replace('id="false_alarm"', 'id="no_upset"'), "upset")}
    assert gone["false_alarm"] == "removed" and gone["no_upset"] == "added"


def test_draft_save_compare_and_publish(conn, client):
    published = engine._published(conn, "upset")
    d = client.post("/work/definitions/draft", json={"process_key": "upset", "by": "alice"}).json()
    assert d["version"] == published["version"] + 1 and d["state"] == "draft"
    # the open draft comes back rather than a new one each time
    assert client.post("/work/definitions/draft", json={"process_key": "upset", "by": "olga"}).json()["id"] == d["id"]

    xml = UPSET.replace('fuzer:sla="PT4H"', 'fuzer:sla="PT2H"')
    assert client.put(f"/work/definitions/{d['id']}", json={"xml": xml, "by": "alice"}).json()["state"] == "draft"
    cmp = client.get(f"/work/definitions/{d['id']}/compare").json()
    assert cmp["against"]["id"] == published["id"]
    assert [(c["element"], c["change"]) for c in cmp["changes"]] == [("triage", "changed")]

    # a diagram without the process, or bad XML, is refused naming why; nothing is saved
    r = client.put(f"/work/definitions/{d['id']}", json={"xml": xml.replace('id="upset"', 'id="upset2"'), "by": "alice"})
    assert r.status_code == 422 and "no process 'upset'" in r.json()["detail"]
    assert client.put(f"/work/definitions/{d['id']}", json={"xml": "<nope", "by": "alice"}).status_code == 422
    assert engine.export_bpmn(conn, d["id"]) == xml

    assert client.post(f"/work/definitions/{d['id']}/check", json={"by": "alice"}).json()["state"] == "checked"
    # saving again sends it back to draft until checked
    client.put(f"/work/definitions/{d['id']}", json={"xml": xml, "by": "alice"})
    assert engine.definitions(conn, "upset")[0]["state"] == "draft"
    assert client.post(f"/work/definitions/{d['id']}/publish", json={"by": "alice"}).status_code == 403
    assert client.post(f"/work/definitions/{d['id']}/publish", json={"by": "olga"}).json()["state"] == "published"

    # a published version never changes; the published one compares against the version it replaced
    r = client.put(f"/work/definitions/{d['id']}", json={"xml": UPSET, "by": "olga"})
    assert r.status_code == 409
    cmp = client.get(f"/work/definitions/{d['id']}/compare").json()
    assert cmp["against"] == {"id": published["id"], "version": published["version"], "state": "superseded"}
    kinds = [e["kind"] for e in conn.execute("select kind from work_event where definition_id = %s order by id",
                                            (d["id"],))]
    assert kinds[:2] == ["draft_started", "draft_saved"] and "published" in kinds

    # with nothing open, a new draft copies the published version (or the one asked for)
    again = engine.draft(conn, "upset", "olga", from_id=published["id"])
    assert engine.export_bpmn(conn, again["id"]) == UPSET
    with pytest.raises(engine.WorkError):
        engine.draft(conn, "daily_report", "olga", from_id=published["id"])


def test_monitor_counts_steps_waiting_and_done(conn, client):
    conn.execute("update work_token set state = 'cancelled' where state in ('ready', 'waiting')")
    conn.execute("update work_process set state = 'cancelled' where state = 'running'")
    conn.commit()
    now = engine.utcnow()
    items = [engine.start(conn, "upset", {"incident_id": f"inc-m{i}"}, "agent:sre", now=now - timedelta(hours=6))
             for i in range(3)]
    t = conn.execute("select id from work_token where item_id = %s and element = 'triage' and state = 'waiting'",
                     (items[0]["item_id"],)).fetchone()["id"]
    engine.complete(conn, t, "alice", {"triage": "false_alarm"}, now=now)

    m = client.get("/work/monitor/upset").json()
    triage = m["elements"]["triage"]
    assert triage["waiting"] == 2 and triage["overdue"] == 2  # PT4H SLA, started 6 h ago
    assert triage["done"] == 1 and round(triage["median_seconds"]) == 6 * 3600
    assert "false_alarm" not in m["elements"] and "is_real" not in m["elements"]  # passed straight through
    states = {(p["state"], p["outcome"]): p["n"] for p in m["processes"]}
    assert states[("running", None)] == 2 and states[("completed", "False alarm")] == 1
    # boundary timers watching a step aren't work waiting
    assert all(e["waiting"] == 0 for k, e in m["elements"].items() if k not in ("triage",))
    v = engine._published(conn, "upset")["id"]
    assert client.get("/work/monitor/upset", params={"definition_id": v}).json()["elements"]["triage"]["waiting"] == 2


def test_roles(client):
    r = client.get("/work/roles").json()
    assert "engineer" in r["person"] and "troubleshooter" in r["agent"]


def test_agent_proposal_is_unsaved_then_uses_existing_lifecycle(conn, client, monkeypatch):
    from fuzer.agents import workflow, worker
    from fuzer.workflow.canvas import Draft

    proposal = Draft.model_validate({
        "steps": [{"id": "start", "component": "start", "name": "Start", "x": 80},
                  {"id": "inspect", "component": "anomaly", "name": "Inspect", "x": 180,
                   "parameters": {"instructions": "Inspect drifts over the last 24 hours"}},
                  {"id": "end", "component": "end", "name": "Done", "x": 380}],
        "connections": [{"source": "start", "target": "inspect"}, {"source": "inspect", "target": "end"}],
    })
    d = engine.import_bpmn(conn, UPSET.replace('id="upset"', 'id="canvas_test"'), "alice")[0]
    before = engine.export_bpmn(conn, d["id"])
    def generate(description, context):
        assert description == "Inspect drifts"
        assert context["process_key"] == "canvas_test"
        assert "troubleshooter" in context["roles"]["agent"]
        return proposal
    monkeypatch.setattr(workflow, "generate", generate)
    url = f"/work/definitions/{d['id']}"
    events = conn.execute("select count(*) as n from work_event").fetchone()["n"]
    r = client.post(url + "/generate", json={"description": "Inspect drifts", "by": "alice"})
    assert r.status_code == 200
    assert r.json()["steps"] == 3 and r.json()["problems"] == []
    assert engine.export_bpmn(conn, d["id"]) == before
    assert conn.execute("select count(*) as n from work_event").fetchone()["n"] == events
    assert client.post(url + "/generate", json={"description": " ", "by": "alice"}).status_code == 400
    assert client.put(url, json={"xml": r.json()["xml"], "by": "alice"}).status_code == 200
    assert client.post(url + "/check", json={"by": "alice"}).json()["state"] == "checked"
    assert client.post(url + "/publish", json={"by": "olga"}).status_code == 200
    assert client.post(url + "/generate", json={"description": "Inspect", "by": "alice"}).status_code == 409
    item = engine.start(conn, "canvas_test", {}, "alice")
    def inspect(call):
        assert call.instructions == "Inspect drifts over the last 24 hours"
        assert call.item["id"] == item["item_id"]
        return {"findings": "No drift"}
    worker.run_once(conn, {"troubleshooter": inspect})
    assert engine.item(conn, item["item_id"])["processes"][0]["state"] == "completed"
    item = engine.start(conn, "canvas_test", {}, "alice")
    claimed = worker.claim(conn, "migration-worker", ["troubleshooter"])
    newer = engine.import_bpmn(conn, r.json()["xml"].replace("inspect", "recheck"), "alice")[0]
    engine.publish(conn, newer["id"], "olga")
    engine.migrate(conn, item["process_id"], newer["id"], "olga", {"inspect": "recheck"})
    call = worker._call(conn, claimed, "migration-worker", engine.utcnow(), worker.LEASE)
    assert call.element == "recheck" and call.instructions == "Inspect drifts over the last 24 hours"
    engine.complete(conn, claimed["id"], "agent:troubleshooter", {}, worker="migration-worker")


def test_agent_drafting_failures_leave_definition_unchanged(conn, client, monkeypatch):
    from fuzer.agents import workflow

    d = engine.import_bpmn(conn, UPSET.replace('id="upset"', 'id="canvas_errors"'), "alice")[0]
    before = engine.export_bpmn(conn, d["id"])
    url = f"/work/definitions/{d['id']}/generate"
    body = {"description": "Inspect anomalies", "by": "alice"}
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert client.post(url, json=body).status_code == 503
    assert client.post("/work/definitions/9999999/generate", json=body).status_code == 404
    assert client.post(url, json={**body, "description": "x" * 12001}).status_code == 422
    def invalid(*args):
        raise ValueError("bad output")
    monkeypatch.setattr(workflow, "generate", invalid)
    assert client.post(url, json=body).status_code == 422
    assert engine.export_bpmn(conn, d["id"]) == before


@pytest.mark.parametrize(("status", "message"), [
    (400, "supports tool calling"),
    (401, "rejected its credentials"),
    (404, "could not find the configured model or endpoint"),
    (429, "rate limit or quota"),
])
def test_agent_provider_failures_explain_next_steps(conn, client, monkeypatch, status, message):
    from fuzer.agents import workflow
    from fuzer.core import llm

    class ProviderError(Exception):
        status_code = status
        code = "provider_error"
        request_id = "provider-request-id"

    def fail(*args):
        raise ProviderError("provider response details must not reach the user")

    d = engine.import_bpmn(conn, UPSET.replace('id="upset"', f'id="canvas_provider_{status}"'), "alice")[0]
    monkeypatch.setattr(workflow, "generate", fail)
    monkeypatch.setattr(llm, "provider_errors", lambda scope: (ProviderError,))
    response = client.post(f"/work/definitions/{d['id']}/generate",
                           json={"description": "Inspect anomalies", "by": "alice"})

    assert response.status_code == 503
    assert message in response.json()["detail"]
    assert "provider response details" not in response.json()["detail"]
