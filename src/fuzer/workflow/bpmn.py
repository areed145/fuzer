"""BPMN 2.0 for the work engine: parse the supported subset and check a definition before it is published.

The subset (#97): start and end events (a terminate end too), `userTask` (a person, by lane role) and
`serviceTask` with `fuzer:agent` (an agent), exclusive and parallel gateways, timer and message
intermediate catch events, timer and message boundary events (interrupting or not) and call activities.
Anything else is refused at publish with the element named.

Fuzer properties are attributes in the `fuzer:` namespace:

- process: `fuzer:workItemType`, `fuzer:correlationKey` (an expression over the start data, e.g.
  `incident_id`), `fuzer:ownerRole` (who gets a step that fails), `fuzer:closeWithCase="true"` (closing the case
  ends the process, as a plain case closes: the one-step standard processes); thresholds (#100) as
  `<fuzer:threshold name=".." value=".."/>` in extensionElements, read in conditions as `thresholds["..."]`.
- lane: `fuzer:role` (a person role) or `fuzer:agent` (an agent role); the lane name is used otherwise. A role
  starting with `=` is an expression over the process data, read when the step starts (`=role`: the queue the
  work's type names, as the standard queue_item process does, #509).
- userTask: `fuzer:role`, `fuzer:assignee`, `fuzer:requiresHuman`, `fuzer:sla` (ISO 8601 duration),
  `fuzer:escalateTo` (the role that gets it once the SLA passes), `fuzer:form` (form-js form key),
  `fuzer:dispatch` (`triage=field_check`: the form data that completes it when the IOC dispatches the case to
  someone outside its role, so the process goes to its own field step and that step is handed to them).
- serviceTask: `fuzer:agent`, `fuzer:fallbackRole` (who gets it when the agent keeps failing),
  `fuzer:component` (canvas preset) and `fuzer:instructions` (parameters passed to the agent worker).

Conditions and timer expressions are Python expressions, as in SpiffWorkflow, so the same files run on
both; they are evaluated by a small interpreter that only knows names, literals, comparisons, boolean
and arithmetic operators, subscripts and a few functions. A bare duration (`PT6H`, as typed in the
modeler) is accepted where a quoted one (`"PT6H"`) is expected.
"""

import ast
import operator
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
FUZER = "https://fuzer.dev/schema/bpmn"
NS = {"bpmn": BPMN, "fuzer": FUZER}

PERSON_ROLES = {"engineer", "ops_lead", "production_engineer", "data_steward", "maintenance_planner", "operator",
                "workflow_admin"}
AGENT_ROLES = {"sre", "reporter", "production-surveillance", "wells-subsurface", "field-operations-facilities",
               "maintenance-integrity", "work-management-supply-chain", "hse-incident-management",
               "asset-performance-reporting", "troubleshooter", "production", "maintenance"}
RETIRED_AGENT_ROLES = {"troubleshooter", "production", "maintenance"}  # steps in flight finish; new work can't pick them
ROLE_SLA: dict[str, str] = {}  # a role's default SLA (work_role, loaded by roles.refresh): a step without one uses it

FLOW_NODES = {"startEvent", "endEvent", "userTask", "serviceTask", "exclusiveGateway", "parallelGateway",
              "intermediateCatchEvent", "boundaryEvent", "callActivity"}
ACTIVITIES = {"userTask", "serviceTask", "callActivity"}
# Harmless elements a modeler adds, ignored by the engine.
IGNORED = {"sequenceFlow", "laneSet", "textAnnotation", "association", "documentation", "extensionElements",
           "dataObject", "dataObjectReference", "dataStoreReference", "ioSpecification", "property"}
EVENT_DEFS = {"timerEventDefinition": "timer", "messageEventDefinition": "message",
              "terminateEventDefinition": "terminate"}
ALLOWED_EVENTS = {"startEvent": {None}, "endEvent": {None, "terminate"}, "intermediateCatchEvent": {"timer", "message"},
                  "boundaryEvent": {"timer", "message"}}


class ExpressionError(Exception):
    pass


@dataclass
class Node:
    id: str
    type: str                                   # local BPMN tag: userTask, exclusiveGateway, ...
    name: str | None = None
    incoming: list[str] = field(default_factory=list)
    outgoing: list[str] = field(default_factory=list)
    attrs: dict = field(default_factory=dict)   # fuzer:* attributes, without the prefix
    event: str | None = None                    # timer | message | terminate
    timer: tuple[str, str] | None = None        # (timeDuration | timeDate | timeCycle, expression)
    message: str | None = None                  # message name
    attached_to: str | None = None
    cancel: bool = True                         # boundary: interrupting
    default: str | None = None                  # gateway: default flow id
    called: str | None = None                   # callActivity: process id
    lane: dict | None = None                    # {name, role, agent}
    extra_events: list[str] = field(default_factory=list)  # event definitions outside the subset

    @property
    def label(self) -> str:
        return self.name or self.id

    @property
    def requires_human(self) -> bool:
        return str(self.attrs.get("requiresHuman", "")).lower() in ("true", "1", "yes")

    @property
    def role(self) -> str | None:
        """A person's task: its own role, else its lane's."""
        return self.attrs.get("role") or (self.lane or {}).get("role")

    @property
    def agent(self) -> str | None:
        return self.attrs.get("agent") or (self.lane or {}).get("agent")


@dataclass
class Flow:
    id: str
    source: str
    target: str
    condition: str | None = None
    name: str | None = None


@dataclass
class Process:
    key: str
    name: str | None
    nodes: dict[str, Node]
    flows: dict[str, Flow]
    attrs: dict
    thresholds: dict
    boundaries: dict[str, list[str]]            # activity id -> its boundary event ids
    unsupported: list[tuple[str, str]]          # (element id, tag) outside the subset

    @property
    def start(self) -> Node:
        return next(n for n in self.nodes.values() if n.type == "startEvent")


def _local(tag: str) -> tuple[str, str]:
    ns, _, local = tag[1:].partition("}") if tag.startswith("{") else ("", "", tag)
    return ns, local


def _fuzer_attrs(el) -> dict:
    return {k.split("}", 1)[1]: v for k, v in el.attrib.items() if k.startswith("{" + FUZER + "}")}


def parse(xml: str | bytes) -> dict[str, Process]:
    """Every process in a BPMN file, by process id. Raises ValueError on XML that doesn't parse."""
    try:
        root = ET.fromstring(xml.encode() if isinstance(xml, str) else xml)
    except ET.ParseError as e:
        raise ValueError(f"not valid XML: {e}") from e
    if _local(root.tag) != (BPMN, "definitions"):
        raise ValueError("not a BPMN 2.0 file (no bpmn:definitions root)")
    messages = {m.get("id"): m.get("name") or m.get("id") for m in root.findall("bpmn:message", NS)}
    return {p.get("id"): _process(p, messages) for p in root.findall("bpmn:process", NS)}


def _process(p, messages: dict) -> Process:
    lanes = {}
    for lane in p.iter(f"{{{BPMN}}}lane"):
        a = _fuzer_attrs(lane)
        info = {"name": lane.get("name"), "role": a.get("role"), "agent": a.get("agent")}
        if not info["role"] and not info["agent"] and info["name"]:
            n = info["name"].strip().lower().replace(" ", "_")
            info["agent" if n in AGENT_ROLES else "role"] = n
        for ref in lane.findall("bpmn:flowNodeRef", NS):
            lanes[(ref.text or "").strip()] = info
    nodes, flows, unsupported = {}, {}, []
    for el in p:
        ns, tag = _local(el.tag)
        if ns != BPMN:
            continue
        if tag == "sequenceFlow":
            cond = el.find("bpmn:conditionExpression", NS)
            text = (cond.text or "").strip() if cond is not None else ""
            flows[el.get("id")] = Flow(el.get("id"), el.get("sourceRef"), el.get("targetRef"), text or None, el.get("name"))
            continue
        if tag in IGNORED:
            continue
        if tag not in FLOW_NODES:
            unsupported.append((el.get("id") or tag, tag))
            continue
        n = Node(el.get("id"), tag, el.get("name"), attrs=_fuzer_attrs(el), lane=lanes.get(el.get("id")),
                 attached_to=el.get("attachedToRef"), cancel=el.get("cancelActivity", "true") != "false",
                 default=el.get("default"), called=el.get("calledElement"))
        for child in el:
            cns, ctag = _local(child.tag)
            if cns != BPMN or not ctag.endswith("EventDefinition"):
                continue
            kind = EVENT_DEFS.get(ctag)
            if kind is None or n.event is not None:
                n.extra_events.append(ctag)
                continue
            n.event = kind
            if kind == "timer":
                for t in ("timeDuration", "timeDate", "timeCycle"):
                    if (te := child.find(f"bpmn:{t}", NS)) is not None:
                        n.timer = (t, (te.text or "").strip())
            elif kind == "message":
                n.message = messages.get(child.get("messageRef"), child.get("messageRef"))
        nodes[n.id] = n
    for f in flows.values():
        if f.source in nodes:
            nodes[f.source].outgoing.append(f.id)
        if f.target in nodes:
            nodes[f.target].incoming.append(f.id)
    boundaries: dict[str, list[str]] = {}
    for n in nodes.values():
        if n.type == "boundaryEvent" and n.attached_to:
            boundaries.setdefault(n.attached_to, []).append(n.id)
    thresholds = {}
    for t in p.iter(f"{{{FUZER}}}threshold"):
        v = t.get("value")
        try:
            thresholds[t.get("name")] = float(v)
        except (TypeError, ValueError):
            thresholds[t.get("name")] = v
    return Process(p.get("id"), p.get("name"), nodes, flows, _fuzer_attrs(p), thresholds, boundaries, unsupported)


# --- checks -------------------------------------------------------------------------------------

@dataclass
class Problem:
    element: str | None
    message: str

    def __str__(self):
        return f"{self.element}: {self.message}" if self.element else self.message


def check(xml: str | bytes, known_processes: set[str] = frozenset(), process_key: str | None = None,
          person_roles: set[str] = PERSON_ROLES, agent_roles: set[str] = AGENT_ROLES) -> list[Problem]:
    """What stops a definition being published, each naming its element. `known_processes` are the
    published process ids a call activity may call (processes in the same file count too);
    `process_key` checks just that process of the file."""
    try:
        procs = parse(xml)
    except ValueError as e:
        return [Problem(None, str(e))]
    if not procs:
        return [Problem(None, "the file has no bpmn:process")]
    known = set(known_processes) | set(procs)
    out = []
    for p in procs.values():
        if process_key and p.key != process_key:
            continue
        out += _check_process(p, known, person_roles, agent_roles)
    return out


def _check_process(p: Process, known: set[str], person_roles: set[str], agent_roles: set[str]) -> list[Problem]:
    out = [Problem(eid, f"bpmn:{tag} is outside the supported subset") for eid, tag in p.unsupported]
    starts = [n for n in p.nodes.values() if n.type == "startEvent"]
    if len(starts) != 1:
        out.append(Problem(p.key, f"a process needs exactly one start event (has {len(starts)})"))
    if (owner := p.attrs.get("ownerRole")) and owner not in person_roles:
        out.append(Problem(p.key, f"unknown role '{owner}'"))
    if p.attrs.get("correlationKey"):
        out += _syntax(p.key, p.attrs["correlationKey"])
    for n in p.nodes.values():
        for extra in n.extra_events:
            out.append(Problem(n.id, f"bpmn:{extra} on a {n.type} is outside the supported subset"))
        if not n.extra_events and n.event not in ALLOWED_EVENTS.get(n.type, {None}):
            out.append(Problem(n.id, f"a {n.event or 'plain'} {n.type} is outside the supported subset"))
        if n.lane and n.lane.get("role") and not is_expression(n.lane["role"]) and n.lane["role"] not in person_roles:
            out.append(Problem(n.id, f"unknown role '{n.lane['role']}' on lane '{n.lane['name']}'"))
        if n.type == "userTask":
            out += _check_user_task(p, n, person_roles)
        elif n.type == "serviceTask":
            if not n.agent:
                out.append(Problem(n.id, "a service task needs fuzer:agent (or an agent lane)"))
            elif n.agent not in agent_roles:
                out.append(Problem(n.id, f"unknown agent '{n.agent}'"))
            if n.requires_human:
                out.append(Problem(n.id, "a requiresHuman step can't be given to an agent; make it a user task"))
            if (fb := n.attrs.get("fallbackRole")) and fb not in person_roles:
                out.append(Problem(n.id, f"unknown role '{fb}'"))
        elif n.type == "callActivity":
            if not n.called:
                out.append(Problem(n.id, "a call activity needs calledElement"))
            elif n.called not in known:
                out.append(Problem(n.id, f"calls '{n.called}', which isn't published"))
        elif n.type == "boundaryEvent":
            target = p.nodes.get(n.attached_to)
            if target is None or target.type not in ACTIVITIES:
                out.append(Problem(n.id, "a boundary event must be attached to a task or call activity"))
        if n.event == "timer":
            if not n.timer or not n.timer[1]:
                out.append(Problem(n.id, "a timer needs timeDuration, timeDate or timeCycle"))
            elif not _literal_timer(n.timer[1]):
                out += _syntax(n.id, n.timer[1])
            elif (err := timer_error(*n.timer)):
                out.append(Problem(n.id, err))
        if n.event == "message" and not n.message:
            out.append(Problem(n.id, "a message event needs a message with a name"))
        out += _check_flows(p, n)
    out += [Problem(nid, "can't be reached from the start event") for nid in _unreachable(p)]
    for f in p.flows.values():
        if f.source not in p.nodes or f.target not in p.nodes:
            out.append(Problem(f.id, "sequence flow joins an element that isn't in the process"))
        if f.condition:
            out += _syntax(f.id, f.condition)
    return out


def _check_user_task(p: Process, n: Node, person_roles: set[str]) -> list[Problem]:
    out = []
    lane = n.lane or {}
    if lane.get("agent") and not n.attrs.get("role"):
        why = "a requiresHuman task" if n.requires_human else "a person's task"
        out.append(Problem(n.id, f"{why} is in agent lane '{lane.get('name') or lane['agent']}'"))
    elif not n.role:
        out.append(Problem(n.id, "a user task needs a role (fuzer:role or a lane)"))
    elif is_expression(n.role):
        out += _syntax(n.id, n.role[1:])
    elif n.role not in person_roles:
        out.append(Problem(n.id, f"unknown role '{n.role}'"))
    if (via := n.attrs.get("dispatch")) is not None and not re.fullmatch(r"\s*\w+\s*=\s*[^,=]+(,\s*\w+\s*=\s*[^,=]+)*", via):
        out.append(Problem(n.id, f"dispatch '{via}' isn't key=value pairs (triage=field_check)"))
    if (esc := n.attrs.get("escalateTo")) and esc not in person_roles:
        out.append(Problem(n.id, f"unknown role '{esc}' in escalateTo"))
    sla = n.attrs.get("sla")
    timers = [b for b in p.boundaries.get(n.id, []) if p.nodes[b].event == "timer"]
    if sla:
        if parse_duration(sla) is None:
            out.append(Problem(n.id, f"SLA '{sla}' isn't an ISO 8601 duration"))
    elif not timers and not is_expression(n.role) and not ROLE_SLA.get(n.role or ""):
        out.append(Problem(n.id, "a person's task needs an SLA (fuzer:sla, a timer boundary event, or its role's default)"))
    return out


def _check_flows(p: Process, n: Node) -> list[Problem]:
    out = []
    if n.type != "endEvent" and not n.outgoing:
        out.append(Problem(n.id, "has no outgoing flow"))
    if n.type not in ("startEvent", "boundaryEvent") and not n.incoming:
        out.append(Problem(n.id, "has no incoming flow"))
    if n.type == "exclusiveGateway" and len(n.outgoing) > 1:
        for fid in n.outgoing:
            if fid != n.default and not p.flows[fid].condition:
                out.append(Problem(fid, f"branch of gateway '{n.label}' has no condition (and isn't its default)"))
    if n.type == "parallelGateway":
        for fid in n.outgoing:
            if p.flows[fid].condition:
                out.append(Problem(fid, f"parallel gateway '{n.label}' takes every branch; remove the condition"))
    if n.type not in ("exclusiveGateway", "parallelGateway") and len(n.outgoing) > 1:
        out.append(Problem(n.id, "more than one outgoing flow: use a gateway to split"))
    return out


def _unreachable(p: Process) -> list[str]:
    starts = [n.id for n in p.nodes.values() if n.type == "startEvent"]
    seen, todo = set(starts), list(starts)
    while todo:
        nid = todo.pop()
        nxt = [p.flows[f].target for f in p.nodes[nid].outgoing if p.flows[f].target in p.nodes]
        nxt += p.boundaries.get(nid, [])
        for t in nxt:
            if t not in seen:
                seen.add(t)
                todo.append(t)
    return [nid for nid in p.nodes if nid not in seen]


def _syntax(element: str, expr: str) -> list[Problem]:
    try:
        _check_ast(ast.parse(expr, mode="eval"))
    except (SyntaxError, ExpressionError) as e:
        return [Problem(element, f"expression '{expr}' isn't supported: {e}")]
    return []


# --- comparing versions ---------------------------------------------------------------------------

def _facts(p: Process) -> dict[str, dict]:
    """What the engine reads from each element, by id (layout left out), the process itself under its key."""
    out = {p.key: {"type": "process", "name": p.name, **{f"fuzer:{k}": v for k, v in p.attrs.items()},
                   **{f"threshold {k}": v for k, v in p.thresholds.items()}}}
    for n in p.nodes.values():
        lane = n.lane or {}
        out[n.id] = {"type": n.type, "name": n.name, **{f"fuzer:{k}": v for k, v in n.attrs.items()},
                     "lane role": lane.get("role"), "lane agent": lane.get("agent"), "event": n.event,
                     "timer": " ".join(n.timer) if n.timer else None, "message": n.message,
                     "attached to": n.attached_to, "interrupting": n.cancel if n.type == "boundaryEvent" else None,
                     "default flow": n.default, "calls": n.called}
    for f in p.flows.values():
        out[f.id] = {"type": "sequenceFlow", "name": f.name, "from": f.source, "to": f.target, "condition": f.condition}
    return {k: {f: v for f, v in d.items() if v is not None} for k, d in out.items()}


def diff(old_xml: str | bytes | None, new_xml: str | bytes, process_key: str) -> list[dict]:
    """Elements added, removed or changed between two versions of a process, with the fields that changed.
    Moving shapes around isn't a change."""
    old = _facts(parse(old_xml)[process_key]) if old_xml else {}
    new = _facts(parse(new_xml)[process_key])
    out = []
    for eid in [*new, *(k for k in old if k not in new)]:
        a, b = old.get(eid), new.get(eid)
        if a == b:
            continue
        cur = b or a
        row = {"element": eid, "type": cur["type"], "name": cur.get("name"),
               "change": "added" if a is None else "removed" if b is None else "changed"}
        if a and b:
            row["fields"] = [{"field": f, "old": a.get(f), "new": b.get(f)}
                             for f in [*b, *(k for k in a if k not in b)] if a.get(f) != b.get(f)]
        out.append(row)
    return out


# --- expressions --------------------------------------------------------------------------------

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
        ast.GtE: operator.ge, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
        ast.Is: operator.is_, ast.IsNot: operator.is_not}
_FUNCS = {"len": len, "min": min, "max": max, "abs": abs, "round": round, "str": str, "int": int, "float": float,
          "bool": bool, "any": any, "all": all}
_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.BinOp,
          ast.Compare, ast.Name, ast.Load, ast.Constant, ast.Subscript, ast.List, ast.Tuple, ast.Set, ast.Dict,
          ast.IfExp, ast.Call, *_BIN, *_CMP)


def _check_ast(tree):
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            raise ExpressionError(f"{type(node).__name__} isn't allowed")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS
                                               and not node.keywords):
            raise ExpressionError("only len, min, max, abs, round, str, int, float, bool, any and all can be called")


def is_expression(value: str | None) -> bool:
    """A `=`-prefixed attribute (a role, an assignee, a work item type) is an expression over the process data."""
    return bool(value) and value.startswith("=")


def resolve(value: str | None, data: dict) -> str | None:
    """An attribute's value for this process data: the expression's result for `=expr`, else the value itself."""
    if not is_expression(value):
        return value
    out = evaluate(value[1:], data)
    return None if out is None else str(out)


def evaluate(expr: str, data: dict):
    """A condition or timer expression over the process data."""
    try:
        tree = ast.parse(expr.strip(), mode="eval")
        _check_ast(tree)
    except SyntaxError as e:
        raise ExpressionError(f"'{expr}': {e.msg}") from e
    return _eval(tree.body, data, expr)


def _eval(node, data, expr):
    match node:
        case ast.Constant():
            return node.value
        case ast.Name():
            if node.id in data:
                return data[node.id]
            if node.id in ("True", "False", "None"):
                return {"True": True, "False": False, "None": None}[node.id]
            raise ExpressionError(f"'{expr}': name '{node.id}' is not defined")
        case ast.BoolOp():
            vals = (lambda: _eval(v, data, expr) for v in node.values)  # noqa: B023
            if isinstance(node.op, ast.And):
                out = True
                for v in vals:
                    if not (out := v()):
                        return out
                return out
            out = False
            for v in vals:
                if out := v():
                    return out
            return out
        case ast.UnaryOp():
            v = _eval(node.operand, data, expr)
            return {ast.Not: lambda: not v, ast.USub: lambda: -v, ast.UAdd: lambda: +v}[type(node.op)]()
        case ast.BinOp():
            return _guard(expr, _BIN[type(node.op)], _eval(node.left, data, expr), _eval(node.right, data, expr))
        case ast.Compare():
            left = _eval(node.left, data, expr)
            for op, comp in zip(node.ops, node.comparators):
                right = _eval(comp, data, expr)
                if not _guard(expr, _CMP[type(op)], left, right):
                    return False
                left = right
            return True
        case ast.Subscript():
            base, key = _eval(node.value, data, expr), _eval(node.slice, data, expr)
            try:
                return base[key]
            except (KeyError, IndexError, TypeError) as e:
                raise ExpressionError(f"'{expr}': no {key!r} in {type(base).__name__}") from e
        case ast.List() | ast.Tuple() | ast.Set():
            vals = [_eval(e, data, expr) for e in node.elts]
            return {ast.List: list, ast.Tuple: tuple, ast.Set: set}[type(node)](vals)
        case ast.Dict():
            return {_eval(k, data, expr): _eval(v, data, expr) for k, v in zip(node.keys, node.values)}
        case ast.IfExp():
            return _eval(node.body if _eval(node.test, data, expr) else node.orelse, data, expr)
        case ast.Call():
            return _guard(expr, _FUNCS[node.func.id], *[_eval(a, data, expr) for a in node.args])
    raise ExpressionError(f"'{expr}': {type(node).__name__} isn't allowed")


def _guard(expr, fn, *args):
    try:
        return fn(*args)
    except (TypeError, ValueError, ZeroDivisionError) as e:
        raise ExpressionError(f"'{expr}': {e}") from e


# --- timers -------------------------------------------------------------------------------------

_DURATION = re.compile(r"^P(?!$)(?:(\d+(?:\.\d+)?)Y)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)W)?(?:(\d+(?:\.\d+)?)D)?"
                       r"(?:T(?=\d)(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$", re.IGNORECASE)


def parse_duration(text: str) -> timedelta | None:
    """An ISO 8601 duration; years and months count as 365 and 30 days."""
    m = _DURATION.match((text or "").strip().strip("'\""))
    if not m:
        return None
    y, mo, w, d, h, mi, s = (float(g) if g else 0.0 for g in m.groups())
    return timedelta(days=y * 365 + mo * 30 + w * 7 + d, hours=h, minutes=mi, seconds=s)


def _literal_timer(text: str) -> bool:
    """A quoted string, or a bare duration / repeating interval / date; anything else is an expression."""
    t = text.strip()
    if not t:
        return False
    if t[0] in "'\"" or parse_duration(t) or re.match(r"^R\d*/", t, re.IGNORECASE):
        return True
    try:
        datetime.fromisoformat(t)
        return True
    except ValueError:
        return False


def _parse_date(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def timer_error(kind: str, text: str) -> str | None:
    try:
        due(kind, text, {}, datetime.now(UTC))
    except (ExpressionError, ValueError) as e:
        return str(e)
    return None


def due(kind: str, text: str, data: dict, now: datetime) -> tuple[datetime, int | None, timedelta | None]:
    """When a timer fires: (first due time, cycles left after it or None, cycle interval or None).
    A cycle `R3/PT1H` fires three times an hour apart; `R/PT1H` repeats until its activity ends."""
    value = text.strip()
    if not _literal_timer(value):
        value = str(evaluate(value, data))
    value = value.strip().strip("'\"")
    if kind == "timeDuration":
        if (d := parse_duration(value)) is None:
            raise ValueError(f"'{value}' isn't an ISO 8601 duration")
        return now + d, None, None
    if kind == "timeDate":
        try:
            return _parse_date(value), None, None
        except ValueError as e:
            raise ValueError(f"'{value}' isn't an ISO 8601 date and time") from e
    parts = value.upper().split("/")
    if len(parts) not in (2, 3) or not parts[0].startswith("R"):
        raise ValueError(f"'{value}' isn't an ISO 8601 repeating interval (R<n>/<duration>)")
    cycles = int(parts[0][1:]) if parts[0][1:] else -1
    if len(parts) == 3:  # R/<start>/<duration>
        start, interval = _parse_date(parts[1]), parse_duration(parts[2])
    else:
        interval = parse_duration(parts[1])
        start = now + interval if interval else None
    if not interval:
        raise ValueError(f"'{value}' has no valid interval")
    return start, (cycles - 1 if cycles > 0 else -1), interval
