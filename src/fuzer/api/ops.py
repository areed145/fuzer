"""The value ranking, board, daily reports, opportunities and studies."""

import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..graph import queries
from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


def _facility(conn, ref: str | None) -> str:
    try:
        fid = queries.resolve_ref(conn, deps.pack(), ref or deps.pack().facility)
    except queries.AmbiguousRef as e:
        raise HTTPException(409, str(e)) from e
    if fid is None:
        raise HTTPException(404, "facility not found")
    return fid


@router.get("/analytics/figures/{kind}")
def live_figure(kind: str, ref: str | None = None, conn=Depends(get_conn)):
    """A complete chart panel built from the latest records, for any page to show."""
    from ..plots import ChartError, analytics
    fid = _facility(conn, ref)
    try:
        return analytics.live(conn, deps.pack(), kind, fid)
    except (ValueError, ChartError) as e:
        raise HTTPException(404 if kind not in analytics.FIGURES else 400, str(e)) from e


@router.get("/analytics/production-efficiency")
def production_efficiency(end: date | None = None, ref: str | None = None, conn=Depends(get_conn)):
    """Fourteen daily samples, ending at the latest facility report unless an end date is supplied."""
    from ..analysis import production_efficiency as pe
    fid = _facility(conn, ref)
    try:
        return pe.production_efficiency(conn, fid, end)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/analytics/flow-capacity")
def flow_capacity(ref: str | None = None, conn=Depends(get_conn)):
    """The latest report day's capacity along the facility's flow network, stage by stage."""
    from ..analysis import flow_capacity as fc
    fid = _facility(conn, ref)
    try:
        return fc.flow_capacity(conn, fid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/value")
def value_ranking(kind: list[str] | None = Query(None), by: Literal["value", "per_effort"] = "value",
                  ref: str | None = None, as_of: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    """Upsets, work, plans and opportunities ranked by the shared value / risk score (#100)."""
    from ..analysis import value
    fid = queries.resolve_ref(conn, deps.pack(), ref) if ref else None
    return value.ranked(conn, deps.pack(), tuple(kind or value.KINDS), as_of, by, fid, min(limit, 500))


@router.get("/board")
def board_page(kind: str | None = None, area: str | None = None, unit: str | None = None, text: str | None = None,
               since: str | None = None, owner: str | None = None, page: int = 1, per_page: int = 50,
               facets: bool = False, conn=Depends(get_conn)):
    """The work board (#99): every outstanding piece of work, ranked by value, one page at a time; with
    `facets`, counts by kind, area and owner for the views."""
    from ..ops import board
    area_id = area if area and area.startswith("fz-") else queries.resolve_ref(conn, deps.pack(), area) if area else None
    unit_id = queries.resolve_ref(conn, deps.pack(), unit) if unit else None
    return board.cards(conn, kind, area_id, unit_id, text, since, max(1, page), min(per_page, 200), owner, facets)


@router.get("/board/history")
def board_history(key: str, conn=Depends(get_conn)):
    from ..ops import board
    return board.history(conn, key)


class Pin(BaseModel):
    key: str
    reason: str = ""
    rank: int | None = None
    value_usd: float | None = None
    by: str = "anonymous"


@router.post("/board/pins")
def board_pin(p: Pin, conn=Depends(get_conn)):
    """Pin a card's rank or value with a reason (both empty: unpin). Applied at the next refresh, which
    this runs."""
    from ..ops import board
    try:
        out = board.pin(conn, p.key, p.by, p.reason, p.rank, p.value_usd)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    board.refresh(conn, deps.pack())
    return out


class Project(BaseModel):
    title: str
    unit: str | None = None
    owner: str | None = None
    due: str | None = None
    benefit_usd: float | None = None
    effort_usd: float | None = None
    scope: str | None = None
    by: str = "anonymous"


@router.post("/board/projects")
def board_project(p: Project, conn=Depends(get_conn)):
    """Add a project to the board."""
    from ..ops import board
    unit_id = queries.resolve_ref(conn, deps.pack(), p.unit) if p.unit else None
    out = board.add_project(conn, p.title, p.by, unit_id, p.owner, p.due, p.benefit_usd, p.effort_usd, p.scope)
    board.refresh(conn, deps.pack())
    return out


@router.get("/reports/daily")
def daily_reports(limit: int = 30, conn=Depends(get_conn)):
    """Days with a daily operations report, newest first (#98)."""
    from ..ops import daily_report
    return daily_report.days(conn, min(limit, 365))


@router.get("/reports/daily/{day}")
def daily_report_day(day: str, version: int | None = None, conn=Depends(get_conn)):
    from ..ops import daily_report
    out = daily_report.get(conn, day, version)
    if out is None:
        raise HTTPException(404, f"no report for {day}")
    return out


@router.get("/opportunities")
def opportunity_list(status: str | None = "open", limit: int = 100, conn=Depends(get_conn)):
    """Opportunities from the daily scan (#102)."""
    from ..ops import opportunities
    return opportunities.listing(conn, status, min(limit, 500))


class OpportunityDecision(BaseModel):
    decision: Literal["accepted", "rejected", "project"]
    by: str = "anonymous"
    reason: str | None = None


@router.post("/opportunities/{key:path}/decision")
def opportunity_decide(key: str, d: OpportunityDecision, conn=Depends(get_conn)):
    """Accept, reject (a reason is required) or turn an opportunity into a board project."""
    from ..ops import board, opportunities
    try:
        out = opportunities.decide(conn, key, d.decision, d.by, d.reason)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    board.refresh(conn, deps.pack())
    return out


class StudyIn(BaseModel):
    kind: Literal["correlate", "correlation_matrix", "modes", "entities", "events", "spe"]
    params: dict[str, Any]
    by: str = "anonymous"
    now: bool | None = None


@router.post("/studies")
def study_start(s: StudyIn, conn=Depends(get_conn)):
    """Run a correlation or clustering study now when it is small, else queue it (#106)."""
    from ..ops import study
    params = dict(s.params)
    for k in ("target", "scope"):
        if isinstance(params.get(k), str) and params[k] != "facility" and not params[k].startswith("fz-"):
            params[k] = queries.resolve_ref(conn, deps.pack(), params[k])
    try:
        return study.start(conn, deps.pack(), s.kind, params, s.by, s.now)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/studies")
def study_list(limit: int = 50, conn=Depends(get_conn)):
    from ..ops import study
    return study.listing(conn, min(limit, 200))


@router.get("/studies/{sid}")
def study_get(sid: int, conn=Depends(get_conn)):
    from ..ops import study
    out = study.get(conn, sid)
    if out is None:
        raise HTTPException(404, f"no study {sid}")
    return out


@router.post("/studies/{sid}/rerun")
def study_rerun(sid: int, by: str = "anonymous", conn=Depends(get_conn)):
    from ..ops import study
    try:
        return study.rerun(conn, deps.pack(), sid, by)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


# ---- exceptions: one router for the IOC and the field ------------------------------------------------------------

class ExceptionDispatch(BaseModel):
    by: str
    to: str
    reason: str | None = None


class FieldReport(BaseModel):
    by: str
    status: Literal["on_site", "done", "could_not_do", "note"]
    note: str | None = None


def _loaded_types() -> None:
    """Exception types are declared by the modules that raise them; load those so labels and audiences resolve."""
    from ..analysis import limits  # noqa: F401
    from ..ops import intake, sre_handoff  # noqa: F401
    from ..sources import alerts  # noqa: F401


@router.get("/exceptions")
def exception_list(state: str | None = "open", type: str | None = None, subject: str | None = None,
                   audience: Literal["ioc", "field"] | None = None, limit: int = Query(200, le=1000),
                   conn=Depends(get_conn)):
    """Exceptions from every source, most valuable then newest first, with the case working each and who has it.
    `state=any` lists every state."""
    from ..ops import exceptions
    _loaded_types()
    return exceptions.listing(conn, state=None if state in (None, "", "any") else state, type=type, subject=subject, audience=audience, limit=limit)


@router.get("/exceptions/field")
def exception_field_jobs(person: str, conn=Depends(get_conn)):
    """A field operator's dispatched jobs (where to go, what was seen, the asset's recent history, the field log)
    and what they are planned onto over the next two weeks."""
    from ..ops import exceptions, plan
    _loaded_types()
    return {"person": person, "jobs": exceptions.field_jobs(conn, person), "coming_up": plan.coming_up(conn, person)}


@router.post("/exceptions/{exception_id}/dispatch")
def exception_dispatch(exception_id: int, d: ExceptionDispatch, conn=Depends(get_conn)):
    """Send an exception to a person or `role:<role>`: its case opens for them, or is handed to them."""
    from ..ops import exceptions
    return _exception_call(conn, exceptions.dispatch, exception_id, d.by, d.to, d.reason)


@router.post("/exceptions/{exception_id}/report")
def exception_report(exception_id: int, r: FieldReport, conn=Depends(get_conn)):
    """A field operator reports back: on site, done, could not do (with why), or a note."""
    from ..ops import exceptions
    return _exception_call(conn, exceptions.report, exception_id, r.by, r.status, r.note)


class ExceptionRaise(BaseModel):
    by: str
    type: str
    key: str
    title: str
    refs: list[str] = []
    priority: float | None = None
    value_usd: float | None = None
    evidence: dict[str, Any] = {}
    source: str | None = None


class ExceptionClear(BaseModel):
    by: str
    type: str
    key: str
    reason: str | None = None


class ExceptionKind(BaseModel):
    by: str
    label: str
    role: str | None = None
    process: str | None = None
    case_type: str | None = None
    audience: list[Literal["ioc", "field"]] = ["ioc"]
    close_case_on_clear: bool = True
    description: str | None = None
    joins: bool = True


class ExceptionSplit(BaseModel):
    by: str
    reason: str | None = None


def _exception_call(conn, fn, *args, **kwargs):
    from ..core.exceptions import ExceptionError
    from ..core.work import WorkError
    _loaded_types()
    try:
        out = fn(conn, *args, **kwargs)
    except (ExceptionError, WorkError) as e:
        conn.rollback()
        raise HTTPException(404 if re.match(r"no exception \d", str(e)) else 400, str(e)) from e
    conn.commit()
    return out


class RefResolve(BaseModel):
    by: str
    ref: str
    fuzer_id: str
    remember: bool = True
    reason: str | None = None


@router.post("/exceptions/{exception_id}/resolve")
def exception_resolve(exception_id: int, d: RefResolve, conn=Depends(get_conn)):
    """Pick the asset an unmatched ref meant (#476); `remember` records it as an alias so the next one matches."""
    from ..ops import exceptions
    from ..sources.catalog import CatalogError
    try:
        return _exception_call(conn, exceptions.resolve_ref, exception_id, d.ref, d.fuzer_id, d.by, d.remember, d.reason)
    except CatalogError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e


@router.post("/exceptions/{exception_id}/split")
def exception_split(exception_id: int, d: ExceptionSplit, conn=Depends(get_conn)):
    """Take an exception that joined another's case back out to a case of its own (a different event)."""
    from ..ops import exceptions
    return _exception_call(conn, exceptions.split, exception_id, d.by, d.reason)


@router.post("/exceptions")
def exception_raise(r: ExceptionRaise, conn=Depends(get_conn)):
    """Raise an exception from another system (or a person): opened once per type and key, matched to entities by
    `refs` (a Fuzer ID, kind:key or source/key), routed by its type. A repeat updates the open one."""
    from ..ops import exceptions
    return _exception_call(conn, exceptions.raise_, r.type, r.key, r.title, r.by, refs=r.refs, priority=r.priority,
                           value_usd=r.value_usd, evidence=r.evidence, source=r.source)


@router.post("/exceptions/clear")
def exception_clear(c: ExceptionClear, conn=Depends(get_conn)):
    """The other system says the condition went away: clear the open exception (and close its case, per its type)."""
    from ..ops import exceptions
    return _exception_call(conn, exceptions.clear, c.type, c.key, c.by, reason=c.reason) or {"cleared": None}


@router.get("/exceptions/types")
def exception_types(conn=Depends(get_conn)):
    """Every exception type, whether a module declares it in code or the deployment defined it as data."""
    from ..ops import exceptions
    _loaded_types()
    return exceptions.type_listing(conn)


@router.get("/exceptions/refs")
def exception_refs(conn=Depends(get_conn)):
    """The reference forms an exception's `refs` are matched by, each with an example from this register."""
    from ..ops import exceptions
    return exceptions.ref_forms(conn)


@router.put("/exceptions/types/{name}")
def exception_type_define(name: str, k: ExceptionKind, conn=Depends(get_conn)):
    """Define or change a type as data, for another system's exceptions."""
    from ..ops import exceptions
    return _exception_call(conn, exceptions.define, name, k.by, k.label, role=k.role, process=k.process,
                           case_type=k.case_type, audience=tuple(k.audience),
                           close_case_on_clear=k.close_case_on_clear, description=k.description, joins=k.joins)


@router.delete("/exceptions/types/{name}")
def exception_type_remove(name: str, conn=Depends(get_conn)):
    """Remove a type defined as data (refused while any of it is open)."""
    from ..ops import exceptions
    if not _exception_call(conn, exceptions.undefine, name):
        raise HTTPException(404, f"no data-defined type {name}")
    return {"removed": name}


# ---- close-outs back to the source system (#466) -------------------------------------------------------------------

class CloseoutDecision(BaseModel):
    by: str
    text: str | None = None
    short_text: str | None = None
    reason: str | None = None


@router.get("/closeouts")
def closeout_list(state: str | None = "draft", item_id: int | None = None, exception_id: int | None = None,
                  conn=Depends(get_conn)):
    """Updates drafted for the systems that raised work the field finished (state draft, sent, discarded or any)."""
    from ..ops import closeout
    return closeout.listing(conn, None if state in (None, "", "any") else state, item_id, exception_id)


@router.get("/closeouts/{closeout_id}")
def closeout_get(closeout_id: int, conn=Depends(get_conn)):
    from ..core.exceptions import ExceptionError
    from ..ops import closeout
    try:
        return closeout.get(conn, closeout_id)
    except ExceptionError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/closeouts/{closeout_id}/approve")
def closeout_approve(closeout_id: int, d: CloseoutDecision, conn=Depends(get_conn)):
    """Send the drafted update (with any edits to its text) to the source system, and finish its case."""
    from ..ops import closeout
    return _exception_call(conn, closeout.approve, closeout_id, d.by, d.text, d.short_text)


@router.post("/closeouts/{closeout_id}/discard")
def closeout_discard(closeout_id: int, d: CloseoutDecision, conn=Depends(get_conn)):
    """Don't send it; its case is cancelled with the reason."""
    from ..ops import closeout
    return _exception_call(conn, closeout.discard, closeout_id, d.by, d.reason)


# ---- shift handover (#467) -----------------------------------------------------------------------------------------

class HandoverNotes(BaseModel):
    by: str
    notes: str


class HandoverAck(BaseModel):
    by: str
    note: str | None = None


class HandoverDraft(BaseModel):
    by: str
    refresh: bool = True


@router.get("/handovers")
def handover_list(limit: int = Query(60, le=500), conn=Depends(get_conn)):
    """Past and current shift handovers, newest first."""
    from ..ops import handover
    return handover.listing(conn, limit)


@router.get("/handovers/current")
def handover_current(conn=Depends(get_conn)):
    """The latest handover, and the shift it covers; null when none was drafted yet."""
    from ..ops import handover
    rows = handover.listing(conn, 1)
    return handover.get(conn, rows[0]["id"]) if rows else None


@router.get("/handovers/{handover_id}")
def handover_get(handover_id: int, conn=Depends(get_conn)):
    from ..core.exceptions import ExceptionError
    from ..ops import handover
    try:
        return handover.get(conn, handover_id)
    except ExceptionError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/handovers/draft")
def handover_draft(d: HandoverDraft, conn=Depends(get_conn)):
    """Draft the handover for the shift change just passed now (or refresh it from live work while unacknowledged)."""
    from ..ops import handover
    out = _exception_call(conn, handover.draft, d.by, force=d.refresh)
    if out is None:
        rows = handover.listing(conn, 1)
        return handover.get(conn, rows[0]["id"]) if rows else None
    return out


@router.post("/handovers/{handover_id}/notes")
def handover_notes(handover_id: int, d: HandoverNotes, conn=Depends(get_conn)):
    """The outgoing lead's notes."""
    from ..ops import handover
    return _exception_call(conn, handover.add_notes, handover_id, d.by, d.notes)


@router.post("/handovers/{handover_id}/acknowledge")
def handover_ack(handover_id: int, d: HandoverAck, conn=Depends(get_conn)):
    """The incoming lead has read it: kept with who and when, and its case closes."""
    from ..ops import handover
    return _exception_call(conn, handover.acknowledge, handover_id, d.by, d.note)


# ---- the integrated plan -----------------------------------------------------------------------------------------

class PlanPropose(BaseModel):
    by: str
    kind: str
    title: str
    start: str
    end: str
    refs: list[str] = []
    source: str | None = None
    source_key: str | None = None
    crew: str | None = None
    crew_hours: float | None = None
    needs_shutdown: bool = False
    deferment_usd: float | None = None
    notes: str | None = None


class PlanChange(BaseModel):
    by: str
    title: str | None = None
    kind: str | None = None
    start: str | None = None
    end: str | None = None
    status: str | None = None
    crew: str | None = None
    crew_hours: float | None = None
    needs_shutdown: bool | None = None
    notes: str | None = None


class TurnaroundBringIn(BaseModel):
    by: str
    item_id: int
    revision: str | None = None


class PlanException(BaseModel):
    by: str
    start: str
    end: str
    crew: str | None = None
    needs_shutdown: bool = False
    notes: str | None = None


def _plan_call(conn, fn, *args, **kwargs):
    from ..core.plan import PlanError
    try:
        out = fn(conn, *args, **kwargs)
    except PlanError as e:
        conn.rollback()
        raise HTTPException(404 if re.match(r"no (plan item|exception) \d", str(e)) else 400, str(e)) from e
    conn.commit()
    return out


@router.get("/plan")
def plan_items(start: str | None = None, end: str | None = None, area: str | None = None, status: str | None = None,
               subject: str | None = None, conn=Depends(get_conn)):
    """The integrated plan in a window (default: a week ago to eight weeks ahead), each item with its clashes
    (same asset, same person), bundling chances (could ride along with a shutdown of its area) and, for work
    starting within 30 days, its assets' spare parts with no stock on record."""
    from ..ops import plan
    return _plan_call(conn, plan.items, start, end, area=area, status=status, subject=subject)


@router.post("/plan")
def plan_propose(p: PlanPropose, conn=Depends(get_conn)):
    """Put an item on the plan; with a source_key, proposing again updates it."""
    from ..ops import plan
    return _plan_call(conn, plan.propose, p.kind, p.title, p.by, p.start, p.end, refs=p.refs, source=p.source,
                      source_key=p.source_key, crew=p.crew, crew_hours=p.crew_hours, needs_shutdown=p.needs_shutdown,
                      deferment_usd=p.deferment_usd, notes=p.notes)


@router.patch("/plan/{item_id}")
def plan_change(item_id: int, c: PlanChange, conn=Depends(get_conn)):
    """Reschedule an item, give it a crew, or move it on (scheduled, in progress, done, cancelled)."""
    from ..ops import plan
    fields = {k: v for k, v in c.model_dump(exclude={"by"}).items() if v is not None}
    return _plan_call(conn, plan.update, item_id, c.by, **fields)


@router.post("/plan/sync")
def plan_sync(conn=Depends(get_conn)):
    """Bring the plan's SAP work orders and maintenance plans up to date with the register now (the rebuild does it
    too)."""
    from ..ops import plan

    def both(c):
        return {**plan.sync_work_orders(c), "maintenance_plans": plan.sync_maintenance_plans(c)}
    return _plan_call(conn, both)


@router.get("/turnarounds")
def turnaround(revision: str | None = None, conn=Depends(get_conn)):
    """A turnaround's scope (orders on the revision, shutdown-only plans in and out of it, bad actors not in it,
    spares out of stock) with its window on the plan, the work that could ride along and shutdown-only work planned
    outside it."""
    from ..ops import turnaround as ta
    return _plan_call(conn, ta.scope, revision)


@router.post("/turnarounds/bring-in")
def turnaround_bring_in(b: TurnaroundBringIn, conn=Depends(get_conn)):
    """Move a plan item into the turnaround's window."""
    from ..ops import turnaround as ta
    return _plan_call(conn, ta.bring_in, b.item_id, b.revision, b.by)


@router.post("/exceptions/{exception_id}/plan")
def exception_plan(exception_id: int, p: PlanException, conn=Depends(get_conn)):
    """Plan an exception rather than work it now: a plan item on its assets, linked to it."""
    from ..ops import plan
    return _plan_call(conn, plan.plan_exception, exception_id, p.by, p.start, p.end, crew=p.crew,
                      needs_shutdown=p.needs_shutdown, notes=p.notes)
