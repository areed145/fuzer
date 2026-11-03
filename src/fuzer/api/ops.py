"""The value ranking, board, daily reports, opportunities and studies."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..graph import queries
from . import deps
from .deps import get_conn

router = APIRouter()


@router.get("/value")
def value_ranking(kind: list[str] | None = Query(None), by: Literal["value", "per_effort"] = "value",
                  ref: str | None = None, as_of: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    """Upsets, work, plans and opportunities ranked by the shared value / risk score (#100)."""
    from .analysis import value
    fid = queries.resolve_ref(conn, deps.PACK, ref) if ref else None
    return value.ranked(conn, deps.PACK, tuple(kind or value.KINDS), as_of, by, fid, min(limit, 500))


@router.get("/board")
def board_page(kind: str | None = None, area: str | None = None, unit: str | None = None, text: str | None = None,
               since: str | None = None, owner: str | None = None, page: int = 1, per_page: int = 50,
               facets: bool = False, conn=Depends(get_conn)):
    """The work board (#99): every outstanding piece of work, ranked by value, one page at a time; with
    `facets`, counts by kind, area and owner for the views."""
    from .ops import board
    area_id = area if area and area.startswith("fz-") else queries.resolve_ref(conn, deps.PACK, area) if area else None
    unit_id = queries.resolve_ref(conn, deps.PACK, unit) if unit else None
    return board.cards(conn, kind, area_id, unit_id, text, since, max(1, page), min(per_page, 200), owner, facets)


@router.get("/board/history")
def board_history(key: str, conn=Depends(get_conn)):
    from .ops import board
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
    from .ops import board
    try:
        out = board.pin(conn, p.key, p.by, p.reason, p.rank, p.value_usd)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    board.refresh(conn, deps.PACK)
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
    from .ops import board
    unit_id = queries.resolve_ref(conn, deps.PACK, p.unit) if p.unit else None
    out = board.add_project(conn, p.title, p.by, unit_id, p.owner, p.due, p.benefit_usd, p.effort_usd, p.scope)
    board.refresh(conn, deps.PACK)
    return out


@router.get("/reports/daily")
def daily_reports(limit: int = 30, conn=Depends(get_conn)):
    """Days with a daily operations report, newest first (#98)."""
    from .ops import daily_report
    return daily_report.days(conn, min(limit, 365))


@router.get("/reports/daily/{day}")
def daily_report_day(day: str, version: int | None = None, conn=Depends(get_conn)):
    from .ops import daily_report
    out = daily_report.get(conn, day, version)
    if out is None:
        raise HTTPException(404, f"no report for {day}")
    return out


@router.get("/opportunities")
def opportunity_list(status: str | None = "open", limit: int = 100, conn=Depends(get_conn)):
    """Opportunities from the daily scan (#102)."""
    from .ops import opportunities
    return opportunities.listing(conn, status, min(limit, 500))


class OpportunityDecision(BaseModel):
    decision: Literal["accepted", "rejected", "project"]
    by: str = "anonymous"
    reason: str | None = None


@router.post("/opportunities/{key:path}/decision")
def opportunity_decide(key: str, d: OpportunityDecision, conn=Depends(get_conn)):
    """Accept, reject (a reason is required) or turn an opportunity into a board project."""
    from .ops import board, opportunities
    try:
        out = opportunities.decide(conn, key, d.decision, d.by, d.reason)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    board.refresh(conn, deps.PACK)
    return out


class StudyIn(BaseModel):
    kind: Literal["correlate", "correlation_matrix", "modes", "entities", "events", "spe"]
    params: dict[str, Any]
    by: str = "anonymous"
    now: bool | None = None


@router.post("/studies")
def study_start(s: StudyIn, conn=Depends(get_conn)):
    """Run a correlation or clustering study now when it is small, else queue it (#106)."""
    from .ops import study
    params = dict(s.params)
    for k in ("target", "scope"):
        if isinstance(params.get(k), str) and params[k] != "facility" and not params[k].startswith("fz-"):
            params[k] = queries.resolve_ref(conn, deps.PACK, params[k])
    try:
        return study.start(conn, deps.PACK, s.kind, params, s.by, s.now)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/studies")
def study_list(limit: int = 50, conn=Depends(get_conn)):
    from .ops import study
    return study.listing(conn, min(limit, 200))


@router.get("/studies/{sid}")
def study_get(sid: int, conn=Depends(get_conn)):
    from .ops import study
    out = study.get(conn, sid)
    if out is None:
        raise HTTPException(404, f"no study {sid}")
    return out


@router.post("/studies/{sid}/rerun")
def study_rerun(sid: int, by: str = "anonymous", conn=Depends(get_conn)):
    from .ops import study
    try:
        return study.rerun(conn, deps.PACK, sid, by)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
