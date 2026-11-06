"""Structured canvas drafts compiled to the same BPMN the workflow engine runs."""

from typing import Literal
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

from . import bpmn

DIAGRAM = "http://www.omg.org/spec/BPMN/20100524/DI"
DC = "http://www.omg.org/spec/DD/20100524/DC"
DI = "http://www.omg.org/spec/DD/20100524/DI"

COMPONENTS = {
    "start": "startEvent", "end": "endEvent", "person": "userTask", "agent": "serviceTask",
    "anomaly": "serviceTask", "data": "serviceTask", "decision": "exclusiveGateway",
    "parallel": "parallelGateway", "timer": "intermediateCatchEvent",
    "message": "intermediateCatchEvent", "call": "callActivity",
}
ATTRS = {
    "userTask": {"role", "assignee", "requiresHuman", "sla", "escalateTo", "form"},
    "serviceTask": {"agent", "fallbackRole", "instructions"},
}


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=100)
    component: Literal["start", "end", "person", "agent", "anomaly", "data", "decision", "parallel",
                       "timer", "message", "call"]
    name: str = Field(max_length=200)
    parameters: dict[str, str] = Field(default_factory=dict, max_length=12)
    x: int = Field(default=180, ge=0, le=10000)
    y: int = Field(default=180, ge=0, le=10000)


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    condition: str | None = Field(default=None, max_length=2000)
    default: bool = False
    name: str = Field(default="", max_length=200)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[Step] = Field(min_length=2, max_length=80)
    connections: list[Connection] = Field(min_length=1, max_length=160)


def compile_bpmn(draft: Draft, key: str, name: str) -> str:
    """Generate XML with diagram positions; never let generated attributes become XML markup."""
    steps = {s.id: s for s in draft.steps}
    if len(steps) != len(draft.steps):
        raise ValueError("step ids must be unique")
    if key in steps:
        raise ValueError("a step id cannot equal the process key")
    used = set(steps) | {key}

    def unique(base):
        value = base
        i = 2
        while value in used:
            value = f"{base}_{i}"
            i += 1
        used.add(value)
        return value

    def sub(parent, ns, tag, **attrs):
        return ET.SubElement(parent, f"{{{ns}}}{tag}", {k: str(v) for k, v in attrs.items()})

    root = ET.Element(f"{{{bpmn.BPMN}}}definitions", id=unique("definitions"), targetNamespace="https://fuzer.dev/workflow")
    process = sub(root, bpmn.BPMN, "process", id=key, name=name, isExecutable="true")
    process.set(f"{{{bpmn.FUZER}}}workItemType", key)
    diagram = sub(root, DIAGRAM, "BPMNDiagram", id=unique("diagram"))
    plane = sub(diagram, DIAGRAM, "BPMNPlane", id=unique("plane"), bpmnElement=key)
    elements = {}
    bounds = {}
    for step in draft.steps:
        kind = COMPONENTS[step.component]
        params = dict(step.parameters)
        allowed = ATTRS.get(kind, set()) | ({"when"} if step.component == "timer" else
                                          {"message"} if step.component == "message" else
                                          {"process"} if step.component == "call" else set())
        if set(params) - allowed or any(len(v) > 8000 for v in params.values()):
            raise ValueError(f"unsupported parameters on step '{step.id}'")
        if step.component in ("anomaly", "data"):
            params.setdefault("agent", "troubleshooter")
            params.setdefault("instructions", "Use detections to inspect anomalies for the subjects; report evidence."
                              if step.component == "anomaly" else
                              "Use detect_changes to inspect new or changed data for the subjects; report evidence.")
        if kind == "userTask":
            params.setdefault("role", "engineer")
            params.setdefault("sla", "PT4H")
        el = elements[step.id] = sub(process, bpmn.BPMN, kind, id=step.id, name=step.name)
        for attr in ATTRS.get(kind, set()):
            if attr in params:
                el.set(f"{{{bpmn.FUZER}}}{attr}", params[attr])
        if step.component in ("anomaly", "data"):
            el.set(f"{{{bpmn.FUZER}}}component", step.component)
        if step.component == "timer":
            timer = sub(el, bpmn.BPMN, "timerEventDefinition")
            sub(timer, bpmn.BPMN, "timeDuration").text = params.get("when", "PT1H")
        elif step.component == "message":
            msg_id = unique(f"message_{step.id}")
            sub(root, bpmn.BPMN, "message", id=msg_id, name=params.get("message", "evidence"))
            sub(el, bpmn.BPMN, "messageEventDefinition", messageRef=msg_id)
        elif step.component == "call":
            el.set("calledElement", params.get("process", ""))
        w, h = (100, 80) if kind in bpmn.ACTIVITIES else (50, 50) if "Gateway" in kind else (36, 36)
        bounds[step.id] = (step.x, step.y, w, h)
        shape = sub(plane, DIAGRAM, "BPMNShape", id=unique(f"shape_{step.id}"), bpmnElement=step.id)
        sub(shape, DC, "Bounds", x=step.x, y=step.y, width=w, height=h)
    defaults = set()
    for i, conn in enumerate(draft.connections):
        if conn.source not in steps or conn.target not in steps:
            raise ValueError("connections must reference existing steps")
        fid = unique(f"flow_{i}")
        flow = sub(process, bpmn.BPMN, "sequenceFlow", id=fid, sourceRef=conn.source,
                   targetRef=conn.target, name=conn.name)
        if conn.default:
            if steps[conn.source].component != "decision" or conn.source in defaults or conn.condition:
                raise ValueError("a decision can have only one unconditional default connection")
            defaults.add(conn.source)
            elements[conn.source].set("default", fid)
        if conn.condition:
            if steps[conn.source].component != "decision":
                raise ValueError("only decision connections can have conditions")
            sub(flow, bpmn.BPMN, "conditionExpression").text = conn.condition
        sub(elements[conn.source], bpmn.BPMN, "outgoing").text = fid
        sub(elements[conn.target], bpmn.BPMN, "incoming").text = fid
        edge = sub(plane, DIAGRAM, "BPMNEdge", id=unique(f"edge_{i}"), bpmnElement=fid)
        x, y, w, h = bounds[conn.source]
        tx, ty, tw, th = bounds[conn.target]
        sub(edge, DI, "waypoint", x=x + w, y=y + h // 2)
        sub(edge, DI, "waypoint", x=tx, y=ty + th // 2)
    for el in elements.values():
        refs = [child for child in el if child.tag in (f"{{{bpmn.BPMN}}}incoming", f"{{{bpmn.BPMN}}}outgoing")]
        for child in refs:
            el.remove(child)
        el[:] = refs + list(el)
    root.remove(diagram)
    root.append(diagram)
    return ET.tostring(root, encoding="unicode")
