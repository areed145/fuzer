"""Standalone work-item operations and workflow item reads."""

from datetime import datetime

from psycopg.types.json import Jsonb

from .assignment import hand_off
from .definitions import (
    DUE_SOON,
    Conflict,
    NotFound,
    WorkError,
    _definition,
    _event,
    _op,
    utcnow,
)
from .runtime import _Run, _update


@_op
def create_item(conn, type: str, title: str, by: str, subjects: list[str] | None = None, priority: float | None = None,
                assignee: str | None = None, role: str | None = None, due: datetime | None = None,
                source: str | None = None, payload: dict | None = None, reason: str | None = None) -> dict:
    row = conn.execute(
        "insert into work_item (type, title, subjects, priority, assignee, role, due, created_by, source, payload) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *",
        (type, title, list(subjects or []), priority, assignee, role, due, by, source or by, Jsonb(payload or {}))).fetchone()
    _event(conn, "created", by, item_id=row["id"], data={"type": type, "assignee": assignee, "role": role,
                                                        **({"reason": reason} if reason else {})})
    return row

def _item(conn, item_id: int, lock: bool = False) -> dict:
    row = conn.execute(f"select * from work_item where id = %s{' for update' if lock else ''}", (item_id,)).fetchone()
    if row is None:
        raise NotFound("unknown work item")
    return row

def _has_process(conn, item_id: int) -> bool:
    return conn.execute("select exists (select 1 from work_process where item_id = %s and state = 'running') as x",
                        (item_id,)).fetchone()["x"]

@_op
def transition(conn, item_id: int, state: str, by: str, reason: str | None = None) -> dict:
    """Move an item that has no running process to another state (done and cancelled close it)."""
    item = _item(conn, item_id, lock=True)
    if _has_process(conn, item_id):
        raise Conflict("this item moves with its process: complete its open step instead")
    closed = state in ("done", "cancelled")
    conn.execute("update work_item set state = %s, closed_at = case when %s then now() end, updated_at = now() "
                 "where id = %s", (state, closed, item_id))
    _event(conn, "state_changed", by, item_id=item_id, data={"from": item["state"], "state": state, "reason": reason})
    return {"item_id": item_id, "state": state}

@_op
def set_item(conn, item_id: int, by: str, reason: str | None = None, priority: float | None = None,
             due: datetime | None = None, now: datetime | None = None) -> dict:
    """Change a case's priority or due date. On a case with a process, the priority goes into its process data (the
    item follows it) and a new due date moves its open person steps' due times (their SLA clock)."""
    run = _Run(conn, by, now)
    item = _item(conn, item_id, lock=True)
    procs = [r["id"] for r in conn.execute("select id from work_process where item_id = %s and state = 'running'",
                                           (item_id,))]
    changed = {}
    if priority is not None:
        conn.execute("update work_item set priority = %s, updated_at = %s where id = %s", (priority, run.now, item_id))
        for pid in procs:
            proc = run.proc(pid)
            proc["data"] = {**proc["data"], "priority": priority}
        changed["priority"] = {"from": item["priority"], "to": priority}
    if due is not None:
        if procs:
            for t in conn.execute("select * from work_token where item_id = %s and state = 'waiting' and kind = any(%s)",
                                  (item_id, ["user", "fix"])).fetchall():
                _update(run, t, due_at=due)
        else:
            conn.execute("update work_item set due = %s, updated_at = %s where id = %s", (due, run.now, item_id))
        changed["due"] = {"from": item["due"].isoformat() if item["due"] else None, "to": due.isoformat()}
    if not changed:
        raise WorkError("nothing to change: give a priority or a due date")
    _event(conn, "item_changed", by, item_id=item_id, data={**changed, "reason": reason}, at=run.now)
    run.items.add(item_id)
    run.flush()
    return {"item_id": item_id, **changed}

def reassign_item(conn, item_id: int, by: str, to: str, reason: str | None = None, now: datetime | None = None) -> dict:
    """Give everything open on a case to a person or a role's queue: each open person step is handed off (or the
    item itself, for a case without a process)."""
    tasks = [r["id"] for r in conn.execute(
        "select id from work_token where item_id = %s and state = 'waiting' and kind = any(%s) order by id",
        (item_id, ["user", "fix"]))]
    if not tasks:
        return assign_item(conn, item_id, by, to, reason)
    for token_id in tasks:
        hand_off(conn, token_id, by, to, reason, now)
    return {"item_id": item_id, "to": to, "tasks": tasks}

@_op
def assign_item(conn, item_id: int, by: str, to: str, reason: str | None = None) -> dict:
    """Hand off an item without a process (a person, `role:<role>` or `agent:<role>`)."""
    item = _item(conn, item_id, lock=True)
    if _has_process(conn, item_id):
        raise Conflict("this item moves with its process: hand off its open step instead")
    person, role = (None, to.split(":", 1)[1]) if ":" in to else (to, item["role"])
    conn.execute("update work_item set assignee = %s, role = %s, updated_at = now() where id = %s", (person, role, item_id))
    _event(conn, "handed_off", by, item_id=item_id,
           data={"from": item["assignee"] or item["role"], "to": to, "reason": reason})
    return {"item_id": item_id, "to": to}

@_op
def comment(conn, item_id: int, by: str, text: str, attachments: list[dict] | None = None) -> dict:
    _item(conn, item_id)
    _event(conn, "commented", by, item_id=item_id, data={"text": text, "attachments": attachments or []})
    return {"item_id": item_id}

def list_items(conn, state: str | None = "open", type: str | None = None, assignee: str | None = None,
               roles: list[str] | None = None, subject: str | None = None, overdue: bool | None = None,
               q: str | None = None, limit: int = 50, offset: int = 0, now: datetime | None = None) -> dict:
    """Items with filters, ranked by priority, with the total and how many are overdue or due soon."""
    now = now or utcnow()
    where = ["(%(state)s::text is null or i.state = %(state)s)", "(%(type)s::text is null or i.type = %(type)s)",
             "(%(assignee)s::text is null or i.assignee = %(assignee)s)",
             "(%(roles)s::text[] is null or i.role = any(%(roles)s))",
             "(%(subject)s::text is null or i.subjects @> array[%(subject)s])",
             "(%(q)s::text is null or i.title ilike '%%' || %(q)s || '%%')"]
    if overdue is not None:
        where.append("(i.due < %(now)s)" if overdue else "(i.due is null or i.due >= %(now)s)")
    args = {"state": state, "type": type, "assignee": assignee, "roles": roles, "subject": subject, "q": q,
            "now": now, "soon": now + DUE_SOON, "limit": limit, "offset": offset}
    cond = " and ".join(where)
    counts = conn.execute(
        f"select count(*) as total, count(*) filter (where i.due < %(now)s and i.closed_at is null) as overdue, "
        f"count(*) filter (where i.due >= %(now)s and i.due < %(soon)s and i.closed_at is null) as due_soon "
        f"from work_item i where {cond}", args).fetchone()
    rows = conn.execute(
        f"""
        select i.id, i.type, i.title, i.subjects, i.state, i.outcome, i.step, i.priority, i.assignee, i.role, i.due,
               i.created_by, i.source, i.created_at, i.updated_at, i.closed_at,
               i.due < %(now)s and i.closed_at is null as overdue,
               (select p.process_key from work_process p where p.item_id = i.id order by p.id limit 1) as process_key,
               exists (select 1 from work_token t where t.item_id = i.id and t.state = 'waiting'
                       and t.escalated_at is not null) as escalated
        from work_item i where {cond}
        order by i.priority desc nulls last, i.due nulls last, i.id limit %(limit)s offset %(offset)s
        """, args).fetchall()
    return {"items": rows, **counts}

def item(conn, item_id: int) -> dict:
    """An item with its processes (version, state, path taken, open steps) and its full history."""
    row = _item(conn, item_id)
    procs = conn.execute(
        "select p.id, p.process_key, d.version, p.definition_id, p.parent_token, p.correlation_key, p.state, p.outcome, "
        "p.data, p.started_by, p.started_at, p.ended_at from work_process p join work_definition d on d.id = p.definition_id "
        "where p.item_id = %s order by p.id", (item_id,)).fetchall()
    steps = conn.execute(
        "select id, process_id, element, name, kind, state, flow, role, assignee, requires_human, due_at, escalated_at, "
        "attempts, error, created_at, finished_at, finished_by, "
        # an agent step no worker holds (#393): with nobody claiming it for long, the agent worker may not be running
        "(kind = 'agent' and state = 'waiting' and (lease_until is null or lease_until < now())) as unclaimed "
        "from work_token where item_id = %s order by id",
        (item_id,)).fetchall()
    for p in procs:
        mine = [s for s in steps if s["process_id"] == p["id"]]
        p["path"] = [s for s in mine if s["state"] == "done"]
        p["open"] = [s for s in mine if s["state"] in ("ready", "waiting")]
    events = conn.execute("select id, kind, actor, element, data, at, process_id, token_id from work_event "
                          "where item_id = %s order by id", (item_id,)).fetchall()
    return {**row, "processes": procs, "events": events}

def process(conn, process_id: int) -> dict:
    row = conn.execute("select item_id from work_process where id = %s", (process_id,)).fetchone()
    if row is None:
        raise NotFound("unknown process")
    detail = item(conn, row["item_id"])
    return next(p for p in detail["processes"] if p["id"] == process_id) | {"item_id": row["item_id"]}


FIELD_ASK = "field_ask"  # the process data key a dispatch's "what to check or do" lands in


def _dispatch_data(text: str | None) -> dict | None:
    """A step's `fuzer:dispatch` ("triage=field_check"): the form data that sends the process to its own field step."""
    pairs = [p.split("=", 1) for p in (text or "").split(",") if "=" in p]
    return {k.strip(): v.strip() for k, v in pairs if k.strip()} or None


def _outside_role(conn, to: str, role: str | None) -> bool:
    """Is `to` (a person or `role:<role>`) someone other than the step's own role would send it to?"""
    from .definitions import roles_of

    if to.startswith("agent:") or not role:
        return False
    if to.startswith("role:"):
        return to[5:] != role
    return role not in roles_of(conn, to)


def _open_user_steps(conn, item_id: int, after: int = 0) -> list[dict]:
    return conn.execute("select id, element, role, assignee from work_token where item_id = %s and kind = 'user' "
                        "and state in ('ready', 'waiting') and id > %s order by id", (item_id, after)).fetchall()


def dispatch_item(conn, item_id: int, by: str, to: str, reason: str | None = None) -> dict:
    """Give a case to someone: an item without a process directly, else its open person step due first.

    A step that declares `fuzer:dispatch` (the upset's Triage: `triage=field_check`) has its own way to the field:
    sending it to someone outside its role completes it with that data and the reason as `field_ask`, and the step
    the process reaches (its Field check) is handed to them. The IOC's decision stays with the IOC, and the field's
    part shows on the process diagram."""
    from .assignment import complete, hand_off

    step = conn.execute("select id, process_id, element, role, assignee from work_token where item_id = %s "
                        "and kind = 'user' and state in ('ready', 'waiting') order by due_at nulls last, id limit 1",
                        (item_id,)).fetchone()
    if not step:
        return assign_item(conn, item_id, by, to, reason)
    data = None
    if step["process_id"]:
        proc = conn.execute("select definition_id from work_process where id = %s", (step["process_id"],)).fetchone()
        node = _definition(conn, proc["definition_id"]).nodes.get(step["element"])
        data = _dispatch_data(node.attrs.get("dispatch")) if node else None
    if not (data and _outside_role(conn, to, step["role"])):
        return hand_off(conn, step["id"], by, to, reason)
    if step["assignee"] and step["assignee"] != by:
        hand_off(conn, step["id"], by, by, f"to send it to {to.removeprefix('role:')}")
    complete(conn, step["id"], by, {**data, FIELD_ASK: (reason or "").strip()})
    nxt = _open_user_steps(conn, item_id, after=step["id"])
    if not nxt:
        return {"task_id": step["id"], "to": None}
    return hand_off(conn, nxt[0]["id"], by, to, reason)


def report_field_step(conn, item_id: int, person: str, result: str, note: str | None, reason: str) -> dict | None:
    """Complete a field step (an open `operator` step `person` holds) with what they found (`field_result`,
    `field_note`), then hand the step the process comes back to (the IOC's decision) to whoever sent them, with
    `reason`. None when they hold no field step on the case."""
    from .assignment import complete, hand_off

    step = conn.execute("select id from work_token where item_id = %s and kind = 'user' and state = 'waiting' "
                        "and assignee = %s and role = 'operator' order by id limit 1", (item_id, person)).fetchone()
    if not step:
        return None
    sender = conn.execute(  # who sent them: the hand-off to them, else whoever completed the step before it
        """
        select coalesce((select actor from work_event where token_id = %(t)s and kind = 'handed_off'
                         and data->>'to' = %(p)s and actor <> %(p)s order by id desc limit 1),
                        (select actor from work_event where item_id = %(i)s and kind = 'completed' and token_id < %(t)s
                         and actor not like 'agent:%%' and actor <> 'system' order by id desc limit 1)) as who
        """, {"t": step["id"], "p": person, "i": item_id}).fetchone()["who"]
    complete(conn, step["id"], person, {"field_result": result, "field_note": note or ""})
    back = _open_user_steps(conn, item_id, after=step["id"])
    if back and sender:
        hand_off(conn, back[0]["id"], person, sender, reason)
    return {"task_id": step["id"], "back_to": sender, "next": back[0]["id"] if back else None}


def _back_to(prior: str | None, person: str, role: str | None) -> str | None:
    """Where handed-back work goes: whoever it was handed from (a person or `role:<role>`), else its role's queue."""
    from . import bpmn

    if prior and ":" not in prior and prior in bpmn.PERSON_ROLES:
        prior = f"role:{prior}"  # an item's own hand-off records its role bare
    if prior and prior != person:
        return prior
    return f"role:{role}" if role else None


def hand_back_item(conn, item_id: int, person: str, by: str, reason: str | None = None) -> dict | None:
    """Give back what was sent to `person` on a case to whoever it was handed from: the open person step they hold
    (a process case), else the case itself. A field operator reporting back on a dispatched job, say, returns the
    IOC's decision step to the IOC instead of leaving it in the operator's list. None when nothing is with them."""
    step = conn.execute(
        "select id, role from work_token where item_id = %s and kind = 'user' and state in ('ready', 'waiting') "
        "and assignee = %s order by due_at nulls last, id limit 1", (item_id, person)).fetchone()
    if step:
        prior = conn.execute(
            "select data->>'from' as f from work_event where token_id = %s and kind = 'handed_off' "
            "and data->>'to' = %s order by id desc limit 1", (step["id"], person)).fetchone()
        to = _back_to(prior and prior["f"], person, step["role"])
        return hand_off(conn, step["id"], by, to, reason) if to else None
    item = _item(conn, item_id)
    if item["assignee"] != person or item["state"] in ("done", "cancelled") or _has_process(conn, item_id):
        return None
    # Handed to them, else opened for them or for a queue they took it from: back to whoever opened it (the
    # dispatcher). Taking it from the queue themselves isn't being sent it, so it doesn't send it back to the queue.
    prior = conn.execute(
        "select f from (select id, data->>'from' as f from work_event where item_id = %(i)s and token_id is null "
        "and kind = 'handed_off' and data->>'to' = %(p)s and actor <> %(p)s union all select id, actor from work_event "
        "where item_id = %(i)s and kind = 'created' and (data->>'assignee' = %(p)s "
        "or (actor <> %(p)s and actor not like 'agent:%%'))) x order by id desc limit 1",
        {"i": item_id, "p": person}).fetchone()
    to = _back_to(prior and prior["f"], person, item["role"])
    return assign_item(conn, item_id, by, to, reason) if to else None


def person_roles(conn) -> list[str]:
    """The person roles work can be routed to (the queues on Teams & routing), built-in ones seeded first."""
    from . import roles

    roles.refresh(conn)
    return [r["name"] for r in conn.execute("select name from work_role where kind = 'person' order by name")]


def published_routing(conn, process_key: str) -> list[dict] | None:
    """Each step of the published version of a process as it routes (roles.routing); None when none is published."""
    from . import roles

    row = conn.execute("select id from work_definition where process_key = %s and state = 'published' "
                       "order by version desc limit 1", (process_key,)).fetchone()
    return roles.routing(conn, row["id"]) if row else None


def role_holders(conn, role: str) -> list[str]:
    """Who on the roster (and the overarching people covering it) holds `role`."""
    from .definitions import people

    return [p["name"] for p in people(conn) if role in (p["roles"] or [])]
