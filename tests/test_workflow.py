"""Work engine (#97): the BPMN subset, behaviour against SpiffWorkflow, guardrails, the worker, timers,
messages, publish checks, the API and listing speed."""

import os
import tempfile
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest

from fuzer.agents import worker
from fuzer.core import db
from fuzer.workflow import bpmn, engine

BPMN_DIR = Path(__file__).with_name("bpmn")
PATH_TYPES = {"startEvent", "endEvent", "userTask", "serviceTask", "intermediateCatchEvent", "boundaryEvent",
              "callActivity"}


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


def publish_file(conn, xml: str, by: str = engine.SYSTEM) -> dict[str, int]:
    rows = engine.import_bpmn(conn, xml, by)
    calls = {k for k, p in bpmn.parse(xml).items() if any(n.type == "callActivity" for n in p.nodes.values())}
    for r in sorted(rows, key=lambda r: r["process_key"] in calls):
        engine.publish(conn, r["id"], by)
    return {r["process_key"]: r["id"] for r in rows}


def quiet(conn):
    """Cancel what earlier tests left running, so workers and ticks only see this test's steps."""
    conn.execute("update work_token set state = 'cancelled' where state in ('ready', 'waiting')")
    conn.execute("update work_process set state = 'cancelled' where state = 'running'")
    conn.commit()


def waiting(conn, item_id: int, kinds=("user", "agent", "fix")) -> list[dict]:
    return conn.execute("select * from work_token where item_id = %s and state = 'waiting' and kind = any(%s) order by id",
                        (item_id, list(kinds))).fetchall()


def task(conn, item_id: int, element: str) -> dict:
    return next(t for t in waiting(conn, item_id) if t["element"] == element)


def events(conn, item_id: int, kind: str | None = None) -> list[dict]:
    return conn.execute("select * from work_event where item_id = %s and (%s::text is null or kind = %s) order by id",
                        (item_id, kind, kind)).fetchall()


# --- behaviour against SpiffWorkflow ---------------------------------------------------------------

class Ours:
    def __init__(self, conn, file: str, process_id: str, data: dict):
        self.conn, self.now = conn, engine.utcnow()
        xml = (BPMN_DIR / file).read_text()
        self.nodes = {n.id: n for p in bpmn.parse(xml).values() for n in p.nodes.values()}
        publish_file(conn, xml)
        out = engine.start(conn, process_id, data, "tester", now=self.now)
        self.item, self.process_id, self.key = out["item_id"], out["process_id"], out["correlation_key"]

    def ready(self) -> list[str]:
        return sorted(t["element"] for t in waiting(self.conn, self.item, ("user", "agent")))

    def complete(self, element: str, data: dict):
        engine.complete(self.conn, task(self.conn, self.item, element)["id"], "tester", data, now=self.now)

    def advance(self, by: timedelta):
        self.now += by
        engine.tick(self.conn, now=self.now)

    def send(self, name: str, payload: dict):
        engine.send_message(self.conn, name, self.key, payload, now=self.now)

    def finished(self) -> bool:
        return self.conn.execute("select state from work_process where id = %s", (self.process_id,)).fetchone()["state"] != "running"

    def path(self) -> Counter:
        rows = self.conn.execute("select element from work_token where item_id = %s and state = 'done'", (self.item,))
        return Counter(r["element"] for r in rows if self.nodes[r["element"]].type in PATH_TYPES)

    def data(self) -> dict:
        d = self.conn.execute("select data from work_process where id = %s", (self.process_id,)).fetchone()["data"]
        d.pop("messages", None)
        return d


class Spiff:
    """The same file on SpiffWorkflow. Service tasks wait to be completed, like agent steps; its timers
    read a clock the test moves."""

    def __init__(self, file: str, process_id: str, data: dict, clock: list):
        from SpiffWorkflow.bpmn.parser.BpmnParser import BpmnParser, BpmnValidator
        from SpiffWorkflow.bpmn.parser.TaskParser import TaskParser
        from SpiffWorkflow.bpmn.parser.util import full_tag
        from SpiffWorkflow.bpmn.specs.defaults import UserTask
        from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
        from SpiffWorkflow.util.task import TaskState

        class Parser(BpmnParser):
            OVERRIDE_PARSER_CLASSES = {full_tag("serviceTask"): (TaskParser, UserTask)}

        parser = Parser(validator=BpmnValidator())
        parser.add_bpmn_file(str(BPMN_DIR / file))
        self.nodes = {n.id: n for p in bpmn.parse((BPMN_DIR / file).read_text()).values() for n in p.nodes.values()}
        self.wf = BpmnWorkflow(parser.get_spec(process_id), parser.get_subprocess_specs(process_id))
        self.state, self.clock, self.done = TaskState, clock, []
        for t in self.wf.get_tasks(state=TaskState.READY):
            t.set_data(**data)
        self._engine()

    def _engine(self):
        record = lambda t: self.done.append(t.task_spec.bpmn_id)  # noqa: E731
        self.wf.do_engine_steps(did_complete_task=record)
        self.wf.refresh_waiting_tasks()
        self.wf.do_engine_steps(did_complete_task=record)

    def _ready(self):
        return self.wf.get_tasks(state=self.state.READY, manual=True)

    def ready(self) -> list[str]:
        return sorted(t.task_spec.bpmn_id for t in self._ready())

    def complete(self, element: str, data: dict):
        t = next(t for t in self._ready() if t.task_spec.bpmn_id == element)
        t.set_data(**data)
        t.run()
        self.done.append(element)
        self._engine()

    def advance(self, by: timedelta):
        self.clock[0] += by
        self._engine()

    def send(self, name: str, payload: dict):
        from SpiffWorkflow.bpmn.specs.event_definitions.message import (
            MessageEventDefinition,
        )
        from SpiffWorkflow.bpmn.util import BpmnEvent
        self.wf.catch(BpmnEvent(MessageEventDefinition(name), payload))
        self._engine()

    def finished(self) -> bool:
        return self.wf.is_completed()

    def path(self) -> Counter:
        return Counter(d for d in self.done if d in self.nodes and self.nodes[d].type in PATH_TYPES)

    def data(self) -> dict:
        return dict(self.wf.data)


@pytest.fixture
def spiff_clock():
    from datetime import datetime

    import SpiffWorkflow.bpmn.specs.event_definitions.timer as timer
    offset = [timedelta()]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + offset[0]

    real, timer.datetime = timer.datetime, Clock
    yield offset
    timer.datetime = real


H, M = timedelta(hours=1), timedelta(minutes=1)
SCENARIOS = {
    "exclusive_branch": ("exclusive.bpmn", "t_exclusive", {}, [("do", "assess", {"score": 7, "kind": "trip"}), ("do", "high", {})]),
    "exclusive_default": ("exclusive.bpmn", "t_exclusive", {}, [("do", "assess", {"score": 7, "kind": "alarm"}), ("do", "low", {})]),
    "parallel_join": ("parallel.bpmn", "t_parallel", {}, [("do", "find_similar", {"similar": 3}), ("do", "check_values", {"ok": True}),
                                                         ("do", "spares", {"spares": "in stock"}), ("do", "decide", {})]),
    "loop": ("loop.bpmn", "t_loop", {}, [("do", "draft", {"v": 1}), ("do", "review", {"decision": "revise"}),
                                         ("do", "draft", {"v": 2}), ("do", "review", {"decision": "ok"})]),
    "timer_waits": ("timers.bpmn", "t_timers", {}, [("time", 5 * H), ("time", 2 * H), ("do", "recheck", {})]),
    "timer_boundary_interrupts": ("timers.bpmn", "t_timers", {}, [("time", 7 * H), ("time", 3 * H), ("do", "escalated", {})]),
    "timer_cycle": ("reminders.bpmn", "t_reminders", {}, [("time", 61 * M), ("time", 61 * M), ("time", 61 * M), ("do", "nudge", {}),
                                                       ("do", "approve", {}), ("do", "nudge", {})]),
    "message_catch_and_boundaries": ("messages.bpmn", "t_messages", {"incident_id": "inc-1"},
                                     [("msg", "evidence", {"severity": "high"}), ("msg", "comment", {"note": "a"}),
                                      ("msg", "comment", {"note": "b"}), ("msg", "withdrawn", {}), ("do", "read_comment", {})]),
    "message_minor": ("messages.bpmn", "t_messages", {"incident_id": "inc-2"}, [("msg", "evidence", {"severity": "low"})]),
    "message_done": ("messages.bpmn", "t_messages", {"incident_id": "inc-3"}, [("msg", "evidence", {"severity": "high"}),
                                                                              ("do", "act", {})]),
    "call_activity": ("call_activity.bpmn", "t_main", {}, [("do", "sub_draft", {}), ("do", "sub_review", {"reviewed": True}),
                                                          ("do", "final", {})]),
    "call_activity_other_branch": ("call_activity.bpmn", "t_main", {}, [("do", "sub_draft", {}), ("do", "sub_review", {"reviewed": False})]),
    "terminate": ("terminate.bpmn", "t_terminate", {}, [("do", "close_out", {})]),
    "terminate_after_side": ("terminate.bpmn", "t_terminate", {}, [("do", "side", {}), ("do", "close_out", {})]),
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_behaviour_matches_spiffworkflow(conn, spiff_clock, name):
    file, process_id, data, steps = SCENARIOS[name]
    ours, spiff = Ours(conn, file, process_id, data), Spiff(file, process_id, data, spiff_clock)
    assert ours.ready() == spiff.ready(), "at start"
    for step in steps:
        for e in (ours, spiff):
            match step:
                case ("do", element, form):
                    e.complete(element, form)
                case ("time", by):
                    e.advance(by)
                case ("msg", message, payload):
                    e.send(message, payload)
        assert ours.ready() == spiff.ready(), f"after {step}"
        assert ours.finished() == spiff.finished(), f"after {step}"
    assert ours.finished(), "every scenario runs to the end"
    assert ours.path() == spiff.path()
    assert ours.data() == spiff.data()


def test_known_difference_reminder_branch_outlives_its_task(conn):
    """A branch a non-interrupting boundary event started runs on by itself (BPMN 2.0). SpiffWorkflow
    cancels it when the task ends while the timer still has cycles left (but keeps it once the cycles are
    used up), so this case is kept out of the comparison above."""
    ours = Ours(conn, "reminders.bpmn", "t_reminders", {})
    ours.advance(61 * M)
    ours.complete("approve", {})
    assert ours.ready() == ["nudge"]
    ours.complete("nudge", {})
    assert ours.finished() and ours.path() == Counter(["start", "approve", "end", "nudge", "end_nudge"])


# --- publish checks --------------------------------------------------------------------------------

def _proc(body: str, attrs: str = "") -> str:
    return (f'<bpmn:definitions xmlns:bpmn="{bpmn.BPMN}" xmlns:fuzer="{bpmn.FUZER}" '
            f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" id="d"><bpmn:process id="bad" {attrs}>{body}'
            f'</bpmn:process></bpmn:definitions>')


GOOD = ('<bpmn:startEvent id="s"/><bpmn:userTask id="u" fuzer:role="engineer" fuzer:sla="PT1H"/><bpmn:endEvent id="e"/>'
        '<bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="u"/><bpmn:sequenceFlow id="f2" sourceRef="u" targetRef="e"/>')


@pytest.mark.parametrize("body,element,words", [
    (GOOD.replace("userTask", "scriptTask"), "u", "outside the supported subset"),
    (GOOD.replace('<bpmn:endEvent id="e"/>', '<bpmn:endEvent id="e"><bpmn:errorEventDefinition/></bpmn:endEvent>'), "e",
     "outside the supported subset"),
    (GOOD.replace('fuzer:role="engineer"', 'fuzer:role="wizard"'), "u", "unknown role 'wizard'"),
    (GOOD.replace('<bpmn:userTask id="u" fuzer:role="engineer" fuzer:sla="PT1H"/>',
                  '<bpmn:serviceTask id="u" fuzer:agent="oracle"/>'), "u", "unknown agent 'oracle'"),
    (GOOD.replace(' fuzer:sla="PT1H"', ""), "u", "needs an SLA"),
    (GOOD + '<bpmn:userTask id="orphan" fuzer:role="engineer" fuzer:sla="PT1H"/>', "orphan", "can't be reached"),
    ('<bpmn:laneSet><bpmn:lane id="l" name="Agents" fuzer:agent="troubleshooter"><bpmn:flowNodeRef>u</bpmn:flowNodeRef>'
     '</bpmn:lane></bpmn:laneSet>' + GOOD.replace('fuzer:role="engineer"', 'fuzer:requiresHuman="true"'), "u",
     "requiresHuman task is in agent lane"),
    ('<bpmn:startEvent id="s"/><bpmn:exclusiveGateway id="g"/><bpmn:endEvent id="a"/><bpmn:endEvent id="b"/>'
     '<bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="g"/>'
     '<bpmn:sequenceFlow id="fa" sourceRef="g" targetRef="a"><bpmn:conditionExpression>x &gt; 1</bpmn:conditionExpression></bpmn:sequenceFlow>'
     '<bpmn:sequenceFlow id="fb" sourceRef="g" targetRef="b"/>', "fb", "has no condition"),
    (GOOD.replace('sourceRef="u" targetRef="e"/>', 'sourceRef="u" targetRef="e"><bpmn:conditionExpression>'
                  '__import__("os")</bpmn:conditionExpression></bpmn:sequenceFlow>').replace(
        '<bpmn:sequenceFlow id="f2" sourceRef="u" targetRef="e"><', '<bpmn:sequenceFlow id="f2" sourceRef="u" targetRef="e"><'),
     "f2", "isn't supported"),
])
def test_publish_refuses_naming_the_element(conn, body, element, words):
    problems = bpmn.check(_proc(body))
    assert any(p.element == element and words in p.message for p in problems), problems
    row = engine.import_bpmn(conn, _proc(body), "olga")[0]
    with pytest.raises(engine.CheckFailed) as e:
        engine.publish(conn, row["id"], "olga")
    assert any(p.element == element for p in e.value.problems)
    state = conn.execute("select state, problems from work_definition where id = %s", (row["id"],)).fetchone()
    assert state["state"] == "draft" and state["problems"]
    assert conn.execute("select count(*) as n from work_event where definition_id = %s and kind = 'publish_refused'",
                        (row["id"],)).fetchone()["n"] == 1


def test_good_definition_and_starting_set_pass():
    assert bpmn.check(_proc(GOOD)) == []
    for f in engine.DEFINITIONS_DIR.glob("*.bpmn"):
        assert bpmn.check(f.read_text()) == [], f.name


def test_publishing_needs_workflow_admin_and_keeps_running_versions(conn):
    row = engine.import_bpmn(conn, _proc(GOOD).replace('id="bad"', 'id="versioned"'), "alice")[0]
    assert engine.check_definition(conn, row["id"], "alice")["state"] == "checked"
    with pytest.raises(engine.Forbidden):
        engine.publish(conn, row["id"], "alice")
    engine.publish(conn, row["id"], "olga")
    assert events_for_definition(conn, row["id"], "published")[0]["actor"] == "olga"
    p1 = engine.start(conn, "versioned", {}, "alice")
    v2_xml = _proc(GOOD.replace('id="u"', 'id="u" name="Renamed"')).replace('id="bad"', 'id="versioned"')
    v2 = engine.import_bpmn(conn, v2_xml, "olga")[0]
    engine.publish(conn, v2["id"], "olga")
    p2 = engine.start(conn, "versioned", {}, "alice")
    defs = {r["process_id"]: r["definition_id"] for r in conn.execute(
        "select id as process_id, definition_id from work_process where id = any(%s)", ([p1["process_id"], p2["process_id"]],))}
    assert defs == {p1["process_id"]: row["id"], p2["process_id"]: v2["id"]}
    assert engine.export_bpmn(conn, v2["id"]) == v2_xml
    # moving a running process to the new version is explicit, per process
    engine.migrate(conn, p1["process_id"], v2["id"], "olga")
    assert task(conn, p1["item_id"], "u")["name"] == "Renamed"
    assert events(conn, p1["item_id"], "migrated")


def events_for_definition(conn, definition_id, kind):
    return conn.execute("select * from work_event where definition_id = %s and kind = %s", (definition_id, kind)).fetchall()


# --- guardrails ----------------------------------------------------------------------------------

def test_agent_cannot_complete_requires_human_step(conn):
    publish_file(conn, (BPMN_DIR / "loop.bpmn").read_text())
    out = engine.start(conn, "t_loop", {}, "alice")
    engine.complete(conn, task(conn, out["item_id"], "draft")["id"], "agent:reporter", {"draft": "text"})
    review = task(conn, out["item_id"], "review")
    assert review["requires_human"]
    with pytest.raises(engine.Forbidden):
        engine.complete(conn, review["id"], "agent:reporter", {"decision": "ok"})
    with pytest.raises(engine.Forbidden):
        engine.hand_off(conn, review["id"], "alice", "agent:reporter")
    with pytest.raises(engine.Forbidden):
        engine.claim(conn, review["id"], "agent:reporter")
    from fastapi.testclient import TestClient

    from fuzer.api import app
    client = TestClient(app)
    r = client.post(f"/work/tasks/{review['id']}/complete", json={"by": "agent:reporter", "data": {"decision": "ok"}})
    assert r.status_code == 403 and "needs a person" in r.json()["detail"]
    assert task(conn, out["item_id"], "review")["state"] == "waiting"
    assert client.post(f"/work/tasks/{review['id']}/complete", json={"by": "olga", "data": {"decision": "ok"}}).status_code == 200
    assert engine.item(conn, out["item_id"])["state"] == "done"


def test_claimed_task_belongs_to_its_claimer(conn):
    publish_file(conn, (BPMN_DIR / "exclusive.bpmn").read_text())
    out = engine.start(conn, "t_exclusive", {}, "alice")
    t = task(conn, out["item_id"], "assess")
    engine.claim(conn, t["id"], "alice")
    with pytest.raises(engine.Conflict):
        engine.claim(conn, t["id"], "bob")
    with pytest.raises(engine.Conflict):
        engine.complete(conn, t["id"], "bob", {"score": 1, "kind": "x"})
    assert engine.my_work(conn, "alice")["mine"][0]["task_id"] == t["id"]
    engine.release(conn, t["id"], "alice")
    assert any(r["task_id"] == t["id"] for r in engine.my_work(conn, "alice")["queue"])
    engine.complete(conn, t["id"], "alice", {"score": 1, "kind": "x"})
    with pytest.raises(engine.Conflict):
        engine.complete(conn, t["id"], "alice", {})


def test_bad_condition_becomes_a_fix_task_not_a_silent_stop(conn):
    publish_file(conn, (BPMN_DIR / "exclusive.bpmn").read_text())
    out = engine.start(conn, "t_exclusive", {}, "alice")
    engine.complete(conn, task(conn, out["item_id"], "assess")["id"], "alice", {})  # no score: condition can't run
    fix = waiting(conn, out["item_id"])[0]
    assert fix["kind"] == "fix" and fix["element"] == "gw" and "score" in fix["error"] and fix["role"] == "engineer"
    engine.complete(conn, fix["id"], "alice", {"score": 9, "kind": "leak"})
    assert [t["element"] for t in waiting(conn, out["item_id"])] == ["high"]


# --- worker ------------------------------------------------------------------------------------

def test_worker_crash_resumes_without_duplicating_agent_work(conn):
    quiet(conn)
    publish_file(conn, (BPMN_DIR / "loop.bpmn").read_text())
    out = engine.start(conn, "t_loop", {"report_date": "2025-12-30"}, "alice")
    calls = []

    def reporter(call: worker.AgentCall):
        if not (call.checkpoint or {}).get("gathered"):
            calls.append("gather")
            call.save({"gathered": True})
            if call.attempt == 1:
                raise KeyboardInterrupt  # the worker process is killed here, mid-step
        calls.append("write")
        return {"report": "drafted", "key": call.key}

    now = engine.utcnow()
    with pytest.raises(KeyboardInterrupt):
        worker.run_once(conn, {"reporter": reporter}, "w1", now=now)
    conn.rollback()
    # Another worker can't take it while the lease is live...
    assert worker.run_once(conn, {"reporter": reporter}, "w2", now=now + timedelta(minutes=1)) is None
    # ...and takes it over, from the checkpoint, once it runs out.
    res = worker.run_once(conn, {"reporter": reporter}, "w2", now=now + worker.LEASE + timedelta(seconds=1))
    assert res["process_state"] == "running"
    assert calls == ["gather", "write"]
    assert len(events(conn, out["item_id"], "agent_result")) == 1
    assert [t["element"] for t in waiting(conn, out["item_id"])] == ["review"]
    data = engine.item(conn, out["item_id"])["processes"][0]["data"]
    assert data["report"] == "drafted" and data["key"].startswith("fuzer-work-")
    # The first worker's late result is refused: it no longer holds the step.
    draft = conn.execute("select id from work_token where item_id = %s and element = 'draft'", (out["item_id"],)).fetchone()
    with pytest.raises(engine.Conflict):
        engine.complete(conn, draft["id"], "agent:reporter", {}, worker="w1")


def test_two_workers_never_claim_the_same_step(conn):
    quiet(conn)
    publish_file(conn, (BPMN_DIR / "parallel.bpmn").read_text())
    out = engine.start(conn, "t_parallel", {}, "alice")
    other = db.connect()
    try:
        with conn.transaction():
            conn.execute("select id from work_token where item_id = %s and element = 'find_similar' for update",
                         (out["item_id"],))
            got = worker.claim(other, "w-other", ["troubleshooter", "maintenance"])
            assert got is not None and got["element"] == "spares"  # skipped the locked step
    finally:
        other.close()


def test_failing_agent_retries_then_goes_to_a_person(conn):
    quiet(conn)
    publish_file(conn, (BPMN_DIR / "exclusive.bpmn").read_text())
    out = engine.start(conn, "t_exclusive", {}, "alice")
    engine.complete(conn, task(conn, out["item_id"], "assess")["id"], "alice", {"score": 1, "kind": "x"})

    def broken(call):
        raise RuntimeError("model overloaded")

    now = engine.utcnow()
    for attempt in range(3):
        now += timedelta(hours=1)
        res = worker.run_once(conn, {"troubleshooter": broken}, "w", now=now)
        assert res["task_id"]
    t = task(conn, out["item_id"], "low")
    assert t["kind"] == "user" and t["role"] == "engineer" and "model overloaded" in t["error"]
    assert len(events(conn, out["item_id"], "agent_failed")) == 2
    assert "failed 3 times" in events(conn, out["item_id"], "handed_off")[-1]["data"]["reason"]
    assert worker.run_once(conn, {"troubleshooter": broken}, "w", now=now + timedelta(hours=1)) is None
    engine.complete(conn, t["id"], "alice", {"done_by_hand": True})
    assert engine.item(conn, out["item_id"])["outcome"] == "end_low"


# --- timers and SLAs -----------------------------------------------------------------------------

def test_overdue_task_escalates_and_says_why(conn):
    quiet(conn)
    out = engine.start(conn, "upset", {"incident_id": "inc-esc", "title": "Separator level trip"}, "sre")
    triage = task(conn, out["item_id"], "triage")
    assert triage["role"] == "engineer" and triage["due_at"]
    assert engine.tick(conn, now=triage["due_at"] - timedelta(minutes=1))["escalated"] == 0
    assert engine.tick(conn, now=triage["due_at"] + timedelta(minutes=1))["escalated"] >= 1
    t = task(conn, out["item_id"], "triage")
    assert t["role"] == "ops_lead" and t["escalated_at"]
    why = events(conn, out["item_id"], "escalated")[0]["data"]["why"]
    assert "SLA PT4H on 'Triage' passed" in why and "engineer queue" in why and "ops_lead" in why
    assert any(r["task_id"] == t["id"] for r in engine.my_work(conn, "olga")["queue"])
    assert engine.tick(conn, now=triage["due_at"] + timedelta(hours=2))["escalated"] == 0  # once


# --- messages ------------------------------------------------------------------------------------

def test_messages_reach_the_process_with_their_correlation_key(conn):
    publish_file(conn, (BPMN_DIR / "messages.bpmn").read_text())
    a = engine.start(conn, "t_messages", {"incident_id": "inc-A"}, "sre")
    b = engine.start(conn, "t_messages", {"incident_id": "inc-B"}, "sre")
    sent = engine.send_message(conn, "evidence", "inc-B", {"severity": "high"})
    assert sent["delivered"] == [b["process_id"]]
    assert [t["element"] for t in waiting(conn, b["item_id"])] == ["act"]
    assert waiting(conn, a["item_id"]) == []
    assert engine.send_message(conn, "evidence", "inc-nobody", {"severity": "high"}) == {"delivered": [], "recorded": []}


def test_same_incident_adds_evidence_instead_of_opening_a_new_process(conn):
    first = engine.start_or_correlate(conn, "upset", {"incident_id": "inc-77", "title": "Compressor trip"}, "agent:sre")
    again = engine.start_or_correlate(conn, "upset", {"incident_id": "inc-77", "note": "trip repeated at 14:05"}, "agent:sre")
    assert first["started"] and not again["started"] and again["process_id"] == first["process_id"]
    assert conn.execute("select count(*) as n from work_process where process_key = 'upset' and correlation_key = 'inc-77'"
                        ).fetchone()["n"] == 1
    data = engine.item(conn, first["item_id"])["processes"][0]["data"]
    assert data["messages"][0]["payload"]["note"] == "trip repeated at 14:05"
    assert events(conn, first["item_id"], "message")[0]["actor"] == "agent:sre"


# --- the upset process end to end ----------------------------------------------------------------

def test_upset_runs_detected_to_resolved(conn):
    quiet(conn)
    out = engine.start_or_correlate(conn, "upset", {"incident_id": "inc-e2e", "title": "HP separator level trip",
                                                    "subjects": ["fz-unit-1"], "priority": 125000}, "agent:sre")
    item = out["item_id"]
    engine.complete(conn, task(conn, item, "triage")["id"], "alice", {"triage": "real"})
    troubleshooter = {"troubleshooter": lambda call: {"diagnosis": "LT-1001 drift", "proposed_action": "recalibrate LT-1001"}}
    maintenance = {"maintenance": lambda call: {"notification_draft": {"text": "Recalibrate LT-1001", "priority": 2}}}
    now = engine.utcnow()
    assert worker.run_once(conn, troubleshooter, "w", now=now)
    approve = task(conn, item, "approve")
    with pytest.raises(engine.Forbidden):
        engine.complete(conn, approve["id"], "agent:troubleshooter", {"approval": "approved"})
    engine.complete(conn, approve["id"], "alice", {"approval": "approved", "reason": "matches the trend"})
    assert worker.run_once(conn, maintenance, "w")
    engine.set_person(conn, "pat", ["maintenance_planner"])
    engine.complete(conn, task(conn, item, "raise_notification")["id"], "pat", {"notification": "10004567"})
    assert waiting(conn, item) == []  # waiting 6 h to re-check
    engine.tick(conn, now=engine.utcnow() + timedelta(hours=7))
    engine.complete(conn, task(conn, item, "confirm")["id"], "alice", {"outcome": "resolved"})
    detail = engine.item(conn, item)
    assert detail["state"] == "done" and detail["outcome"] == "Resolved" and detail["priority"] == 125000
    path = [s["element"] for s in detail["processes"][0]["path"]]
    for a, b in [("detected", "triage"), ("triage", "diagnose"), ("diagnose", "approve"), ("approve", "draft_notification"),
                 ("raise_notification", "recheck"), ("recheck", "confirm"), ("confirm", "resolved")]:
        assert path.index(a) < path.index(b)
    kinds = [(e["kind"], e["actor"]) for e in detail["events"]]
    assert ("agent_result", "agent:troubleshooter") in kinds and ("completed", "alice") in kinds
    assert ("completed", "pat") in kinds and ("timer_fired", "system") in kinds and kinds[-1][0] == "state_changed"


# --- a new process through the API, no code changes ----------------------------------------------

NEW_PROCESS = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:fuzer="https://fuzer.dev/schema/bpmn"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" id="d" targetNamespace="https://fuzer.dev/test">
  <bpmn:process id="instrument_fault" isExecutable="true" fuzer:workItemType="instrument_fault" fuzer:correlationKey="tag">
    <bpmn:extensionElements><fuzer:threshold name="auto_close" value="1000" /></bpmn:extensionElements>
    <bpmn:laneSet>
      <bpmn:lane id="l_agent" name="SRE" fuzer:agent="sre"><bpmn:flowNodeRef>start</bpmn:flowNodeRef><bpmn:flowNodeRef>assess</bpmn:flowNodeRef></bpmn:lane>
      <bpmn:lane id="l_eng" name="Engineer" fuzer:role="engineer"><bpmn:flowNodeRef>confirm</bpmn:flowNodeRef><bpmn:flowNodeRef>gw</bpmn:flowNodeRef></bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="start" />
    <bpmn:serviceTask id="assess" name="Assess instrument" />
    <bpmn:exclusiveGateway id="gw" default="f_person" />
    <bpmn:userTask id="confirm" name="Confirm fault" fuzer:form="instrument-fault" fuzer:requiresHuman="true" />
    <bpmn:boundaryEvent id="sla" attachedToRef="confirm" cancelActivity="false"><bpmn:timerEventDefinition><bpmn:timeDuration>P1D</bpmn:timeDuration></bpmn:timerEventDefinition></bpmn:boundaryEvent>
    <bpmn:endEvent id="closed" name="Auto-closed" />
    <bpmn:endEvent id="confirmed" name="Confirmed" />
    <bpmn:endEvent id="reminded" name="Reminder sent" />
    <bpmn:sequenceFlow id="f1" sourceRef="start" targetRef="assess" />
    <bpmn:sequenceFlow id="f2" sourceRef="assess" targetRef="gw" />
    <bpmn:sequenceFlow id="f_auto" sourceRef="gw" targetRef="closed"><bpmn:conditionExpression>value_at_risk &lt; thresholds["auto_close"]</bpmn:conditionExpression></bpmn:sequenceFlow>
    <bpmn:sequenceFlow id="f_person" sourceRef="gw" targetRef="confirm" />
    <bpmn:sequenceFlow id="f3" sourceRef="confirm" targetRef="confirmed" />
    <bpmn:sequenceFlow id="f4" sourceRef="sla" targetRef="reminded" />
  </bpmn:process>
</bpmn:definitions>"""


def test_new_process_published_and_run_through_the_api(conn):
    quiet(conn)
    from fastapi.testclient import TestClient

    from fuzer.api import app
    client = TestClient(app)
    rows = client.post("/work/definitions", json={"xml": NEW_PROCESS, "by": "olga"}).json()
    did = rows[0]["id"]
    assert client.post(f"/work/definitions/{did}/check", json={"by": "olga"}).json() == {"id": did, "state": "checked", "problems": []}
    assert client.post(f"/work/definitions/{did}/publish", json={"by": "alice"}).status_code == 403
    assert client.post(f"/work/definitions/{did}/publish", json={"by": "olga"}).json()["state"] == "published"
    assert "instrument_fault" in client.get(f"/work/definitions/{did}/bpmn").text

    started = client.post("/work/processes", json={"process_key": "instrument_fault", "by": "agent:sre", "correlate": True,
                                                    "data": {"tag": "20-PT-1001A", "title": "PT-1001A flatlined"}}).json()
    item = started["item_id"]
    assert started["correlation_key"] == "20-PT-1001A"
    assert worker.run_once(conn, {"sre": lambda call: {"value_at_risk": 50000}}, "w")
    inbox = client.get("/work/inbox", params={"person": "alice"}).json()
    t = next(r for r in inbox["queue"] if r["item_id"] == item)
    assert t["name"] == "Confirm fault" and inbox["counts"]["queue"]["total"] >= 1
    assert client.post(f"/work/tasks/{t['task_id']}/claim", json={"by": "alice"}).status_code == 200
    assert client.post(f"/work/tasks/{t['task_id']}/complete", json={"by": "alice", "data": {"fault": "confirmed"}}).status_code == 200
    detail = client.get(f"/work/items/{item}").json()
    assert detail["state"] == "done" and detail["outcome"] == "Confirmed" and detail["type"] == "instrument_fault"
    assert [e["kind"] for e in detail["events"]][:2] == ["created", "started"]

    # under the threshold it closes itself
    started = client.post("/work/processes", json={"process_key": "instrument_fault", "by": "agent:sre",
                                                    "data": {"tag": "20-TT-1002", "title": "TT-1002 noisy"}}).json()
    assert worker.run_once(conn, {"sre": lambda call: {"value_at_risk": 10}}, "w")
    assert client.get(f"/work/items/{started['item_id']}").json()["outcome"] == "Auto-closed"


def test_items_without_a_process_and_listing(conn):
    from fastapi.testclient import TestClient

    from fuzer.api import app
    client = TestClient(app)
    created = client.post("/work/items", json={"type": "lesson_review", "title": "Review lesson 12", "by": "system",
                                               "role": "engineer", "priority": 5}).json()
    iid = created["id"]
    assert any(r["item_id"] == iid for r in client.get("/work/inbox", params={"person": "alice"}).json()["queue"])
    client.post(f"/work/items/{iid}/hand-off", json={"by": "system", "to": "alice"})
    client.post(f"/work/items/{iid}/comment", json={"by": "alice", "text": "looking"})
    assert client.post(f"/work/items/{iid}/transition", json={"by": "alice", "state": "done"}).json()["state"] == "done"
    kinds = [e["kind"] for e in client.get(f"/work/items/{iid}").json()["events"]]
    assert kinds == ["created", "handed_off", "commented", "state_changed"]
    upset = engine.start(conn, "upset", {"incident_id": "inc-list", "title": "Listed"}, "sre")
    listed = client.get("/work/items", params={"type": "upset", "state": "all"}).json()
    assert listed["total"] >= 1 and all(i["type"] == "upset" for i in listed["items"])
    r = client.post(f"/work/items/{upset['item_id']}/transition", json={"by": "alice", "state": "done"})
    assert r.status_code == 409 and "complete its open step" in r.json()["detail"]


# --- scale ---------------------------------------------------------------------------------------

def test_ten_thousand_open_items_list_and_filter_fast(conn):
    now = engine.utcnow()
    roles = ["engineer", "ops_lead", "maintenance_planner", "production_engineer"]
    rows = [("upset" if i % 3 else "opportunity", f"item {i}", [f"fz-{i % 500}"], "open", float(i % 997),
             f"person{i % 40}" if i % 4 == 0 else None, roles[i % 4], now + timedelta(hours=(i % 200) - 50), "bench")
            for i in range(10_000)]
    db.copy_rows(conn, "work_item", ["type", "title", "subjects", "state", "priority", "assignee", "role", "due", "created_by"], rows)
    conn.execute("analyze work_item")
    conn.commit()
    queries = [dict(), dict(type="upset"), dict(roles=["engineer"]), dict(assignee="person8"), dict(overdue=True),
               dict(subject="fz-42"), dict(q="item 99"), dict(type="opportunity", roles=["ops_lead"], offset=200)]
    for q in queries:
        t0 = time.perf_counter()
        out = engine.list_items(conn, **q)
        took = time.perf_counter() - t0
        assert out["items"] and took < 0.25, (q, took)
    assert engine.list_items(conn)["total"] >= 10_000
    t0 = time.perf_counter()
    engine.my_work(conn, "person8", ["engineer"])
    assert time.perf_counter() - t0 < 0.25
