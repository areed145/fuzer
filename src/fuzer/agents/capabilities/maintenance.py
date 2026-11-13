"""The maintenance and reliability engineer's tools: bad actors, backlog, compliance, turnarounds. Declared with
`capability` (#133); mcp_server serves them."""

from typing import Any, Literal

from ...core import db
from ...graph import queries
from .. import mcp_server
from ..mcp_server import _chart, _plain, _resolve, capability


@capability("maintenance-integrity", kind="query")
def list_members(ref: str, kind: Literal["tag", "equipment"] | None = None, limit: int = 100,
                 offset: int = 0) -> dict[str, Any]:
    """Everything in an equipment class (including its subtypes), e.g. ref="ISO14224:PU" for all pumps or
    "ISO14224:PU.CE" for centrifugal pumps, or every instance of an equipment model. Paged: use offset."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"of": fid, **queries.members(conn, fid, kind, min(limit, 500), offset)})


@capability("maintenance-integrity", "work-management-supply-chain", "asset-performance-reporting", kind="query")
def bad_actors(since: str | None = None, until: str | None = None, limit: int = 15) -> dict[str, Any]:
    """Equipment ranked by failures (SAP notifications), downtime caused and repeat ISO 14224 failure
    modes, with MTBF per unit and per class. Default window: 2 years."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.bad_actors(conn, since, until, min(limit, 100)))


@capability("maintenance-integrity", "work-management-supply-chain", "asset-performance-reporting", kind="query")
def work_order_backlog(as_of: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Open SAP work orders by priority, type and age; overdue orders (past basic finish); the oldest;
    open notifications with no order planned yet."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.work_order_backlog(conn, as_of, min(limit, 100)))


@capability("work-management-supply-chain", "maintenance-integrity", kind="calc")
def backlog_burn(since: str | None = None, until: str | None = None, days: int = 365) -> dict[str, Any]:
    """Is the backlog under control (#348)? Per month: work orders created (by start) against closed (by finish) and
    the orders open at the month's end, with whether the backlog is growing, steady or shrinking; then the days from
    a notification to the order raised for it (median, 90th percentile, longest). Orders with no notification are
    counted and left out of the lag, never read as zero."""
    from ...analysis import backlog
    with db.connect() as conn:
        return _plain(backlog.burn(conn, since, until, days))


@capability("work-management-supply-chain", "maintenance-integrity", kind="plot")
def plot_backlog_burn(since: str | None = None, until: str | None = None, days: int = 365) -> dict[str, Any]:
    """Chart work orders created and closed each month, with the open backlog at each month's end."""
    from ...plots import maintenance
    return _chart(maintenance.backlog_burn, {"since": since, "until": until, "days": days})


@capability("work-management-supply-chain", "maintenance-integrity", kind="calc")
def work_execution(since: str | None = None, until: str | None = None, days: int = 730,
                   by: Literal["quarter", "month"] = "quarter") -> dict[str, Any]:
    """Schedule compliance and repeat repair rate (#373) per quarter (or month): closed orders finished by their basic
    finish, with the late ones and how late; corrective orders started within 30 days of a corrective order on the
    same equipment finishing, with the one before. A period with fewer than 5 orders behind a rate says too little
    data. These measure the records, never people or crews."""
    from ...analysis import work_execution as we
    with db.connect() as conn:
        return _plain(we.trend(conn, since, until, max(90, min(days, 1825)), by))


@capability("work-management-supply-chain", "maintenance-integrity", kind="plot")
def plot_work_execution(since: str | None = None, until: str | None = None, days: int = 730,
                        by: Literal["quarter", "month"] = "quarter") -> dict[str, Any]:
    """Chart schedule compliance and the repeat repair rate per quarter (or month)."""
    from ...plots import maintenance
    return _chart(maintenance.work_execution, {"since": since, "until": until, "days": max(90, min(days, 1825)), "by": by})


@capability("maintenance-integrity", "field-operations-facilities", kind="query")
def rotating_screen(at: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Every rotating machine (pumps, compressors, gas turbines, motors) through the rotating health checks at
    one date, worst first: `act` (a severe flag: vibration zone D, head loss, accelerating failures, low-flow
    running), then `watch`. Use it for "which machines need attention?" before `calc("rotating_health", ref)`
    on the ones it names. Class-wide failure patterns are listed once under `class_wide_failures`."""
    from ...analysis import rotating
    with db.connect() as conn:
        return _plain(rotating.screen(conn, at, min(limit, 100)))


@capability("work-management-supply-chain", "maintenance-integrity", kind="query")
def backlog_health(as_of: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Backlog health: ageing of open corrective (PM01) orders (flag when over 25 % are older than 90 days),
    priority 1-2 orders past basic finish, unplanned notifications on bad actors, and the open orders
    ranked by value / risk in money (production at risk, criticality, compliance, effort). Each flag
    states its threshold; use `ranked` to argue what to do first."""
    from ...analysis import backlog
    with db.connect() as conn:
        return _plain(backlog.health(conn, mcp_server.PACK, as_of, min(limit, 50)))


@capability("maintenance-integrity", "work-management-supply-chain", "asset-performance-reporting", kind="query")
def maintenance_compliance(as_of: str | None = None, horizon_days: int = 90, limit: int = 25) -> dict[str, Any]:
    """Preventive maintenance (PM) and inspection (INSP) plans: on-time share, overdue tasks and tasks
    due within the horizon; tasks that need a shutdown are marked."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.maintenance_compliance(conn, as_of, min(horizon_days, 1100), min(limit, 200)))


@capability("maintenance-integrity", "work-management-supply-chain", "asset-performance-reporting", kind="query")
def turnaround_scope(revision: str | None = None, as_of: str | None = None) -> dict[str, Any]:
    """Turnaround (shutdown) scope: work orders planned for the revision (default: the next one), plans
    that need the unit down and aren't in scope yet, bad actors not in scope, spares for scoped
    units that are out of stock (order long-lead items now), and the planning schedule's activities for the
    revision when one is loaded: what is late (finish passed, not done) or slipped past its baseline, as of
    `as_of` (default: the data's latest day)."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.turnaround_scope(conn, revision, as_of_date=as_of))


@capability(
    "orchestrator", "maintenance-integrity", "work-management-supply-chain", "asset-performance-reporting",
    kind="calc"
)
def value_score(ref: str | None = None,
                kinds: list[Literal["incident", "work_order", "maintenance_plan", "opportunity"]] | None = None,
                by: Literal["value", "per_effort"] = "value", as_of: str | None = None,
                limit: int = 20) -> dict[str, Any]:
    """The shared value / risk score: upsets, open work orders, overdue or due maintenance plans and gas
    lift opportunities ranked in money over the pack's horizon (by value, or by value per effort). Each
    item has its production, risk (consequence x likelihood on the risk matrix, from SAP ABC criticality
    and failure history), compliance, urgency and effort, with inputs, sources, what is unknown and a
    one-line reason. Use these numbers when saying one thing matters more than another. With ref (an
    item or a tag): only the items for it."""
    from ...analysis import value
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain(value.ranked(conn, mcp_server.PACK, tuple(kinds or value.KINDS), as_of, by, fid, min(limit, 200)))


@capability("maintenance-integrity", kind="plot")
def plot_wall_thickness(ref: str | None = None, kind: Literal["pipeline", "tag"] | None = None,
                        min_required_mm: float | None = None) -> dict[str, Any]:
    """Chart wall thickness: with ref, each CML on that vessel / piping / pipeline over time with loss
    rates (and retirement dates if min_required_mm is given); without ref, the items losing wall
    fastest across the asset (kind narrows to pipelines or tags)."""
    from ...plots import maintenance
    return _chart(maintenance.wall_thickness, {"kind": kind, "min_required_mm": min_required_mm}, {"fid": ref})


@capability("maintenance-integrity", kind="plot")
def plot_reliability(ref: str, method: Literal["weibull", "crow_amsaa"] = "weibull") -> dict[str, Any]:
    """Chart reliability from failure notifications on a unit or an equipment class: a Weibull plot of
    times between failures (beta, eta), or Crow-AMSAA cumulative failures (beta > 1: speeding up)."""
    from ...plots import maintenance
    return _chart(maintenance.reliability, {"method": method}, {"fid": ref})


@capability("maintenance-integrity", "work-management-supply-chain", "asset-performance-reporting", kind="plot")
def plot_bad_actors(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Chart bad actors as bubbles: failures against oil lost to the downtime they caused, size =
    downtime events, colour = ISO 14224 class. Default window: 2 years."""
    from ...plots import maintenance
    return _chart(maintenance.bad_actors, {"since": since, "until": until})


@capability(
    "maintenance-integrity", "work-management-supply-chain", "asset-performance-reporting", kind="plot"
)
def plot_backlog(as_of: str | None = None) -> dict[str, Any]:
    """Chart open SAP work orders by priority and age, with overdue counts and notifications with no order in
    the summary."""
    from ...plots import maintenance
    return _chart(maintenance.backlog, {"as_of": as_of})


@capability(
    "maintenance-integrity", "work-management-supply-chain", "asset-performance-reporting", kind="plot"
)
def plot_compliance(as_of: str | None = None, horizon_days: int = 90) -> dict[str, Any]:
    """Chart PM and inspection plans by kind: overdue, due within the horizon and later, with the worst overdue
    plans in the summary. A date position in SAP, not the equipment's condition."""
    from ...plots import maintenance
    return _chart(maintenance.compliance, {"as_of": as_of, "horizon_days": horizon_days})


@capability("maintenance-integrity", kind="plot")
def plot_peers(ref: str) -> dict[str, Any]:
    """Chart the units of one equipment class (or a unit's class) by failures per year, outliers marked."""
    from ...plots import maintenance
    return _chart(maintenance.peers, {}, {"fid": ref})


@capability("maintenance-integrity", "work-management-supply-chain", "asset-performance-reporting", kind="plot")
def plot_score_breakdown(ref: str) -> dict[str, Any]:
    """Chart what makes up an item's value score (#100): production, risk and compliance at stake, urgency and
    effort, to show why one card ranks above another. ref: a work order, plan, incident or tag."""
    from ...plots import general
    return _chart(lambda conn, **kw: general.score_breakdown(conn, pack=mcp_server.PACK, **kw), {}, {"ref": ref})


@capability("maintenance-integrity", "asset-performance-reporting", kind="calc")
def maintenance_spend(by: Literal["unit", "class", "type"] = "unit", since: str | None = None,
                      until: str | None = None, days: int = 365) -> dict[str, Any]:
    """Work order cost (#335) by unit, ISO 14224 class or order type for a window (default the last year), totalled
    per currency and never across them; an order whose source states no currency is in `unknown`. Orders without a
    cost are counted (`orders_without_cost`). Recorded maintenance cost, not accounting truth."""
    from ...analysis import costs
    with db.connect() as conn:
        try:
            out = costs.spend(conn, since, until, by, days)
        except ValueError as e:
            return {"error": str(e)}
    for r in out["rows"]:
        r["refs"] = r["refs"][:5]
    return _plain(out)


@capability("maintenance-integrity", "production-surveillance", "asset-performance-reporting", kind="calc")
def cost_of_unreliability(since: str | None = None, until: str | None = None, days: int = 365,
                          limit: int = 15) -> dict[str, Any]:
    """The cost of unreliability by unit (#352): oil lost to the downtime each unit caused, at the pack's oil price and
    currency, beside its corrective orders' cost in their own currency, ranked. A unit's total is given only when
    both parts share a currency; quote the price and currency with every number. An operational estimate, not a
    financial figure."""
    from ...analysis import costs
    with db.connect() as conn:
        out = costs.unreliability(conn, mcp_server.PACK, since, until, days, min(limit, 50))
    for r in out["units"]:
        r["refs"] = r["refs"][:5]
    return _plain(out)


@capability("maintenance-integrity", "asset-performance-reporting", kind="plot")
def plot_maintenance_spend(by: Literal["unit", "class", "type"] = "unit", since: str | None = None,
                           until: str | None = None, days: int = 365) -> dict[str, Any]:
    """Chart work order cost ranked by unit, class or order type, one series per currency (unknown labelled)."""
    from ...plots import maintenance
    return _chart(maintenance.spend, {"since": since, "until": until, "by": by, "days": days})


@capability("maintenance-integrity", "asset-performance-reporting", kind="plot")
def plot_cost_of_unreliability(since: str | None = None, until: str | None = None, days: int = 365) -> dict[str, Any]:
    """Chart each unit's lost oil value beside its corrective order cost, each with its currency."""
    from ...plots import maintenance
    return _chart(lambda conn, **kw: maintenance.unreliability(conn, mcp_server.PACK, **kw),
                  {"since": since, "until": until, "days": days})
