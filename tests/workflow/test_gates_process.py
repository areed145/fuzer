"""Human gates in processes (P5.10b, #621): a confidence gate on an agent step stops it at a person below the
policy's threshold, tighter on a critical asset, and a process that drops a required gate is refused at publish."""

import os
import tempfile

import pytest
from psycopg.types.json import Jsonb

from fuzer.core import db, gates
from fuzer.workflow import bpmn, engine

PROCESS = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:fuzer="https://fuzer.dev/schema/bpmn"
  id="d" targetNamespace="https://fuzer.dev/test">
  <bpmn:process id="false_alarm" isExecutable="true" fuzer:workItemType="false_alarm">
    <bpmn:startEvent id="start" />
    <bpmn:serviceTask id="assess" name="Close as a false alarm?" fuzer:agent="sre" fuzer:gate="auto_close" fuzer:gateRole="engineer" />
    <bpmn:userTask id="review" name="Review" fuzer:role="engineer" fuzer:sla="PT4H" />
    <bpmn:endEvent id="done" />
    <bpmn:sequenceFlow id="f1" sourceRef="start" targetRef="assess" />
    <bpmn:sequenceFlow id="f2" sourceRef="assess" targetRef="review" />
    <bpmn:sequenceFlow id="f3" sourceRef="review" targetRef="done" />
  </bpmn:process>
</bpmn:definitions>"""


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        engine._parsed.clear()
        engine.load_defaults(c)
        engine.set_person(c, "olga", ["workflow_admin", "engineer"])
        c.execute("insert into entity (fuzer_id, kind) values ('fz-pump-a', 'equipment'), ('fz-pump-c', 'equipment')")
        c.execute("insert into golden (fuzer_id, field, value, source, rule) values ('fz-pump-a', 'criticality', %s, "
                  "'sap', 't'), ('fz-pump-c', 'criticality', %s, 'sap', 't')", (Jsonb("A"), Jsonb("C")))
        c.commit()
        row = engine.import_bpmn(c, PROCESS, "olga")[0]
        engine.publish(c, row["id"], "olga")
        yield c


def run(conn, subject: str, result: dict) -> dict:
    started = engine.start(conn, "false_alarm", {"title": f"Alarm on {subject}"}, by="agent:sre",
                           subjects=[subject])
    step = conn.execute("select id from work_token where item_id = %s and element = 'assess'",
                        (started["item_id"],)).fetchone()["id"]
    out = engine.complete(conn, step, "agent:sre", result)
    token = conn.execute("select kind, role, element, state, requires_human from work_token where id = %s",
                         (step,)).fetchone()
    return {"out": out, "token": token, "item": started["item_id"]}


def test_a_confident_result_goes_ahead_and_records_why(conn):
    r = run(conn, "fz-pump-c", {"confidence": 0.97, "proposed": "false_alarm"})
    assert r["token"]["state"] == "done" and not r["out"].get("gated")
    [d] = gates.decisions(conn, r["item"])
    assert (d["confidence"], d["threshold"], d["fired"], d["criticality"]) == (0.97, 0.95, False, "C")


def test_lower_confidence_stops_at_the_gate_role_with_the_uncertainty_in_plain_words(conn):
    r = run(conn, "fz-pump-c", {"confidence": 0.7, "proposed": "false_alarm"})
    assert r["out"]["gated"] and r["out"]["to"] == "role:engineer"
    assert r["token"]["kind"] == "user" and r["token"]["role"] == "engineer" and r["token"]["state"] == "waiting"
    assert r["token"]["requires_human"]
    assert r["out"]["reason"] == ("An automatic close as duplicate or false alarm stopped for a person: its confidence "
                                  "70% is below the 95% needed on a C-criticality asset.")
    ev = conn.execute("select data from work_event where item_id = %s and kind = 'gated'", (r["item"],)).fetchone()
    assert ev["data"]["proposal"]["proposed"] == "false_alarm"
    inbox = engine.my_work(conn, "olga")
    assert any(t["item_id"] == r["item"] for t in inbox["queue"])
    # the person decides: the step needs a person now, so the agent can't finish it
    with pytest.raises(engine.Forbidden):
        engine.complete(conn, conn.execute("select id from work_token where item_id = %s and element = 'assess'",
                                           (r["item"],)).fetchone()["id"], "agent:sre", {"confidence": 1})


def test_a_critical_asset_stops_even_a_confident_result(conn):
    r = run(conn, "fz-pump-a", {"confidence": 0.99, "proposed": "false_alarm"})
    assert r["out"]["gated"] and "A-criticality asset always stops for a person" in r["out"]["reason"]


def test_a_case_on_an_a_and_a_c_asset_is_gated_as_a(conn):
    started = engine.start(conn, "false_alarm", {"title": "Alarm on two pumps"}, by="agent:sre",
                           subjects=["fz-pump-c", "fz-pump-a"])
    step = conn.execute("select id from work_token where item_id = %s and element = 'assess'",
                        (started["item_id"],)).fetchone()["id"]
    out = engine.complete(conn, step, "agent:sre", {"confidence": 0.99, "proposed": "false_alarm"})
    assert out["gated"] and "A-criticality asset always stops for a person" in out["reason"]
    assert gates.decisions(conn, started["item_id"])[0]["criticality"] == "A"
    assert gates.most_critical(["C", None, "A", "B"]) == "A" and gates.most_critical([None]) is None


def test_could_not_check_stops_it(conn):
    r = run(conn, "fz-pump-c", {"confidence": 0.99, "could_not_check": "the historian (no data since 06:00)"})
    assert r["out"]["gated"] and "could not check the historian" in r["out"]["reason"]


def _proc(body: str) -> str:
    return ('<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
            'xmlns:fuzer="https://fuzer.dev/schema/bpmn" id="d"><bpmn:process id="edited">' + body +
            '</bpmn:process></bpmn:definitions>')


FLOW = ('<bpmn:startEvent id="s"/>{step}<bpmn:endEvent id="e"/>'
        '<bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="u"/><bpmn:sequenceFlow id="f2" sourceRef="u" targetRef="e"/>')


@pytest.mark.parametrize("step,words", [
    ('<bpmn:userTask id="u" fuzer:role="ops_lead" fuzer:sla="PT1H" fuzer:dispatch="triage=field_check"/>',
     'a step that dispatches to the field is a gate'),
    ('<bpmn:serviceTask id="u" fuzer:agent="sre" fuzer:gate="field_dispatch"/>', "always needs a person"),
    ('<bpmn:serviceTask id="u" fuzer:agent="sre" fuzer:gate="whatever"/>', "isn't a confidence gate"),
    ('<bpmn:userTask id="u" fuzer:role="ops_lead" fuzer:sla="PT1H" fuzer:gate="auto_close"/>', "isn't a fixed gate"),
])
def test_a_process_edit_cannot_drop_or_bend_a_gate(conn, step, words):
    xml = _proc(FLOW.format(step=step))
    assert any(words in p.message for p in bpmn.check(xml)), bpmn.check(xml)
    row = engine.import_bpmn(conn, xml, "olga")[0]
    with pytest.raises(engine.CheckFailed):
        engine.publish(conn, row["id"], "olga")


def test_a_fixed_gate_step_is_a_persons_only(conn):
    ok = _proc(FLOW.format(step='<bpmn:userTask id="u" fuzer:role="ops_lead" fuzer:sla="PT1H" '
                                 'fuzer:dispatch="triage=field_check" fuzer:gate="field_dispatch"/>'))
    assert bpmn.check(ok) == []
    assert bpmn.parse(ok)["edited"].nodes["u"].fixed_gate == "field_dispatch"
    # the shipped upset process carries its field dispatch gate
    upset = conn.execute("select xml from work_definition where process_key = 'upset' and state = 'published'").fetchone()
    assert 'fuzer:gate="field_dispatch"' in upset["xml"]
