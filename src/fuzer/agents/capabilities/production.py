"""The production engineer's tools: production, deferment, wells, gas lift, back-pressure. Declared with `capability`
(#133); mcp_server serves them."""

from typing import Any, Literal

from ...core import db
from .. import mcp_server
from ..mcp_server import _chart, _plain, _resolve, capability


@capability("production-surveillance", "field-operations-facilities", kind="calc")
def backpressure_sensitivity(scope: str = "facility", at: str | None = None) -> dict[str, Any]:
    """Oil gained per bar less back-pressure, for each back-pressure-limited well in scope (a well, a jacket
    area or "facility") and summed per header: from the inflow and choke equations, and from the well's
    tests against the header pressure at the time. Where the two disagree, say so."""
    from ...analysis import hydraulics
    with db.connect() as conn:
        sc = scope
        if scope != "facility":
            sc, err = _resolve(conn, scope)
            if err:
                return err
        return _plain(hydraulics.backpressure_sensitivity(conn, sc, at))


@capability("production-surveillance", "field-operations-facilities", kind="calc")
def debottleneck_options(scope: str = "facility", at: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Ranked actions against back-pressure: lower a separator to its next limit, pig a flowline whose
    pressure drop is rising, reroute a well to a lower header, open a throttled choke. Each with oil gained
    (from the back-pressure model), confidence, the next constraint it runs into, and what would confirm it.
    Proposals for people to decide, never operating instructions."""
    from ...analysis import hydraulics
    with db.connect() as conn:
        sc = scope
        if scope != "facility":
            sc, err = _resolve(conn, scope)
            if err:
                return err
        return _plain(hydraulics.debottleneck_options(conn, mcp_server.PACK, sc, at, limit=min(limit, 30)))


@capability("production-surveillance", "field-operations-facilities", kind="calc")
def capacity_headroom(ref: str, at: str | None = None) -> dict[str, Any]:
    """How close each point on a unit (compressor, separator, pump) runs to its nearest limit (alarm, IOW or
    learned range): what a debottleneck would run into next."""
    from ...analysis import hydraulics
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        try:
            return _plain(hydraulics.headroom(conn, mcp_server.PACK, fid, at))
        except hydraulics.CalcError as e:
            return {"error": str(e)}


@capability("production-surveillance", "wells-subsurface", "asset-performance-reporting", kind="query")
def opportunities(status: Literal["open", "rejected", "accepted", "project", "gone"] | None = "open",
                  limit: int = 20) -> dict[str, Any]:
    """Gains left on the table from the daily opportunity scan (#102): lift gas to reallocate between wells,
    wells below their decline or with stale tests, units worth a permanent fix, shut-in wells that may be
    recoverable. Each with its gain (Sm3/d oil), #100 value, basis, confidence, cited evidence and what would
    confirm it. Proposals for engineers to accept or reject on the board."""
    from ...ops import opportunities as opp
    with db.connect() as conn:
        rows = opp.listing(conn, status, min(limit, 100))
    return _plain({"opportunities": rows, "count": len(rows)})


@capability("production-surveillance", "wells-subsurface", "maintenance-integrity", kind="calc")
def cluster_entities(kind: Literal["wells", "points"] = "wells", scope: str | None = None, since: str | None = None,
                     until: str | None = None) -> dict[str, Any]:
    """Peer groups: wells by decline, water cut and lift gas response; or the points in a scope by how they
    move together (redundant points, mislabelled tags, natural sub-units; needs scope, since, until)."""
    from ...analysis import clusters
    with db.connect() as conn:
        sc = scope
        if scope:
            sc, err = _resolve(conn, scope)
            if err:
                return err
        try:
            return _plain(clusters.entities(conn, kind, sc, since, until))
        except clusters.CalcError as e:
            return {"error": str(e)}


@capability("production-surveillance", "asset-performance-reporting", kind="query")
def production_summary(since: str | None = None, until: str | None = None,
                       by: Literal["month", "week"] = "month") -> dict[str, Any]:
    """Oil actual vs potential (production efficiency %), gas export, lift gas and flare per month or
    week, with deferred oil by reason. Default: the last 12 months. Dates YYYY-MM-DD."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.production_summary(conn, since, until, by))


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "asset-performance-reporting",
    kind="query"
)
def production_constraints(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """What has been limiting production: losses by reason with the units that cause them, frequency,
    last occurrence and trend (worse / steady / better). Default: the last 12 months."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.constraints(conn, since, until))


@capability("production-surveillance", "wells-subsurface", kind="plot")
def plot_decline(well: str, model: Literal["auto", "exponential", "hyperbolic", "harmonic"] = "auto",
                 forecast_months: int = 12) -> dict[str, Any]:
    """Chart a well's oil rate decline from its tests (log scale) with an Arps fit after the last step
    change (valve failure, breakthrough, repair), and a forecast. The summary has qi, b, the decline
    per year, step changes and the forecast oil."""
    from ...plots import production
    return _chart(production.decline, {"model": model, "forecast_months": min(forecast_months, 60)}, {"well": well})


@capability("production-surveillance", "wells-subsurface", kind="plot")
def plot_water_diagnostic(well: str) -> dict[str, Any]:
    """Chart a well's water diagnostic: water cut against cumulative oil, and WOR and its derivative on
    log scales (Chan). Rising derivative = channelling; falling then rising = coning; a step = breakthrough."""
    from ...plots import production
    return _chart(production.water_diagnostic, {}, {"well": well})


@capability("production-surveillance", "wells-subsurface", kind="plot")
def plot_gas_lift_curve(well: str, breakeven: float = 0.5) -> dict[str, Any]:
    """Chart a gas lifted well's performance curve: oil against lift gas from its tests (decline taken
    out), the current point, the marginal response there, and the optimum where one more kSm3/d of gas
    gains `breakeven` Sm3/d of oil. Tests far below the curve (valve failed open) are shown apart."""
    from ...plots import production
    return _chart(production.gas_lift_curve, {"breakeven": breakeven}, {"well": well})


@capability("production-surveillance", "wells-subsurface", kind="calc")
def gas_lift_stability(well: str | None = None, days: int = 3) -> dict[str, Any]:
    """Gas lift casing heading and instability (#341). Without a well: every gas lifted well's casing pressure, lift
    gas and wellhead pressure over the last `days` through a regularity test (Thornhill, Huang and Zhang 2003),
    heading wells first: `heading` when the casing pressure cycles regularly with a swing of 2 % of its level or
    more, `unstable` when lift gas or wellhead pressure does without it, with the period, the swing and which signals
    cycle together. With a well: that well alone. Candidates for the engineer; never a gas or choke change."""
    from ...analysis import gas_lift_stability as gls
    days = max(1, min(days, 14))
    with db.connect() as conn:
        if well:
            fid, err = _resolve(conn, well)
            if err:
                return err
            try:
                d = gls.well(conn, fid, days)
            except gls.CalcError as e:
                return {"error": str(e)}
            return _plain({k: v for k, v in d.items() if k != "series"})
        return _plain(gls.screen(conn, days))


@capability("production-surveillance", "wells-subsurface", kind="plot")
def plot_gas_lift_heading(well: str, days: int = 3) -> dict[str, Any]:
    """Chart one gas lifted well's casing pressure, wellhead pressure and lift gas over the last `days`: a heading
    well's three cycle together."""
    from ...plots import production
    return _chart(production.gas_lift_heading, {"days": max(1, min(days, 14))}, {"well": well})


@capability("production-surveillance", "wells-subsurface", kind="plot")
def plot_gas_lift_screen(days: int = 3) -> dict[str, Any]:
    """Chart the gas lifted wells ranked by casing pressure swing over the last `days`, heading wells marked."""
    from ...plots import production
    return _chart(production.gas_lift_screen, {"days": max(1, min(days, 14))})


@capability("production-surveillance", "wells-subsurface", kind="plot")
def plot_gas_lift_allocation(ref: str | None = None) -> dict[str, Any]:
    """Chart every gas lifted well's marginal response (oil per extra lift gas) grouped by lift gas
    header, with suggested gas moves. ref: a header, jacket or manifold to narrow it; default all."""
    from ...plots import production
    return _chart(production.gas_lift_allocation, {}, {"ref": ref})


@capability("production-surveillance", "field-operations-facilities", kind="plot")
def plot_backpressure_sensitivity(scope: str = "facility", at: str | None = None) -> dict[str, Any]:
    """Chart oil per bar of back-pressure for each back-pressure-limited well: the model's figure and the
    tests' side by side. scope: a well, a jacket area or "facility"."""
    from ...plots import production
    if scope == "facility":
        return _chart(production.backpressure_sensitivity, {"scope": scope, "at": at})
    return _chart(production.backpressure_sensitivity, {"at": at}, {"scope": scope})


@capability("production-surveillance", "field-operations-facilities", kind="plot")
def plot_choke_criticality(well: str, days: int = 90) -> dict[str, Any]:
    """Chart back over wellhead pressure through a well's choke over time against the critical ratio (0.55):
    under it the choke limits the well and back-pressure doesn't reach it."""
    from ...plots import production
    return _chart(production.choke_criticality, {"days": min(days, 730)}, {"well": well})


@capability("production-surveillance", "field-operations-facilities", kind="plot")
def plot_debottleneck(at: str | None = None) -> dict[str, Any]:
    """Chart the debottleneck options ranked by oil gained, coloured by kind."""
    from ...plots import production
    return _chart(lambda conn, **kw: production.debottleneck(conn, pack=mcp_server.PACK, **kw), {"at": at})


@capability("production-surveillance", "asset-performance-reporting", kind="plot")
def plot_loss_waterfall(since: str, until: str) -> dict[str, Any]:
    """Chart oil potential, planned losses, unplanned losses by reason and actual over a period, priced."""
    from ...plots import general
    return _chart(lambda conn, **kw: general.loss_waterfall(conn, pack=mcp_server.PACK, **kw), {"since": since, "until": until})


@capability("production-surveillance", "hse-incident-management", "asset-performance-reporting", kind="plot")
def plot_flare(since: str, until: str) -> dict[str, Any]:
    """Chart daily flare from DAY001 and its CO2 per barrel of oil (an estimate)."""
    from ...plots import general
    return _chart(general.flare, {"since": since, "until": until})


@capability("production-surveillance", "wells-subsurface", "asset-performance-reporting", kind="calc")
def forecast_vs_actual(well: str | None = None, since: str | None = None, until: str | None = None,
                       days: int = 365) -> dict[str, Any]:
    """Each producing well's own decline forecast (fitted before the window, 90 % range) against its tests in the
    window (default the last year), ranked by the gap, with a field line. Separates a new loss (below_forecast,
    and since which test) from natural decline; wells with too few tests are listed under not_fitted, never fitted.
    The actual is the well test rate. A gap is a lead for review: the reservoir cause stays unknown."""
    from ...analysis import forecast_actual as fa
    with db.connect() as conn:
        fid = None
        if well:
            fid, err = _resolve(conn, well)
            if err:
                return err
        out = fa.forecast_vs_actual(conn, fid, since, until, days)
    if not well:  # a compact answer: the leads and the field line, every well's points stay behind the chart
        out["wells"] = [{k: v for k, v in w.items() if k not in ("points", "sufficiency")}
                        for w in out["wells"] if w["verdict"] != "on_forecast"]
    return _plain(out)


@capability("production-surveillance", "asset-performance-reporting", kind="plot")
def plot_forecast_vs_actual(well: str | None = None, since: str | None = None, until: str | None = None,
                            days: int = 365) -> dict[str, Any]:
    """Chart a well's forecast band against its tests with its downtime marked; without a well, the wells furthest
    under their forecast."""
    from ...plots import production
    return _chart(production.forecast_vs_actual, {"since": since, "until": until, "days": days}, {"well": well})


@capability("wells-subsurface", "production-surveillance", "asset-performance-reporting", kind="calc")
def intervention_gains(well: str | None = None, since: str | None = None, until: str | None = None,
                       days: int = 730) -> dict[str, Any]:
    """Did the well work pay off (#340)? Each recorded intervention (a well-category downtime: slickline, scale
    squeeze, valve change, survey) with oil rate, water cut and GOR before and after. The oil gain is net of the
    decline fitted through the tests before, with its 90 % range: gained, lost or no_clear_change. An intervention
    with no test on one side, or too few before to net out decline, is unknown, never zero. Attribution is not proof."""
    from ...analysis import interventions as iv
    with db.connect() as conn:
        fid = None
        if well:
            fid, err = _resolve(conn, well)
            if err:
                return err
        return _plain(iv.gains(conn, since, until, fid, days))


@capability("wells-subsurface", "asset-performance-reporting", kind="plot")
def plot_intervention_gains(since: str | None = None, until: str | None = None, days: int = 730) -> dict[str, Any]:
    """Chart each intervention's oil gain net of decline, with its range; unknown ones are listed, not drawn."""
    from ...plots import production
    return _chart(production.intervention_gains, {"since": since, "until": until, "days": days})


@capability("wells-subsurface", "production-surveillance", kind="calc")
def well_diagnostics(well: str | None = None) -> dict[str, Any]:
    """Water breakthrough and gas screening (#339). With a well: its water cut against cumulative oil (estimated from
    tests and labelled so), its GOR over time, and the first test where water cut stepped up 15 points over the tests
    before (a breakthrough suggestion) or GOR climbed 30 % end to end. Without: every well with such a pattern, and
    test staleness across the field. Patterns are suggestions; the mechanism stays the engineer's question."""
    from ...analysis import well_diagnostics as wd
    with db.connect() as conn:
        if well:
            fid, err = _resolve(conn, well)
            if err:
                return err
            return _plain(wd.diagnose(conn, fid))
        return _plain(wd.screen(conn))


@capability("wells-subsurface", "production-surveillance", kind="calc")
def test_staleness(max_days: int = 90) -> dict[str, Any]:
    """Every well's latest test and its age at the data's latest day; stale beyond `max_days` (default 90). Rates,
    forecasts and allocation built on a stale test carry its age. A well with no test on file is listed apart."""
    from ...analysis import well_diagnostics as wd
    with db.connect() as conn:
        return _plain(wd.staleness(conn, max(1, min(max_days, 3650))))


@capability("production-surveillance", kind="calc")
def field_allocation_factor(since: str | None = None, until: str | None = None, days: int = 365) -> dict[str, Any]:
    """Well test against allocated oil, field-wide, month by month (#338): the oil the daily report books over what the
    producing wells offer at their latest test within 120 days, cut by each well's own downtime, with the factor
    ignoring downtime beside it and the median age of the tests used. Days with a facility or tag downtime, or a field
    trip, are left out. Flags low or high (outside 0.90 to 1.10) and drifting (latest 3 months against the first 3 by
    0.05 or more). Well status history isn't loaded. For one meter's factor, use calc allocation_factor."""
    from ...analysis import well_test_factor
    with db.connect() as conn:
        return _plain(well_test_factor.trend(conn, since, until, days))


@capability("production-surveillance", kind="plot")
def plot_field_allocation_factor(since: str | None = None, until: str | None = None, days: int = 365) -> dict[str, Any]:
    """Chart allocated oil over the wells' tested offer each month, with the band and the age of the tests."""
    from ...plots import production
    return _chart(production.field_allocation_factor, {"since": since, "until": until, "days": days})


@capability("wells-subsurface", "production-surveillance", kind="plot")
def plot_gor_trend(well: str) -> dict[str, Any]:
    """Chart one well's GOR over its tests, with its first and latest levels when it climbed."""
    from ...plots import production
    return _chart(production.gor_trend, {}, {"well": well})


@capability("wells-subsurface", kind="calc")
def voidage_replacement(since: str | None = None, until: str | None = None, days: int = 730) -> dict[str, Any]:
    """Water injected against the reservoir volume produced, month by month (#365): oil times its formation volume
    factor plus produced water plus free gas (GOR above the solution GOR), with water cut and GOR from each well's
    latest test within 120 days. The ratio, its running total since injection started, and a verdict on the latest 3
    months (under 0.9 under-replacing, over 1.1 over-injecting). No recorded PVT means the ratio is unknown; a window
    with no injection says so instead of a ratio of zero. Screening only: the injection target stays with engineers."""
    from ...analysis import water_injection
    with db.connect() as conn:
        return _plain(water_injection.voidage(conn, since, until, days))


@capability("wells-subsurface", kind="calc")
def injector_hall(well: str | None = None, since: str | None = None, until: str | None = None,
                  days: int = 730) -> dict[str, Any]:
    """Each water injector's Hall plot, or one injector's (#365): cumulative wellhead pressure x days on against
    cumulative water, and the latest 3 months' slope over the first half's. Rising (over 1.25 times) reads plugging or
    skin; falling (under 0.8 times) a fracture opening or channelling. Wellhead pressure, so read each injector
    against itself."""
    from ...analysis import water_injection
    with db.connect() as conn:
        return _plain(water_injection.hall(conn, since, until, days, well))


@capability("wells-subsurface", kind="plot")
def plot_voidage_replacement(since: str | None = None, until: str | None = None, days: int = 730) -> dict[str, Any]:
    """Chart water injected against produced reservoir volume each month, with the ratio and its running total."""
    from ...plots import production
    return _chart(production.voidage_replacement, {"since": since, "until": until, "days": days})


@capability("wells-subsurface", kind="plot")
def plot_hall(well: str | None = None, since: str | None = None, until: str | None = None,
              days: int = 730) -> dict[str, Any]:
    """Chart each injector's Hall plot, or one injector's, with the injectors whose slope bent marked."""
    from ...plots import production
    return _chart(production.hall_plot, {"since": since, "until": until, "days": days}, {"well": well})
