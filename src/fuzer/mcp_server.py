"""MCP server: lets AI agents query the Fuzer asset graph.

All tools are read-only. Review decisions stay with people in the web UI.
Run with `fuzer mcp` (stdio) or `fuzer mcp --http` (streamable HTTP).
"""

import json
from datetime import date
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import db, graph, queries
from .pack import load_pack

PACK = load_pack()
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

INSTRUCTIONS = f"""\
Fuzer is the asset data graph for facility {PACK.facility} (oil & gas). It merges records from SAP, JDE,
Maximo, PI / PI AF, Energy Components (allocation network), P&IDs and operator logs into one entity per
real-world thing, and links them.

Entity kinds: facility, area, tag (a functional position such as 20-P-1001B), equipment (the physical
item installed at a tag, identified by serial number; pumps get swapped, tags stay), equipment_model
(manufacturer + model), equipment_class (ISO 14224 category / class / type, e.g. ISO14224:PU.CE =
centrifugal pumps), well (A-07, sidetracks as "A-07 ST1"), timeseries (a PI point), allocation_node,
document (P&ID sheet), log_entry, event, notification and work_order (SAP PM, with ISO 14224 failure
mode / mechanism and maintenance activity), downtime (EC deferment: affects a well or the facility,
caused_by tags, lost oil), daily_report (EC DAY001), incident (an upset on one unit: about the unit,
includes its events, downtime and notifications), document (P&ID sheet, OEM manual, troubleshooting
guide or procedure revision; applies_to models / classes / tags; supersedes older revisions),
document_section (a section of a document, part_of it, with heading, page and text).

Links: part_of, installed_at (equipment -> tag, dated), has_timeseries, flows_to (production / allocation
flow, dated), lift_gas_to (gas lift network: compression -> lift gas headers -> jacket gas lift manifolds,
sometimes through another jacket's manifold -> gas lifted wells, dated), located_at (manifolds, wells ->
the jacket or platform they are on), mentions (document or log -> entity), sidetrack_of, classified_as (tag / equipment ->
class), instance_of (equipment -> model), is_a (model -> class, class -> parent), reports (log entry -> event) and
involves (event -> tag / well, with a role). Events (trips, starts, changeovers, shut-ins, choke changes,
alarms, leaks, instrument faults, ...) were read from operator logs by Claude; each cites its log entry. Dated links have from/to dates; pass
as_of=YYYY-MM-DD to see what was true on a day.

Measurements that aren't in PI (well tests, lab results at sample points, wall thickness at CMLs) are
landed and kept: `measurements(ref)` returns them per quantity and location next to process values.

Charts: the plot_* tools build interactive charts from Fuzer data and return a figure id, a
`[[figure:<id>]]` marker and a summary of what the chart shows (never the plotted arrays); the chart
opens at the returned link. `look_at_figure` shows one as an image when the summary isn't enough.

Any tool taking `ref` accepts a Fuzer ID (fz-...), a tag in any style (20-PT-1001A, 20PT1001A), a well
name, a PI point name or a source record key. Every value carries the source it came from; cite sources
when you answer. If a ref is ambiguous the tool returns candidates; pick one or ask the user.
"""

server = MCPServer(name="fuzer", title="Fuzer asset graph", version="0.1.0", instructions=INSTRUCTIONS)

REL_LABELS = {
    ("part_of", "out"): "part_of", ("part_of", "in"): "contains",
    ("installed_at", "out"): "installed_at", ("installed_at", "in"): "equipment_installed_here",
    ("has_timeseries", "out"): "timeseries", ("has_timeseries", "in"): "measures",
    ("flows_to", "out"): "flows_to", ("flows_to", "in"): "fed_by",
    ("mentions", "out"): "mentions", ("mentions", "in"): "mentioned_in",
    ("sidetrack_of", "out"): "sidetrack_of", ("sidetrack_of", "in"): "sidetracks",
    ("involves", "out"): "involves", ("involves", "in"): "events",
    ("reports", "out"): "reports_event", ("reports", "in"): "reported_in",
    ("classified_as", "out"): "class", ("classified_as", "in"): "members",
    ("instance_of", "out"): "model", ("instance_of", "in"): "instances",
    ("is_a", "out"): "is_a", ("is_a", "in"): "subtypes",
    ("concerns", "out"): "concerns", ("concerns", "in"): "maintenance",
    ("for_notification", "out"): "notification", ("for_notification", "in"): "work_orders",
    ("related_to", "out"): "related_events", ("related_to", "in"): "related_notifications",
    ("affects", "out"): "affects", ("affects", "in"): "downtime",
    ("caused_by", "out"): "caused_by", ("caused_by", "in"): "downtime_caused",
    ("reports_on", "out"): "reports_on", ("reports_on", "in"): "daily_reports",
    ("about", "out"): "about", ("about", "in"): "incidents",
    ("includes", "out"): "includes", ("includes", "in"): "in_incident",
    ("applies_to", "out"): "applies_to", ("applies_to", "in"): "documents",
    ("supersedes", "out"): "supersedes", ("supersedes", "in"): "superseded_by",
    ("uses_part", "out"): "spare_parts", ("uses_part", "in"): "used_by",
    ("pipes_to", "out"): "pipes_to", ("pipes_to", "in"): "piped_from",
    ("tests", "out"): "tests", ("tests", "in"): "well_tests",
    ("lift_gas_to", "out"): "supplies_lift_gas_to", ("lift_gas_to", "in"): "lift_gas_from",
}


def _plain(obj: Any) -> Any:
    """Dates and other DB types -> JSON-safe values."""
    return json.loads(json.dumps(obj, default=str))


def _resolve(conn, ref: str) -> tuple[str | None, dict | None]:
    """Fuzer ID for ref, or an error payload for the agent."""
    try:
        fid = queries.resolve_ref(conn, PACK, ref)
    except queries.AmbiguousRef as e:
        return None, {"error": f"{ref!r} is ambiguous", "candidates": _plain(e.candidates)}
    if fid is None:
        return None, {"error": f"nothing found for {ref!r}; try the search tool"}
    return fid, None


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


@server.tool(annotations=READ_ONLY)
def get_stats() -> dict[str, Any]:
    """Counts of entities by kind, links, source records and open review items."""
    with db.connect() as conn:
        return queries.stats(conn, PACK)


@server.tool(annotations=READ_ONLY)
def get_ontology() -> dict[str, Any]:
    """The data model as it exists in Fuzer: entity kinds with counts, fields and feeding sources,
    and which link types connect which kinds (with counts and sources). Call this first when unsure
    what can be asked."""
    with db.connect() as conn:
        return _plain(queries.ontology(conn))


@server.tool(annotations=READ_ONLY)
def search(query: str, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Find entities by tag, well, PI point, serial number, source key or description text.

    kind optionally filters to one entity kind (tag, equipment, well, timeseries, ...).
    """
    with db.connect() as conn:
        return _plain(queries.search(conn, PACK, query, kind, min(limit, 200)))


@server.tool(annotations=READ_ONLY)
def get_entity(ref: str, as_of: str | None = None) -> dict[str, Any]:
    """Everything Fuzer knows about one entity: chosen values (with source and any disagreeing
    sources), the source records merged into it, and its links grouped by relationship.

    as_of (YYYY-MM-DD) limits dated links to those valid on that day.
    """
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        e = queries.entity(conn, fid, _date(as_of), per_group=50, pack=PACK)

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


@server.tool(annotations=READ_ONLY)
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
        rows = graph.trace(conn, fid, rel, direction, _date(as_of), min(max_depth, 25))
    return _plain({
        "start": fid, "direction": direction, **({"as_of": as_of} if as_of else {}),
        "path": [{"depth": r["depth"], "id": r["fuzer_id"], "label": r["label"], "kind": r["kind"],
                  **({"from": r["valid_from"], "to": r["valid_to"] or "now"}
                     if r["valid_from"] or r["valid_to"] else {})} for r in rows],
    })


@server.tool(annotations=READ_ONLY)
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


@server.tool(annotations=READ_ONLY)
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


@server.tool(annotations=READ_ONLY)
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


@server.tool(annotations=READ_ONLY)
def list_members(ref: str, kind: Literal["tag", "equipment"] | None = None, limit: int = 100,
                 offset: int = 0) -> dict[str, Any]:
    """Everything in an equipment class (including its subtypes), e.g. ref="ISO14224:PU" for all pumps or
    "ISO14224:PU.CE" for centrifugal pumps, or every instance of an equipment model. Paged: use offset."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"of": fid, **queries.members(conn, fid, kind, min(limit, 500), offset)})


@server.tool(annotations=READ_ONLY)
def maintenance_history(ref: str, since: str | None = None, limit: int = 20) -> dict[str, Any]:
    """SAP notifications and work orders for a tag, a piece of equipment, every instance of a model, or a
    whole class (e.g. "ISO14224:PU.CE"), newest first, with counts by ISO 14224 failure mode. since is a
    date (YYYY-MM-DD). Use it for failure history and "what usually goes wrong with this model"."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"for": fid, **queries.maintenance(conn, fid, since, min(limit, 200))})


@server.tool(annotations=READ_ONLY)
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


@server.tool(annotations=READ_ONLY)
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


@server.tool(annotations=READ_ONLY)
def incident_detail(ref: str) -> dict[str, Any]:
    """One incident's timeline (every event, downtime record and notification in it, in order), the most
    similar past incidents on the same unit or class (how those went is often the best clue), and suggested
    reading: manual / guide sections in scope of the unit that cover the incident's failure modes."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        from . import knowledge
        return _plain({"incident": fid, "timeline": queries.incident_timeline(conn, fid),
                       "similar": queries.similar_incidents(conn, fid, 10),
                       "suggested_reading": knowledge.for_incident(conn, fid, 5)})


@server.tool(annotations=READ_ONLY)
def search_knowledge(question: str, ref: str | None = None, limit: int = 8,
                     include_superseded: bool = False, failure_modes: list[str] | None = None) -> dict[str, Any]:
    """Search OEM manuals, troubleshooting guides, procedures and past incidents, returning cited
    passages (document, revision, page, section, snippet with [[matches]]).

    ref scopes the search to what applies to one tag / equipment / well: the item itself, its model's
    manuals, its class's procedures and its area's guides, ranked closest first. Always cite the document
    and page you use. Superseded revisions are left out unless include_superseded is true. failure_modes
    (ISO 14224 codes such as VIB, ELP, FTS, OHE) boosts passages about those modes."""
    from . import knowledge
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain({"for": fid or "facility",
                       "passages": knowledge.search(conn, question, fid, min(limit, 30), include_superseded,
                                                    failure_modes)})


@server.tool(annotations=READ_ONLY)
def documents_for(ref: str) -> dict[str, Any]:
    """Current documents that apply to a tag / equipment / model / class: its OEM manual (via model), class
    procedures and area troubleshooting guides, closest first, with why each applies."""
    from . import knowledge
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"for": fid, "documents": knowledge.documents_for(conn, fid)})


@server.tool(annotations=READ_ONLY)
def record_history(ref: str, limit: int = 20) -> dict[str, Any]:
    """How the source records behind an entity changed: which system, when, and each field's old and new
    value (e.g. a manufacturer corrected in SAP, a description edited, a record removed from an export)."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"for": fid, "changes": queries.record_history(conn, PACK, fid, min(limit, 200))})


@server.tool(annotations=READ_ONLY)
def lessons_learned(status: Literal["approved", "suggested", "all"] = "approved", limit: int = 20) -> dict[str, Any]:
    """Lessons learned per equipment model (or class) and ISO 14224 failure mode: the corrective work that has
    fixed it before, how often it held for 30 days, and the incidents behind it. Approved lessons were
    reviewed by engineers; suggested ones are drafts. Approved lessons also come back from search_knowledge."""
    from . import lessons
    with db.connect() as conn:
        return _plain({"lessons": lessons.listing(conn, None if status == "all" else status, min(limit, 200))})


@server.tool(annotations=READ_ONLY)
def spare_parts(ref: str) -> dict[str, Any]:
    """Spare parts for a tag, piece of equipment or equipment model: SAP material, manufacturer part number,
    stock on hand (0 = none in stock), storage bin, how many units use it, and manual sections naming it."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"for": fid, "parts": queries.spares(conn, fid)})


@server.tool(annotations=READ_ONLY)
def troubleshooting_brief(ref: str) -> dict[str, Any]:
    """Start here when troubleshooting. For an incident, tag or piece of equipment: what happened (timeline),
    similar past incidents and how they ended, engineer-approved lessons for its model / class, manual and
    guide sections covering its failure modes, maintenance history, downtime, spares in stock, and suggested
    checks. Every item has the id it came from: cite them, and use get_entity / search_knowledge to dig in."""
    from . import assistant
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain(assistant.brief(conn, PACK, fid))


@server.prompt(title="Troubleshoot an upset")
def troubleshoot(what: str) -> str:
    """Guided troubleshooting of an event, incident or piece of equipment."""
    return f"""Help an operations engineer troubleshoot: {what}

1. Call troubleshooting_brief with the tag, equipment or incident. If it's ambiguous, ask which one.
2. Summarise what happened from the timeline, then what has worked before: approved lessons first, then
   similar past incidents and how they ended.
3. Give a short, ordered list of checks. Back each with a citation (lesson, document and page, incident or
   work order). Say plainly where Fuzer has no evidence.
4. Mention spares that are out of stock and recent repeat failures.
5. Never instruct anyone to operate plant equipment directly; recommend checks for the responsible
   operator or engineer, and follow site procedures (permit to work, isolation)."""


@server.tool(annotations=READ_ONLY)
def process_values(ref: str, at: str | None = None, hours_before: int = 48, hours_after: int = 24) -> dict[str, Any]:
    """Process values (PI points on the unit and its instruments) around a time: per point min / max /
    mean / last and the provider used. For an incident, at defaults to its start. at is ISO date or time."""
    from . import timeseries
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        out = timeseries.around(conn, fid, at, min(hours_before, 720), min(hours_after, 720))
    if "series" in out:
        out["series"] = [{k: v for k, v in s.items() if k != "values"} for s in out["series"]]
    return _plain(out)


@server.tool(annotations=READ_ONLY)
def measurements(ref: str, quantity: str | None = None, since: str | None = None, until: str | None = None,
                 history: bool = False) -> dict[str, Any]:
    """Sparse measurements that aren't in PI, on a well, tag, equipment, pipeline or an incident's unit:
    well tests (oil_rate, water_cut, gor, lift_gas_rate, wellhead_pressure), lab results at sample points
    (bsw, salt, density, oil_in_water, h2s, co2, water_dewpoint, chloride) and wall thickness at CMLs
    (wall_thickness, mm). One series per quantity and location with the latest value, min / max, the
    change per year (wall loss rate, decline) and the last three readings; history=true returns every
    reading. A qualifier "<" means below the detection limit. Dates YYYY-MM-DD."""
    from . import measurements as m
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


# --- asset-wide operations (operations.py): dates default to the latest daily report ("today") ----

@server.tool(annotations=READ_ONLY)
def production_summary(since: str | None = None, until: str | None = None,
                       by: Literal["month", "week"] = "month") -> dict[str, Any]:
    """Oil actual vs potential (production efficiency %), gas export, lift gas and flare per month or
    week, with deferred oil by reason. Default: the last 12 months. Dates YYYY-MM-DD."""
    from . import operations
    with db.connect() as conn:
        return _plain(operations.production_summary(conn, since, until, by))


@server.tool(annotations=READ_ONLY)
def deferment_pareto(by: Literal["reason", "category", "tag", "class", "well"] = "reason",
                     since: str | None = None, until: str | None = None, limit: int = 15) -> dict[str, Any]:
    """Deferred (lost) oil ranked by downtime reason, reason category, causing tag, its ISO 14224 class,
    or affected well, with each one's share. Default: the last 12 months."""
    from . import operations
    with db.connect() as conn:
        return _plain(operations.deferment_pareto(conn, by, since, until, min(limit, 100)))


@server.tool(annotations=READ_ONLY)
def production_constraints(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """What has been limiting production: losses by reason with the units that cause them, frequency,
    last occurrence and trend (worse / steady / better). Default: the last 12 months."""
    from . import operations
    with db.connect() as conn:
        return _plain(operations.constraints(conn, since, until))


@server.tool(annotations=READ_ONLY)
def well_performance(ref: str | None = None, since: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Well tests. With ref (a well): its test history (oil rate, water cut, GOR, lift gas, WHP). Without:
    wells ranked by oil rate decline over the window, water cut risers, and deferment per well.
    Default window: 12 months."""
    from . import operations
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain(operations.well_performance(conn, fid, since, min(limit, 100)))


@server.tool(annotations=READ_ONLY)
def gas_lift_network(ref: str | None = None, since: str | None = None, limit: int = 15) -> dict[str, Any]:
    """The gas lift network: compression -> lift gas headers -> jacket gas lift manifolds (some through
    another jacket) -> gas lifted wells. Per header: jackets, wells, lift gas and oil from latest tests. Per
    well: lift gas, oil per lift gas and measured response to more lift gas (Sm3/d oil per extra kSm3/d),
    least and most responsive wells, wells lifting mostly water. With ref (header, manifold, jacket,
    compressor or well): only that part, plus its supply path. Default window: 12 months of tests."""
    from . import operations
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain(operations.gas_lift_network(conn, fid, since, min(limit, 100)))


@server.tool(annotations=READ_ONLY)
def bad_actors(since: str | None = None, until: str | None = None, limit: int = 15) -> dict[str, Any]:
    """Equipment ranked by failures (SAP notifications), downtime caused and repeat ISO 14224 failure
    modes, with MTBF per unit and per class. Default window: 2 years."""
    from . import operations
    with db.connect() as conn:
        return _plain(operations.bad_actors(conn, since, until, min(limit, 100)))


@server.tool(annotations=READ_ONLY)
def work_order_backlog(as_of: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Open SAP work orders by priority, type and age; overdue orders (past basic finish); the oldest;
    open notifications with no order planned yet."""
    from . import operations
    with db.connect() as conn:
        return _plain(operations.work_order_backlog(conn, as_of, min(limit, 100)))


@server.tool(annotations=READ_ONLY)
def maintenance_compliance(as_of: str | None = None, horizon_days: int = 90, limit: int = 25) -> dict[str, Any]:
    """Preventive maintenance (PM) and inspection (INSP) plans: on-time share, overdue tasks and tasks
    due within the horizon; tasks that need a shutdown are marked."""
    from . import operations
    with db.connect() as conn:
        return _plain(operations.maintenance_compliance(conn, as_of, min(horizon_days, 1100), min(limit, 200)))


@server.tool(annotations=READ_ONLY)
def turnaround_scope(revision: str | None = None) -> dict[str, Any]:
    """Turnaround (shutdown) scope: work orders planned for the revision (default: the next one), plans
    that need the unit down and aren't in scope yet, bad actors not in scope, and spares for scoped
    units that are out of stock (order long-lead items now)."""
    from . import operations
    with db.connect() as conn:
        return _plain(operations.turnaround_scope(conn, revision))


@server.tool(annotations=READ_ONLY)
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
    from . import value
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain(value.ranked(conn, PACK, tuple(kinds or value.KINDS), as_of, by, fid, min(limit, 200)))


@server.tool(annotations=READ_ONLY)
def link_text(text: str) -> list[dict[str, Any]]:
    """Find tags, wells and known plant nicknames ("test sep") in free text and link them to entities.
    Short tags without an area (e.g. "PT-2001") may exist in several areas; those come back with
    candidates instead of one fuzer_id."""
    with db.connect() as conn:
        return _plain(queries.link_text(conn, PACK, text))


@server.tool(annotations=READ_ONLY)
def list_reviews(kind: Literal["match", "conflict"] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Open questions waiting for a person: possible duplicate records ("match") and sources that
    disagree on a value ("conflict"). Decisions are made by people in the Fuzer web UI."""
    with db.connect() as conn:
        rows = queries.reviews(conn, "open", kind, min(limit, 200))
    return _plain([{"id": r["id"], "kind": r["kind"], "entity": r["label"], **r["payload"]} for r in rows])



# --- charts (plots/): built here from Fuzer data; the agent gets an id, a marker and a summary -------

# Read-only like every other tool: a chart is kept (assistant_figure) so the answer can show it, but no
# plant or register data changes, and clients shouldn't ask for approval per chart.
CHART = READ_ONLY


def _chart(build, params: dict, refs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve refs, build and store a chart; the result is what the agent sees (never the arrays)."""
    from . import plots
    with db.connect() as conn:
        args = dict(params)
        for k, ref in (refs or {}).items():
            if ref is None:
                args[k] = None
                continue
            ids = []
            for r in ref if isinstance(ref, list) else [ref]:
                fid, err = _resolve(conn, r)
                if err:
                    return err
                ids.append(fid)
            args[k] = ids if isinstance(ref, list) else ids[0]
        try:
            fig = build(conn, **args)
        except plots.ChartError as e:
            return {"error": str(e)}
        return _plain(plots.store(conn, fig, _plain({"chart": fig.kind, **params, **(refs or {})})))


@server.tool(annotations=CHART)
def plot_trend(refs: list[str], at: str | None = None, hours_before: int = 48, hours_after: int = 24) -> dict[str, Any]:
    """Chart process values (PI / ADX) on stacked panels sharing time, with incident lead-ins shaded.
    refs: tags, wells or PI points (a tag gives its instruments' points; up to 6 series). at: the
    centre (default: the first ref's latest incident)."""
    from .plots import general
    return _chart(general.trend, {"at": at, "hours_before": min(hours_before, 720), "hours_after": min(hours_after, 720)},
                  {"ids": refs})


@server.tool(annotations=CHART)
def plot_measurements(ref: str, quantity: str | None = None, since: str | None = None,
                      until: str | None = None) -> dict[str, Any]:
    """Chart landed measurements over time on a well, tag, equipment or pipeline (well tests, lab
    results, wall thickness): a panel per quantity, a line per location. Hollow points are below the
    detection limit."""
    from .plots import general
    return _chart(general.measurements, {"quantity": quantity, "since": since, "until": until}, {"fid": ref})


@server.tool(annotations=CHART)
def plot_xy(x: str, y: str, at: str | None = None, hours_before: int = 72, hours_after: int = 24) -> dict[str, Any]:
    """Chart one PI point against another at the same times (operating envelope), coloured by time, with
    a straight-line fit and r. x and y are PI points (timeseries)."""
    from .plots import general
    return _chart(general.xy, {"at": at, "hours_before": min(hours_before, 720), "hours_after": min(hours_after, 720)},
                  {"x_id": x, "y_id": y})


@server.tool(annotations=CHART)
def plot_bar(dataset: Literal["deferment_by_reason", "deferment_by_well", "deferment_by_tag", "deferment_by_class",
                              "failures_by_tag", "oil_change_by_well"],
             since: str | None = None, until: str | None = None, limit: int = 15) -> dict[str, Any]:
    """Chart a ranking as bars: deferred oil by reason / well / tag / class (Pareto with the cumulative
    share), failures by unit, or oil rate change by well. Dates YYYY-MM-DD."""
    from .plots import general
    return _chart(general.bar, {"dataset": dataset, "since": since, "until": until, "limit": min(limit, 40)})


@server.tool(annotations=CHART)
def plot_distribution(ref: str, split_at: str, hours_before: int = 168, hours_after: int = 168) -> dict[str, Any]:
    """Chart one PI point before and after a moment (a repair, a setpoint change) as two boxes, with
    medians and spread in the summary."""
    from .plots import general
    return _chart(general.distribution, {"split_at": split_at, "hours_before": min(hours_before, 2160),
                                         "hours_after": min(hours_after, 2160)}, {"fid": ref})


@server.tool(annotations=CHART)
def plot_custom(title: str, traces: list[dict[str, Any]], x_title: str | None = None,
                y_title: str | None = None) -> dict[str, Any]:
    """Chart numbers you worked out yourself, when no other chart covers them: up to 8 traces
    [{name, x, y, kind: line | markers | bar}], 500 points in all. Labelled as assistant-entered data;
    prefer the other chart tools, which read the data themselves."""
    from .plots import general
    return _chart(lambda conn, **kw: general.custom(**kw), {"title": title, "traces": traces, "x_title": x_title,
                                                           "y_title": y_title})


@server.tool(annotations=CHART)
def plot_incident(ref: str) -> dict[str, Any]:
    """Chart an incident on one page (an incident, or a unit's latest): the process values that moved
    most in the lead-in, stacked, with its log events, trips, notifications and downtime as markers."""
    from .plots import troubleshooting
    return _chart(troubleshooting.incident, {}, {"fid": ref})


@server.tool(annotations=CHART)
def plot_correlation(ref: str, at: str | None = None, hours_before: int = 72, hours_after: int = 12,
                     upstream: bool = True, target: str | None = None) -> dict[str, Any]:
    """Chart how the process values on a unit (and on what feeds it) move together, as a correlation
    heatmap. target: a PI point (e.g. the one that tripped); the summary then gives, for each other
    point, the lag at which it best matches the target: what moved first."""
    from .plots import troubleshooting
    return _chart(troubleshooting.correlation, {"at": at, "hours_before": min(hours_before, 720),
                                                "hours_after": min(hours_after, 720), "upstream": upstream},
                  {"fid": ref, "target": target})


@server.tool(annotations=CHART)
def plot_vibration(ref: str, at: str | None = None, days: int = 30) -> dict[str, Any]:
    """Chart a machine's overall vibration against ISO 20816 zones A-D, with temperature on a second
    axis and incident lead-ins shaded. ref: a pump or compressor tag."""
    from .plots import troubleshooting
    return _chart(troubleshooting.vibration, {"at": at, "days": min(days, 120)}, {"fid": ref})


@server.tool(annotations=CHART)
def plot_decline(well: str, model: Literal["auto", "exponential", "hyperbolic", "harmonic"] = "auto",
                 forecast_months: int = 12) -> dict[str, Any]:
    """Chart a well's oil rate decline from its tests (log scale) with an Arps fit after the last step
    change (valve failure, breakthrough, repair), and a forecast. The summary has qi, b, the decline
    per year, step changes and the forecast oil."""
    from .plots import production
    return _chart(production.decline, {"model": model, "forecast_months": min(forecast_months, 60)}, {"well": well})


@server.tool(annotations=CHART)
def plot_water_diagnostic(well: str) -> dict[str, Any]:
    """Chart a well's water diagnostic: water cut against cumulative oil, and WOR and its derivative on
    log scales (Chan). Rising derivative = channelling; falling then rising = coning; a step = breakthrough."""
    from .plots import production
    return _chart(production.water_diagnostic, {}, {"well": well})


@server.tool(annotations=CHART)
def plot_gas_lift_curve(well: str, breakeven: float = 0.5) -> dict[str, Any]:
    """Chart a gas lifted well's performance curve: oil against lift gas from its tests (decline taken
    out), the current point, the marginal response there, and the optimum where one more kSm3/d of gas
    gains `breakeven` Sm3/d of oil. Tests far below the curve (valve failed open) are shown apart."""
    from .plots import production
    return _chart(production.gas_lift_curve, {"breakeven": breakeven}, {"well": well})


@server.tool(annotations=CHART)
def plot_gas_lift_allocation(ref: str | None = None) -> dict[str, Any]:
    """Chart every gas lifted well's marginal response (oil per extra lift gas) grouped by lift gas
    header, with suggested gas moves. ref: a header, jacket or manifold to narrow it; default all."""
    from .plots import production
    return _chart(production.gas_lift_allocation, {}, {"ref": ref})


@server.tool(annotations=CHART)
def plot_wall_thickness(ref: str | None = None, kind: Literal["pipeline", "tag"] | None = None,
                        min_required_mm: float | None = None) -> dict[str, Any]:
    """Chart wall thickness: with ref, each CML on that vessel / piping / pipeline over time with loss
    rates (and retirement dates if min_required_mm is given); without ref, the items losing wall
    fastest across the asset (kind narrows to pipelines or tags)."""
    from .plots import maintenance
    return _chart(maintenance.wall_thickness, {"kind": kind, "min_required_mm": min_required_mm}, {"fid": ref})


@server.tool(annotations=CHART)
def plot_reliability(ref: str, method: Literal["weibull", "crow_amsaa"] = "weibull") -> dict[str, Any]:
    """Chart reliability from failure notifications on a unit or an equipment class: a Weibull plot of
    times between failures (beta, eta), or Crow-AMSAA cumulative failures (beta > 1: speeding up)."""
    from .plots import maintenance
    return _chart(maintenance.reliability, {"method": method}, {"fid": ref})


@server.tool(annotations=CHART)
def plot_bad_actors(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Chart bad actors as bubbles: failures against oil lost to the downtime they caused, size =
    downtime events, colour = ISO 14224 class. Default window: 2 years."""
    from .plots import maintenance
    return _chart(maintenance.bad_actors, {"since": since, "until": until})


@server.tool(annotations=CHART)
def plot_pump_curve(ref: str, days: int = 365) -> dict[str, Any]:
    """Chart a pump against its OEM curve: operating points from PI (head from discharge minus suction
    pressure, flow) coloured by time over the head and efficiency curves, with the BEP and preferred
    operating region. The summary gives head against the curve month by month (falling = wear rings /
    impeller) and the share of time in the preferred region. ref: a pump tag or equipment."""
    from .plots import maintenance
    return _chart(maintenance.pump_curve, {"days": min(days, 1100)}, {"fid": ref})


@server.tool(annotations=CHART)
def plot_choke(ref: str, days: int = 730) -> dict[str, Any]:
    """Chart a well's production choke against its OEM characteristic: effective Cv at each well test
    (oil wells) or from PI rates (gas wells) against the opening. Above the curve = eroded trim; below =
    restricted (scale, hydrate). The summary gives Cv against the curve by quarter and replacements.
    ref: a well or its choke tag. Says which datasheet is waiting when the model has no curve yet."""
    from .plots import production
    return _chart(production.choke, {"days": min(days, 1100)}, {"fid": ref})


@server.tool(annotations=READ_ONLY)
def equipment_curves(ref: str) -> dict[str, Any]:
    """The OEM curves (pump head / efficiency / NPSHr / power against flow, or choke Cv against opening)
    and ratings (BEP, operating regions, max Cv) for an equipment model, or the model of an equipment
    item or tag, and where they came from (vendor export or an accepted datasheet reading)."""
    from . import curves
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        model = curves.model_of(conn, fid)
        out = curves.for_model(conn, model) if model else None
        return _plain(out or {"model_id": model, "series": {}, "waiting": curves.waiting(conn, model) if model else []})


@server.tool(annotations=READ_ONLY)
def look_at_figure(figure: str) -> list[Any]:
    """See a chart you made, as an image. Only when its summary leaves you unsure how it reads (a fit
    that may miss the late points, an outlier that may set the scale, a chart that may be empty) or the
    user asks how it looks; not for every chart. If it looks wrong, change the options or say so."""
    from mcp.server.mcpserver.utilities.types import Image

    from . import plots
    with db.connect() as conn:
        row = plots.get(conn, figure.removeprefix("[[figure:").removesuffix("]]"))
    if row is None:
        return [json.dumps({"error": f"no figure {figure!r}"})]
    return [json.dumps(_plain({"figure": row["id"], "title": row["title"], "summary": row["summary"]})),
            Image(data=plots.png(row["spec"]), format="png")]

def main(http: bool = False, host: str = "127.0.0.1", port: int = 8001) -> None:
    db.database_url()  # start embedded Postgres up front if it's in use
    with db.connect() as conn:
        db.init_schema(conn)  # an older database gets any newer tables
    if http:
        server.run("streamable-http", host=host, port=port)
    else:
        server.run()
