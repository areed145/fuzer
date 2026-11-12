"""Conformance (P8.2): for every asset the person may open, the operating model it runs, its deviations and whether
they are justified, and its work in practice. Only granted projects are read; the rest are named (D6)."""

from fastapi import APIRouter, HTTPException

from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


@router.get("/standards/conformance")
def conformance(days: int = 28):
    """Each asset against its operating model (P8.2): version, deviations with their owner and review date, a verdict
    (standard, justified or drifted) and its work in practice over `days`. Reads only."""
    from ..core import access, db
    from ..workflow import conformance as standards
    from .security import current

    if days < 1 or days > 366:
        raise HTTPException(400, "a period is 1 to 366 days")
    allowed = None
    if (who := current.get()) is not None:
        with db.connect() as conn:
            allowed = access.projects_for(conn, who)
    return standards.fleet(allowed, days)
