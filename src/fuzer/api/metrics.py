"""The metric registry (P7.1): every registered KPI with its definition, and one metric's value now, by a grain, with
the records behind it.

    GET /metrics            every metric's declaration
    GET /metrics/{key}      its value now (?grain= one of its grains), rows by that grain and the refs behind it;
                            a dated metric takes ?at=YYYY-MM-DD, the day its period ends; ?history=true adds its
                            values over the weeks before, oldest first
"""

from datetime import date

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
def one(key: str, grain: str | None = None, at: date | None = None, history: bool = False, conn=Depends(get_conn)):
    """One metric now, the same value a page, the daily report or an agent reads; `history` adds its earlier values."""
    from ..core.jsonsafe import plain
    from ..core.shared_reads import shared

    try:
        # The Metrics page asks for every metric's history at once, a fifth of a second of CPU each: people opening it
        # together share one computation of each until the data changes (#1069).
        return shared(conn, "metric", (key, grain, at, history),
                      lambda: plain(metrics.value(conn, deps.pack(), key, at=at, grain=grain, history=history)))
    except metrics.MetricError as e:
        raise HTTPException(404 if "no metric" in str(e) else 400, str(e)) from e
