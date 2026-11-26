"""The value ranking, board, daily reports, opportunities and studies."""

import re
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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
def live_figure(kind: str, ref: str | None = None, revision: str | None = None, well: str | None = None,
                conn=Depends(get_conn)):
    """A complete chart panel built from the latest records, for any page to show (a turnaround's by its revision, a
    well's trajectory by the well)."""
    from ..plots import ChartError, analytics
    fid = _facility(conn, ref)
    opts = {k: v for k, v in (("revision", revision), ("well", well)) if v}
    try:
        return analytics.live(conn, deps.pack(), kind, fid, **opts)
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
    """One day's report; "latest" is the newest day with one, and a day that isn't a date has none (#1141)."""
    from datetime import date

    from ..ops import daily_report
    if day == "latest":
        newest = conn.execute("select max(day) as day from daily_ops_report").fetchone()["day"]
        out = daily_report.get(conn, str(newest), version, deps.pack()) if newest else None
    else:
        try:
            date.fromisoformat(day)
        except ValueError:
            raise HTTPException(404, f"no report for {day}")
        out = daily_report.get(conn, day, version, deps.pack())
    if out is None:
        raise HTTPException(404, f"no report for {day}")
    return daily_report.for_people(out)


class ReportDraft(BaseModel):
    by: str = "agent:reporter"


@router.get("/reports/regulatory")
def regulatory_reports(limit: int = 50, conn=Depends(get_conn)):
    """The regulatory report templates, and each report drafted, newest period first (P7.6)."""
    from ..ops import reports
    return reports.listing(conn, min(limit, 500))


@router.get("/reports/regulatory/{template}/{period}")
def regulatory_report(template: str, period: str, version: int | None = None, conn=Depends(get_conn)):
    """One version of a regulatory report (the latest by default): its sections, each figure with its basis and the
    rows it is made of, the figures marked as resting on estimated or missing data, and its review."""
    from ..ops import reports
    try:
        out = reports.get(conn, template, period, version)
    except reports.ReportError as e:
        raise HTTPException(404, str(e)) from e
    if out is None:
        raise HTTPException(404, f"no {template} report for {period}")
    return out


@router.post("/reports/regulatory/{template}/{period}/draft")
def regulatory_report_draft(template: str, period: str, body: ReportDraft, conn=Depends(get_conn)):
    """Draft the report from its records now: a new version when its figures changed, and its review case."""
    from ..ops import reports
    try:
        return reports.assemble(conn, deps.pack(), template, period, by=body.by)
    except reports.ReportError as e:
        conn.rollback()
        raise HTTPException(404 if "template" in str(e) else 400, str(e)) from e


class ReportApprove(BaseModel):
    by: str
    version: int | None = None
    note: str | None = None


class ReportSubmit(BaseModel):
    by: str
    system: str | None = None


def _report_call(conn, fn, *args, **kw):
    from ..core import gates
    from ..ops import reports, writeback
    try:
        return fn(*args, **kw)
    except gates.GateRefused as e:
        conn.rollback()
        raise HTTPException(403, str(e)) from e
    except (reports.ReportError, writeback.WritebackError) as e:
        conn.rollback()
        raise HTTPException(404 if "no report template" in str(e) else 409, str(e)) from e


@router.post("/reports/regulatory/{template}/{period}/approve")
def regulatory_report_approve(template: str, period: str, body: ReportApprove, conn=Depends(get_conn)):
    """A named person approves the latest version (a fixed gate), with a note when figures are marked."""
    from ..ops import reports
    return _report_call(conn, reports.approve, conn, template, period, body.by, body.version, body.note)


@router.get("/reports/regulatory/{template}/{period}/export")
def regulatory_report_export(template: str, period: str, version: int | None = None, conn=Depends(get_conn)):
    """A version as a CSV file (filename, content, sha256); a draft says DRAFT."""
    from ..ops import reports
    return _report_call(conn, reports.export, conn, template, period, version)


@router.post("/reports/regulatory/{template}/{period}/submit")
def regulatory_report_submit(template: str, period: str, body: ReportSubmit, conn=Depends(get_conn)):
    """Send the approved version through the write-back queue to a system, or record the exported file as filed."""
    from ..ops import reports
    return _report_call(conn, reports.submit, conn, template, period, body.by, body.system)


class Obligation(BaseModel):
    name: str
    template: str
    by: str
    regulator: str | None = None
    due_days: int = 15
    lead_days: int = 10
    draft_ahead: bool = True
    active: bool = True
    note: str | None = None


@router.get("/reports/obligations")
def report_obligations(conn=Depends(get_conn)):
    """Every regulatory obligation, and the calendar of what is open now: each period's due date and its report."""
    from ..ops import reports
    return reports.obligations(conn)


@router.post("/reports/obligations")
def report_obligation_set(body: Obligation, conn=Depends(get_conn)):
    """Declare or change an obligation (P7.6c)."""
    from ..ops import reports
    return _report_call(conn, reports.set_obligation, conn, body.name, body.template, body.by, regulator=body.regulator,
                        due_days=body.due_days, lead_days=body.lead_days, draft_ahead=body.draft_ahead,
                        active=body.active, note=body.note)


@router.get("/opportunities")
def opportunity_list(status: str | None = "open", limit: int = 100, conn=Depends(get_conn)):
    """Opportunities from the daily scan (#102)."""
    from ..ops import opportunities
    return opportunities.listing(conn, status, min(limit, 500))


@router.get("/opportunities/{key:path}")
def opportunity_get(key: str, conn=Depends(get_conn)):
    """One opportunity with its working: its value and how it is made, its moves, and the tests behind it."""
    from ..ops import opportunities
    try:
        return opportunities.get(conn, key, deps.pack())
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


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
    kind: Literal["correlate", "correlation_matrix", "modes", "entities", "events", "spe", "cascades"]
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
    study.sample(conn, deps.pack())  # a demo facility opens with a few studies to read (#1078)
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
    override: str | None = None   # why it goes though it isn't ready (P5.5): required when it isn't
    due: datetime | None = None   # when it is wanted back (red team #23): the case's open steps are due then


class Closeout(BaseModel):
    by: str
    disposition: Literal["real", "false_alarm", "duplicate", "data_problem", "done", "log_only"]
    failure_mode: str | None = None
    mechanism: str | None = None
    cause: str | None = None
    activity: str | None = None
    outcome: str | None = None
    note: str | None = None
    duplicate_of: int | None = None
    suggested: dict[str, Any] | None = None   # what the form was pre-filled with, kept beside what was confirmed
    follow_up_to: str | None = None           # contained or not fixed: who the follow-up goes to (a person or role:<role>)


@router.get("/work/items/{item_id}/closeout")
def case_closeout(item_id: int, conn=Depends(get_conn)):
    """A case's close-out (P6.1a): the record if any, what to pre-fill and the controlled lists."""
    from ..ops import disposition
    try:
        return disposition.form(conn, deps.pack(), item_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/work/items/{item_id}/closeout")
def case_closeout_record(item_id: int, c: Closeout, conn=Depends(get_conn)):
    """Record what a case was and close it (P6.1a): a real problem needs failure mode, cause and action. Answers with
    the case as it now stands (`item`), so the page drops what a closed case doesn't offer at once (tour F12)."""
    from ..ops import disposition
    fields = c.model_dump(exclude={"by", "disposition"})
    try:
        row = disposition.record(conn, deps.pack(), item_id, c.by, c.disposition, **fields)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    conn.commit()
    return {**row, "item": _case_now(conn, item_id)}


class Recheck(BaseModel):
    by: str
    held: bool
    note: str | None = None


@router.post("/work/items/{item_id}/recheck")
def case_recheck(item_id: int, r: Recheck, conn=Depends(get_conn)):
    """Did the fix hold (P6.1b)? A no reopens the case as a repeat."""
    from ..ops import disposition
    try:
        row = disposition.answer_recheck(conn, item_id, r.by, r.held, r.note)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    conn.commit()
    return row


@router.get("/plant/scorecard/fleet")
def plant_scorecard_fleet(days: int = 30):
    """Across assets (P7.5b): the scorecard for every project the signed-in person may open, the rest named as left
    out (D6). Without sign-in (a local instance) every project is read."""
    from ..core import access, db
    from ..ops import fleet
    from .security import current
    if days < 1 or days > 366:
        raise HTTPException(400, "a period is 1 to 366 days")
    allowed = None
    if (who := current.get()) is not None:
        with db.connect() as conn:
            allowed = access.projects_for(conn, who)
    return fleet.scorecards(allowed, days)


@router.get("/plant/scorecard")
def plant_scorecard(until: date | None = None, days: int = 30, conn=Depends(get_conn)):
    """How the plant is running (P7.3a, #353): the KPIs for the period against the one before, each citing its window,
    unit, source and records; unknown where the records can't say."""
    from ..analysis import kpis
    try:
        return kpis.scorecard(conn, until.isoformat() if until else None, days)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/plant/forecast-vs-actual")
def plant_forecast_vs_actual(well: str | None = None, days: int = 365, conn=Depends(get_conn)):
    """Each producing well's own decline forecast against its tests in the window, ranked by the gap, with a field
    line and the wells too thin to fit (P7.3b, #336)."""
    from ..analysis import forecast_actual as fa
    out = fa.forecast_vs_actual(conn, well, days=max(30, min(days, 1095)))
    for w in out["wells"]:
        w.pop("sufficiency", None)
    return out


@router.get("/plant/cost-of-unreliability")
def plant_cost_of_unreliability(days: int = 365, conn=Depends(get_conn)):
    """Per unit, oil lost to the downtime it caused at the pack's price beside its corrective order cost, each in its
    currency, ranked; never summed across currencies (P7.3c, #352)."""
    from ..analysis import costs
    return costs.unreliability(conn, deps.pack(), days=max(30, min(days, 1095)))


@router.get("/plant/intervention-gains")
def plant_intervention_gains(days: int = 730, conn=Depends(get_conn)):
    """Every recorded well intervention with its oil gain net of the decline before it; unknown where a side has no
    test (P7.5a, #340)."""
    from ..analysis import interventions as iv
    return iv.gains(conn, days=max(30, min(days, 1825)))


@router.get("/plant/accounting")
def plant_accounting(days: int = 365, conn=Depends(get_conn)):
    """Each month's daily allocated oil and gas against production accounting, and operating cost per accounted barrel,
    each citing both sources and periods (P7.5c, #372)."""
    from ..analysis import accounting
    days = max(31, min(days, 1825))
    return {"reconciliation": accounting.reconcile(conn, days=days), "unit_cost": accounting.unit_cost(conn, days=days)}


@router.get("/plant/work-execution")
def plant_work_execution(days: int = 730, conn=Depends(get_conn)):
    """Schedule compliance and repeat repair rate per quarter, from the work order records (#373)."""
    from ..analysis import work_execution as we
    return we.trend(conn, days=max(90, min(days, 1825)))


@router.get("/plant/backlog-burn")
def plant_backlog_burn(days: int = 365, conn=Depends(get_conn)):
    """Work orders created against closed each month, the open backlog at each month's end, and the notification-to-
    order lag (#348)."""
    from ..analysis import backlog
    return backlog.burn(conn, days=max(31, min(days, 1825)))


@router.get("/plant/allocation-factor")
def plant_allocation_factor(days: int = 365, conn=Depends(get_conn)):
    """Allocated oil over the producing wells' tested offer each month, shut-in wells taken out by downtime (#338)."""
    from ..analysis import well_test_factor
    return well_test_factor.trend(conn, days=max(31, min(days, 1825)))


@router.get("/plant/gas-balance")
def plant_gas_balance(days: int = 365, conn=Depends(get_conn)):
    """The daily gas balance with its closure gap and the stretches it stayed open (#369)."""
    from ..analysis import gas_balance as gb
    return gb.daily(conn, days=max(7, min(days, 1825)))


@router.get("/plant/reservoir-pressure")
def plant_reservoir_pressure(conn=Depends(get_conn)):
    """Productivity index per surveyed well and the one-tank material balance, with their assumptions (#364)."""
    from ..analysis import reservoir_pressure as rp
    return {"productivity": rp.productivity(conn), "material_balance": rp.material_balance(conn)}


@router.get("/plant/vibration-spectra")
def plant_vibration_spectra(tag: str | None = None, conn=Depends(get_conn)):
    """Condition-monitoring spectra read against their fault frequencies, per measurement point (#367)."""
    from ..analysis import vibration_spectra as vs
    return vs.spectra(conn, tag)


@router.get("/plant/co2-corrosion")
def plant_co2_corrosion(days: int = 365, conn=Depends(get_conn)):
    """CO2 corrosion screening: predicted against measured corrosion rate per line (#368)."""
    from ..analysis import co2_corrosion
    return co2_corrosion.screen(conn, days=max(90, min(days, 1825)))


@router.get("/plant/ili")
def plant_ili(line: str | None = None, conn=Depends(get_conn)):
    """ILI anomalies: growth between runs and remaining strength against MOP, per line (#366)."""
    from ..analysis import ili
    return ili.anomalies(conn, line)


@router.get("/plant/ldar")
def plant_ldar(days: int = 730, conn=Depends(get_conn)):
    """LDAR surveys: leaks found, repeat leakers, and coverage with unsurveyed equipment unknown (#371)."""
    from ..analysis import ldar
    return ldar.surveys(conn, days=max(31, min(days, 1825)))


@router.get("/plant/water-injection")
def plant_water_injection(days: int = 730, conn=Depends(get_conn)):
    """Voidage replacement month by month and each water injector's Hall plot (#365)."""
    from ..analysis import water_injection as wi
    days = max(62, min(days, 1825))
    return {"voidage": wi.voidage(conn, days=days), "hall": wi.hall(conn, days=days)}


@router.get("/plant/gas-lift-stability")
def plant_gas_lift_stability(days: int = 3, conn=Depends(get_conn)):
    """Gas lifted wells whose casing pressure heads, or whose lift gas or wellhead pressure cycles (#341)."""
    from ..analysis import gas_lift_stability as gls
    return gls.screen(conn, max(1, min(days, 14)))


@router.get("/plant/quality")
def plant_quality(days: int = 365, conn=Depends(get_conn)):
    """Each lab analyte at each sample point over the window, gaps in sampling, and drifts first (#342)."""
    from ..analysis import quality_trend as qt
    return qt.trends(conn, days=max(30, min(days, 1825)))


@router.get("/plant/alarm-performance")
def plant_alarm_performance(days: int = 2, conn=Depends(get_conn)):
    """The alarm system against ISA-18.2 and EEMUA 191: its rate, floods and bad actors, and the alarm cases open now
    (P3.10b, #840). Measures and work for the alarm owner, never alarms to acknowledge."""
    from ..analysis import alarm_performance
    return alarm_performance.summary(conn, deps.pack(), max(1, min(days, 7)))


@router.get("/plant/well-screen")
def plant_well_screen(conn=Depends(get_conn)):
    """Wells whose water cut stepped up or whose GOR climbed, and how old each well's latest test is (#339)."""
    from ..analysis import well_diagnostics as wd
    return wd.screen(conn)


@router.get("/work/performance")
def work_performance(conn=Depends(get_conn)):
    """How the work is running (P7.2a): this week against the last four, per queue and work type, with the case ids
    behind each count."""
    from ..ops import performance
    return performance.measures(conn)


@router.get("/work/performance/detectors")
def work_performance_detectors(conn=Depends(get_conn)):
    """How detection is performing (P7.4a): per detector, its cases and close-outs, hours to case, the value it
    confirmed and how its confirmed cases were found, with whether it earns its keep or needs tuning."""
    from ..ops import earning
    return earning.detectors(conn)


@router.get("/closeout/missing")
def closeout_missing(conn=Depends(get_conn)):
    """Closed cases with no close-out yet (P6.1a)."""
    from ..ops import disposition
    return disposition.missing(conn)


class FieldReport(BaseModel):
    by: str
    status: Literal["on_site", "done", "could_not_do", "note"]
    note: str | None = None
    reading: dict[str, Any] | None = None  # {value, unit, what}
    photos: list[str] = []                 # data URLs, resized on the phone
    client_id: str | None = None           # a report the phone kept offline: applied once (P5.3c)
    at: str | None = None                  # when it was made, on the phone
    override: str | None = None            # why it starts without a valid permit (P5.11a): required when it has none
    permit_in_hand: bool | None = None     # confirmed on site where no permit records are loaded (#742): kept, never required


def _loaded_types() -> None:
    """Exception types are declared by the modules that raise them; load those so labels and audiences resolve."""
    from ..analysis import limits, records, rotating, trips  # noqa: F401
    from ..ops import intake, routines, sre_handoff  # noqa: F401
    from ..sources import alerts  # noqa: F401


@router.get("/exceptions")
def exception_list(state: str | None = "open", type: str | None = None, subject: str | None = None,
                   audience: Literal["ioc", "field"] | None = None, at: str | None = None,
                   limit: int = Query(200, le=1000), closed_since: datetime | None = None, conn=Depends(get_conn)):
    """Exceptions from every source, most valuable then newest first, with the case working each and who has it.
    `state=any` lists every state; `closed_since` keeps the ones closed or cleared from then on; `at` keeps those
    gathered at a place on Home's map (a platform, pad, facility; comma-separated place ids)."""
    from ..ops import attention, exceptions
    _loaded_types()
    rows = exceptions.listing(conn, state=None if state in (None, "", "any") else state, type=type, subject=subject,
                              audience=audience, limit=1000 if at else limit, closed_since=closed_since)
    return attention.only_at(conn, rows, at)[:limit] if at else rows


@router.get("/work/counts")
def work_counts(person: str | None = None, conn=Depends(get_conn)):
    """The headline work figures, counted over the List's own rows (ops.worklist.figures): open, overdue, each stage,
    and what waits on the person. Each equals the List with the matching filter, and every page that shows one of these
    (the Work badge, Home, My work, the handover) reads it here."""
    from ..ops import worklist
    _loaded_types()
    return worklist.figures(conn, person)


@router.get("/cases")
def case_list(state: Literal["open", "closed", "all"] = "open", kind: str | None = None, stage: str | None = None,
              holder: str | None = None, queue: str | None = None, waiting_on: str | None = None, overdue: bool | None = None,
              subject: str | None = None, at: str | None = None, source: str | None = None,
              facility: str | None = None, area: str | None = None, asset: str | None = None, within: str | None = None,
              q: str | None = None, sort: Literal["value", "due", "opened", "updated", "title"] = "value",
              limit: int = Query(500, le=5000), person: str | None = None, conn=Depends(get_conn)):
    """All the work as one list: every event and every case, filtered on any dimension (kind, stage, holder, queue,
    source, facility, area, asset as comma lists; waiting_on a person; overdue; subject; at places; within entities;
    words) and sorted. The List and the Board read
    it; a preset is only a set of these filters. With the rows: how many match, and per dimension how many match on
    each value with that dimension's own filter left out."""
    from ..ops import worklist
    _loaded_types()
    return worklist.rows(conn, state=state, kind=kind, stage=stage, holder=holder, queue=queue, waiting_on=waiting_on,
                         facility=facility, area=area, asset=asset, within=within,
                         overdue=overdue, subject=subject, at=at, source=source, q=q, sort=sort, limit=limit,
                         person=person)


@router.get("/attention")
def attention(conn=Depends(get_conn)):
    """Where attention is needed: open exceptions gathered at the located places they are about (platforms, pads,
    facilities), most urgent first, with counts, top items and crews out there. Read only."""
    from ..ops import attention as at
    return at.hotspots(conn)


@router.get("/attention/places")
def attention_places(ids: str = Query(..., max_length=20000), conn=Depends(get_conn)):
    """Where each entity (comma-separated ids, at most 200) sits on Home's map: the located platform, pad or
    facility its chain leads to, else the nearest located thing. Ids with no location are left out. Read only."""
    from ..ops import attention as at
    wanted = [i for i in dict.fromkeys(ids.split(",")) if i][:200]
    return at.locate(conn, wanted)


class SuppressionIn(BaseModel):
    refs: list[str]
    start: str
    end: str
    reason: str
    by: str


class SuppressionEnd(BaseModel):
    by: str


@router.get("/exceptions/suppressed")
def exception_suppressed(conn=Depends(get_conn)):
    """What planned work, shutdowns and people's windows are holding back now, and every finding they held back,
    counted (P3.5b)."""
    from ..ops import suppression
    return {"windows": suppression.windows(conn), **suppression.found(conn)}


@router.post("/exceptions/suppressions")
def exception_suppress(s: SuppressionIn, conn=Depends(get_conn)):
    """A person's window: a machine's detections about these assets from `start` to `end` are counted, not opened."""
    from ..core.exceptions import ExceptionError
    from ..ops import suppression
    try:
        return suppression.add(conn, s.refs, s.start, s.end, s.reason, s.by)
    except (ExceptionError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@router.post("/exceptions/suppressions/{suppression_id}/end")
def exception_suppression_end(suppression_id: int, m: SuppressionEnd, conn=Depends(get_conn)):
    """End a person's window now; what it held back stays recorded."""
    from ..core.exceptions import ExceptionError
    from ..ops import suppression
    try:
        return suppression.end(conn, suppression_id, m.by)
    except ExceptionError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/exceptions/field")
def exception_field_jobs(person: str, brief: bool = False, conn=Depends(get_conn)):
    """A field operator's dispatched jobs (where to go, what was seen, the asset's recent history, the field log)
    and what they are planned onto over the next two weeks. `brief` gives each job without what is looked up per
    job, for a page that only counts them."""
    from ..ops import exceptions, plan
    _loaded_types()
    return {"person": person, "jobs": exceptions.field_jobs(conn, person, brief),
            "coming_up": plan.coming_up(conn, person)}


@router.post("/exceptions/{exception_id}/dispatch")
def exception_dispatch(exception_id: int, d: ExceptionDispatch, conn=Depends(get_conn)):
    """Send an exception to a person or `role:<role>`: its case opens for them, or is handed to them. A job that is
    not ready to dispatch (P5.5) goes only with a reason, kept on its case's history."""
    from ..ops import exceptions, readiness
    try:
        ready = readiness.for_exception(conn, exception_id, d.to)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    override = (d.override or "").strip()
    row = conn.execute("select item_id from exception_record where id = %s", (exception_id,)).fetchone()
    if d.by == d.to and row and row["item_id"]:
        # A field operator taking a job the IOC already sent to their queue: readiness was the sender's call (with
        # its reason, when it went though not ready), and the operator has no say on spares or crew.
        return _exception_call(conn, exceptions.dispatch, exception_id, d.by, d.to, d.reason)
    if not ready["ready"] and not override:
        raise HTTPException(409, "Not ready to dispatch: " + "; ".join(ready["blockers"])
                            + ". Say why it should go anyway.")
    out = _exception_call(conn, exceptions.dispatch, exception_id, d.by, d.to, d.reason,
                          override=override if not ready["ready"] else None,
                          blockers=ready["blockers"])
    if d.due and out.get("item_id"):
        from ..core import work
        _exception_call(conn, work.set_due, out["item_id"], d.by, d.due, "wanted back by then when sent")
    return out


@router.get("/exceptions/{exception_id}/readiness")
def exception_readiness(exception_id: int, to: str | None = None, conn=Depends(get_conn)):
    """Is this job ready to dispatch (P5.5)? Ready, or each blocker by name, from its equipment's records; for a send
    to `to`, the checks that send needs and whether they hold it (#1401)."""
    from ..ops import readiness
    try:
        return readiness.for_exception(conn, exception_id, to)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


class OptionChoice(BaseModel):
    by: str
    option: Literal["dispatch", "window", "shelve", "escalate", "watch"]
    reason: str | None = None     # required when it is not the recommended option
    to: str | None = None         # dispatch: a person or role:<role>; the operator queue when left out
    ask: str | None = None        # dispatch: what to check or do (all the field sees)
    due: datetime | None = None   # dispatch: when it is wanted back
    priority: float | None = None
    override: str | None = None   # dispatch: why it goes though not ready (P5.5)


@router.get("/exceptions/{exception_id}/options")
def exception_options(exception_id: int, conn=Depends(get_conn)):
    """The options for an event (P5.20): dispatch now, ride along with the next visit, shelve, escalate; each with when
    it would be done, the production deferred until then, the risk of waiting, readiness and clashes, one recommended
    with its reason."""
    from ..ops import options
    _loaded_types()
    try:
        return options.for_exception(conn, exception_id, viewer=_viewer_roles(conn))
    except options.OptionsError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/work/items/{item_id}/options")
def case_options(item_id: int, conn=Depends(get_conn)):
    """The options for the event a case works (P5.20)."""
    from ..ops import options
    _loaded_types()
    return options.for_case(conn, item_id, viewer=_viewer_roles(conn))


def _viewer_roles(conn) -> list[str] | None:
    """The routing roles the signed-in person acts as, so an option never escalates to them (#1381); None signed out."""
    from ..core import work
    from .security import current
    who = current.get()
    return work.roles_of(conn, who.actor) if who is not None else None


@router.post("/exceptions/{exception_id}/options/choose")
def exception_option_choose(exception_id: int, c: OptionChoice, conn=Depends(get_conn)):
    """Carry out one of an event's options and record the choice, the recommendation and the reason (P5.20). Answers
    with its case as it now stands (`item`), which the page shows at once (P5.29d)."""
    from ..ops import options
    out = _exception_call(conn, options.choose, exception_id, c.by, c.option, c.reason, to=c.to, ask=c.ask,
                          due=c.due, priority=c.priority, override=c.override)
    return {**out, "item": _case_now(conn, out["event"]["item_id"])}


def _case_now(conn, item_id: int | None) -> dict | None:
    """The case an action changed, as GET /work/items reads its processes, steps, holder and subjects (P5.29d): a page
    shows the action's result with its confirmation, not after reading everything again on a busy server."""
    from ..workflow import engine
    return engine.item(conn, item_id) if item_id else None


@router.get("/options/acceptance")
def option_acceptance(days: int = Query(90, ge=1, le=365), conn=Depends(get_conn)):
    """How often the recommended option was taken, per work type (P5.20)."""
    from ..ops import options
    _loaded_types()
    return options.acceptance(conn, days)


@router.get("/rounds")
def rounds_this_shift(conn=Depends(get_conn)):
    """This shift's operator rounds (P5.4), each with how far it has got; opens them on the first ask of a shift."""
    from ..ops import rounds
    out = rounds.runs(conn)
    conn.commit()
    return out


@router.get("/rounds/{run_id}")
def round_run(run_id: int, conn=Depends(get_conn)):
    """A round's points in order, each with what was taken on it."""
    from ..ops import rounds
    return _exception_call(conn, rounds.run, run_id)


class RoundTaken(BaseModel):
    point: str
    by: str
    value: float | None = None
    passed: bool | None = None
    note: str | None = None
    client_id: str | None = None           # a reading the phone kept offline: applied once (P5.17d)
    at: str | None = None                  # when the phone took it


@router.post("/rounds/{run_id}/readings")
def round_reading(run_id: int, t: RoundTaken, conn=Depends(get_conn)):
    """An operator's reading or check on a point of a round (P5.4b): kept as a measurement on its asset, and outside
    its limits (or a failed check) a round_finding that routes a case to the IOC."""
    from ..ops import rounds
    out = _exception_call(conn, rounds.take, run_id, t.point, t.by, value=t.value, passed=t.passed, note=t.note,
                          at=t.at, client_id=t.client_id)
    conn.commit()
    return out


class FieldHandover(BaseModel):
    by: str
    to: str
    done: str
    left: str
    unsafe: str | None = None


@router.post("/exceptions/{exception_id}/handover")
def exception_handover(exception_id: int, h: FieldHandover, conn=Depends(get_conn)):
    """Hand a job in progress to someone on the incoming crew (P5.11c); it stays with the sender until accepted."""
    from ..ops import exceptions
    return _exception_call(conn, exceptions.hand_over, exception_id, h.by, h.to, h.done, h.left, h.unsafe)


class ByPerson(BaseModel):
    by: str


class HandoverAccept(BaseModel):
    by: str
    client_id: str | None = None           # accepted on a phone without signal: applied once (P5.17e)


@router.post("/exceptions/{exception_id}/handover/accept")
def exception_handover_accept(exception_id: int, b: HandoverAccept, conn=Depends(get_conn)):
    """The incoming person accepts a job handed to them (P5.11c)."""
    from ..ops import exceptions
    return _exception_call(conn, exceptions.accept_handover, exception_id, b.by, b.client_id)


@router.post("/exceptions/{exception_id}/report")
def exception_report(exception_id: int, r: FieldReport, conn=Depends(get_conn)):
    """A field operator reports back: on site, done, could not do (with why), or a note."""
    from ..ops import exceptions
    return _exception_call(conn, exceptions.report, exception_id, r.by, r.status, r.note, reading=r.reading,
                           photos=r.photos, client_id=r.client_id, at=r.at, override=r.override,
                           permit_in_hand=r.permit_in_hand)


class MessageNote(BaseModel):
    by: str
    text: str


@router.post("/messages")
def message_note(m: MessageNote, conn=Depends(get_conn)):
    """A note a field operator sends from the phone (P2.8): read as any message to the IOC is, into a case about the
    entity it names, a question for the data steward, or nothing to do. Returns the message with its outcome."""
    from ..core.jsonsafe import plain
    from ..ops import messages
    _loaded_types()
    try:
        out = messages.receive(conn, deps.pack(), m.by, m.text)
    except messages.MessageError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e
    conn.commit()
    return plain(out)


class MessageEntities(BaseModel):
    by: str
    fuzer_ids: list[str]


@router.post("/exceptions/{exception_id}/message/entities")
def message_entities(exception_id: int, m: MessageEntities, conn=Depends(get_conn)):
    """Change the entities a message case is about (#1382): a wrong match, by the reader or a steward, redone on the
    case. Returns the case, what it is about now, and the case as it now stands (`item`, P5.29d)."""
    from ..ops import messages
    _loaded_types()
    try:
        out = messages.change_entities(conn, exception_id, m.fuzer_ids, m.by)
    except messages.MessageError as e:
        conn.rollback()
        raise HTTPException(404 if str(e).startswith("no message case") else 400, str(e)) from e
    conn.commit()
    return {**out, "item": _case_now(conn, out["case"])}


@router.get("/exceptions/{exception_id}/photos/{photo_id}")
def exception_photo(exception_id: int, photo_id: int, conn=Depends(get_conn)):
    """A photo the field attached to a report."""
    from ..ops import exceptions
    try:
        p = exceptions.photo(conn, exception_id, photo_id)
    except exceptions.ExceptionError as e:
        raise HTTPException(404, str(e)) from e
    return Response(content=bytes(p["data"]), media_type=p["mime"], headers={"Cache-Control": "private, max-age=86400"})


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
    due_hours: str | None = None  # priority -> hours, "1:24,2:72" (#509)


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


class ExceptionShelve(BaseModel):
    by: str
    reason: str
    until: str


@router.post("/exceptions/{exception_id}/shelve")
def exception_shelve(exception_id: int, d: ExceptionShelve, conn=Depends(get_conn)):
    """Set a standing condition aside with a reason, off the open list until `until` (P3.5c). Never a safety case
    type; at most `exceptions.max_shelve_days`."""
    from ..ops import exceptions
    try:
        return _exception_call(conn, exceptions.shelve, exception_id, d.by, d.reason, d.until)
    except ValueError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e


@router.post("/exceptions/{exception_id}/unshelve")
def exception_unshelve(exception_id: int, d: ExceptionSplit, conn=Depends(get_conn)):
    """Put a shelved exception back on the open list now."""
    from ..ops import exceptions
    return _exception_call(conn, exceptions.unshelve, exception_id, d.by, d.reason or "taken off the shelf")


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


class ExceptionJoin(BaseModel):
    into: int
    by: str
    reason: str | None = None


@router.post("/exceptions/{exception_id}/join")
def exception_join(exception_id: int, d: ExceptionJoin, conn=Depends(get_conn)):
    """Add an event to another's case (Options' "Add to that job" on a case the field already has, tour F, F6): its
    case is merged into that one, which the field then reads it on. Answers with the case it joined (`item`)."""
    from ..ops import exceptions
    row = _exception_call(conn, exceptions.join, exception_id, d.into, d.by, d.reason)
    return {"event": {k: row[k] for k in ("id", "item_id", "joined_to", "state")}, "item": _case_now(conn, row["item_id"])}


class UpsetMerge(BaseModel):
    into: int
    by: str
    reason: str | None = None


def _cascade_call(conn, fn, *args, **kwargs):
    from ..ops.cascades import CascadeError
    try:
        return _exception_call(conn, fn, *args, **kwargs)
    except CascadeError as e:
        conn.rollback()
        raise HTTPException(404 if str(e).startswith("no upset") else 400, str(e)) from e


@router.get("/upsets/{upset_id}/view")
def upset_view(upset_id: int, conn=Depends(get_conn)):
    """What the triage page shows for an upset: detections in time order, how it spread on the plant model, related
    upsets (likely cause, likely knock-on, seen together before, coincident) and the ones merged into it."""
    from ..ops import cascades
    return _cascade_call(conn, cascades.view, upset_id)


@router.get("/upsets/{upset_id}/related")
def upset_related(upset_id: int, hours: float = Query(12, gt=0, le=72), conn=Depends(get_conn)):
    """Other upsets within `hours` of this one, most likely related first."""
    from ..ops import cascades
    return _cascade_call(conn, cascades.related, upset_id, hours)


@router.post("/upsets/{upset_id}/merge")
def upset_merge(upset_id: int, m: UpsetMerge, conn=Depends(get_conn)):
    """The IOC says this upset is part of the same event as `into`: its case joins that one's, its own is cancelled."""
    from ..ops import cascades
    return _cascade_call(conn, cascades.merge, upset_id, m.into, m.by, m.reason)


@router.post("/exceptions")
def exception_raise(r: ExceptionRaise, conn=Depends(get_conn)):
    """Raise an exception from another system (or a person): opened once per type and key, matched to entities by
    `refs` (a Fuzer ID, kind:key or source/key), routed by its type. A repeat updates the open one."""
    from ..core.findings import external
    from ..ops import exceptions
    return _exception_call(conn, exceptions.raise_, r.type, r.key, r.title, r.by, refs=r.refs, priority=r.priority,
                           value_usd=r.value_usd, finding=external(r.source or r.by, r.evidence), source=r.source)


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


@router.get("/work/kinds")
def work_kinds(conn=Depends(get_conn)):
    """Each kind of work's one label, by its name: what every page calls a case's or an event's kind (#1358)."""
    from ..ops import exceptions
    _loaded_types()
    return exceptions.work_labels(conn)


@router.get("/exceptions/refs")
def exception_refs(conn=Depends(get_conn)):
    """The reference forms an exception's `refs` are matched by, each with an example from this register."""
    from ..ops import exceptions
    return exceptions.ref_forms(conn)


# ---- routing: one table for what every kind of work starts (#509) --------------------------------------------------

@router.get("/routing")
def routing_table(conn=Depends(get_conn)):
    """The routing table: every type of work, where it is declared, how it joins one event's case, what it starts
    (a process, a queue, or the IOC's dispatch) and when it is due."""
    from ..ops import routing
    return _exception_call(conn, routing.routes)


@router.get("/routing/{type}/explain")
def routing_explain(type: str, refs: list[str] = Query(default=[]), key: str | None = None,
                    priority: str | None = None, to: str | None = None, conn=Depends(get_conn)):
    """Dry-run an intake of this type, writing nothing: the entities its refs match, the open exception it would add
    to or the event's case it would join, the process (each step's lane and who holds it) or queue it goes to, and
    when it is due. `priority` is the source's own priority (SAP's 1-4); `to` explains a dispatch to that person or
    `role:<name>`."""
    from ..ops import routing
    out = _exception_call(conn, routing.explain, type, refs=refs, key=key,
                          evidence={"priority": priority} if priority else None, to=to)
    return out


@router.put("/exceptions/types/{name}")
def exception_type_define(name: str, k: ExceptionKind, conn=Depends(get_conn)):
    """Define or change a type as data, for another system's exceptions."""
    from ..ops import exceptions
    return _exception_call(conn, exceptions.define, name, k.by, k.label, role=k.role, process=k.process,
                           case_type=k.case_type, audience=tuple(k.audience),
                           close_case_on_clear=k.close_case_on_clear, description=k.description, joins=k.joins,
                           due_hours=k.due_hours)


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


@router.post("/closeouts/{closeout_id}/send-back")
def closeout_send_back(closeout_id: int, d: CloseoutDecision, conn=Depends(get_conn)):
    """Send it back with why: nothing is written, and the job's case hears the reason (#1145)."""
    from ..ops import closeout
    return _exception_call(conn, closeout.send_back, closeout_id, d.by, d.reason or "")


# ---- the approved write-back queue (P5.7, #634) --------------------------------------------------------------------

class WritebackIn(BaseModel):
    by: str
    system: str
    kind: str
    payload: dict[str, Any] = {}
    record: str | None = None
    action: str = "update"
    key: str | None = None
    item_id: int | None = None
    title: str | None = None
    subjects: list[str] = []


class WritebackDecision(BaseModel):
    by: str
    payload: dict[str, Any] | None = None   # an approver's edits
    reason: str | None = None
    note: str | None = None


@router.get("/writebacks")
def writebacks(system: str | None = None, state: str | None = None, item_id: int | None = None,
               conn=Depends(get_conn)):
    """The write-back queue: every change proposed to a source system, newest first."""
    from ..ops import writeback
    return writeback.listing(conn, system, state, item_id)


@router.get("/writebacks/{wid}")
def writeback_detail(wid: int, conn=Depends(get_conn)):
    """One write-back with who may approve it, when it goes by its system's cadence, and its history."""
    from ..ops import writeback
    return _exception_call(conn, writeback.detail, wid)


@router.post("/writebacks")
def writeback_propose(w: WritebackIn, conn=Depends(get_conn)):
    """Propose a change to a source system: it waits for approval in the approver role's queue."""
    from ..ops import writeback
    return _exception_call(conn, writeback.propose, w.system, w.kind, w.payload, w.by, record=w.record,
                           action=w.action, key=w.key, item_id=w.item_id, title=w.title, subjects=w.subjects)


@router.post("/writebacks/{wid}/approve")
def writeback_approve(wid: int, d: WritebackDecision, conn=Depends(get_conn)):
    """A named person in one of the system's approver roles approves it; it goes by the system's cadence."""
    from ..ops import writeback
    return _exception_call(conn, writeback.approve, wid, d.by, d.payload)


@router.post("/writebacks/{wid}/reject")
def writeback_reject(wid: int, d: WritebackDecision, conn=Depends(get_conn)):
    from ..ops import writeback
    return _exception_call(conn, writeback.reject, wid, d.by, d.reason)


@router.post("/writebacks/{wid}/confirm")
def writeback_confirm(wid: int, d: WritebackDecision, conn=Depends(get_conn)):
    """The system's owner says an exported change is applied: the case is released."""
    from ..core import gates
    from ..ops import writeback

    if gates.machine(d.by):
        raise HTTPException(403, "confirming a write-back by hand needs a named person")
    return _exception_call(conn, writeback.confirm, wid, d.by, d.note)


@router.post("/writebacks/{wid}/fail")
def writeback_fail(wid: int, d: WritebackDecision, conn=Depends(get_conn)):
    """The system's owner says an exported change could not be applied: back to the approver with the reason."""
    from ..ops import writeback

    if not (d.reason or "").strip():
        raise HTTPException(400, "say why it could not be applied")
    return _exception_call(conn, writeback.fail, wid, d.reason, d.by)


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
    """Draft the handover for the shift in progress, so the outgoing lead writes it before they go (reading it shows
    its lines from live work while the shift runs). At the shift change it opens for the incoming lead; a handover
    whose shift ended is not redrafted."""
    from ..ops import handover
    out = _exception_call(conn, handover.draft, d.by, force=d.refresh, current=True)
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
    place: Literal["field", "desk"] | None = None  # where the job is done


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
        from ..core.plan import PlanOverload
        if isinstance(e, PlanOverload):
            raise HTTPException(409, str(e)) from e
        raise HTTPException(404 if re.match(r"no (plan item|exception|schedule activity) \d|no schedule |no resource |no resource capacity window ", str(e)) else 400, str(e)) from e
    conn.commit()
    return out


@router.get("/plan/at-risk")
def plan_at_risk(start: str | None = None, days: int = 90, conn=Depends(get_conn)):
    """Production at risk from planned shutdowns (P5.9a): each window of overlapping outages over the horizon, the
    wells behind the units going down at their latest test rate, counted once per window, with the durations used."""
    from ..ops import at_risk
    return _plan_call(conn, at_risk.windows, start, days=max(1, min(days, 366)))


@router.get("/plan")
def plan_items(start: str | None = None, end: str | None = None, area: str | None = None, status: str | None = None,
               subject: str | None = None, within: str | None = None, active: bool = False, conn=Depends(get_conn)):
    """The integrated plan in a window (default: a week ago to eight weeks ahead), each item with its clashes
    (same asset, same person), bundling chances (could ride along with a shutdown of its area) and, for work
    starting within 30 days, its assets' spare parts with no stock on record. `active` keeps the work still to do."""
    from ..ops import plan
    return _plan_call(conn, plan.items, start, end, area=area, status=status, subject=subject, within=within,
                      active=active)


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
    fields = {k: v for k, v in c.model_dump(exclude={"by", "clear_not_before"}).items() if v is not None}
    if c.clear_not_before:
        fields["not_before"] = None
    return _plan_call(conn, plan.update, item_id, c.by, **fields)


class PlanBook(BaseModel):
    by: str
    resource: str
    quantity: float = 1
    person: str | None = None   # the person or role:<role> it goes to
    reason: str | None = None   # why it is booked over capacity


@router.post("/plan/{item_id}/book")
def plan_book(item_id: int, b: PlanBook, conn=Depends(get_conn)):
    """Book a job on a crew or other resource for its dates, checked against capacity with every other source's
    demand. Over capacity it is refused (409, naming the overload) unless `reason` says why it goes anyway."""
    from ..ops import plan
    return _plan_call(conn, plan.book, item_id, b.by, b.resource, b.quantity, person=b.person, reason=b.reason)


@router.get("/plan/{item_id}/prerequisites")
def plan_prerequisites(item_id: int, conn=Depends(get_conn)):
    """What a job needs before it is released: each prerequisite satisfied or not, with what holds it; ready when all
    are (step 5 of the work model)."""
    from ..ops import plan
    return _plan_call(conn, plan.prerequisites, item_id)


class PlanRelease(BaseModel):
    by: str
    reason: str | None = None  # why it goes though a prerequisite is open


@router.post("/plan/{item_id}/release")
def plan_release(item_id: int, r: PlanRelease, conn=Depends(get_conn)):
    """Release a job to the field. With a prerequisite open it is refused (409, naming it) unless `reason` says why it
    goes anyway; a job whose case waits to be sent goes to its crew."""
    from ..ops import plan
    return _plan_call(conn, plan.release, item_id, r.by, r.reason)


class PlanReschedule(BaseModel):
    by: str
    start: datetime
    end: datetime
    reason: str


@router.post("/plan/{item_id}/reschedule")
def plan_reschedule(item_id: int, r: PlanReschedule, conn=Depends(get_conn)):
    """Move a job with the reason on its case; a released job goes back to waiting for release."""
    from ..ops import plan
    return _plan_call(conn, plan.reschedule, item_id, r.by, r.start, r.end, r.reason)


@router.post("/plan/sync")
def plan_sync(conn=Depends(get_conn)):
    """Bring the plan's SAP work orders, maintenance plans and planning-system schedules up to date with the register
    now (the rebuild does it too)."""
    from ..ops import business_plan, containers, plan, schedule, source_links

    def both(c):
        return {**plan.sync_work_orders(c), "maintenance_plans": plan.sync_maintenance_plans(c),
                "planning_system": schedule.sync_activities(c), "containers": containers.sync(c),
                "cases": source_links.sync_orders(c), "activities": source_links.sync_activities(c),
                "business_plan": business_plan.sync(c), "moves": source_links.send_moves(c)}
    return _plan_call(conn, both)


# ---- routines (step 7 of the work model) --------------------------------------------------------------------------

@router.get("/routines")
def routines_list(conn=Depends(get_conn)):
    """Every routine (SAP's maintenance plans and those kept here), with its next due date, open occurrence and
    whether it is late."""
    from ..ops import routines
    return routines.listing(conn)


class RoutineNew(BaseModel):
    by: str
    title: str
    interval_days: float
    next_due: date
    targets: list[str] = []
    anchor: Literal["fixed", "floating"] = "fixed"
    template: dict = {}
    lead_days: float = 14
    tolerance_days: float = 0
    owner_role: str = "maintenance_planner"


@router.post("/routines")
def routine_create(r: RoutineNew, conn=Depends(get_conn)):
    """A routine kept in Fuzer (a routine duty): each due date becomes a need, a case and a job within its lead time."""
    from ..ops import routines
    try:
        out = routines.create(conn, r.title, r.by, r.interval_days, r.next_due, targets=r.targets, anchor=r.anchor,
                              template=r.template, lead_days=r.lead_days, tolerance_days=r.tolerance_days,
                              owner_role=r.owner_role)
    except routines.RoutineError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e
    conn.commit()
    return out


@router.post("/routines/run")
def routines_run(conn=Depends(get_conn)):
    """Raise what is due now (the register follow-up does it too)."""
    from ..ops import routines
    out = {"sap": routines.sync_sap(conn), **routines.raise_due(conn)}
    conn.commit()
    return out


# ---- source links and status mapping (step 8 of the work model) ---------------------------------------------------

@router.get("/status-map")
def status_map(system: str | None = None, conn=Depends(get_conn)):
    """Each system's statuses and the Fuzer state each means, with what a Fuzer move sends back."""
    from ..ops import source_links
    out = source_links.status_map(conn, system)
    conn.commit()  # the seed rows, the first time
    return out


class StatusRow(BaseModel):
    by: str
    system: str
    object_type: str
    status: str
    state: str
    user_status: str = ""
    move: str | None = None
    note: str | None = None


@router.put("/status-map")
def status_map_set(r: StatusRow, conn=Depends(get_conn)):
    """Add or change one row of a system's status mapping."""
    from ..ops import source_links
    try:
        out = source_links.set_status(conn, r.system, r.object_type, r.status, r.state, r.by,
                                      user_status=r.user_status, move=r.move, note=r.note)
    except source_links.LinkError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e
    conn.commit()
    return out


@router.get("/source-links/{object}/{object_id}")
def source_links_of(object: str, object_id: int, conn=Depends(get_conn)):
    """The objects other systems keep for one Fuzer job, case, need or container, with their status there."""
    from ..ops import source_links
    if object not in source_links.OBJECTS:
        raise HTTPException(404, f"no such object kind {object}")
    return source_links.links(conn, object, object_id)


# ---- containers (step 6 of the work model) ------------------------------------------------------------------------

def _container_call(conn, fn, *args, **kwargs):
    from ..ops.containers import ContainerError
    try:
        out = fn(conn, *args, **kwargs)
    except ContainerError as e:
        conn.rollback()
        raise HTTPException(404 if str(e).startswith(("no container", "no case")) else 400, str(e)) from e
    conn.commit()
    return out


@router.get("/containers")
def containers_list(kind: str | None = None, status: str | None = "open", conn=Depends(get_conn)):
    """Every container (business plan, program, project, turnaround, package, campaign), parents first, each with
    its jobs by stage, its cases and its children."""
    from ..ops import containers
    return containers.listing(conn, kind, status or None)


@router.get("/containers/{container_id}")
def container_get(container_id: int, conn=Depends(get_conn)):
    """One container with its parents, children, jobs and cases."""
    from ..ops import containers
    return _container_call(conn, containers.get, container_id)


# ---- the business plan against actual (step 9 of the work model) --------------------------------------------------

def _plan_line_call(conn, fn, *args, **kwargs):
    from ..ops.business_plan import PlanLineError
    try:
        out = fn(conn, *args, **kwargs)
    except PlanLineError as e:
        conn.rollback()
        raise HTTPException(404 if str(e).startswith(("no plan line", "no container", "no case")) else 400, str(e)) from e
    conn.commit()
    return out


@router.get("/containers/{container_id}/plan")
def container_plan(container_id: int, conn=Depends(get_conn)):
    """The plan's intent lines, each estimate against its cases' actuals, the totals and the unplanned work."""
    from ..ops import business_plan
    return _plan_line_call(conn, business_plan.against_actual, container_id)


class PlanLineNew(BaseModel):
    by: str
    title: str
    kind: str | None = None
    subjects: list[str] = []
    period_start: date | None = None
    period_end: date | None = None
    estimate_hours: float | None = None
    estimate_cost: float | None = None
    estimate_value_usd: float | None = None
    owner: str | None = None
    note: str | None = None


@router.post("/containers/{container_id}/plan-lines")
def plan_line_add(container_id: int, r: PlanLineNew, conn=Depends(get_conn)):
    """An intent line on the plan: what it means to do, where, when and for how much."""
    from ..ops import business_plan
    return _plan_line_call(conn, business_plan.add_line, container_id, r.title, r.by,
                           **r.model_dump(exclude={"by", "title"}))


class PlanLineChange(BaseModel):
    by: str
    fields: dict[str, Any]


@router.put("/plan-lines/{line_id}")
def plan_line_update(line_id: int, r: PlanLineChange, conn=Depends(get_conn)):
    """Change a line, or drop it (status dropped)."""
    from ..ops import business_plan
    return _plan_line_call(conn, business_plan.update_line, line_id, r.by, **r.fields)


class PlanLineMatch(BaseModel):
    by: str
    item_id: int


@router.post("/plan-lines/{line_id}/cases")
def plan_line_match(line_id: int, r: PlanLineMatch, conn=Depends(get_conn)):
    """This case does that line's work; a case counts against one line."""
    from ..ops import business_plan
    return _plan_line_call(conn, business_plan.match, line_id, r.item_id, r.by)


@router.delete("/plan-lines/cases/{item_id}")
def plan_line_unmatch(item_id: int, by: str, conn=Depends(get_conn)):
    from ..ops import business_plan
    return {"unmatched": _plan_line_call(conn, business_plan.unmatch, item_id, by)}


@router.post("/containers/{container_id}/plan/match")
def container_plan_match(container_id: int, by: str = "fuzer", conn=Depends(get_conn)):
    """Match the cases whose jobs are on a line's assets in its period to that line."""
    from ..ops import business_plan
    return _plan_line_call(conn, business_plan.auto_match, container_id, by)


class ContainerNew(BaseModel):
    by: str
    kind: str
    title: str
    code: str | None = None
    parent: int | None = None
    owner: str | None = None
    start_at: datetime | None = None
    finish_by: datetime | None = None


@router.post("/containers")
def container_create(c: ContainerNew, conn=Depends(get_conn)):
    """Make a container, with the governing case its gates run on."""
    from ..ops import containers
    return _container_call(conn, containers.create, c.kind, c.title, c.by, code=c.code, parent=c.parent,
                           owner=c.owner, start_at=c.start_at, finish_by=c.finish_by)


class ContainerMove(BaseModel):
    by: str
    parent: int | None = None


@router.patch("/containers/{container_id}")
def container_move(container_id: int, m: ContainerMove, conn=Depends(get_conn)):
    """Put a container under another (or at the top)."""
    from ..ops import containers
    return _container_call(conn, containers.move, container_id, m.by, m.parent)


class ContainerCase(BaseModel):
    by: str
    item_id: int


@router.post("/containers/{container_id}/cases")
def container_add_case(container_id: int, c: ContainerCase, conn=Depends(get_conn)):
    """Put a case in a container (a case is in one)."""
    from ..ops import containers
    return _container_call(conn, containers.add_case, container_id, c.item_id, c.by)


@router.post("/containers/{container_id}/govern")
def container_govern(container_id: int, c: ContainerMove, conn=Depends(get_conn)):
    """Open a container's governing case if it has none."""
    from ..ops import containers
    return _container_call(conn, containers.govern, container_id, c.by)


class ScheduleApply(BaseModel):
    by: str


class ScheduleActivity(BaseModel):
    by: str
    code: str
    name: str
    duration_hours: float
    wbs: str | None = None
    refs: list[str] = []
    after: list[str] = []


class ScheduleActivityChange(BaseModel):
    by: str
    name: str | None = None
    code: str | None = None
    wbs: str | None = None
    duration_hours: float | None = None
    percent_complete: float | None = None
    status: str | None = None
    notes: str | None = None
    not_before: datetime | None = None
    clear_not_before: bool = False
    adds_boed: float | None = None  # production it brings online when it finishes, BOE/d (0 clears it)


class ScheduleLink(BaseModel):
    by: str
    pred: int
    succ: int
    type: Literal["FS", "SS", "FF", "SF"] = "FS"
    lag_hours: float = 0


class ScheduleDemand(BaseModel):
    by: str
    resource: str
    quantity: float | None = None


class PlanResourceChange(BaseModel):
    by: str
    name: str | None = None
    kind: str | None = None
    unit: str | None = None
    capacity: float | None = None
    work_center: str | None = None
    counts: list[str] | None = None   # the resources whose people it also carries (bed space counts the crews)
    routine: str | None = None        # "rounds": its people also do the operator rounds; "" for none


@router.get("/plan/schedules")
def schedules(conn=Depends(get_conn)):
    """The schedules on the integrated plan (turnarounds, projects, campaigns) with their window and size."""
    from ..ops import schedule
    return _plan_call(conn, schedule.projects)


@router.get("/plan/schedules/{code}")
def schedule_analysis(code: str, conn=Depends(get_conn)):
    """A schedule's activities with early and late dates, float and the critical path, its schedule levelled against
    every shared resource (other planned work on them included), and the overloads if nothing were levelled."""
    from ..ops import schedule
    return _plan_call(conn, schedule.analyse, code)


@router.post("/plan/schedules/{code}/apply")
def schedule_apply(code: str, body: ScheduleApply, conn=Depends(get_conn)):
    """Schedule: put the levelled dates on the plan."""
    from ..ops import schedule
    return _plan_call(conn, schedule.apply, code, body.by)


@router.post("/plan/schedules/{code}/activities")
def schedule_add(code: str, a: ScheduleActivity, conn=Depends(get_conn)):
    from ..ops import schedule
    return _plan_call(conn, schedule.add_activity, code, a.code, a.name, a.duration_hours, a.by, wbs=a.wbs,
                      refs=a.refs, after=a.after)


@router.patch("/plan/schedules/activities/{item_id}")
def schedule_change(item_id: int, c: ScheduleActivityChange, conn=Depends(get_conn)):
    from ..ops import schedule
    fields = {k: v for k, v in c.model_dump(exclude={"by", "clear_not_before"}).items() if v is not None}
    if c.clear_not_before:
        fields["not_before"] = None
    return _plan_call(conn, schedule.change_activity, item_id, c.by, **fields)


@router.delete("/plan/schedules/activities/{item_id}")
def schedule_remove(item_id: int, by: str = "", conn=Depends(get_conn)):
    from ..ops import schedule
    return _plan_call(conn, schedule.remove_activity, item_id, by)


@router.put("/plan/schedules/activities/{item_id}/demand")
def schedule_demand(item_id: int, d: ScheduleDemand, conn=Depends(get_conn)):
    """What an activity needs of a resource while it runs; no quantity removes it."""
    from ..ops import schedule
    return _plan_call(conn, schedule.set_demand, item_id, d.resource, d.quantity, d.by)


@router.post("/plan/schedules/links")
def schedule_link(link: ScheduleLink, conn=Depends(get_conn)):
    from ..ops import schedule
    return _plan_call(conn, schedule.link, link.pred, link.succ, link.by, link.type, link.lag_hours)


@router.delete("/plan/schedules/links")
def schedule_unlink(pred: int, succ: int, by: str = "", conn=Depends(get_conn)):
    from ..ops import schedule
    return _plan_call(conn, schedule.unlink, pred, succ, by)


@router.get("/plan/resources")
def plan_resources(conn=Depends(get_conn)):
    """The shared resources work is levelled against, with their usual capacity."""
    return conn.execute("select * from plan_resource order by kind, code").fetchall()


@router.put("/plan/resources/{code}")
def plan_resource_change(code: str, r: PlanResourceChange, conn=Depends(get_conn)):
    from ..ops import schedule
    return _plan_call(conn, schedule.set_resource, code, r.by, name=r.name, kind=r.kind, unit=r.unit,
                      capacity=r.capacity, work_center=r.work_center, counts=r.counts, routine=r.routine)


@router.get("/plan/logistics")
def plan_logistics(start: datetime | None = None, days: int = Query(14, ge=1, le=60), conn=Depends(get_conn)):
    """Logistics from the plan: day by day, each crew's jobs, who travels, the seats and beds they take
    and the cranes, vessels and deck space booked."""
    from ..ops import schedule
    return _plan_call(conn, schedule.logistics, start, days)


class CapacityWindow(BaseModel):
    by: str
    start: datetime
    end: datetime
    capacity: float
    reason: str | None = None


@router.get("/plan/resources/load")
def plan_resource_load(resource: str | None = None, start: datetime | None = None, days: int = Query(42, ge=1, le=180),
                       conn=Depends(get_conn)):
    """For resource owners: each shared resource's demand from every source on the plan, day by day, against its
    capacity, with each overload, the jobs in it and the move that clears it at the least cost."""
    from ..ops import schedule
    return _plan_call(conn, schedule.resource_load, resource, start, days)


@router.post("/plan/resources/{code}/capacity")
def plan_capacity_window(code: str, w: CapacityWindow, conn=Depends(get_conn)):
    """A different capacity for a while: leave, a crane's certification lapsing, a vessel in dock."""
    from ..ops import schedule
    return _plan_call(conn, schedule.set_capacity_window, code, w.start, w.end, w.capacity, w.by, w.reason)


@router.delete("/plan/resources/capacity/{window_id}")
def plan_capacity_window_remove(window_id: int, by: str = "", conn=Depends(get_conn)):
    from ..ops import schedule
    return _plan_call(conn, schedule.remove_capacity_window, window_id, by)


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
