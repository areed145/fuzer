"""Plots capabilities, declared with `capability` (#133)."""

import json
from typing import Any, Literal
from ...core import db
from ..mcp_server import _chart, _plain, capability


@capability("troubleshooter", "production", "maintenance", kind="plot")
def plot_trend(refs: list[str], at: str | None = None, hours_before: int = 48, hours_after: int = 24) -> dict[str, Any]:
    """Chart process values (PI / ADX) on stacked panels sharing time, with incident lead-ins shaded.
    refs: tags, wells or PI points (a tag gives its instruments' points; up to 6 series). at: the
    centre (default: the first ref's latest incident)."""
    from ...plots import general
    return _chart(general.trend, {"at": at, "hours_before": min(hours_before, 720), "hours_after": min(hours_after, 720)},
                  {"ids": refs})


@capability("all", kind="plot")
def plot_measurements(ref: str, quantity: str | None = None, since: str | None = None,
                      until: str | None = None) -> dict[str, Any]:
    """Chart landed measurements over time on a well, tag, equipment or pipeline (well tests, lab
    results, wall thickness): a panel per quantity, a line per location. Hollow points are below the
    detection limit."""
    from ...plots import general
    return _chart(general.measurements, {"quantity": quantity, "since": since, "until": until}, {"fid": ref})


@capability("troubleshooter", "production", "maintenance", kind="plot")
def plot_xy(x: str, y: str, at: str | None = None, hours_before: int = 72, hours_after: int = 24) -> dict[str, Any]:
    """Chart one PI point against another at the same times (operating envelope), coloured by time, with
    a straight-line fit and r. x and y are PI points (timeseries)."""
    from ...plots import general
    return _chart(general.xy, {"at": at, "hours_before": min(hours_before, 720), "hours_after": min(hours_after, 720)},
                  {"x_id": x, "y_id": y})


@capability("all", kind="plot")
def plot_bar(dataset: Literal["deferment_by_reason", "deferment_by_well", "deferment_by_tag", "deferment_by_class",
                              "failures_by_tag", "oil_change_by_well"],
             since: str | None = None, until: str | None = None, limit: int = 15) -> dict[str, Any]:
    """Chart a ranking as bars: deferred oil by reason / well / tag / class (Pareto with the cumulative
    share), failures by unit, or oil rate change by well. Dates YYYY-MM-DD."""
    from ...plots import general
    return _chart(general.bar, {"dataset": dataset, "since": since, "until": until, "limit": min(limit, 40)})


@capability("troubleshooter", "production", "maintenance", kind="plot")
def plot_distribution(ref: str, split_at: str, hours_before: int = 168, hours_after: int = 168) -> dict[str, Any]:
    """Chart one PI point before and after a moment (a repair, a setpoint change) as two boxes, with
    medians and spread in the summary."""
    from ...plots import general
    return _chart(general.distribution, {"split_at": split_at, "hours_before": min(hours_before, 2160),
                                         "hours_after": min(hours_after, 2160)}, {"fid": ref})


@capability("all", kind="plot")
def plot_custom(title: str, traces: list[dict[str, Any]], x_title: str | None = None,
                y_title: str | None = None) -> dict[str, Any]:
    """Chart numbers you worked out yourself, when no other chart covers them: up to 8 traces
    [{name, x, y, kind: line | markers | bar}], 500 points in all. Labelled as assistant-entered data;
    prefer the other chart tools, which read the data themselves."""
    from ...plots import general
    return _chart(lambda conn, **kw: general.custom(**kw), {"title": title, "traces": traces, "x_title": x_title,
                                                           "y_title": y_title})


@capability("troubleshooter", "maintenance", kind="plot")
def plot_vibration(ref: str, at: str | None = None, days: int = 30) -> dict[str, Any]:
    """Chart a machine's overall vibration against ISO 20816 zones A-D, with temperature on a second
    axis and incident lead-ins shaded. ref: a pump or compressor tag."""
    from ...plots import troubleshooting
    return _chart(troubleshooting.vibration, {"at": at, "days": min(days, 120)}, {"fid": ref})


@capability("troubleshooter", "maintenance", kind="plot")
def plot_pump_curve(ref: str, days: int = 365) -> dict[str, Any]:
    """Chart a pump against its OEM curve: operating points from PI (head from discharge minus suction
    pressure, flow) coloured by time over the head and efficiency curves, with the BEP and preferred
    operating region. The summary gives head against the curve month by month (falling = wear rings /
    impeller) and the share of time in the preferred region. ref: a pump tag or equipment."""
    from ...plots import maintenance
    return _chart(maintenance.pump_curve, {"days": min(days, 1100)}, {"fid": ref})


@capability("troubleshooter", "production", "maintenance", kind="plot")
def plot_choke(ref: str, days: int = 730) -> dict[str, Any]:
    """Chart a well's production choke against its OEM characteristic: effective Cv at each well test
    (oil wells) or from PI rates (gas wells) against the opening. Above the curve = eroded trim; below =
    restricted (scale, hydrate). The summary gives Cv against the curve by quarter and replacements.
    ref: a well or its choke tag. Says which datasheet is waiting when the model has no curve yet."""
    from ...plots import production
    return _chart(production.choke, {"days": min(days, 1100)}, {"fid": ref})


@capability("troubleshooter", "production", kind="plot")
def plot_pressure_profile(well: str, at: str | None = None, ref_at: str | None = None) -> dict[str, Any]:
    """Chart pressure at each node down a well's flow path (wellhead, jacket header, separator,
    compressor suction), today and against a reference date: where back-pressure builds."""
    from ...plots import production
    return _chart(production.pressure_profile, {"at": at, "ref_at": ref_at}, {"well": well})


@capability("troubleshooter", "production", kind="plot")
def plot_dp_drift(well: str, days: int = 60) -> dict[str, Any]:
    """Chart a well's wellhead-to-header pressure drop over time against its baseline band, with any rise
    marked (a flowline waxing or scaling)."""
    from ...plots import production
    return _chart(production.dp_drift, {"days": min(days, 365)}, {"well": well})


@capability("troubleshooter", "maintenance", kind="plot")
def plot_compressor_performance(ref: str, days: int = 90) -> dict[str, Any]:
    """Chart a compressor stage's daily polytropic efficiency against its first week (fouling, wear, hot recycle)."""
    from ...plots import maintenance
    return _chart(maintenance.compressor_performance, {"days": min(days, 730)}, {"fid": ref})


@capability("troubleshooter", "maintenance", kind="plot")
def plot_cooler_fouling(ref: str, days: int = 240) -> dict[str, Any]:
    """Chart a cooler's daily outlet temperature against its clean baseline and its high limit."""
    from ...plots import maintenance
    return _chart(maintenance.cooler_fouling, {"days": min(days, 730)}, {"fid": ref})


@capability("all", kind="query")
def look_at_figure(figure: str) -> list[Any]:
    """See a chart you made, as an image. Only when its summary leaves you unsure how it reads (a fit
    that may miss the late points, an outlier that may set the scale, a chart that may be empty) or the
    user asks how it looks; not for every chart. If it looks wrong, change the options or say so."""
    from mcp.server.mcpserver.utilities.types import Image

    from ... import plots
    with db.connect() as conn:
        row = plots.get(conn, figure.removeprefix("[[figure:").removesuffix("]]"))
    if row is None:
        return [json.dumps({"error": f"no figure {figure!r}"})]
    return [json.dumps(_plain({"figure": row["id"], "title": row["title"], "summary": row["summary"]})),
            Image(data=plots.png(row["spec"]), format="png")]
