"""The workflow engine (#97): items, tasks, processes, definitions, forms and people."""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import (
    JSONResponse,
    Response,
)
from pydantic import BaseModel, Field

from ..workflow import engine as work
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)
log = logging.getLogger(__name__)


# --- work engine (#97) ---------------------------------------------------------------------------

def work_error(request, e: work.WorkError):
    body = {"detail": str(e)}
    if isinstance(e, work.CheckFailed):
        body["problems"] = [vars(p) for p in e.problems]
    return JSONResponse(body, status_code=e.status)


class WorkBy(BaseModel):
    by: str


class WorkDefinitionIn(BaseModel):
    xml: str
    by: str


class WorkStart(BaseModel):
    process_key: str
    by: str
    data: dict[str, Any] = {}
    title: str | None = None
    subjects: list[str] | None = None
    priority: float | None = None
    correlation_key: str | None = None
    correlate: bool = False   # send to the open process with the same correlation key instead, if there is one


class WorkComplete(BaseModel):
    by: str
    data: dict[str, Any] = {}


class WorkHandOff(BaseModel):
    by: str
    to: str                   # a person, role:<role> or agent:<role>
    reason: str | None = None


class WorkComment(BaseModel):
    by: str
    text: str
    attachments: list[dict[str, Any]] = []


class WorkTransition(BaseModel):
    by: str
    state: str
    reason: str | None = None


class WorkItemIn(BaseModel):
    type: str
    title: str
    by: str
    subjects: list[str] = []
    priority: float | None = None
    assignee: str | None = None
    role: str | None = None
    due: datetime | None = None
    source: str | None = None
    payload: dict[str, Any] = {}


class DraftConfirm(BaseModel):
    by: str = ""


class WorkMessage(BaseModel):
    name: str
    correlation_key: str
    by: str = work.SYSTEM
    payload: dict[str, Any] = {}
    process_key: str | None = None


class WorkDraft(BaseModel):
    process_key: str
    by: str
    from_id: int | None = None  # copy this version (default: the published one) unless a draft is open


class WorkGenerate(BaseModel):
    description: str = Field(min_length=1, max_length=12000)
    by: str = Field(min_length=1, max_length=200)


class WorkMigrate(BaseModel):
    by: str
    definition_id: int
    mapping: dict[str, str] = {}


class WorkBulk(BaseModel):
    by: str
    task_ids: list[int] = Field(max_length=1000)
    action: str               # the label of a bulk action the tasks' form declares
    reason: str | None = None


class WorkPerson(BaseModel):
    roles: list[str]
    by: str = work.SYSTEM


class WorkCover(BaseModel):
    roles: list[str]
    projects: list[str] = []  # empty: every project
    by: str = work.SYSTEM


@router.get("/work/items")
def work_items(state: str | None = "open", type: str | None = None, assignee: str | None = None,
               role: list[str] | None = Query(None), subject: str | None = None, overdue: bool | None = None,
               q: str | None = None, limit: int = 50, offset: int = 0, conn=Depends(get_conn)):
    """Work items ranked by priority, with filters, the total and how many are overdue or due soon."""
    return work.list_items(conn, None if state == "all" else state, type, assignee, role, subject, overdue, q,
                           min(limit, 1000), offset)


@router.post("/work/items")
def work_item_create(i: WorkItemIn, conn=Depends(get_conn)):
    """A task with an owner, a due date and a history. Asked of a role's queue, it runs as the standard task process
    (the role's SLA and escalation, #509); for a named person it is a plain work item."""
    import secrets

    from ..ops import routing
    return routing.open_task(conn, i.type, i.title, i.by, key=f"api:{secrets.token_hex(8)}", role=i.role,
                             assignee=i.assignee, data=i.payload, subjects=i.subjects, priority=i.priority, due=i.due,
                             source=i.source)


@router.get("/work/drafts/{draft_id}")
def work_draft(draft_id: str, conn=Depends(get_conn)):
    """Work the assistant proposed to the person reading it, and what became of it."""
    from ..workflow import work_drafts
    return work_drafts.get(conn, draft_id)


@router.post("/work/drafts/{draft_id}/confirm")
def work_draft_confirm(draft_id: str, m: DraftConfirm, conn=Depends(get_conn)):
    """The person creates the item. Sending this twice gives the same item."""
    from ..workflow import work_drafts
    return work_drafts.confirm(conn, draft_id, m.by)


@router.get("/work/items/{item_id}")
def work_item(item_id: int, conn=Depends(get_conn)):
    """An item with its processes (path taken, open steps), every event in its history, the finding of every
    exception it works (`sources`, P1.1), and whether an agents
    provider is configured: without one no agent worker runs, so an agent step will wait until a person takes it."""
    from ..agents import ledger
    from ..core import llm, settings
    from ..ops import exceptions

    return {**work.item(conn, item_id), "agents_configured": llm.configured("agents"),
            "sources": exceptions.on_case(conn, item_id),
            "agent_spend": {"usd": ledger.case_spend(conn, item_id), "cap_usd": settings.get(conn, "agents.case_cap_usd")}}


@router.get("/work/agent-spend")
def work_agent_spend(since: datetime | None = None, until: datetime | None = None, conn=Depends(get_conn)):
    """Agent spend in a window (P4.4), by agent, by work type and by asset, from each agent step's ledger."""
    from ..agents import ledger

    return ledger.spend(conn, since, until)


@router.post("/work/items/{item_id}/comment")
def work_item_comment(item_id: int, c: WorkComment, conn=Depends(get_conn)):
    return work.comment(conn, item_id, c.by, c.text, c.attachments)


@router.post("/work/items/{item_id}/transition")
def work_item_transition(item_id: int, t: WorkTransition, conn=Depends(get_conn)):
    return work.transition(conn, item_id, t.state, t.by, t.reason)


@router.post("/work/items/{item_id}/hand-off")
def work_item_hand_off(item_id: int, h: WorkHandOff, conn=Depends(get_conn)):
    return work.assign_item(conn, item_id, h.by, h.to, h.reason)


@router.get("/work/inbox")
def work_inbox(person: str, role: list[str] | None = Query(None), conn=Depends(get_conn)):
    """A person's tasks ('mine') and their roles' unclaimed queue, with overdue and due-soon counts.
    Roles default to the ones the person holds."""
    return work.my_work(conn, person, role)


class NotificationsRead(BaseModel):
    person: str
    ids: list[int] | None = None   # None: all of theirs


@router.get("/work/notifications")
def work_notifications(person: str, conn=Depends(get_conn)):
    """What a person has been told (P5.2, #604): work sent, handed back, escalated or assigned to them, and steps
    coming due, newest first, each with a link to the case. Makes any that are due first."""
    from ..workflow import notify

    notify.scan(conn)
    return notify.mine(conn, person)


@router.post("/work/notifications/read")
def work_notifications_read(r: NotificationsRead, conn=Depends(get_conn)):
    from ..workflow import notify

    return notify.mark_read(conn, r.person, r.ids)


@router.get("/work/inbox/fields")
def work_inbox_fields(person: str):
    """A person's Inbox in every field (project) they hold a role in and may open (#497): the overarching view for
    specialists and monitoring staff whose work cuts across fields."""
    from ..core import access, db
    from .security import current

    allowed = None
    if (who := current.get()) is not None:
        with db.connect() as conn:
            allowed = access.projects_for(conn, who)
    return work.work_across(person, allowed)


@router.get("/work/covers")
def work_covers(conn=Depends(get_conn)):
    """The overarching people in this instance, with the fields they cover ([] is every field)."""
    return work.covers(conn)


@router.put("/work/people/{name}/cover")
def work_person_cover(name: str, c: WorkCover, conn=Depends(get_conn)):
    """Make someone overarching (#497): their roles count in every field they cover, on top of each field's roster."""
    return work.set_cover(conn, name, c.roles, c.projects, c.by)


@router.delete("/work/people/{name}/cover")
def work_person_cover_remove(name: str, by: str = work.SYSTEM, conn=Depends(get_conn)):
    return work.remove_cover(conn, name, by)


@router.get("/work/agents/{role}")
def work_agent_queue(role: str, conn=Depends(get_conn)):
    return work.agent_work(conn, role)


@router.get("/work/tasks/{task_id}")
def work_task(task_id: int, conn=Depends(get_conn)):
    """One step with its item, process data, form-js form, bulk actions and evidence, and every exception its case
    works (`sources`: one event reported by several systems, #464)."""
    from ..ops import exceptions

    out = work.task(conn, task_id)
    if isinstance(out, dict) and out.get("item"):
        out["sources"] = exceptions.on_case(conn, out["item"]["id"])
    return out


@router.post("/work/tasks/bulk-complete")
def work_bulk_complete(b: WorkBulk, conn=Depends(get_conn)):
    """Complete several steps with a bulk action their form declares (never requiresHuman steps)."""
    return work.bulk_complete(conn, b.task_ids, b.by, b.action, b.reason)


@router.post("/work/tasks/{task_id}/claim")
def work_claim(task_id: int, b: WorkBy, conn=Depends(get_conn)):
    return work.claim(conn, task_id, b.by)


@router.post("/work/tasks/{task_id}/release")
def work_release(task_id: int, b: WorkBy, conn=Depends(get_conn)):
    return work.release(conn, task_id, b.by)


@router.post("/work/tasks/{task_id}/complete")
def work_complete(task_id: int, c: WorkComplete, conn=Depends(get_conn)):
    """Complete a step with its form data. A requiresHuman step refuses agents (403)."""
    return work.complete(conn, task_id, c.by, c.data)


@router.post("/work/tasks/{task_id}/hand-off")
def work_task_hand_off(task_id: int, h: WorkHandOff, conn=Depends(get_conn)):
    return work.hand_off(conn, task_id, h.by, h.to, h.reason)


@router.post("/work/processes")
def work_start(s: WorkStart, conn=Depends(get_conn)):
    """Start a published process on a new work item (or, with `correlate`, add to the open one)."""
    if s.correlate:
        return work.start_or_correlate(conn, s.process_key, s.data, s.by, title=s.title, subjects=s.subjects,
                                       priority=s.priority)
    return work.start(conn, s.process_key, s.data, s.by, s.title, s.subjects, s.priority, s.correlation_key)


@router.get("/work/processes/{process_id}")
def work_process(process_id: int, conn=Depends(get_conn)):
    return work.process(conn, process_id)


@router.post("/work/processes/{process_id}/migrate")
def work_migrate(process_id: int, m: WorkMigrate, conn=Depends(get_conn)):
    """Move one running process to another published version (explicit, per process)."""
    return work.migrate(conn, process_id, m.definition_id, m.by, m.mapping)


@router.post("/work/processes/{process_id}/cancel")
def work_cancel(process_id: int, t: WorkTransition, conn=Depends(get_conn)):
    return work.cancel(conn, process_id, t.by, t.reason)


@router.post("/work/messages")
def work_message(m: WorkMessage, conn=Depends(get_conn)):
    """Send a message (new evidence, a notification raised, a comment) to the open process with its correlation key."""
    return work.send_message(conn, m.name, m.correlation_key, m.payload, m.by, m.process_key)


@router.get("/work/definitions")
def work_definitions(process_key: str | None = None, conn=Depends(get_conn)):
    return work.definitions(conn, process_key)


@router.post("/work/definitions")
def work_definition_import(d: WorkDefinitionIn, conn=Depends(get_conn)):
    """Import a .bpmn file as draft versions (one per process in it)."""
    return work.import_bpmn(conn, d.xml, d.by)


@router.post("/work/definitions/draft")
def work_definition_draft(d: WorkDraft, conn=Depends(get_conn)):
    """The draft to edit in the modeler: the open one, or a new one copied from a version."""
    return work.draft(conn, d.process_key, d.by, d.from_id)


@router.put("/work/definitions/{definition_id}")
def work_definition_save(definition_id: int, d: WorkDefinitionIn, conn=Depends(get_conn)):
    """Save the modeler's diagram over a draft (published versions never change)."""
    return work.save_draft(conn, definition_id, d.xml, d.by)


@router.post("/work/definitions/{definition_id}/generate")
def work_definition_generate(definition_id: int, d: WorkGenerate, conn=Depends(get_conn)):
    """Propose BPMN for review without writing any definition or starting work."""
    from ..agents import workflow
    from ..core import llm
    from ..workflow.canvas import compile_bpmn
    if not d.description.strip() or not d.by.strip():
        raise HTTPException(400, "a description and person are required")
    row = work._definition_row(conn, definition_id)
    if row["state"] not in ("draft", "checked"):
        raise work.Conflict("start a new draft before generating a workflow")
    published = work._published_keys(conn)
    context = {"process_key": row["process_key"], "name": row["name"], "roles": work.roles(),
               "forms": [f["key"] for f in work.forms(conn)],
               "processes": sorted(published - {row["process_key"]})}
    conn.commit()  # don't hold a database transaction while the agent runs
    try:
        draft = workflow.generate(d.description.strip(), context)
        xml = compile_bpmn(draft, row["process_key"], row["name"] or row["process_key"])
    except workflow.Unavailable as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, "The agent returned an invalid draft. Try a more specific description.") from e
    except Exception as e:
        if not isinstance(e, llm.provider_errors("agents")):
            raise
        status = getattr(e, "status_code", None)
        code = getattr(e, "code", None)
        request_id = getattr(e, "request_id", None)
        log.warning("Workflow drafting provider error: status=%s code=%s request_id=%s",
                    status, code, request_id)
        if status in (401, 403):
            detail = "The Agents provider rejected its credentials. Check the provider API key and permissions in Settings."
        elif status == 404:
            detail = "The Agents provider could not find the configured model or endpoint. Check provider, model and base URL in Settings."
        elif status == 429:
            detail = "The Agents provider rate limit or quota was reached. Check the provider account before retrying."
        elif status == 400:
            detail = "The Agents provider rejected the draft request. Check that the configured model supports tool calling."
        else:
            detail = "The Agents provider is unavailable. Check its configuration and try again later."
        raise HTTPException(503, detail) from e
    return {"xml": xml, "problems": [vars(p) for p in work._problems(conn, {**row, "xml": xml})],
            "steps": len(draft.steps), "connections": len(draft.connections)}


@router.get("/work/definitions/{definition_id}/compare")
def work_definition_compare(definition_id: int, against: int | None = None, conn=Depends(get_conn)):
    """Elements added, removed or changed against another version (default: the published one)."""
    return work.compare(conn, definition_id, against)


@router.get("/work/monitor/{process_key}")
def work_monitor(process_key: str, definition_id: int | None = None, days: int = 30, conn=Depends(get_conn)):
    """Steps waiting and finished at each element of a process, for the diagram overlay."""
    return work.monitor(conn, process_key, definition_id, min(max(days, 1), 365))


@router.get("/work/roles")
def work_roles():
    """The person roles and agent roles a process may name."""
    return work.roles()


@router.post("/work/definitions/{definition_id}/check")
def work_definition_check(definition_id: int, b: WorkBy, conn=Depends(get_conn)):
    return work.check_definition(conn, definition_id, b.by)


@router.post("/work/definitions/{definition_id}/publish")
def work_definition_publish(definition_id: int, b: WorkBy, conn=Depends(get_conn)):
    """Publish a version (workflow_admin role). Refused with 422 and each problem's element if a check fails."""
    return work.publish(conn, definition_id, b.by)


@router.get("/work/definitions/{definition_id}/bpmn")
def work_definition_export(definition_id: int, conn=Depends(get_conn)):
    return Response(work.export_bpmn(conn, definition_id), media_type="application/xml")


@router.get("/work/forms")
def work_forms(conn=Depends(get_conn)):
    return work.forms(conn)


@router.get("/work/forms/{key}")
def work_form(key: str, conn=Depends(get_conn)):
    if (f := work.form(conn, key)) is None:
        raise HTTPException(404, f"no form '{key}'")
    return f


@router.post("/work/forms")
def work_form_import(schema: dict[str, Any], by: str, conn=Depends(get_conn)):
    """A form-js schema as the next version of the form its `id` names."""
    return work.import_form(conn, schema, by)


@router.get("/work/people")
def work_people(conn=Depends(get_conn)):
    return work.people(conn)


@router.put("/work/people/{name}")
def work_person(name: str, p: WorkPerson, conn=Depends(get_conn)):
    return work.set_person(conn, name, p.roles, p.by)


@router.delete("/work/people/{name}")
def work_person_remove(name: str, by: str = work.SYSTEM, conn=Depends(get_conn)):
    """Take a person off the roster; what they had claimed goes back to its role's queue."""
    return work.remove_person(conn, name, by)


@router.get("/work/roles/summary")
def work_role_summary(conn=Depends(get_conn)):
    """Each person role: the processes with steps for it, who holds it and what waits in its queue."""
    return work.role_summary(conn)


@router.post("/work/tick")
def work_tick(conn=Depends(get_conn)):
    """Fire due timers and escalate overdue tasks now (the Dagster schedule does this every few minutes), and draft
    the shift handover when a shift change has passed."""
    from ..ops import handover

    out = work.tick(conn)
    drafted = handover.at_shift_change(conn)
    conn.commit()
    return {**out, "handover": drafted["id"] if drafted else None}



# --- roles, routing and escalation; cases; process statistics ----------------------------------------

class RoleIn(BaseModel):
    by: str
    kind: str | None = None
    description: str | None = None
    default_sla: str | None = None
    escalate_to: str | None = None
    assignment: str | None = None


class AwayIn(BaseModel):
    by: str
    until: datetime | None = None
    delegate: str | None = None
    note: str | None = None


class ItemChange(BaseModel):
    by: str
    reason: str | None = None
    priority: float | None = None
    due: datetime | None = None


class ItemReassign(BaseModel):
    by: str
    to: str
    reason: str | None = None


def _role_error(fn, *args, **kwargs):
    from ..workflow import roles

    try:
        out = fn(*args, **kwargs)
    except roles.RoleError as e:
        raise HTTPException(400, str(e)) from e
    return out


@router.get("/work/roles/catalog")
def work_roles_catalog(conn=Depends(get_conn)):
    """Every role (person and agent): description, default SLA, where overdue steps go, how steps are assigned,
    members (and who's away), the processes that use it, and what waits in its queue."""
    from ..workflow import roles

    out = roles.listing(conn, work.utcnow())
    conn.commit()
    return out


@router.put("/work/roles/{name}")
def work_role_set(name: str, r: RoleIn, conn=Depends(get_conn)):
    from ..workflow import roles

    values = {k: v for k, v in r.model_dump(exclude={"by"}).items() if v is not None or k in r.model_fields_set}
    out = _role_error(roles.set_role, conn, name, r.by, **values)
    conn.commit()
    return out


@router.delete("/work/roles/{name}")
def work_role_delete(name: str, by: str, conn=Depends(get_conn)):
    from ..workflow import roles

    out = _role_error(roles.delete_role, conn, name, by)
    conn.commit()
    return out


@router.put("/work/people/{name}/away")
def work_person_away(name: str, a: AwayIn, conn=Depends(get_conn)):
    """Away until a time, with who covers (until empty: back)."""
    from ..workflow import roles

    out = _role_error(roles.set_away, conn, name, a.by, a.until, a.delegate, a.note)
    conn.commit()
    return out


@router.get("/work/workload")
def work_workload(conn=Depends(get_conn)):
    """Steps per person and per role queue (overdue, escalated), who's away, agent queues and recent agent failures."""
    from ..workflow import roles

    return roles.workload(conn, work.utcnow())


@router.get("/work/stats")
def work_stats(days: int = 30, conn=Depends(get_conn)):
    """Per published process: running, waiting and overdue steps, finished, cycle time, SLA breaches."""
    from ..workflow import roles

    return roles.process_stats(conn, work.utcnow(), min(max(days, 1), 365))


@router.get("/work/instances")
def work_instances(process_key: str, state: str | None = "running", limit: int = 200, conn=Depends(get_conn)):
    """A process's instances with their open steps (who has them, due, overdue, escalated) and age."""
    from ..workflow import roles

    return roles.instances(conn, process_key, work.utcnow(), None if state == "all" else state, min(limit, 1000))


@router.post("/work/items/{item_id}/set")
def work_item_set(item_id: int, c: ItemChange, conn=Depends(get_conn)):
    """Change a case's priority or due date (a running process: its open steps' due times)."""
    return work.set_item(conn, item_id, c.by, c.reason, c.priority, c.due)


@router.post("/work/items/{item_id}/reassign")
def work_item_reassign(item_id: int, r: ItemReassign, conn=Depends(get_conn)):
    """Give everything open on a case to a person or a role's queue (role:<role>)."""
    return work.reassign_item(conn, item_id, r.by, r.to, r.reason)


@router.get("/work/definitions/{definition_id}/routing")
def work_routing(definition_id: int, conn=Depends(get_conn)):
    """Each step of a process version as it routes: role, SLA (step or role default), escalation chain, assignment."""
    from ..workflow import roles

    return roles.routing(conn, definition_id)
