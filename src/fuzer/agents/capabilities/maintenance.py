"""The maintenance and reliability engineer's tools: bad actors, backlog, compliance, turnarounds. Declared with
`capability` (#133); mcp_server serves them."""

from typing import Any, Literal

from ...core import db
from ...graph import queries
from .. import mcp_server
from ..mcp_server import _chart, _plain, _resolve, capability


@capability("maintenance", kind="query")
def list_members(ref: str, kind: Literal["tag", "equipment"] | None = None, limit: int = 100,
                 offset: int = 0) -> dict[str, Any]:
    """Everything in an equipment class (including its subtypes), e.g. ref="ISO14224:PU" for all pumps or
    "ISO14224:PU.CE" for centrifugal pumps, or every instance of an equipment model. Paged: use offset."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"of": fid, **queries.members(conn, fid, kind, min(limit, 500), offset)})


@capability("maintenance", kind="query")
def bad_actors(since: str | None = None, until: str | None = None, limit: int = 15) -> dict[str, Any]:
    """Equipment ranked by failures (SAP notifications), downtime caused and repeat ISO 14224 failure
    modes, with MTBF per unit and per class. Default window: 2 years."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.bad_actors(conn, since, until, min(limit, 100)))


@capability("maintenance", kind="query")
def work_order_backlog(as_of: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Open SAP work orders by priority, type and age; overdue orders (past basic finish); the oldest;
    open notifications with no order planned yet."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.work_order_backlog(conn, as_of, min(limit, 100)))


@capability("maintenance", kind="query")
def maintenance_compliance(as_of: str | None = None, horizon_days: int = 90, limit: int = 25) -> dict[str, Any]:
    """Preventive maintenance (PM) and inspection (INSP) plans: on-time share, overdue tasks and tasks
    due within the horizon; tasks that need a shutdown are marked."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.maintenance_compliance(conn, as_of, min(horizon_days, 1100), min(limit, 200)))


@capability("maintenance", kind="query")
def turnaround_scope(revision: str | None = None) -> dict[str, Any]:
    """Turnaround (shutdown) scope: work orders planned for the revision (default: the next one), plans
    that need the unit down and aren't in scope yet, bad actors not in scope, and spares for scoped
    units that are out of stock (order long-lead items now)."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.turnaround_scope(conn, revision))


@capability("orchestrator", "maintenance", kind="calc")
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


@capability("maintenance", kind="plot")
def plot_wall_thickness(ref: str | None = None, kind: Literal["pipeline", "tag"] | None = None,
                        min_required_mm: float | None = None) -> dict[str, Any]:
    """Chart wall thickness: with ref, each CML on that vessel / piping / pipeline over time with loss
    rates (and retirement dates if min_required_mm is given); without ref, the items losing wall
    fastest across the asset (kind narrows to pipelines or tags)."""
    from ...plots import maintenance
    return _chart(maintenance.wall_thickness, {"kind": kind, "min_required_mm": min_required_mm}, {"fid": ref})


@capability("maintenance", kind="plot")
def plot_reliability(ref: str, method: Literal["weibull", "crow_amsaa"] = "weibull") -> dict[str, Any]:
    """Chart reliability from failure notifications on a unit or an equipment class: a Weibull plot of
    times between failures (beta, eta), or Crow-AMSAA cumulative failures (beta > 1: speeding up)."""
    from ...plots import maintenance
    return _chart(maintenance.reliability, {"method": method}, {"fid": ref})


@capability("maintenance", kind="plot")
def plot_bad_actors(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Chart bad actors as bubbles: failures against oil lost to the downtime they caused, size =
    downtime events, colour = ISO 14224 class. Default window: 2 years."""
    from ...plots import maintenance
    return _chart(maintenance.bad_actors, {"since": since, "until": until})


@capability("maintenance", kind="plot")
def plot_peers(ref: str) -> dict[str, Any]:
    """Chart the units of one equipment class (or a unit's class) by failures per year, outliers marked."""
    from ...plots import maintenance
    return _chart(maintenance.peers, {}, {"fid": ref})


@capability("maintenance", kind="plot")
def plot_score_breakdown(ref: str) -> dict[str, Any]:
    """Chart what makes up an item's value score (#100): production, risk and compliance at stake, urgency and
    effort, to show why one card ranks above another. ref: a work order, plan, incident or tag."""
    from ...plots import general
    return _chart(lambda conn, **kw: general.score_breakdown(conn, pack=mcp_server.PACK, **kw), {}, {"ref": ref})
