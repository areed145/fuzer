"""Task completion, assignment, escalation, and inbox operations."""

from datetime import datetime, timedelta

from . import bpmn
from .definitions import (
    DEFAULT_SLA, SYSTEM, TASK_KINDS, Conflict, Forbidden, NotFound, WorkError,
    _definition, _event, _op, form, is_agent, roles_of, utcnow,
)
from .runtime import (
    _Run, _advance, _cancel_boundaries, _leave, _owner, _update,
)

@_op
def remove_person(conn, name: str, by: str = SYSTEM, now: datetime | None = None) -> dict:
    """Take a person off the roster (#202). Steps they had claimed or been handed go back to their
    role's queue, and so do work items without a process assigned to them."""
    if not conn.execute("select 1 from work_person where name = %s", (name,)).fetchone():
        raise NotFound(f"no person '{name}'")
    run = _Run(conn, by, now)
    ids = [r["id"] for r in conn.execute(
        "select id from work_token where state = 'waiting' and kind = any(%s) and assignee = %s order by id",
        (list(TASK_KINDS), name))]
    for token_id in ids:
        proc, t = _locked_task(run, token_id)
        _update(run, t, assignee=None)
        run.event("released", proc, t, reason=f"{name} was removed")
    items = [r["id"] for r in conn.execute(
        """
        update work_item i set assignee = null, updated_at = %s
        where i.closed_at is null and i.assignee = %s and not exists (select 1 from work_process p where p.item_id = i.id)
        returning i.id
        """, (run.now, name))]
    for item_id in items:
        run.event("released", item_id=item_id, reason=f"{name} was removed")
    run.flush()
    conn.execute("delete from work_person where name = %s", (name,))
    _event(conn, "person_removed", by, data={"person": name, "tasks": ids, "items": items}, at=run.now)
    return {"name": name, "released_tasks": ids, "released_items": items}

def _locked_task(run: _Run, token_id: int) -> tuple[dict, dict]:
    t = run.conn.execute("select process_id from work_token where id = %s", (token_id,)).fetchone()
    if t is None:
        raise NotFound("unknown task")
    proc = run.proc(t["process_id"])
    return proc, run.conn.execute("select * from work_token where id = %s", (token_id,)).fetchone()

def _waiting_task(t: dict) -> None:
    if t["state"] != "waiting" or t["kind"] not in TASK_KINDS:
        raise Conflict(f"step '{t['name'] or t['element']}' isn't waiting for anyone (it is {t['state']})")

@_op
def complete(conn, token_id: int, by: str, data: dict | None = None, worker: str | None = None,
             now: datetime | None = None) -> dict:
    """Complete a person's or agent's step with its form data (merged into the process data)."""
    run = _Run(conn, by, now)
    proc, t = _locked_task(run, token_id)
    _waiting_task(t)
    if t["requires_human"] and is_agent(by):
        raise Forbidden(f"step '{t['name'] or t['element']}' needs a person; {by} can't complete it")
    if t["kind"] in ("user", "fix") and t["assignee"] and t["assignee"] != by:
        raise Conflict(f"step '{t['name'] or t['element']}' is claimed by {t['assignee']}")
    if t["kind"] == "agent":
        if is_agent(by) and by != f"agent:{t['role']}":
            raise Forbidden(f"step '{t['name'] or t['element']}' is for agent:{t['role']}, not {by}")
        live = t["claimed_by"] and t["lease_until"] and t["lease_until"] > run.now
        if worker is not None and t["claimed_by"] != worker:
            raise Conflict(f"worker {worker} no longer holds step {token_id} (claimed by {t['claimed_by']})")
        if worker is None and live:
            raise Conflict(f"an agent is working on step '{t['name'] or t['element']}' ({t['claimed_by']})")
    data = dict(data or {})
    proc["data"].update(data)
    kind = "agent_result" if t["kind"] == "agent" and is_agent(by) else "completed"
    run.event(kind, proc, t, step=t["name"] or t["element"], data=data, decision=data.get("decision"))
    if t["kind"] == "fix":
        _update(run, t, state="ready", kind="auto", error=None, role=None, due_at=None)
    else:
        _cancel_boundaries(run, t)
        _leave(run, proc, t, run.defn(proc).nodes[t["element"]].outgoing, by=by)
    _advance(run, proc)
    run.flush()
    return {"task_id": token_id, "process_id": proc["id"], "item_id": proc["item_id"], "process_state": proc["state"]}

@_op
def claim(conn, token_id: int, by: str, now: datetime | None = None) -> dict:
    run = _Run(conn, by, now)
    proc, t = _locked_task(run, token_id)
    _waiting_task(t)
    if t["kind"] == "agent":
        raise Conflict("agent steps are claimed by workers; hand it off to take it over")
    if t["requires_human"] and is_agent(by):
        raise Forbidden(f"step '{t['name'] or t['element']}' needs a person")
    if t["assignee"] and t["assignee"] != by:
        raise Conflict(f"already claimed by {t['assignee']}")
    _update(run, t, assignee=by)
    run.event("claimed", proc, t)
    run.flush()
    return {"task_id": token_id, "assignee": by}

@_op
def release(conn, token_id: int, by: str, now: datetime | None = None) -> dict:
    run = _Run(conn, by, now)
    proc, t = _locked_task(run, token_id)
    _waiting_task(t)
    if t["assignee"] != by:
        raise Conflict(f"only {t['assignee'] or 'whoever claims it'} can release it")
    _update(run, t, assignee=None)
    run.event("released", proc, t)
    run.flush()
    return {"task_id": token_id, "assignee": None}

@_op
def hand_off(conn, token_id: int, by: str, to: str, reason: str | None = None, now: datetime | None = None) -> dict:
    """Give a step to a person (`alice`), a role's queue (`role:ops_lead`) or an agent (`agent:troubleshooter`)."""
    run = _Run(conn, by, now)
    proc, t = _locked_task(run, token_id)
    _waiting_task(t)
    before = t["assignee"] or (f"agent:{t['role']}" if t["kind"] == "agent" else f"role:{t['role']}")
    if t["kind"] == "agent" and t["claimed_by"] and t["lease_until"] and t["lease_until"] > run.now:
        raise Conflict(f"an agent is working on it ({t['claimed_by']})")
    n = run.defn(proc).nodes[t["element"]]
    if to.startswith("agent:"):
        role = to.split(":", 1)[1]
        if t["requires_human"]:
            raise Forbidden(f"step '{n.label}' needs a person; it can't go to an agent")
        if role not in bpmn.AGENT_ROLES:
            raise WorkError(f"unknown agent '{role}'")
        if t["kind"] == "fix":
            raise WorkError("a failed step needs a person to fix it")
        _update(run, t, kind="agent", role=role, assignee=None, run_after=run.now, attempts=0, claimed_by=None,
                lease_until=None, error=None)
    else:
        role, person = (to.split(":", 1)[1], None) if to.startswith("role:") else (None, to)
        if role and role not in bpmn.PERSON_ROLES:
            raise WorkError(f"unknown role '{role}'")
        if person:
            from . import roles

            if (cover := roles.covering(conn, person, run.now)) != person:
                run.event("delegated", proc, t, **{"for": person, "to": cover})
                person = cover
        cols = {"assignee": person, "claimed_by": None, "lease_until": None}
        if role:
            cols["role"] = role
        if t["kind"] == "agent":
            cols |= {"kind": "user", "role": role or n.attrs.get("fallbackRole") or _owner(run, proc),
                     "due_at": t["due_at"] or run.now + DEFAULT_SLA}
        _update(run, t, **cols)
    run.event("handed_off", proc, t, **{"from": before, "to": to, "reason": reason})
    run.flush()
    return {"task_id": token_id, "to": to}

def agent_failed(conn, token_id: int, worker: str, error: str, now: datetime | None = None,
                 max_attempts: int = 3, backoff: timedelta = timedelta(seconds=30)) -> dict:
    """An agent step raised: retry later with backoff, or after `max_attempts` give it to a person."""
    return _agent_failed(conn, token_id, worker, error, now, max_attempts, backoff)

@_op
def _agent_failed(conn, token_id, worker, error, now, max_attempts, backoff) -> dict:
    run = _Run(conn, f"agent:{worker}", now)
    proc, t = _locked_task(run, token_id)
    if t["state"] != "waiting" or t["kind"] != "agent" or t["claimed_by"] != worker:
        return {"task_id": token_id, "ignored": True}
    actor = f"agent:{t['role']}"
    if t["attempts"] >= max_attempts:
        n = run.defn(proc).nodes[t["element"]]
        role = n.attrs.get("fallbackRole") or _owner(run, proc)
        _update(run, t, kind="user", role=role, assignee=None, claimed_by=None, lease_until=None, error=error,
                due_at=run.now + DEFAULT_SLA)
        run.event("handed_off", proc, t, actor=actor, **{"from": actor, "to": f"role:{role}",
                  "reason": f"agent {t['role']} failed {t['attempts']} times; last error: {error}"})
        out = {"task_id": token_id, "handed_to": role}
    else:
        retry = run.now + backoff * 2 ** (t["attempts"] - 1)
        _update(run, t, claimed_by=None, lease_until=None, run_after=retry, error=error)
        run.event("agent_failed", proc, t, actor=actor, error=error, attempt=t["attempts"], retry_at=retry.isoformat())
        out = {"task_id": token_id, "retry_at": retry}
    run.flush()
    return out

_TASKS = """
    select t.id as task_id, t.element, t.name, t.kind, t.role, t.assignee, t.requires_human, t.due_at, t.escalated_at,
           t.error, t.created_at, t.due_at < %(now)s as overdue, t.claimed_by, t.lease_until,
           i.id as item_id, i.type, i.title, i.priority, i.subjects, p.id as process_id, p.process_key, p.definition_id
    from work_token t join work_process p on p.id = t.process_id join work_item i on i.id = t.item_id
    where t.state = 'waiting' and t.kind = any(%(kinds)s) and {who}
    order by i.priority desc nulls last, t.due_at nulls last, t.id limit %(limit)s
"""

_ITEMS = """
    select null::bigint as task_id, null as element, i.step as name, 'item' as kind, i.role, i.assignee,
           false as requires_human, i.due as due_at, null::timestamptz as escalated_at, null as error, i.created_at,
           i.due < %(now)s as overdue, null as claimed_by, null::timestamptz as lease_until, i.id as item_id, i.type, i.title, i.priority, i.subjects,
           null::bigint as process_id, null as process_key, null::bigint as definition_id
    from work_item i
    where i.closed_at is null and {who} and not exists (select 1 from work_process p where p.item_id = i.id)
    order by i.priority desc nulls last, i.due nulls last, i.id limit %(limit)s
"""

def my_work(conn, person: str, roles: list[str] | None = None, limit: int = 200, now: datetime | None = None) -> dict:
    """A person's open tasks ('mine': claimed by or assigned to them, or to someone away whom they cover) and their
    roles' queue (unclaimed)."""
    from . import roles as roles_

    now = now or utcnow()
    roles = roles if roles is not None else roles_of(conn, person)
    covering = roles_.covered_by(conn, person, now)
    args = {"person": person, "people": [person, *covering], "roles": roles, "now": now, "limit": limit,
            "kinds": ["user", "fix"]}
    mine = conn.execute(_TASKS.format(who="t.assignee = any(%(people)s)"), args).fetchall() + \
        conn.execute(_ITEMS.format(who="i.assignee = any(%(people)s)"), args).fetchall()
    for r in mine:
        r["covering_for"] = r["assignee"] if r["assignee"] != person else None
    queue = conn.execute(_TASKS.format(who="t.assignee is null and t.role = any(%(roles)s)"), args).fetchall() + \
        conn.execute(_ITEMS.format(who="i.assignee is null and i.role = any(%(roles)s)"), args).fetchall()
    soon = now + DUE_SOON
    _bulk_labels(conn, mine + queue)

    def counts(rows):
        return {"total": len(rows), "overdue": sum(bool(r["overdue"]) for r in rows),
                "due_soon": sum(1 for r in rows if r["due_at"] and now <= r["due_at"] < soon)}
    return {"person": person, "roles": roles, "covering": covering, "mine": mine, "queue": queue,
            "counts": {"mine": counts(mine), "queue": counts(queue)}}

def _bulk_labels(conn, rows: list[dict]) -> None:
    """Each person step's bulk actions (labels from its form), so the inbox knows which rows can be ticked."""
    labels: dict[tuple, list[str]] = {}
    for r in rows:
        key = (r["definition_id"], r["element"])
        if r["kind"] != "user" or r["requires_human"]:
            r["bulk"] = []
            continue
        if key not in labels:
            f = _definition(conn, r["definition_id"]).nodes[r["element"]].attrs.get("form")
            schema = (form(conn, f) or {}).get("schema") or {} if f else {}
            labels[key] = [b["label"] for b in schema.get("fuzer", {}).get("bulk", [])]
        r["bulk"] = labels[key]

def agent_work(conn, role: str, limit: int = 50, now: datetime | None = None) -> list[dict]:
    """Agent steps waiting for an agent role, claimed or not."""
    now = now or utcnow()
    return conn.execute(_TASKS.format(who="t.role = %(role)s"),
                        {"role": role, "now": now, "limit": limit, "kinds": ["agent"]}).fetchall()

def agent_inbox(conn, role: str, limit: int = 50, now: datetime | None = None) -> dict:
    """What an agent role has to do: its agent steps, person steps it claimed or was handed (not
    requiresHuman ones, which it can't take), and items without a process in its queue."""
    now = now or utcnow()
    args = {"person": f"agent:{role}", "roles": [role], "now": now, "limit": limit, "kinds": ["user"]}
    return {"agent": role, "steps": agent_work(conn, role, limit, now),
            "tasks": conn.execute(_TASKS.format(who="t.assignee = %(person)s"), args).fetchall(),
            "items": conn.execute(_ITEMS.format(who="(i.assignee = %(person)s or (i.assignee is null and "
                                                   "i.role = any(%(roles)s)))"), args).fetchall()}

def _form_of(conn, t: dict) -> dict | None:
    proc = conn.execute("select definition_id from work_process where id = %s", (t["process_id"],)).fetchone()
    key = _definition(conn, proc["definition_id"]).nodes[t["element"]].attrs.get("form")
    return (form(conn, key) or {"key": key, "version": None, "schema": None}) if key else None

def task(conn, token_id: int, now: datetime | None = None) -> dict:
    """One step as the inbox shows it: the step, its item, the process data (the form's starting values),
    the form-js form and its bulk actions, and the evidence so far: what agents found, messages that
    reached the process, comments, and the entities it is about."""
    now = now or utcnow()
    t = conn.execute(
        """
        select t.id as task_id, t.element, t.name, t.kind, t.state, t.role, t.assignee, t.requires_human, t.due_at,
               t.escalated_at, t.error, t.created_at, t.finished_at, t.finished_by, t.claimed_by, t.lease_until,
               t.attempts, t.due_at < %s as overdue, t.process_id, t.item_id, p.process_key, p.definition_id,
               d.version, p.data
        from work_token t join work_process p on p.id = t.process_id join work_definition d on d.id = p.definition_id
        where t.id = %s
        """, (now, token_id)).fetchone()
    if t is None:
        raise NotFound("unknown task")
    item = conn.execute("select id, type, title, subjects, state, outcome, step, priority, assignee, role, due, "
                        "created_by, source, created_at from work_item where id = %s", (t["item_id"],)).fetchone()
    f = _form_of(conn, {"process_id": t["process_id"], "element": t["element"]})
    events = conn.execute("select id, kind, actor, element, data, at from work_event where item_id = %s "
                          "and kind in ('agent_result', 'message', 'commented') order by id desc", (t["item_id"],)).fetchall()
    subjects = conn.execute(
        "select s.id as fuzer_id, e.kind, l.label from unnest(%s::text[]) with ordinality s(id, n) "
        "left join entity e on e.fuzer_id = s.id left join entity_label l on l.fuzer_id = s.id order by s.n",
        (item["subjects"],)).fetchall()
    return {
        "task": {k: v for k, v in t.items() if k != "data"}, "item": item, "data": t["data"],
        "form": f and {"key": f["key"], "version": f["version"],
                       "schema": f["schema"] and {k: v for k, v in f["schema"].items() if k != "fuzer"}},
        "bulk": ((f or {}).get("schema") or {}).get("fuzer", {}).get("bulk", []),
        "evidence": {"agents": [e for e in events if e["kind"] == "agent_result"],
                     "messages": [e for e in events if e["kind"] == "message"],
                     "comments": [e for e in events if e["kind"] == "commented"],
                     "subjects": subjects},
    }

def bulk_complete(conn, token_ids: list[int], by: str, action: str, reason: str | None = None,
                  now: datetime | None = None) -> dict:
    """Complete several person steps with one of their forms' bulk actions (closing false alarms, say).
    Only actions a step's form declares count, and never on a requiresHuman step: those stay one by one.
    Each step is its own transaction; the ones refused say why."""
    done, refused = [], []
    for tid in token_ids:
        try:
            t = conn.execute("select * from work_token where id = %s", (tid,)).fetchone()
            if t is None:
                raise NotFound("unknown task")
            conn.commit()
            if t["requires_human"]:
                raise Forbidden(f"step '{t['name'] or t['element']}' needs a decision of its own")
            f = _form_of(conn, t) if t["kind"] == "user" else None
            bulk = next((b for b in ((f or {}).get("schema") or {}).get("fuzer", {}).get("bulk", [])
                         if b["label"] == action), None)
            if bulk is None:
                raise WorkError(f"step '{t['name'] or t['element']}' has no bulk action '{action}'")
            data = dict(bulk["data"])
            if reason and bulk.get("reason_key"):
                data[bulk["reason_key"]] = reason
            complete(conn, tid, by, data, now=now)
            done.append(tid)
        except WorkError as e:
            conn.rollback()
            refused.append({"task_id": tid, "error": str(e)})
    return {"done": done, "refused": refused}
