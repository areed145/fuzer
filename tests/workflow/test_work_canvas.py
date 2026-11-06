"""Canvas proposals use the existing BPMN subset, with no executable model-generated markup."""

from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

from fuzer.agents import workflow
from fuzer.workflow import bpmn
from fuzer.workflow.canvas import Draft, compile_bpmn


def proposal():
    return {
        "steps": [
            {"id": "start", "component": "start", "name": "Start", "x": 80, "y": 160},
            {"id": "anomalies", "component": "anomaly", "name": "Find anomalies", "x": 180, "y": 140,
             "parameters": {"instructions": 'Detect "drifts" < 24h & report evidence'}},
            {"id": "changes", "component": "data", "name": "Inspect changed data", "x": 360, "y": 140},
            {"id": "review", "component": "person", "name": "Review", "x": 540, "y": 140,
             "parameters": {"requiresHuman": "true"}},
            {"id": "end", "component": "end", "name": "End", "x": 740, "y": 160},
        ],
        "connections": [{"source": a, "target": z} for a, z in
                        zip(["start", "anomalies", "changes", "review"], ["anomalies", "changes", "review", "end"])],
    }


def test_detection_draft_is_bpmn_with_layout_and_safe_parameters():
    draft = Draft.model_validate(proposal())
    xml = compile_bpmn(draft, "inspection", 'Inspect <data> & "anomalies"')
    assert bpmn.check(xml) == []
    p = bpmn.parse(xml)["inspection"]
    assert p.name == 'Inspect <data> & "anomalies"'
    assert p.nodes["anomalies"].attrs["instructions"] == 'Detect "drifts" < 24h & report evidence'
    assert p.nodes["anomalies"].agent == p.nodes["changes"].agent == "troubleshooter"
    assert p.nodes["changes"].attrs["component"] == "data"
    assert p.nodes["review"].requires_human
    assert len(p.flows) == 4
    root = ET.fromstring(xml)
    shapes = root.findall(".//{http://www.omg.org/spec/BPMN/20100524/DI}BPMNShape")
    assert len(shapes) == 5
    assert shapes[1][0].get("x") == "180"


@pytest.mark.parametrize("component,parameters", [
    ("timer", {"when": "PT6H"}), ("message", {"message": "evidence"}), ("call", {"process": "child"}),
])
def test_wait_and_call_components(component, parameters):
    data = proposal()
    data["steps"][1].update(component=component, parameters=parameters)
    xml = compile_bpmn(Draft.model_validate(data), "inspection", "Inspect")
    assert bpmn.check(xml, known_processes={"child"}) == []
    node = bpmn.parse(xml)["inspection"].nodes["anomalies"]
    if component == "timer":
        assert node.timer == ("timeDuration", "PT6H")
    elif component == "message":
        assert node.message == "evidence"
    else:
        assert node.called == "child"


def test_decision_connections_and_parallel_round_trip():
    data = proposal()
    data["steps"][1].update(component="decision", parameters={})
    data["steps"][2].update(component="parallel", parameters={})
    data["connections"][1]["condition"] = 'review == "approved"'
    data["connections"].append({"source": "anomalies", "target": "end", "default": True})
    xml = compile_bpmn(Draft.model_validate(data), "inspection", "Inspect")
    assert bpmn.check(xml) == []
    p = bpmn.parse(xml)["inspection"]
    assert p.nodes["anomalies"].default == "flow_4"
    assert p.flows["flow_1"].condition == 'review == "approved"'


@pytest.mark.parametrize("change", ["duplicate", "dangling", "attribute", "default", "condition", "process_id"])
def test_invalid_drafts_are_rejected(change):
    data = proposal()
    if change == "duplicate":
        data["steps"][1]["id"] = "start"
    elif change == "dangling":
        data["connections"][0]["target"] = "missing"
    elif change == "attribute":
        data["steps"][1]["parameters"] = {"script": "execute()"}
    elif change == "default":
        data["connections"][0]["default"] = True
    elif change == "condition":
        data["connections"][0]["condition"] = "True"
    else:
        data["steps"][0]["id"] = "inspection"
    with pytest.raises(ValueError):
        compile_bpmn(Draft.model_validate(data), "inspection", "Inspect")


def test_generated_ids_do_not_collide_with_process_or_step_ids():
    data = proposal()
    data["steps"][1]["id"] = "flow_0"
    data["connections"][0]["target"] = data["connections"][1]["source"] = "flow_0"
    xml = compile_bpmn(Draft.model_validate(data), "definitions", "Inspect")
    ids = [e.get("id") for e in ET.fromstring(xml).iter() if e.get("id")]
    assert len(ids) == len(set(ids))
    assert bpmn.check(xml) == []


def test_arbitrary_connection_order_passes_reference_engine_xml_schema():
    from lxml import etree
    from SpiffWorkflow.bpmn.parser.BpmnParser import BpmnValidator

    data = proposal()
    data["connections"].reverse()
    xml = compile_bpmn(Draft.model_validate(data), "inspection", "Inspect")
    BpmnValidator().validate(etree.fromstring(xml.encode()))


def test_unknown_components_and_extra_properties_are_rejected():
    data = proposal()
    data["steps"][1]["component"] = "script"
    with pytest.raises(ValidationError):
        Draft.model_validate(data)
    data = proposal()
    data["execute"] = True
    with pytest.raises(ValidationError):
        Draft.model_validate(data)


def test_agent_uses_tool_only_output_and_validates_it():
    class FakeMessages:
        def create(self, **kwargs):
            assert kwargs["tool_choice"] == {"type": "auto"}
            assert len(kwargs["tools"]) == 1
            assert kwargs["tools"][0]["strict"] is True
            assert "description" in kwargs["messages"][0]["content"]
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="draft_workflow", input=proposal())])
    draft = workflow.generate("Inspect anomalies and changed data", {}, client=SimpleNamespace(messages=FakeMessages()))
    assert len(draft.steps) == 5
    assert bpmn.check(compile_bpmn(draft, "inspection", "Inspect")) == []


def test_agent_requires_configuration_without_calling_external_services(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(workflow.Unavailable, match="Configure an API key"):
        workflow.generate("Inspect anomalies", {})


def test_detection_worker_passes_parameters_to_specialist(monkeypatch):
    import fuzer.agents
    from fuzer.agents.worker import AgentCall, assistant_agents

    prompts = []
    def ask(prompt):
        prompts.append(prompt)
        return {"answer": "No drift detected", "usage": {}}
    monkeypatch.setattr(fuzer.agents, "ask_sync", ask)
    call = AgentCall(1, "troubleshooter", "inspect", "Inspect", {"id": 1, "type": "inspection",
                    "title": "Review", "subjects": ["unit_1"]}, {"window": "PT24H"}, 1, None,
                     instructions="Use detections for drifts over 24 hours")
    result = assistant_agents()["troubleshooter"](call)
    assert "Use detections for drifts over 24 hours" in prompts[0]
    assert "unit_1" in prompts[0] and "PT24H" in prompts[0]
    assert result["inspect"]["answer"] == "No drift detected"
