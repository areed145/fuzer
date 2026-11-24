"""Metric capabilities (P7.1), declared with `capability` (#133): an agent asked for a KPI reads the registered metric,
so it gives the same value the page and the scorecard show."""

from typing import Any

from ...core import db, metrics
from .. import mcp_server
from ..mcp_server import _plain, capability


@capability("all", kind="query")
def list_metrics() -> list[dict[str, Any]]:
    """Every registered KPI: key, label, definition, formula, unit, standard, the grains it can be cut by and the
    records behind it. Call this before quoting a KPI, then get_metric for its value."""
    return [m.describe() for m in metrics.registry(mcp_server.PACK).values()]


@capability("all", kind="query")
def get_metric(key: str, grain: str | None = None, at: str | None = None, scope: str | None = None,
               window: str | None = None) -> dict[str, Any]:
    """One registered KPI: its value, with its definition and unit, and by a grain (one of the metric's grains:
    queue, type, area, class, facility, well, cause, ...) its rows and the cases or records behind each. Quote the
    value as returned, with its period. scope is an entity id: the value for it and everything beneath it (the result's
    scope is None when the metric is for the whole register). window is a named window (1d, 7d, 30d, 90d, qtd, ytd)
    ending on at (YYYY-MM-DD); without them a metric reads its own period."""
    from datetime import date

    with db.connect() as conn:
        try:
            return _plain(metrics.value(conn, mcp_server.PACK, key, scope=scope, window=window,
                                        at=date.fromisoformat(at) if at else None, grain=grain))
        except (metrics.MetricError, ValueError) as e:
            return {"error": str(e), "metrics": sorted(metrics.registry(mcp_server.PACK))}
