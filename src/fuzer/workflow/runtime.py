"""BPMN process execution and runtime operations."""

from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from . import bpmn
from .definitions import (
    DEFAULT_OWNER, DEFAULT_SLA, SYSTEM, TASK_KINDS, Conflict, NotFound, WorkError,
    _definition, _event, _op, _published, is_agent, utcnow,
)

class _Run:
    """One operation: the clock, who is acting, and the processes it has locked (data written at the end)."""

    def __init__(self, conn, actor: str, now: datetime | None = None):
        self.conn, self.actor, self.now = conn, actor, now or utcnow()
        self.procs: dict[int, dict] = {}
        self.items: set[int] = set()

    def proc(self, process_id: int, skip_locked: bool = False) -> dict | None:
        if process_id not in self.procs:
            row = self.conn.execute(f"select * from work_process where id = %s for update{' skip locked' if skip_locked else ''}",
                                    (process_id,)).fetchone()
            if row is None:
                if skip_locked:
                    return None
                raise NotFound("unknown process")
            self.procs[process_id] = row
            self.items.add(row["item_id"])
        return self.procs[process_id]

    def defn(self, proc: dict) -> bpmn.Process:
        return _definition(self.conn, proc["definition_id"])

    def event(self, kind: str, proc: dict | None = None, token: dict | None = None, actor: str | None = None,
              item_id=None, **data):
        _event(self.conn, kind, actor or self.actor, item_id or (proc or {}).get("item_id"),
               (proc or {}).get("id"), (token or {}).get("id"), (proc or {}).get("definition_id"),
               (token or {}).get("element"), data, self.now)

    def flush(self):
        for p in self.procs.values():
            self.conn.execute("update work_process set data = %s, state = %s, outcome = %s, ended_at = %s where id = %s",
                              (Jsonb(p["data"]), p["state"], p["outcome"], p["ended_at"], p["id"]))
        for item_id in self.items:
            _sync_item(self.conn, item_id, self.now)

def _scope(run: _Run, proc: dict) -> dict:
    return {**proc["data"], "thresholds": run.defn(proc).thresholds}

def _token(run: _Run, proc: dict, node: bpmn.Node, state: str, kind: str, **cols) -> dict:
    cols = {"process_id": proc["id"], "item_id": proc["item_id"], "element": node.id, "name": node.name,
            "kind": kind, "state": state, "created_at": run.now, **cols}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return run.conn.execute(f"insert into work_token ({names}) values ({marks}) returning *", list(cols.values())).fetchone()

def _update(run: _Run, token: dict, **cols) -> dict:
    sets = ", ".join(f"{k} = %s" for k in cols)
    return run.conn.execute(f"update work_token set {sets} where id = %s returning *", [*cols.values(), token["id"]]).fetchone()

def _finish(run: _Run, token: dict, state: str = "done", by: str | None = None) -> dict:
    return _update(run, token, state=state, finished_at=run.now, finished_by=by, lease_until=None)

def _start(run: _Run, definition: dict, data: dict, item_id: int, parent_token: int | None = None,
           correlation_key: str | None = None) -> dict:
    p = _definition(run.conn, definition["id"])
    proc = run.conn.execute(
        "insert into work_process (definition_id, process_key, item_id, parent_token, correlation_key, data, "
        "started_by, started_at) values (%s, %s, %s, %s, %s, %s, %s, %s) returning *",
        (definition["id"], definition["process_key"], item_id, parent_token, correlation_key, Jsonb(data),
         run.actor, run.now)).fetchone()
    run.procs[proc["id"]] = proc
    run.items.add(item_id)
    run.event("started", proc, process_key=definition["process_key"], version=definition["version"],
              correlation_key=correlation_key, parent_token=parent_token)
    _token(run, proc, p.start, "ready", "auto")
    _advance(run, proc)
    return proc

def _advance(run: _Run, proc: dict) -> None:
    """Move every ready step on until all are waiting or the process has ended."""
    while proc["state"] == "running":
        t = run.conn.execute("select * from work_token where process_id = %s and state = 'ready' order by id limit 1",
                             (proc["id"],)).fetchone()
        if t is None:
            return
        try:
            _step(run, proc, t)
        except (bpmn.ExpressionError, ValueError) as e:
            _fail(run, proc, t, str(e))

def _step(run: _Run, proc: dict, t: dict) -> None:
    p = run.defn(proc)
    n = p.nodes[t["element"]]
    match n.type:
        case "startEvent":
            _leave(run, proc, t, n.outgoing)
        case "endEvent":
            _end(run, proc, t, n)
        case "exclusiveGateway":
            _leave(run, proc, t, [_choose(p, n, _scope(run, proc))])
        case "parallelGateway":
            _parallel(run, proc, t, n)
        case "userTask":
            from . import roles

            sla = bpmn.parse_duration(n.attrs.get("sla", "")) or roles.sla_of(run.conn, n.role)
            assignee = n.attrs.get("assignee")
            if assignee and assignee.startswith("="):
                assignee = bpmn.evaluate(assignee[1:], _scope(run, proc))
            how = "named" if assignee else None
            if not assignee and (assignee := roles.pick(run.conn, n.role, run.now)):
                how = "auto"  # the role assigns round-robin or to the least loaded
            covering = roles.covering(run.conn, assignee, run.now) if assignee else None
            t = _update(run, t, state="waiting", kind="user", role=n.role, assignee=covering or None,
                        requires_human=n.requires_human, due_at=run.now + sla if sla else None)
            _boundaries(run, proc, t, n)
            run.event("step_started", proc, t, step=n.label, role=n.role, assignee=t["assignee"],
                      due=t["due_at"].isoformat() if t["due_at"] else None)
            if how == "auto":
                run.event("auto_assigned", proc, t, to=assignee, role=n.role)
            if covering and covering != assignee:
                run.event("delegated", proc, t, **{"for": assignee, "to": covering})
        case "serviceTask":
            t = _update(run, t, state="waiting", kind="agent", role=n.agent, run_after=run.now)
            _boundaries(run, proc, t, n)
            run.event("step_started", proc, t, step=n.label, agent=n.agent)
        case "intermediateCatchEvent":
            if n.event == "timer":
                at, _, _ = bpmn.due(*n.timer, _scope(run, proc), run.now)
                _update(run, t, state="waiting", kind="timer", due_at=at)
            else:
                _update(run, t, state="waiting", kind="message", message_name=n.message,
                        correlation_key=proc["correlation_key"])
        case "callActivity":
            t = _update(run, t, state="waiting", kind="call")
            _boundaries(run, proc, t, n)
            child_def = _published(run.conn, n.called)
            run.event("step_started", proc, t, step=n.label, calls=n.called)
            _start(run, child_def, dict(proc["data"]), proc["item_id"], t["id"], proc["correlation_key"])

def _choose(p: bpmn.Process, n: bpmn.Node, data: dict) -> str:
    if len(n.outgoing) == 1 and not p.flows[n.outgoing[0]].condition:
        return n.outgoing[0]
    for fid in n.outgoing:
        f = p.flows[fid]
        if fid != n.default and f.condition and bpmn.evaluate(f.condition, data):
            return fid
    if n.default:
        return n.default
    raise bpmn.ExpressionError(f"no branch of gateway '{n.label}' matched and it has no default")

def _parallel(run: _Run, proc: dict, t: dict, n: bpmn.Node) -> None:
    p = run.defn(proc)
    if len(n.incoming) > 1:
        _update(run, t, state="waiting", kind="join")
        arrived = run.conn.execute("select * from work_token where process_id = %s and element = %s and state = 'waiting' "
                                   "and kind = 'join' order by id", (proc["id"], n.id)).fetchall()
        first = {}
        for a in arrived:
            first.setdefault(a["flow"], a)
        if not set(n.incoming) <= set(first):
            return
        for a in first.values():
            _finish(run, a)
    else:
        _finish(run, t)
    for fid in n.outgoing:
        _token(run, proc, p.nodes[p.flows[fid].target], "ready", "auto", flow=fid)

def _leave(run: _Run, proc: dict, t: dict, flows: list[str], by: str | None = None) -> None:
    p = run.defn(proc)
    _finish(run, t, by=by)
    for fid in flows:
        _token(run, proc, p.nodes[p.flows[fid].target], "ready", "auto", flow=fid)

def _boundaries(run: _Run, proc: dict, t: dict, n: bpmn.Node) -> None:
    p = run.defn(proc)
    for bid in p.boundaries.get(n.id, []):
        b = p.nodes[bid]
        if b.event == "timer":
            at, cycles, _ = bpmn.due(*b.timer, _scope(run, proc), run.now)
            _token(run, proc, b, "waiting", "timer", due_at=at, cycles_left=cycles, attached_to=t["id"])
        else:
            _token(run, proc, b, "waiting", "message", message_name=b.message, attached_to=t["id"],
                   correlation_key=proc["correlation_key"])

def _cancel_boundaries(run: _Run, t: dict) -> None:
    run.conn.execute("update work_token set state = 'cancelled', finished_at = %s where attached_to = %s and state = 'waiting'",
                     (run.now, t["id"]))

def _cancel_activity(run: _Run, proc: dict, t: dict, why: str) -> None:
    """Interrupt an activity: the step, its boundary events and, for a call activity, the called process."""
    _finish(run, t, state="cancelled")
    _cancel_boundaries(run, t)
    for child in run.conn.execute("select id from work_process where parent_token = %s and state = 'running'", (t["id"],)):
        _cancel_process(run, run.proc(child["id"]), why)
    if t["kind"] in TASK_KINDS:
        run.event("step_cancelled", proc, t, why=why)

def _cancel_process(run: _Run, proc: dict, why: str, state: str = "cancelled") -> None:
    for t in run.conn.execute("select * from work_token where process_id = %s and state in ('ready', 'waiting')",
                              (proc["id"],)).fetchall():
        if t["kind"] == "call":
            _cancel_activity(run, proc, t, why)
        else:
            _finish(run, t, state="cancelled")
    proc.update(state=state, ended_at=run.now)

def _fail(run: _Run, proc: dict, t: dict, error: str) -> None:
    """A step that can't go on becomes a task for the process owner, who can fix the data and retry it."""
    owner = run.defn(proc).attrs.get("ownerRole") or DEFAULT_OWNER
    _cancel_boundaries(run, t)
    t = _update(run, t, state="waiting", kind="fix", role=owner, assignee=None, error=error, due_at=run.now + DEFAULT_SLA)
    run.event("failed", proc, t, error=error, role=owner)

def _end(run: _Run, proc: dict, t: dict, n: bpmn.Node) -> None:
    _update(run, t, state="done", kind="end", finished_at=run.now)
    if n.event == "terminate":
        _cancel_process(run, proc, f"terminated at '{n.label}'", state="terminated")
        _process_ended(run, proc, n.label, "terminated")
        return
    left = run.conn.execute("select count(*) as n from work_token where process_id = %s and state in ('ready', 'waiting')",
                            (proc["id"],)).fetchone()["n"]
    if left == 0:
        _process_ended(run, proc, n.label, "completed")

def _process_ended(run: _Run, proc: dict, outcome: str, state: str) -> None:
    proc.update(state=state, outcome=outcome, ended_at=run.now)
    run.event("completed_process", proc, outcome=outcome, state=state)
    if proc["parent_token"]:
        parent_token = run.conn.execute("select * from work_token where id = %s", (proc["parent_token"],)).fetchone()
        parent = run.proc(parent_token["process_id"])
        if parent_token["state"] != "waiting":
            return
        parent["data"].update(proc["data"])
        _cancel_boundaries(run, parent_token)
        _leave(run, parent, parent_token, run.defn(parent).nodes[parent_token["element"]].outgoing)
        _advance(run, parent)
    else:
        run.conn.execute("update work_item set state = 'done', outcome = %s, closed_at = %s, updated_at = %s "
                         "where id = %s and closed_at is null", (outcome, run.now, run.now, proc["item_id"]))
        run.event("state_changed", proc, state="done", outcome=outcome)

def _sync_item(conn, item_id: int, now: datetime) -> None:
    """The item's step, queue and due date from its open tasks (any process on it)."""
    conn.execute(
        """
        update work_item i set step = s.step, assignee = s.assignee, role = s.role, due = s.due, updated_at = %(now)s,
               priority = coalesce(s.priority, i.priority)
        from (
            select string_agg(coalesce(t.name, t.element), ', ' order by t.id) as step,
                   case when count(*) = 1 then max(t.assignee) end as assignee,
                   case when count(distinct t.role) = 1 then max(t.role) end as role,
                   min(t.due_at) as due,
                   (select max((p.data->>'priority')::double precision) from work_process p
                    where p.item_id = %(id)s and jsonb_typeof(p.data->'priority') = 'number') as priority
            from work_token t where t.item_id = %(id)s and t.state = 'waiting' and t.kind = any(%(kinds)s)
        ) s
        where i.id = %(id)s and exists (select 1 from work_process p where p.item_id = i.id)
        """, {"id": item_id, "now": now, "kinds": list(TASK_KINDS)})

def _correlation(p: bpmn.Process, data: dict) -> str | None:
    if expr := p.attrs.get("correlationKey"):
        try:
            value = bpmn.evaluate(expr, data)
        except bpmn.ExpressionError as e:
            raise WorkError(f"can't work out the correlation key: {e}") from e
        return None if value is None else str(value)
    return None

def _new_item(run: _Run, p: bpmn.Process, data: dict, title, subjects, priority) -> int:
    item = run.conn.execute(
        "insert into work_item (type, title, subjects, priority, created_by, source, payload, created_at, updated_at) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (p.attrs.get("workItemType") or p.key, title or data.get("title") or p.name or p.key,
         list(subjects or data.get("subjects") or []), priority if priority is not None else data.get("priority"),
         run.actor, data.get("source") or run.actor, Jsonb({"start": data}), run.now, run.now)).fetchone()["id"]
    run.event("created", item_id=item, type=p.attrs.get("workItemType") or p.key)
    return item

def _start_new(run: _Run, process_key: str, data: dict, title, subjects, priority, correlation_key) -> dict:
    d = _published(run.conn, process_key)
    p = _definition(run.conn, d["id"])
    key = correlation_key if correlation_key is not None else _correlation(p, data)
    item_id = _new_item(run, p, data, title, subjects, priority)
    proc = _start(run, d, data, item_id, correlation_key=key)
    return {"process_id": proc["id"], "item_id": item_id, "state": proc["state"], "correlation_key": key,
            "started": True}

@_op
def start(conn, process_key: str, data: dict | None = None, by: str = SYSTEM, title: str | None = None,
          subjects: list[str] | None = None, priority: float | None = None, correlation_key: str | None = None,
          now: datetime | None = None) -> dict:
    """Start the published version of a process on a new work item."""
    run = _Run(conn, by, now)
    out = _start_new(run, process_key, dict(data or {}), title, subjects, priority, correlation_key)
    run.flush()
    return out

@_op
def start_or_correlate(conn, process_key: str, data: dict, by: str = SYSTEM, message: str = "evidence",
                       title: str | None = None, subjects: list[str] | None = None, priority: float | None = None,
                       now: datetime | None = None) -> dict:
    """What the SRE calls per grouped upset: when a process for the same correlation key (incident id)
    is already open, the data goes to it as a message (new evidence); otherwise a new one starts."""
    run = _Run(conn, by, now)
    d = _published(conn, process_key)
    key = _correlation(_definition(conn, d["id"]), data)
    if key is None:
        raise WorkError(f"process '{process_key}' has no fuzer:correlationKey, or the data gives no value for it")
    conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"work:{process_key}:{key}",))
    open_proc = conn.execute("select id, item_id from work_process where process_key = %s and correlation_key = %s "
                             "and state = 'running' and parent_token is null order by id limit 1", (process_key, key)).fetchone()
    if open_proc:
        sent = _send(run, message, key, data, process_key)
        run.flush()
        return {"process_id": open_proc["id"], "item_id": open_proc["item_id"], "correlation_key": key,
                "started": False, **sent}
    out = _start_new(run, process_key, dict(data), title, subjects, priority, key)
    run.flush()
    return out

def _owner(run: _Run, proc: dict) -> str:
    return run.defn(proc).attrs.get("ownerRole") or DEFAULT_OWNER

def _send(run: _Run, name: str, key: str, payload: dict, process_key: str | None = None) -> dict:
    waiting = run.conn.execute(
        """
        select t.id, t.process_id from work_token t join work_process p on p.id = t.process_id
        where t.state = 'waiting' and t.kind = 'message' and t.message_name = %s and t.correlation_key = %s
          and (%s::text is null or p.process_key = %s or p.parent_token is not null)
        order by t.id
        """, (name, key, process_key, process_key)).fetchall()
    delivered = []
    for w in waiting:
        if w["process_id"] in delivered:
            continue
        proc = run.proc(w["process_id"])
        t = run.conn.execute("select * from work_token where id = %s", (w["id"],)).fetchone()
        if t["state"] != "waiting" or proc["state"] != "running":
            continue
        proc["data"].update(payload)
        run.event("message", proc, t, name=name, correlation_key=key, payload=payload, delivered=True)
        _fire(run, proc, t)
        delivered.append(proc["id"])
    recorded = []
    if not delivered:  # nothing waiting for it: keep it on the open process as evidence
        for r in run.conn.execute("select id from work_process where correlation_key = %s and state = 'running' and "
                                  "parent_token is null and (%s::text is null or process_key = %s) order by id",
                                  (key, process_key, process_key)).fetchall():
            proc = run.proc(r["id"])
            proc["data"].setdefault("messages", []).append({"name": name, "payload": payload, "by": run.actor,
                                                           "at": run.now.isoformat()})
            run.event("message", proc, name=name, correlation_key=key, payload=payload, delivered=False)
            recorded.append(proc["id"])
    return {"delivered": delivered, "recorded": recorded}

@_op
def send_message(conn, name: str, correlation_key: str, payload: dict | None = None, by: str = SYSTEM,
                 process_key: str | None = None, now: datetime | None = None) -> dict:
    """Send a message (new evidence, a SAP notification raised, a comment) to the open process with this
    correlation key. A process waiting for it moves on; otherwise it is kept on the process as evidence."""
    run = _Run(conn, by, now)
    out = _send(run, name, str(correlation_key), dict(payload or {}), process_key)
    run.flush()
    return out

def _fire(run: _Run, proc: dict, t: dict) -> None:
    """A timer passed or a message arrived for a waiting event."""
    p = run.defn(proc)
    n = p.nodes[t["element"]]
    if n.type == "intermediateCatchEvent":
        _leave(run, proc, t, n.outgoing)
    else:
        activity = run.conn.execute("select * from work_token where id = %s", (t["attached_to"],)).fetchone()
        if n.cancel:
            _cancel_activity(run, proc, activity, f"boundary event '{n.label}'")
            _leave(run, proc, t, n.outgoing)
        else:
            for fid in n.outgoing:
                _token(run, proc, p.nodes[p.flows[fid].target], "ready", "auto", flow=fid)
            # A message boundary fires once, as in SpiffWorkflow; later messages are kept as evidence.
            if n.event == "timer" and t["cycles_left"]:
                _, _, interval = bpmn.due(*n.timer, _scope(run, proc), run.now)
                _update(run, t, due_at=t["due_at"] + interval,
                        cycles_left=t["cycles_left"] - 1 if t["cycles_left"] > 0 else -1)
            else:
                _finish(run, t)
    _advance(run, proc)

def tick(conn, now: datetime | None = None, limit: int = 1000) -> dict:
    """Fire timers that are due and escalate person tasks past their SLA (run from a Dagster schedule).
    Each process is its own transaction; a process another tick holds is skipped."""
    now = now or utcnow()
    due = conn.execute("select id, process_id from work_token where state = 'waiting' and kind = 'timer' and due_at <= %s "
                       "order by due_at, id limit %s", (now, limit)).fetchall()
    # past its SLA and not escalated since it was last due (an escalation to a role with an SLA sets a new due time)
    late = conn.execute("select id, process_id from work_token where state = 'waiting' and kind = 'user' and due_at <= %s "
                        "and (escalated_at is null or escalated_at < due_at) order by due_at, id limit %s",
                        (now, limit)).fetchall()
    conn.commit()
    fired = sum(_tick_timer(conn, r["process_id"], r["id"], now) for r in due)
    escalated = sum(_escalate(conn, r["process_id"], r["id"], now) for r in late)
    return {"timers": fired, "escalated": escalated}

@_op
def _tick_timer(conn, process_id: int, token_id: int, now: datetime) -> int:
    run = _Run(conn, SYSTEM, now)
    proc = run.proc(process_id, skip_locked=True)
    if proc is None or proc["state"] != "running":
        return 0
    t = conn.execute("select * from work_token where id = %s", (token_id,)).fetchone()
    if t["state"] != "waiting" or t["due_at"] > now:
        return 0
    run.event("timer_fired", proc, t, due=t["due_at"].isoformat())
    _fire(run, proc, t)
    run.flush()
    return 1

@_op
def _escalate(conn, process_id: int, token_id: int, now: datetime) -> int:
    run = _Run(conn, SYSTEM, now)
    proc = run.proc(process_id, skip_locked=True)
    if proc is None:
        return 0
    t = conn.execute("select * from work_token where id = %s", (token_id,)).fetchone()
    if t["state"] != "waiting" or t["kind"] != "user" or t["due_at"] > now or (
            t["escalated_at"] and t["escalated_at"] >= t["due_at"]):
        return 0
    from . import roles

    n = run.defn(proc).nodes[t["element"]]
    holder = t["assignee"] or f"the {t['role']} queue"
    hops = conn.execute("select count(*) as n from work_event where token_id = %s and kind = 'escalated'",
                        (token_id,)).fetchone()["n"]
    # the step's own escalateTo first; after that (and for steps naming none) the role's escalation chain
    to = n.attrs.get("escalateTo") if hops == 0 and n.attrs.get("escalateTo") else roles.escalation_target(conn, t["role"], None)
    if hops >= roles.MAX_HOPS or to == t["role"]:
        to = None
    sla_text = n.attrs.get("sla") if hops == 0 and n.attrs.get("sla") else (roles.role(conn, t["role"]) or {}).get("default_sla")
    sla_part = f"SLA {sla_text}" if sla_text else "SLA"
    why = (f"{sla_part} on '{n.label}' passed at {t['due_at']:%Y-%m-%d %H:%M} UTC "
           f"while it was with {holder}") + (f"; moved to the {to} queue" if to else "")
    cols = {"escalated_at": now}
    if to:
        cols |= {"role": to, "assignee": roles.pick(conn, to, now)}
        if (sla := roles.sla_of(conn, to)) and hops + 1 < roles.MAX_HOPS:
            cols["due_at"] = now + sla  # the next role gets its own SLA, and escalates on from there
    _update(run, t, **cols)
    run.event("escalated", proc, t, why=why, **{"from": holder, "to": f"role:{to}" if to else holder,
                                                 "due": t["due_at"].isoformat()})
    run.flush()
    return 1

@_op
def migrate(conn, process_id: int, definition_id: int, by: str, mapping: dict[str, str] | None = None,
            now: datetime | None = None) -> dict:
    """Move one running process to another published version of its definition. Each open step must exist
    in the new version, under its own id or the one `mapping` gives it."""
    run = _Run(conn, by, now)
    proc = run.proc(process_id)
    if proc["state"] != "running":
        raise Conflict("only running processes can move to a new version")
    new = conn.execute("select * from work_definition where id = %s", (definition_id,)).fetchone()
    if new is None or new["process_key"] != proc["process_key"] or new["state"] not in ("published", "superseded"):
        raise WorkError(f"definition {definition_id} isn't a published version of '{proc['process_key']}'")
    old_p, new_p = run.defn(proc), _definition(conn, definition_id)
    mapping = mapping or {}
    open_tokens = conn.execute("select * from work_token where process_id = %s and state in ('ready', 'waiting')",
                               (process_id,)).fetchall()
    for t in open_tokens:
        el = mapping.get(t["element"], t["element"])
        if el not in new_p.nodes or new_p.nodes[el].type != old_p.nodes[t["element"]].type:
            raise Conflict(f"step '{t['element']}' has no matching {old_p.nodes[t['element']].type} in version {new['version']}")
    for t in open_tokens:
        el = mapping.get(t["element"], t["element"])
        if (el, new_p.nodes[el].name) != (t["element"], t["name"]):
            _update(run, t, element=el, name=new_p.nodes[el].name)
    old = proc["definition_id"]
    conn.execute("update work_process set definition_id = %s where id = %s", (definition_id, process_id))
    proc["definition_id"] = definition_id
    run.event("migrated", proc, from_definition=old, to_definition=definition_id, version=new["version"], mapping=mapping)
    run.flush()
    return {"process_id": process_id, "definition_id": definition_id, "version": new["version"]}

@_op
def cancel(conn, process_id: int, by: str, reason: str | None = None, now: datetime | None = None) -> dict:
    run = _Run(conn, by, now)
    proc = run.proc(process_id)
    if proc["state"] != "running":
        raise Conflict(f"process is {proc['state']}")
    _cancel_process(run, proc, reason or f"cancelled by {by}")
    run.event("cancelled", proc, reason=reason)
    if not proc["parent_token"]:
        conn.execute("update work_item set state = 'cancelled', closed_at = %s, updated_at = %s where id = %s",
                     (run.now, run.now, proc["item_id"]))
    run.flush()
    return {"process_id": process_id, "state": "cancelled"}
