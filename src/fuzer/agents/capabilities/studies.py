"""Studies capabilities, declared with `capability` (#133)."""

from typing import Any, Literal

from ...core import db
from .. import mcp_server
from ..mcp_server import _plain, _resolve, _trim, capability


@capability("production-surveillance", "wells-subsurface", "field-operations-facilities", kind="query")
def process_values(ref: str, at: str | None = None, hours_before: int = 48, hours_after: int = 24) -> dict[str, Any]:
    """Process values (PI points on the unit and its instruments) around a time: per point min / max /
    mean / last and the provider used. For an incident, at defaults to its start. at is ISO date or time."""
    from ...analysis import values

    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        out = values.around(conn, fid, at, min(hours_before, 720), min(hours_after, 720), pack=mcp_server.PACK)
    if "series" in out:
        out["series"] = [{k: v for k, v in s.items() if k != "values"} for s in out["series"]]
    return _plain(out)


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    kind="query"
)
def limits(ref: str, at: str | None = None) -> dict[str, Any]:
    """Operating limits on a PI point, or on every point of a unit (tag, equipment, well): the alarm limits
    that count (hihi / hi / lo / lolo, from the DCS or PI AF), integrity operating windows (IOWs: class,
    low / high and the time allowed outside), the normal range learned from history while the unit runs
    (used as the limit, labelled learned, when no source gives one), each source's values, checks that
    fail (sources disagree, disabled, standing, out of order, implausible, missing) and the latest
    excursions with any work item opened for them. at (ISO date) for the limits valid then."""
    from ...analysis import limits as lim
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain(lim.for_entity(conn, mcp_server.PACK, fid, at))


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    kind="calc"
)
def calc(check: Literal["decline", "gas_lift", "pump_curve", "choke", "reliability", "wall_loss", "well_constraint",
                       "dp_drift", "compressor_performance", "cooler_fouling", "peers"], ref: str,
         method: Literal["weibull", "crow_amsaa"] | None = None, model: str | None = None, days: int | None = None,
         breakeven: float | None = None, min_required_mm: float | None = None, until: str | None = None) -> dict[str, Any]:
    """One diagnostic check as a number against what it should be: {value, expected, deviation,
    deviation_pct, flag, basis, sources}. Cite it as evidence; flag is set only when they disagree enough to
    act on. decline (well): latest test against its Arps fit (below_decline, step_down). gas_lift (well):
    lift gas against the optimum on its curve (more_gas, less_gas, below_curve = valve failed open).
    pump_curve (pump): head against the OEM curve at the same flow, latest month (head_loss). choke (well
    or choke): effective Cv against the characteristic (eroded, restricted). reliability (tag, equipment or
    class; method weibull | crow_amsaa): beta against 1 (wear_out, early_life, failures_accelerating).
    wall_loss (vessel, piping, pipeline): fastest CML loss against the median, remaining life with
    min_required_mm (retire_soon, localised). well_constraint (well): shut_in, choke (critical flow) or
    back_pressure (the header holds it back), from back / wellhead pressure. dp_drift (well): the wellhead to
    header pressure drop rising (dp_rising: a flowline waxing or scaling) since when. until (ISO) works it out
    as of then. compressor_performance (stage tag, e.g. 20-K-2300A): polytropic efficiency against its first
    week (efficiency_drop). cooler_fouling (cooler tag): outlet temperature above its clean baseline, the rate
    since the last clean and when it reaches its limit (fouling). peers (tag, class or model): the class's units
    side by side, outliers or class_wide_failures. The plot_* chart of the same name shows the fit."""
    from ...analysis import calcs
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        try:
            return _plain(calcs.run(conn, check, fid, method=method, model=model, days=days, breakeven=breakeven,
                                    min_required_mm=min_required_mm, until=until))
        except calcs.CalcError as e:
            return {"error": str(e)}


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    "hse-incident-management", kind="calc"
)
def detect_changes(ref: str, since: str | None = None, until: str | None = None, limit: int = 40) -> dict[str, Any]:
    """Where a PI point (or each point on a unit) stepped (a trip, a choke move), drifted (fouling, wear)
    or changed its jitter (a transmitter starting to hunt), with when it started, the value before and
    after, and a score; the `limit` strongest. Default window: the last 14 days; since / until ISO times."""
    from ...analysis import signals
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        since, until = signals.window(conn, since, until)
        return _plain(signals.changes(conn, fid, since, until, limit=min(limit, 200)))


@capability("production-surveillance", "wells-subsurface", "field-operations-facilities", kind="calc")
def pressure_profile(well: str, at: str | None = None) -> dict[str, Any]:
    """Pressure at each node from a well's wellhead down its flow path (jacket production header,
    separator, compressor suction) and the drop across each segment: where the back-pressure comes from."""
    from ...analysis import hydraulics
    with db.connect() as conn:
        fid, err = _resolve(conn, well)
        if err:
            return err
        try:
            return _plain(hydraulics.pressure_profile(conn, fid, at))
        except hydraulics.CalcError as e:
            return {"error": str(e)}


@capability("field-operations-facilities", "maintenance-integrity", "hse-incident-management", kind="calc")
def alarm_analysis(since: str, until: str, scope: str | None = None) -> dict[str, Any]:
    """Alarm performance (ISA-18.2 / EEMUA 191) over a window: alarms per hour against 6, floods (over 10 in
    10 minutes), chattering and standing alarms, and the top bad-actor points. An alarm is a point going past
    an enabled DCS / AF alarm limit while its unit runs. scope: a unit or area (default: everything)."""
    from ...analysis import alarms
    with db.connect() as conn:
        sc = scope
        if scope:
            sc, err = _resolve(conn, scope)
            if err:
                return err
        return _plain(alarms.alarm_analysis(conn, mcp_server.PACK, sc, since, until))


@capability("field-operations-facilities", "maintenance-integrity", "hse-incident-management", kind="calc")
def trip_analysis(since: str, until: str, scope: str | None = None) -> dict[str, Any]:
    """Stops (trips), starts and failed starts per unit with a run signal over a window, with the days between
    stops and the share of time running. scope: a unit or area (default: everything)."""
    from ...analysis import alarms
    with db.connect() as conn:
        sc = scope
        if scope:
            sc, err = _resolve(conn, scope)
            if err:
                return err
        try:
            return _plain(alarms.trip_analysis(conn, mcp_server.PACK, sc, since, until))
        except alarms.CalcError as e:
            return {"error": str(e)}


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    "hse-incident-management", "asset-performance-reporting", kind="calc"
)
def compare_periods(ref: str, before_since: str, before_until: str, after_since: str, after_until: str) -> dict[str, Any]:
    """Did the change work? Each point on a PI point or unit in two windows (before and after a choke move,
    a repair, a swap): medians, the change, and whether it is significant (Welch's t with autocorrelation
    taken into account). Use it to close the loop when a work item closes."""
    from ...analysis import cases
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        try:
            return _plain(cases.compare_periods(conn, fid, (before_since, before_until), (after_since, after_until)))
        except cases.CalcError as e:
            return {"error": str(e)}


@capability("field-operations-facilities", "maintenance-integrity", "hse-incident-management", kind="calc")
def similar_cases(ref: str) -> dict[str, Any]:
    """Past incidents like this one (an incident, "upset:<id>", or a tag or well's latest incident): same unit
    or class, shared failure modes and event types, with the work orders done for each and whether the fix
    held (no incident on the unit within 90 days)."""
    from ...analysis import cases
    with db.connect() as conn:
        fid = ref
        if not ref.startswith("upset:"):
            fid, err = _resolve(conn, ref)
            if err:
                return err
        try:
            return _plain(cases.similar_cases(conn, fid))
        except cases.CalcError as e:
            return {"error": str(e)}


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    kind="calc"
)
def start_study(kind: Literal["correlate", "correlation_matrix", "modes", "entities", "events", "spe"],
                params: dict[str, Any], agent: str = "assistant") -> dict[str, Any]:
    """Keep a correlation or clustering study (params as for correlate / correlation_matrix / cluster_modes /
    cluster_entities / cluster_events, plus spe: scope, train_since, train_until, since, until). Small ones run
    now and come back with a summary; big ones (a facility scope, long windows) queue for the background job:
    check with get_study. Cite it as study:<id>."""
    from ...ops import study
    with db.connect() as conn:
        p = dict(params)
        for k in ("target", "scope"):
            if isinstance(p.get(k), str) and p[k] != "facility":
                fid, err = _resolve(conn, p[k])
                if err:
                    return err
                p[k] = fid
        try:
            out = study.start(conn, mcp_server.PACK, kind, p, f"agent:{agent}")
        except ValueError as e:
            return {"error": str(e)}
    return _plain({k: v for k, v in out.items() if k != "result"} | {"result": _trim(out.get("result"))})


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    kind="calc"
)
def get_study(study_id: int) -> dict[str, Any]:
    """A kept study: its parameters, status, summary, figure and (when done) its top results."""
    from ...ops import study
    with db.connect() as conn:
        out = study.get(conn, study_id)
    return _plain({k: v for k, v in out.items() if k != "result"} | {"result": _trim(out.get("result"))}) if out else \
        {"error": f"no study {study_id}"}
