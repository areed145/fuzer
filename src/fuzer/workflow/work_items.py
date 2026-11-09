"""Standalone work-item operations and workflow item reads."""

from datetime import datetime

from psycopg.types.json import Jsonb

from .assignment import hand_off
from .definitions import (
    DUE_SOON, Conflict, NotFound, WorkError, _event, _op, utcnow,
)
from .runtime import _Run, _sync_item, _update

@_op
def create_item(conn, type: str, title: str, by: str, subjects: list[str] | None = None, priority: float | None = None,
                assignee: str | None = None, role: str | None = None, due: datetime | None = None,
                source: str | None = None, payload: dict | None = None) -> dict:
    row = conn.execute(
        "insert into work_item (type, title, subjects, priority, assignee, role, due, created_by, source, payload) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *",
        (type, title, list(subjects or []), priority, assignee, role, due, by, source or by, Jsonb(payload or {}))).fetchone()
    _event(conn, "created", by, item_id=row["id"], data={"type": type, "assignee": assignee, "role": role})
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


def dispatch_item(conn, item_id: int, by: str, to: str, reason: str | None = None) -> dict:
    """Give a case to someone: an item without a process directly, else its open person step due first."""
    from .assignment import hand_off

    step = conn.execute("select id from work_token where item_id = %s and kind = 'user' and state in ('ready', 'waiting') "
                        "order by due_at nulls last, id limit 1", (item_id,)).fetchone()
    if step:
        return hand_off(conn, step["id"], by, to, reason)
    return assign_item(conn, item_id, by, to, reason)
