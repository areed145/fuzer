"""The metric registry (P7.1): every registered KPI with its definition, and one metric's value now, by a grain, with
the records behind it.

    GET /metrics            every metric's declaration
    GET /metrics/{key}      its value now (?grain= one of its grains), rows by that grain and the refs behind it
"""

from fastapi import APIRouter, Depends, HTTPException

from ..core import metrics
from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


@router.get("/metrics")
def listing():
    """Every registered metric: definition, formula, unit, standard, grains and the records behind it."""
    return [m.describe() for m in metrics.registry().values()]


@router.get("/metrics/{key}")
def one(key: str, grain: str | None = None, conn=Depends(get_conn)):
    """One metric now, the same value a page, the daily report or an agent reads."""
    from ..core.jsonsafe import plain

    try:
        return plain(metrics.value(conn, deps.pack(), key, grain))
    except metrics.MetricError as e:
        raise HTTPException(404 if "no metric" in str(e) else 400, str(e)) from e
