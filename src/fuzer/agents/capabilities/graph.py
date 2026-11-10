"""Graph capabilities, declared with `capability` (#133)."""

from typing import Any, Literal

from ...core import db
from ...graph import edges, ontology, queries
from .. import mcp_server
from ..mcp_server import REL_LABELS, _date, _plain, _resolve, capability


@capability("orchestrator", kind="query")
def get_stats() -> dict[str, Any]:
    """Counts of entities by kind, links, source records and open review items."""
    with db.connect() as conn:
        return queries.stats(conn, mcp_server.PACK)


@capability("all", kind="query")
def get_ontology() -> dict[str, Any]:
    """The data model: entity kinds with what each is, counts, fields and feeding sources; which link
    types connect which kinds (with counts and sources); and what each link type means. Includes kinds
    and link types added for this asset. Call this first when unsure what can be asked."""
    with db.connect() as conn:
        m = ontology.model(conn)
    return _plain({
        "kinds": [{k: v for k, v in kind.items() if k in ("kind", "label", "description", "count", "fields", "sources")}
                  for kind in m["kinds"]],
        "relations": [{k: v for k, v in r.items() if k in ("src_kind", "rel", "dst_kind", "n", "dated", "sources")}
                      for r in m["relations"]],
        "link_types": {r["name"]: {"description": r["description"], "from": r["from_kinds"] or "any",
                                   "to": r["to_kinds"] or "any", "dated": r["dated"]} for r in m["model"]["relations"]},
        "undefined": m["needs_definition"]["kinds"] + m["needs_definition"]["relations"],
    })


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


@capability(
    "orchestrator", "production-surveillance", "wells-subsurface", "field-operations-facilities", kind="query"
)
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


@capability("wells-subsurface", "field-operations-facilities", "maintenance-integrity", kind="query")
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


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "hse-incident-management",
    kind="query"
)
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


@capability(
    "wells-subsurface", "field-operations-facilities", "maintenance-integrity", "work-management-supply-chain",
    "hse-incident-management", kind="query"
)
def maintenance_history(ref: str, since: str | None = None, limit: int = 20) -> dict[str, Any]:
    """SAP notifications and work orders for a tag, a piece of equipment, every instance of a model, or a
    whole class (e.g. "ISO14224:PU.CE"), newest first, with counts by ISO 14224 failure mode. since is a
    date (YYYY-MM-DD). Use it for failure history and "what usually goes wrong with this model"."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"for": fid, **queries.maintenance(conn, fid, since, min(limit, 200))})


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "hse-incident-management",
    kind="query"
)
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


@capability("field-operations-facilities", "maintenance-integrity", "work-management-supply-chain", kind="query")
def spare_parts(ref: str) -> dict[str, Any]:
    """Spare parts for a tag, piece of equipment or equipment model: SAP material, manufacturer part number,
    stock on hand (0 = none in stock), storage bin, how many units use it, and manual sections naming it."""
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"for": fid, "parts": queries.spares(conn, fid)})


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
