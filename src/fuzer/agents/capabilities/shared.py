"""Tools every agent, or several specialists, use: search, entities, records, measurements, limits, calcs. Declared
with `capability` (#133); mcp_server serves them."""

import json
from typing import Any, Literal

from ...core import db
from ...graph import edges, queries
from ...workflow import engine as work
from .. import mcp_server
from ..mcp_server import (
    REL_LABELS,
    AgentRole,
    _chart,
    _date,
    _plain,
    _resolve,
    _trim,
    _work,
    capability,
)


@capability("orchestrator", kind="query")
def get_stats() -> dict[str, Any]:
    """Counts of entities by kind, links, source records and open review items."""
    with db.connect() as conn:
        return queries.stats(conn, mcp_server.PACK)


@capability("all", kind="query")
def get_ontology() -> dict[str, Any]:
    """The data model as it exists in Fuzer: entity kinds with counts, fields and feeding sources,
    and which link types connect which kinds (with counts and sources). Call this first when unsure
    what can be asked."""
    with db.connect() as conn:
        return _plain(queries.ontology(conn))


@capability("all", kind="query")
def search(query: str, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Find entities by tag, well, PI point, serial number, source key or description text.

    kind optionally filters to one entity kind (tag, equipment, well, timeseries, ...).
    """
    with db.connect() as conn:
        return _plain(queries.search(conn, mcp_server.PACK, query, kind, min(limit, 200)))


@capability("all", kind="query")
def get_entity(ref: str, as_of: str | None = None) -> dict[str, Any]:
    """Everything Fuzer knows about one entity: chosen values (with source and any disagreeing
    sources), the source records merged into it, and its links grouped by relationship.

    as_of (YYYY-MM-DD) limits dated links to those valid on that day.
    """
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        e = queries.entity(conn, fid, _date(as_of), per_group=50, pack=mcp_server.PACK)

    values = {}
    for field, g in e["golden"].items():
        others = [{"source": v["source"], "value": v["value"]}
                  for v in e["values"].get(field, []) if v["value"] != g["value"]]
        values[field] = {"value": g["value"], "source": g["source"],
                         **({"reviewed": True} if g["rule"] == "review" else {}),
                         **({"other_sources_say": others} if others else {})}

    links: dict[str, list] = {}
    for x in e["edges"]:
        item = {"id": x["other"], "label": x["other_label"], "kind": x["other_kind"]}
        if x["other_description"]:
            item["description"] = x["other_description"]
        if x["valid_from"] or x["valid_to"]:
            item["from"], item["to"] = x["valid_from"], x["valid_to"] or "now"
        if x["props"] and x["props"].get("text"):
            item["text"] = x["props"]["text"]
        item["sources"] = x["sources"]
        links.setdefault(REL_LABELS.get((x["rel"], x["direction"]), x["rel"]), []).append(item)

    return _plain({
        "fuzer_id": e["fuzer_id"], "kind": e["kind"], "label": e["label"],
        "values": values,
        "source_records": [f"{r['source']}:{r['record_type']}:{r['source_key']}" for r in e["records"]],
        "links": links,
        "link_totals": {REL_LABELS.get(tuple(k.split(":")[::-1]), k): n for k, n in e["edge_counts"].items()
                        if n > 50},  # groups cut to 50 here; use search / get_events to dig in
        **({"as_of": as_of} if as_of else {}),
    })


@capability("orchestrator", "troubleshooter", "production", kind="query")
def trace_flow(ref: str, direction: Literal["up", "down"] = "up", as_of: str | None = None, max_depth: int = 10,
               network: Literal["allocation", "lift_gas", "piping"] = "allocation") -> dict[str, Any]:
    """Follow a flow network from an entity.

    network "allocation" (default): production flow from Energy Components; "up" finds what feeds it
    (wells -> jacket manifolds -> separator), "down" where it goes (separator -> meter -> sales point).
    network "lift_gas": the gas lift network; "up" from a well finds its supply path (gas lift manifold,
    any upstream jackets' manifolds, lift gas header, compression trains), "down" from a header or
    compressor finds every manifold and well it supplies. network "piping": process lines from the P&IDs.
    as_of (YYYY-MM-DD) uses the routing valid on that day; without it, all routings past and present.
    """
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        rel = {"piping": "pipes_to", "lift_gas": "lift_gas_to"}.get(network, "flows_to")
        rows = edges.trace(conn, fid, rel, direction, _date(as_of), min(max_depth, 25))
    return _plain({
        "start": fid, "direction": direction, **({"as_of": as_of} if as_of else {}),
        "path": [{"depth": r["depth"], "id": r["fuzer_id"], "label": r["label"], "kind": r["kind"],
                  **({"from": r["valid_from"], "to": r["valid_to"] or "now"}
                     if r["valid_from"] or r["valid_to"] else {})} for r in rows],
    })


@capability("troubleshooter", "maintenance", kind="query")
def equipment_history(tag: str) -> dict[str, Any]:
    """Physical equipment installed at a tag over time (serial, manufacturer, model, install dates).
    Use it to answer questions like "when was 20-P-1001B swapped and what replaced it?"."""
    with db.connect() as conn:
        fid, err = _resolve(conn, tag)
        if err:
            return err
        rows = conn.execute(
            """
            select e.src as id, l.label, e.valid_from, e.valid_to,
                   jsonb_object_agg(g.field, g.value) filter (where g.field is not null) as fields
            from edge e
            left join entity_label l on l.fuzer_id = e.src
            left join golden g on g.fuzer_id = e.src
            where e.dst = %s and e.rel = 'installed_at'
            group by e.src, l.label, e.valid_from, e.valid_to
            order by e.valid_from nulls first
            """,
            (fid,),
        ).fetchall()
    return _plain({"tag": fid, "installs": [
        {"id": r["id"], "label": r["label"], "from": r["valid_from"], "to": r["valid_to"] or "now",
         **(r["fields"] or {})} for r in rows]})


@capability("troubleshooter", "production", kind="query")
def get_events(ref: str | None = None, since: str | None = None, until: str | None = None,
               event_type: str | None = None, limit: int = 50) -> dict[str, Any]:
    """What happened, from the operator logs: trips, starts, stops, changeovers, shut-ins, choke changes,
    alarms, leaks, maintenance, instrument faults. Newest first, each with the log entry it came from.

    ref limits to events involving one tag / well (any naming style) or reported by one log entry;
    omit it for the whole facility. since / until are dates (YYYY-MM-DD). event_type is one of
    trip, start, stop, changeover, shut_in, bring_online, choke_change, alarm, leak, maintenance,
    work_order, well_test, instrument_fault, other.
    """
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        rows = queries.events(conn, fid, since, until, event_type, min(limit, 500))
    return _plain({"for": fid or "facility", "events": rows})


@capability("troubleshooter", "maintenance", kind="query")
def maintenance_history(ref: str, since: str | None = None, limit: int = 20) -> dict[str, Any]:
    """SAP notifications and work orders for a tag, a piece of equipment, every instance of a model, or a
    whole class (e.g. "ISO14224:PU.CE"), newest first, with counts by ISO 14224 failure mode. since is a
    date (YYYY-MM-DD). Use it for failure history and "what usually goes wrong with this model"."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"for": fid, **queries.maintenance(conn, fid, since, min(limit, 200))})


@capability("troubleshooter", "production", kind="query")
def downtime(ref: str | None = None, since: str | None = None, until: str | None = None,
             rank_by: Literal["tag", "class", "reason"] | None = None, limit: int = 20) -> dict[str, Any]:
    """Production downtime and deferment from Energy Components, with lost oil (Sm3) and hours.

    rank_by ranks lost oil by causing tag, by class of the causing equipment, or by reason (for "what
    costs us most?"). Otherwise lists downtime affecting a well / the facility, or caused by a tag,
    equipment, model or class (ref); omit ref for the whole facility. since / until are dates.
    """
    with db.connect() as conn:
        if rank_by:
            return _plain({"rank_by": rank_by, "ranking": queries.downtime_ranking(conn, rank_by, since, until,
                                                                                   min(limit, 200))})
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain({"for": fid or "facility", **queries.downtime(conn, fid, since, until, min(limit, 200))})


@capability("all", kind="query")
def get_incidents(ref: str | None = None, since: str | None = None, until: str | None = None,
                  limit: int = 20) -> dict[str, Any]:
    """Incidents: logged events, downtime and SAP notifications on one piece of plant within a few days of
    each other, grouped into one upset with lost oil, failure modes and event types. ref limits to a tag,
    equipment, model or class (omit for the whole facility); since / until are dates."""
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain({"for": fid or "facility", **queries.incidents(conn, fid, since, until, min(limit, 200))})


@capability("all", kind="query")
def search_knowledge(question: str, ref: str | None = None, limit: int = 8,
                     include_superseded: bool = False, failure_modes: list[str] | None = None) -> dict[str, Any]:
    """Search OEM manuals, troubleshooting guides, procedures and past incidents, returning cited
    passages (document, revision, page, section, snippet with [[matches]]).

    ref scopes the search to what applies to one tag / equipment / well: the item itself, its model's
    manuals, its class's procedures and its area's guides, ranked closest first. Always cite the document
    and page you use. Superseded revisions are left out unless include_superseded is true. failure_modes
    (ISO 14224 codes such as VIB, ELP, FTS, OHE) boosts passages about those modes."""
    from ...graph import knowledge
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain({"for": fid or "facility",
                       "passages": knowledge.search(conn, question, fid, min(limit, 30), include_superseded,
                                                    failure_modes)})


@capability("all", kind="query")
def documents_for(ref: str) -> dict[str, Any]:
    """Current documents that apply to a tag / equipment / model / class: its OEM manual (via model), class
    procedures and area troubleshooting guides, closest first, with why each applies."""
    from ...graph import knowledge
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"for": fid, "documents": knowledge.documents_for(conn, fid)})


@capability("troubleshooter", "maintenance", kind="query")
def lessons_learned(status: Literal["approved", "suggested", "all"] = "approved", limit: int = 20) -> dict[str, Any]:
    """Lessons learned per equipment model (or class) and ISO 14224 failure mode: the corrective work that has
    fixed it before, how often it held for 30 days, and the incidents behind it. Approved lessons were
    reviewed by engineers; suggested ones are drafts. Approved lessons also come back from search_knowledge."""
    from ...graph import lessons
    with db.connect() as conn:
        return _plain({"lessons": lessons.listing(conn, None if status == "all" else status, min(limit, 200))})


@capability("troubleshooter", "maintenance", kind="query")
def spare_parts(ref: str) -> dict[str, Any]:
    """Spare parts for a tag, piece of equipment or equipment model: SAP material, manufacturer part number,
    stock on hand (0 = none in stock), storage bin, how many units use it, and manual sections naming it."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"for": fid, "parts": queries.spares(conn, fid)})


@capability("troubleshooter", "production", kind="query")
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


@capability("troubleshooter", "production", "maintenance", kind="query")
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


@capability("troubleshooter", "production", "maintenance", kind="calc")
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


@capability("troubleshooter", "production", kind="calc")
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


@capability("all", kind="query")
def board(area: str | None = None, unit: str | None = None, kind: str | None = None, text: str | None = None,
          since: str | None = None, limit: int = 10) -> dict[str, Any]:
    """The work board (#99): outstanding work across SAP backlog, PMs and inspections, incidents and SRE
    upsets, workflow items, opportunities, reviews and projects, one card per piece of work, ranked by value
    (#100). Each card: rank, value and why, its records, whether a person pinned it and why it moved.
    Filter by area or unit (any ref), kind, words in the title, or new since a time. "Top 5 on train B":
    unit or area, limit 5."""
    from ...ops import board as b
    with db.connect() as conn:
        args = {}
        for k, ref in (("area", area), ("unit", unit)):
            if ref:
                fid, err = _resolve(conn, ref)
                if err:
                    return err
                args[k] = fid
        out = b.cards(conn, kind, args.get("area"), args.get("unit"), text, since, 1, min(limit, 50))
    for c in out["cards"]:
        c.pop("breakdown", None)
    return _plain(out)


@capability(kind="query")
def next_work(agent: str, limit: int = 5) -> dict[str, Any]:
    """The workflow steps your agent role can take now, highest on the board first. Claim one with
    claim_work."""
    from ...ops import board as b
    with db.connect() as conn:
        return _plain({"agent": agent, "steps": b.next_work(conn, agent, min(limit, 20))})


@capability("all", kind="query")
def get_daily_ops_report(day: str | None = None, version: int | None = None) -> dict[str, Any]:
    """The daily operations report for a production day (YYYY-MM-DD; default the latest): headline,
    production and deferment, upsets and events, wells, maintenance and integrity, look-ahead, every line
    citing its records. With the checks (uncited lines, numbers not in the records) and what changed since
    the previous version. For "what happened on the 28th?"."""
    from ...ops import daily_report
    with db.connect() as conn:
        if day is None:
            got = daily_report.days(conn, 1)
            day = got[0]["day"] if got else None
        out = daily_report.get(conn, day, version) if day else None
    return _plain(out) if out else {"error": f"no report for {day}" if day else "no reports yet"}


@capability("troubleshooter", "production", kind="calc")
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


@capability("troubleshooter", "maintenance", kind="calc")
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


@capability("troubleshooter", "maintenance", kind="calc")
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


@capability("troubleshooter", "production", "maintenance", kind="calc")
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


@capability("troubleshooter", "maintenance", kind="calc")
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


@capability("troubleshooter", "production", kind="calc")
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


@capability("troubleshooter", "production", kind="calc")
def get_study(study_id: int) -> dict[str, Any]:
    """A kept study: its parameters, status, summary, figure and (when done) its top results."""
    from ...ops import study
    with db.connect() as conn:
        out = study.get(conn, study_id)
    return _plain({k: v for k, v in out.items() if k != "result"} | {"result": _trim(out.get("result"))}) if out else \
        {"error": f"no study {study_id}"}


@capability("all", kind="query")
def what_changed(since: str, until: str | None = None, limit: int = 20) -> dict[str, Any]:
    """What happened in the records between two times (plant time): new log events, notifications, work
    orders started and finished, downtime started and ended, incidents, equipment installed or removed,
    limits taking or ending effect, excursions and new well tests, with counts. Start here for "what
    happened since ..." and each shift's or run's snapshot. since / until ISO times (until: now)."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.what_changed(conn, since, until, min(limit, 100)))


@capability("troubleshooter", "production", "maintenance", kind="query")
def measurements(ref: str, quantity: str | None = None, since: str | None = None, until: str | None = None,
                 history: bool = False) -> dict[str, Any]:
    """Sparse measurements that aren't in PI, on a well, tag, equipment, pipeline or an incident's unit:
    well tests (oil_rate, water_cut, gor, lift_gas_rate, wellhead_pressure), lab results at sample points
    (bsw, salt, density, oil_in_water, h2s, co2, water_dewpoint, chloride) and wall thickness at CMLs
    (wall_thickness, mm). One series per quantity and location with the latest value, min / max, the
    change per year (wall loss rate, decline) and the last three readings; history=true returns every
    reading. A qualifier "<" means below the detection limit. Dates YYYY-MM-DD."""
    from ...graph import measurements as m
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        if history:
            return _plain(m.for_entity(conn, fid, quantity, since, until))
        out = m.for_entity(conn, fid, quantity, since, until)
    out["series"] = [{k: v for k, v in s.items() if k != "values"} | {"recent": s["values"][-3:]}
                     for s in out["series"]]
    return _plain(out)


@capability("production", "maintenance", kind="query")
def deferment_pareto(by: Literal["reason", "category", "tag", "class", "well"] = "reason",
                     since: str | None = None, until: str | None = None, limit: int = 15) -> dict[str, Any]:
    """Deferred (lost) oil ranked by downtime reason, reason category, causing tag, its ISO 14224 class,
    or affected well, with each one's share. Default: the last 12 months."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.deferment_pareto(conn, by, since, until, min(limit, 100)))


@capability("troubleshooter", "production", "maintenance", kind="query")
def well_performance(ref: str | None = None, since: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Well tests. With ref (a well): its test history (oil rate, water cut, GOR, lift gas, WHP). Without:
    wells ranked by oil rate decline over the window, water cut risers, and deferment per well.
    Default window: 12 months."""
    from ...graph import operations
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain(operations.well_performance(conn, fid, since, min(limit, 100)))


@capability("troubleshooter", "production", kind="query")
def gas_lift_network(ref: str | None = None, since: str | None = None, limit: int = 15) -> dict[str, Any]:
    """The gas lift network: compression -> lift gas headers -> jacket gas lift manifolds (some through
    another jacket) -> gas lifted wells. Per header: jackets, wells, lift gas and oil from latest tests. Per
    well: lift gas, oil per lift gas and measured response to more lift gas (Sm3/d oil per extra kSm3/d),
    least and most responsive wells, wells lifting mostly water. With ref (header, manifold, jacket,
    compressor or well): only that part, plus its supply path. Default window: 12 months of tests."""
    from ...graph import operations
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain(operations.gas_lift_network(conn, fid, since, min(limit, 100)))


@capability("all", kind="query")
def link_text(text: str) -> list[dict[str, Any]]:
    """Find tags, wells and known plant nicknames ("test sep") in free text and link them to entities.
    Short tags without an area (e.g. "PT-2001") may exist in several areas; those come back with
    candidates instead of one fuzer_id."""
    with db.connect() as conn:
        return _plain(queries.link_text(conn, mcp_server.PACK, text))


@capability("all", kind="query")
def recall(ref: str, include_ended: bool = False) -> dict[str, Any]:
    """What the assistant learned in earlier conversations about an entity and the units it is part of: short
    dated facts from answers engineers rated useful (or statements they marked to remember). They are not
    source records: check them against the data and cite them as `fact:<id>` (learned from a conversation).
    include_ended adds `ended`: facts that are no longer true, as history only, with what replaced them.
    Ended facts carry no id because they must not be cited; cite the fact that replaced them."""
    from .. import memory

    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        scope = memory.units(conn, fid)
        rows = memory.facts(conn, scope, None if include_ended else "current", 50)
    keep = ("id", "text", "subject", "entities", "valid_from", "valid_to", "learned_at", "episode", "sources")
    out = {"about": fid, "facts": [{k: r[k] for k in keep} for r in rows if r["status"] == "current"],
           "note": "learned from conversations, not source records"}
    if include_ended:
        out["ended"] = [{"text": r["text"], "subject": r["subject"], "valid_from": r["valid_from"],
                         "valid_to": r["valid_to"], "ended_at": r["ended_at"], "ended_by": r["ended_by"],
                         "replaced_by": f"fact:{r['invalidated_by']}" if r["invalidated_by"] else None}
                        for r in rows if r["status"] == "ended"]
    return _plain(out)


@capability(kind="query")
def list_reviews(kind: Literal["match", "conflict"] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Open questions waiting for a person: possible duplicate records ("match") and sources that
    disagree on a value ("conflict"). Decisions are made by people in the Fuzer web UI."""
    with db.connect() as conn:
        rows = queries.reviews(conn, "open", kind, min(limit, 200))
    return _plain([{"id": r["id"], "kind": r["kind"], "entity": r["label"], **r["payload"]} for r in rows])


@capability(kind="query")
def my_work(agent: AgentRole, limit: int = 20) -> dict[str, Any]:
    """Your open work as agent role `agent`: agent steps waiting for your role (claimed_by and
    lease_until show whether a worker has one), person steps you claimed or were handed, and work items
    in your queue. Each step has a task_id for claim_work / complete_work / hand_off."""
    return _work(work.agent_inbox, agent, min(limit, 200))


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


@capability("troubleshooter", "production", "maintenance", kind="calc")
def equipment_curves(ref: str) -> dict[str, Any]:
    """The OEM curves (pump head / efficiency / NPSHr / power against flow, or choke Cv against opening)
    and ratings (BEP, operating regions, max Cv) for an equipment model, or the model of an equipment
    item or tag, and where they came from (vendor export or an accepted datasheet reading)."""
    from ...sources import curves
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        model = curves.model_of(conn, fid)
        out = curves.for_model(conn, model) if model else None
        return _plain(out or {"model_id": model, "series": {}, "waiting": curves.waiting(conn, model) if model else []})


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
