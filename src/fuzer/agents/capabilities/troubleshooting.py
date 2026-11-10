"""Tools for a unit or upset: incidents, process values, limits, equipment and charts. Declared with `capability`
(#133); mcp_server serves them."""

from typing import Any, Literal

from ...core import db
from ...graph import queries
from .. import mcp_server
from ..mcp_server import (
    _chart,
    _date,
    _plain,
    _resolve,
    _resolve_point,
    _scope,
    capability,
)


@capability("field-operations-facilities", kind="query")
def equipment_changes(since: str | None = None) -> list[dict[str, Any]]:
    """Equipment removals and installs (swaps) across the facility, newest first.
    since (YYYY-MM-DD) limits to changes on or after that date."""
    with db.connect() as conn:
        rows = conn.execute(
            """
            select tl.label as tag, e.dst as tag_id, el.label as equipment, e.src as equipment_id,
                   e.valid_from as installed, e.valid_to as removed
            from edge e
            left join entity_label tl on tl.fuzer_id = e.dst
            left join entity_label el on el.fuzer_id = e.src
            where e.rel = 'installed_at'
              and e.dst in (select dst from edge where rel = 'installed_at' and valid_to is not null)
              and (%(since)s::date is null or greatest(e.valid_from, e.valid_to) >= %(since)s::date)
            order by coalesce(e.valid_to, e.valid_from) desc, tl.label
            """,
            {"since": _date(since)},
        ).fetchall()
    return _plain(rows)


@capability("field-operations-facilities", "hse-incident-management", kind="query")
def incident_detail(ref: str) -> dict[str, Any]:
    """One incident's timeline (every event, downtime record and notification in it, in order), the most
    similar past incidents on the same unit or class (how those went is often the best clue), and suggested
    reading: manual / guide sections in scope of the unit that cover the incident's failure modes."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        from ...graph import knowledge
        return _plain({"incident": fid, "timeline": queries.incident_timeline(conn, fid),
                       "similar": queries.similar_incidents(conn, fid, 10),
                       "suggested_reading": knowledge.for_incident(conn, fid, 5)})


@capability("wells-subsurface", "field-operations-facilities", "hse-incident-management", kind="query")
def record_history(ref: str, limit: int = 20) -> dict[str, Any]:
    """How the source records behind an entity changed: which system, when, and each field's old and new
    value (e.g. a manufacturer corrected in SAP, a description edited, a record removed from an export)."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"for": fid, "changes": queries.record_history(conn, mcp_server.PACK, fid, min(limit, 200))})


@capability("field-operations-facilities", kind="query")
def troubleshooting_brief(ref: str) -> dict[str, Any]:
    """Start here when troubleshooting. For an incident, tag or piece of equipment: what happened (timeline),
    similar past incidents and how they ended, engineer-approved lessons for its model / class, manual and
    guide sections covering its failure modes, maintenance history, downtime, spares in stock, and suggested
    checks. Every item has the id it came from: cite them, and use get_entity / search_knowledge to dig in."""
    from .. import assistant
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain(assistant.brief(conn, mcp_server.PACK, fid))


@capability("field-operations-facilities", kind="calc")
def instrument_health(ref: str, since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Can the readings be trusted? On a PI point, or every point on a unit: frozen (no change while the
    unit runs), stuck (jitter collapsed), noisy (hunting: jitter several times its usual), out of range,
    and redundant transmitters (A / B) drifting apart. Each finding has its span, value against the usual,
    basis and sources. Check it before blaming the process on one transmitter's word. Default window: the
    last 14 days; since / until ISO times."""
    from ...analysis import signals
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        since, until = signals.window(conn, since, until)
        return _plain(signals.instrument_health(conn, mcp_server.PACK, fid, since, until))


@capability("field-operations-facilities", kind="query")
def detections(since: str | None = None, detector: Literal["excursions", "stops", "steps", "drifts", "instruments",
                                                           "models"] | None = None, limit: int = 100) -> dict[str, Any]:
    """What the SRE's scheduled scans found in the process data (#96), newest first: the point and unit,
    detector (excursions outside limits, unit stops, steps, drifts, bad instruments, equipment against its
    curve), what it found (key), start and end (null = still going) and evidence. since: ISO time; only
    those going on after it."""
    from ...ops import sre
    with db.connect() as conn:
        rows = sre.recent(conn, since, detector, min(limit, 500))
    return _plain({"detections": rows, "count": len(rows)})


@capability("field-operations-facilities", "hse-incident-management", kind="query")
def get_upsets(since: str | None = None, status: Literal["detected", "confirmed", "resolved", "false_alarm"] | None = None,
               kind: Literal["process", "instrument", "slow", "standing"] = "process", limit: int = 30) -> dict[str, Any]:
    """Upsets the SRE found in the process data (#96), newest first: detections close in time and connected
    in the plant model grouped into one, with the earliest as the likely first cause (unit, point and what
    it found), the units it reached and its status. kind: process (trips, excursions, steps), instrument
    (the week's instrument-health summary: every bad reading that week; upset_detail lists them by area), slow
    (drift, equipment against its curve) or standing (alarms already on when scanning began)."""
    from ...ops import sre
    with db.connect() as conn:
        rows = sre.upsets(conn, since, status, kind, min(limit, 200))
    return _plain({"upsets": rows, "count": len(rows)})


@capability("field-operations-facilities", "hse-incident-management", kind="query")
def upset_detail(upset_id: int) -> dict[str, Any]:
    """One upset: its detections in time order (the first is the likely first cause), with evidence, and
    how many on each unit."""
    from ...ops import sre
    with db.connect() as conn:
        out = sre.upset_detail(conn, upset_id)
    return _plain(out) if out else {"error": f"no upset {upset_id}"}


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    kind="calc"
)
def correlate(target: str, scope: str | list[str] = "facility", since: str | None = None, until: str | None = None,
              max_lag_minutes: int = 360, path: Literal["flows_to:up", "flows_to:down", "lift_gas_to:up",
                                                        "lift_gas_to:down"] | None = None,
              method: Literal["pearson", "spearman"] = "pearson", top: int = 15) -> dict[str, Any]:
    """What moves with a PI point, and what moved first. Ranks every point in scope (a unit, an area, a list
    of points, or "facility"; path adds what feeds or follows the unit) by the strongest correlation at any
    lag up to max_lag_minutes either way. leads_by_minutes > 0: it moved first. `leaders`: the strongly
    correlated points that moved first, earliest first, usually nearer the cause than the target's
    siblings. `connected`: the plant model links the two (a strong correlation with no path is worth
    checking). Rows where the plant was down are left out. Default window: the last 3 days."""
    from ...analysis import signals, studies
    with db.connect() as conn:
        fid, err = _resolve_point(conn, target)
        if err:
            return err
        sc, err = _scope(conn, scope)
        if err:
            return err
        since, until = signals.window(conn, since, until, 3)
        try:
            return _plain(studies.correlate(conn, mcp_server.PACK, fid, sc, since, until, max_lag_minutes=min(max_lag_minutes, 1440),
                                            method=method, top=min(top, 50), path=path))
        except studies.CalcError as e:
            return {"error": str(e)}


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    kind="calc"
)
def correlation_matrix(scope: str | list[str], since: str | None = None, until: str | None = None,
                       path: Literal["flows_to:up", "flows_to:down", "lift_gas_to:up", "lift_gas_to:down"] | None = None,
                       method: Literal["pearson", "spearman"] = "pearson", top: int = 15) -> dict[str, Any]:
    """Which points in a scope move together: the strongest pairs (r, and whether the plant model connects
    them) and the points ordered so related groups sit together. plot_correlation draws a unit's heatmap.
    Default window: the last 3 days."""
    from ...analysis import signals, studies
    with db.connect() as conn:
        sc, err = _scope(conn, scope)
        if err:
            return err
        since, until = signals.window(conn, since, until, 3)
        try:
            out = studies.correlation_matrix(conn, mcp_server.PACK, sc, since, until, method=method, top=min(top, 50), path=path)
        except studies.CalcError as e:
            return {"error": str(e)}
    return _plain({k: v for k, v in out.items() if k not in ("matrix", "ids")} | {"order": out["order"][:60]})


@capability("production-surveillance", "field-operations-facilities", kind="calc")
def cluster_modes(scope: str | list[str], since: str, until: str, k: int | None = None) -> dict[str, Any]:
    """Operating modes of a unit, train or list of points over a window: stopped (its run signals) and its
    running regimes (k-means on the points that carry them: fouling, a new setpoint), with each mode's share,
    the points that differ most, how often it moves between modes and the mode before each stop."""
    from ...analysis import clusters
    with db.connect() as conn:
        sc, err = _scope(conn, scope)
        if err:
            return err
        try:
            out = clusters.modes(conn, sc, since, until, k, pack=mcp_server.PACK)
        except clusters.CalcError as e:
            return {"error": str(e)}
    return _plain({k_: v for k_, v in out.items() if k_ not in ("times", "labels")})


@capability("field-operations-facilities", "maintenance-integrity", kind="calc")
def cluster_events(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """SRE upsets grouped by signature (which kinds of unit raised which findings): recurring upset types."""
    from ...analysis import clusters
    with db.connect() as conn:
        try:
            return _plain(clusters.events(conn, since, until))
        except clusters.CalcError as e:
            return {"error": str(e)}


@capability("field-operations-facilities", "hse-incident-management", kind="plot")
def plot_incident(ref: str) -> dict[str, Any]:
    """Chart an incident on one page (an incident, or a unit's latest): the process values that moved
    most in the lead-in, stacked, with its log events, trips, notifications and downtime as markers."""
    from ...plots import troubleshooting
    return _chart(troubleshooting.incident, {}, {"fid": ref})


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    kind="plot"
)
def plot_correlation(ref: str, at: str | None = None, hours_before: int = 72, hours_after: int = 12,
                     upstream: bool = True, target: str | None = None) -> dict[str, Any]:
    """Chart how the process values on a unit (and on what feeds it) move together, as a correlation
    heatmap. target: a PI point (e.g. the one that tripped); the summary then gives, for each other
    point, the lag at which it best matches the target: what moved first."""
    from ...plots import troubleshooting
    return _chart(troubleshooting.correlation, {"at": at, "hours_before": min(hours_before, 720),
                                                "hours_after": min(hours_after, 720), "upstream": upstream},
                  {"fid": ref, "target": target})


@capability("field-operations-facilities", kind="plot")
def plot_instrument_health(ref: str, since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Chart a point's or a unit's readings with the stretches instrument_health flags shaded (noisy,
    frozen or stuck, out of range, apart from its redundant twin). Default window: the last 14 days."""
    from ...plots import troubleshooting
    return _chart(lambda conn, **kw: troubleshooting.instrument_health(conn, pack=mcp_server.PACK, **kw),
                  {"since": since, "until": until}, {"fid": ref})


@capability("production-surveillance", "field-operations-facilities", kind="plot")
def plot_changes(ref: str, since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Chart a point's or a unit's readings with the steps and jitter changes detect_changes finds marked
    and drifts shaded. Default window: the last 14 days."""
    from ...plots import troubleshooting
    return _chart(troubleshooting.changes, {"since": since, "until": until}, {"fid": ref})


@capability("field-operations-facilities", "hse-incident-management", kind="plot")
def plot_state_timeline(since: str, until: str, scope: str | None = None) -> dict[str, Any]:
    """Chart each unit's state (running, startup, stopped, transition) as swimlanes over a window: an upset
    spreading through a train or the plant. scope: a unit or area (default: everything with a run signal)."""
    from ...plots import troubleshooting
    build = lambda conn, **kw: troubleshooting.state_timeline(conn, pack=mcp_server.PACK, **kw)
    return _chart(build, {"since": since, "until": until}, {"scope": scope} if scope else {"scope": None})


@capability("field-operations-facilities", "hse-incident-management", kind="plot")
def plot_alarms(since: str, until: str, scope: str | None = None) -> dict[str, Any]:
    """Chart alarms per 10 minutes over a window against the flood line, with floods, chattering, standing
    alarms and the bad actors in the summary."""
    from ...plots import troubleshooting
    build = lambda conn, **kw: troubleshooting.alarms_chart(conn, pack=mcp_server.PACK, **kw)
    return _chart(build, {"since": since, "until": until}, {"scope": scope} if scope else {"scope": None})


@capability("field-operations-facilities", "hse-incident-management", kind="plot")
def plot_propagation(upset_id: int) -> dict[str, Any]:
    """Chart an SRE upset on the plant model: units coloured by when each was first detected, plant model
    links between them, the likely first cause marked."""
    from ...plots import troubleshooting
    return _chart(troubleshooting.propagation, {"upset": upset_id})


@capability("field-operations-facilities", "maintenance-integrity", kind="plot")
def plot_operating_window(ref: str, since: str, until: str) -> dict[str, Any]:
    """Chart a PI point against its limits (alarm, IOW, learned range) with the minutes outside per day."""
    from ...plots import general
    return _chart(lambda conn, **kw: general.operating_window(conn, pack=mcp_server.PACK, **kw), {"since": since, "until": until},
                  {"fid": ref})


@capability("production-surveillance", "field-operations-facilities", kind="plot")
def plot_modes(scope: str | list[str], since: str, until: str, k: int | None = None) -> dict[str, Any]:
    """Chart a unit's operating modes over time as a coloured strip under its regime-carrying point."""
    from ...plots import troubleshooting
    with db.connect() as conn:
        sc, err = _scope(conn, scope)
    if err:
        return err
    return _chart(lambda conn, **kw: troubleshooting.modes_chart(conn, sc, pack=mcp_server.PACK, **kw), {"since": since, "until": until, "k": k})
