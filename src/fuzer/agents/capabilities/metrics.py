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
    return [m.describe() for m in metrics.registry().values()]


@capability("all", kind="query")
def get_metric(key: str, grain: str | None = None, at: str | None = None) -> dict[str, Any]:
    """One registered KPI now: its value, with its definition and unit, and by a grain (one of the metric's grains:
    queue, type, area, class, facility, system, dataset) its rows and the cases or records behind each. Quote the
    value as returned, with its period. For a dated metric, at (YYYY-MM-DD) is the day its period ends."""
    from datetime import date

    with db.connect() as conn:
        try:
            return _plain(metrics.value(conn, mcp_server.PACK, key, grain, date.fromisoformat(at) if at else None))
        except (metrics.MetricError, ValueError) as e:
            return {"error": str(e), "metrics": sorted(metrics.registry())}
