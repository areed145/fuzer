"""The metric registry (P7.1): every registered KPI with its definition, and one metric's value now, by a grain, with
the records behind it.

    GET /metrics            every metric's declaration
    GET /metrics/{key}      its value (?grain= one of its grains), rows by that grain and the refs behind it;
                            ?scope=<entity id> rolls up that entity and what is beneath it, ?window= a named window
                            (core/windows) and ?at=YYYY-MM-DD the day it ends; ?history=true adds its values over the
                            periods before, oldest first
    GET /row-sets           every registered row set: its columns, whether it is scoped and its own window
    GET /row-sets/{key}     its rows for ?scope, ?window and ?at, at most ?limit, each opening its case or entity
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
    return [m.describe() for m in metrics.registry(deps.pack()).values()]


@router.get("/metrics/{key}")
def one(key: str, grain: str | None = None, at: date | None = None, history: bool = False, scope: str | None = None,
        window: str | None = None, conn=Depends(get_conn)):
    """One metric now, the same value a page, the daily report or an agent reads; `history` adds its earlier values."""
    from ..core.jsonsafe import plain
    from .shared_reads import shared

    try:
        # The Metrics page asks for every metric's history at once, a fifth of a second of CPU each: people opening it
        # together share one computation of each until the data changes (#1069).
        return shared(conn, "metric", (key, grain, at, history, scope, window),
                      lambda: plain(metrics.value(conn, deps.pack(), key, scope=scope, window=window, at=at, grain=grain,
                                                  history=history)))
    except metrics.MetricError as e:
        raise HTTPException(404 if str(e).startswith(("no metric", "no entity")) else 400, str(e)) from e


@router.get("/row-sets")
def row_sets():
    """Every registered row set: its columns, whether it is cut by scope, and its own window."""
    from ..core import rowsets

    return [r.describe() for r in rowsets.registry(deps.pack()).values()]


@router.get("/row-sets/{key}")
def row_set(key: str, scope: str | None = None, window: str | None = None, at: date | None = None, limit: int = 50,
            conn=Depends(get_conn)):
    """One row set's rows for a scope and window, the same rows a report's table lists."""
    from ..core import rowsets
    from ..core.jsonsafe import plain

    try:
        return plain(rowsets.read(conn, deps.pack(), key, scope=scope, window=window, at=at, limit=limit))
    except (rowsets.RowSetError, metrics.MetricError) as e:
        raise HTTPException(404 if str(e).startswith("no ") else 400, str(e)) from e
