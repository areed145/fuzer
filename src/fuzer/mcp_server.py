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
flow, dated), mentions (document or log -> entity), sidetrack_of, classified_as (tag / equipment ->
class), instance_of (equipment -> model), is_a (model -> class, class -> parent), reports (log entry -> event) and
involves (event -> tag / well, with a role). Events (trips, starts, changeovers, shut-ins, choke changes,
alarms, leaks, instrument faults, ...) were read from operator logs by Claude; each cites its log entry. Dated links have from/to dates; pass
as_of=YYYY-MM-DD to see what was true on a day.

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
def trace_flow(ref: str, direction: Literal["up", "down"] = "up", as_of: str | None = None,
               max_depth: int = 10) -> dict[str, Any]:
    """Follow the production / allocation network from an entity.

    direction "up" finds what feeds it (e.g. wells feeding a separator); "down" finds where it goes
    (e.g. separator -> surge drum -> fiscal meter -> sales point). as_of (YYYY-MM-DD) uses the routing
    valid on that day; without it, all routings past and present are included.
    """
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        rows = graph.trace(conn, fid, "flows_to", direction, _date(as_of), min(max_depth, 25))
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
    """One incident's timeline (every event, downtime record and notification in it, in order) and the
    most similar past incidents on the same unit or equipment class: how those went is often the best clue."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"incident": fid, "timeline": queries.incident_timeline(conn, fid),
                       "similar": queries.similar_incidents(conn, fid, 10)})


@server.tool(annotations=READ_ONLY)
def search_knowledge(question: str, ref: str | None = None, limit: int = 8,
                     include_superseded: bool = False) -> dict[str, Any]:
    """Search OEM manuals, troubleshooting guides, procedures and past incidents, returning cited
    passages (document, revision, page, section, snippet with [[matches]]).

    ref scopes the search to what applies to one tag / equipment / well: the item itself, its model's
    manuals, its class's procedures and its area's guides, ranked closest first. Always cite the document
    and page you use. Superseded revisions are left out unless include_superseded is true."""
    from . import knowledge
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain({"for": fid or "facility",
                       "passages": knowledge.search(conn, question, fid, min(limit, 30), include_superseded)})


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


def main(http: bool = False, host: str = "127.0.0.1", port: int = 8001) -> None:
    db.database_url()  # start embedded Postgres up front if it's in use
    if http:
        server.run("streamable-http", host=host, port=port)
    else:
        server.run()
