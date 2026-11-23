"""An entity's place in the register, with the work rolled up through it: the chain above it, and the first levels
beneath it, each node carrying the open work about it or anything in it and how many things in it are down now.

    view(conn, fid, levels)   {up, children, more, total, kinds, open_work, down}

Open work is the List's open rows (`worklist.rows`, filtered `within` the entity), so a node's count is the number
All work shows when filtered to it. Down now is `status_now.down`: a newest downtime with no end.
"""

from ..graph import hierarchy


def view(conn, fid: str, levels: int = 2) -> dict:
    from . import status_now, worklist
    from .attention import RANK, band

    tree = hierarchy.subtree(conn, fid)
    out = hierarchy.beneath(conn, fid, levels, tree)
    work = worklist.rows(conn, within=fid, limit=100_000)["rows"]
    down = status_now.down(conn, list(tree))

    def chain(nid: str) -> list[str]:
        """The node and every ancestor up to (not including) fid."""
        seen: list[str] = []
        while nid in tree and nid not in seen:
            seen.append(nid)
            nid = tree[nid]["parent"]
        return seen

    counts: dict[str, int] = {}
    worst: dict[str, str] = {}
    for r in work:
        hit: set[str] = set()
        for s in r.get("subjects") or []:
            hit.update(chain(s))
        b = band(r.get("priority"))
        for nid in hit:
            counts[nid] = counts.get(nid, 0) + 1
            if nid not in worst or RANK[b] < RANK[worst[nid]]:
                worst[nid] = b
    downs: dict[str, int] = {}
    for nid in down:
        for a in chain(nid):
            downs[a] = downs.get(a, 0) + 1

    def dress(node: dict) -> dict:
        nid = node["id"]
        node.update(open_work=counts.get(nid, 0), work_band=worst.get(nid), down=downs.get(nid, 0))
        for c in node["children"]:
            dress(c)
        return node

    for c in out["children"]:
        dress(c)
    return {"up": hierarchy.up(conn, fid), **out, "open_work": len(work), "down": len(down)}


# The links an entity page's Connections tab draws.
NETWORK_RELS = ("flows_to", "pipes_to", "lift_gas_to", "relieves_to", "drives", "controls")
NO_TRENDS = ("document", "document_section", "log_entry", "timeseries")


def tabs(conn, fid: str) -> dict:
    """What an entity page has to show on each tab, counting the entity and everything beneath it, so a tab with
    nothing in it is left off: {work, schedule, documents, map, scene, trends, connections}. Counts are numbers;
    the rest say only whether there is something."""
    from ..graph import knowledge
    from . import worklist

    row = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()
    if row is None:
        return {}
    kind = row["kind"]
    tree = hierarchy.subtree(conn, fid)
    ids = [fid, *tree]
    work = worklist.rows(conn, state="all", within=fid, limit=1)["total"]
    schedule = conn.execute(
        "select count(*) as n from plan_item where status <> 'cancelled' and end_at > now() - interval '7 days'"
        " and (subjects && %(ids)s or area_id = any(%(ids)s))", {"ids": ids}).fetchone()["n"]
    docs = len(knowledge.documents_for(conn, fid)) + len(knowledge.documents_beneath(conn, fid))
    located = conn.execute(
        """with recursive up (id, depth) as (
               select %(fid)s::text, 0
               union
               select e.dst, up.depth + 1 from up join edge e on e.src = up.id
                    and e.rel in ('part_of', 'installed_at', 'located_at', 'about') where up.depth < 4)
           select exists (select 1 from geo_index where fuzer_id = any(%(ids)s) or fuzer_id in (select id from up)) as yes""",
        {"fid": fid, "ids": ids}).fetchone()["yes"]
    up = hierarchy.up(conn, fid)
    scene = kind in ("facility", "area") and bool(tree) or any(u["kind"] in ("area", "facility") for u in up)
    trends = kind not in NO_TRENDS and conn.execute(
        """select exists (
               select 1 from edge t where t.rel = 'has_timeseries' and (t.src = %(fid)s
                   or t.src in (select dst from edge i where i.src = %(fid)s and i.rel = 'installed_at'))
           ) or %(kind)s in ('well', 'pipeline', 'pipeline_segment') as yes""", {"fid": fid, "kind": kind}).fetchone()["yes"]
    connections = conn.execute(
        "select exists (select 1 from edge where rel = any(%(rels)s) and (src = any(%(ids)s) or dst = any(%(ids)s))) as yes",
        {"rels": list(NETWORK_RELS), "ids": ids}).fetchone()["yes"]
    return {"work": work, "schedule": schedule, "documents": docs, "map": bool(located), "scene": bool(scene),
            "trends": bool(trends), "connections": bool(connections), "beneath": len(tree)}


def picture(conn, fid: str, days_ahead: int = 14, top: int = 8) -> dict | None:
    """The facts an entity's summary is written from, the entity and everything beneath it rolled up, compact enough
    for one short model call or an assistant tool: what it is and where it sits, what is beneath it by kind, the
    children carrying open work or down, the open work (most valuable first), what is down now, the jobs planned
    in the next `days_ahead` days and how many documents apply. Every list is cut to `top` with its total kept."""
    from datetime import UTC, datetime, timedelta

    from . import plan, status_now, worklist

    row = conn.execute(
        """select e.kind, coalesce(l.label, e.fuzer_id) as label, g.value #>> '{}' as description from entity e
           left join entity_label l on l.fuzer_id = e.fuzer_id
           left join golden g on g.fuzer_id = e.fuzer_id and g.field = 'description' where e.fuzer_id = %s""",
        (fid,)).fetchone()
    if row is None:
        return None
    tree = view(conn, fid, levels=1)
    ids = [fid, *hierarchy.subtree(conn, fid)]
    work = worklist.rows(conn, within=fid, limit=500)
    names = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)",
        (list({s for r in work["rows"][:top] for s in r.get("subjects") or []}),))}
    down = status_now.down(conn, ids)
    now = datetime.now(UTC)
    planned = [i for i in plan.items(conn, now, now + timedelta(days=days_ahead), within=fid)
               if i["status"] not in ("done", "cancelled")]
    hot = sorted((c for c in tree["children"] if c["open_work"] or c["down"]),
                 key=lambda c: (-c["down"], -c["open_work"], c["label"]))
    return {
        "id": fid, "kind": row["kind"], "label": row["label"], "description": row["description"],
        "sits_in": [u["label"] for u in tree["up"]],
        "beneath": tree["kinds"],
        "children_needing_attention": [{"id": c["id"], "label": c["label"], "kind": c["kind"], "open_work": c["open_work"],
                                        "worst_priority": c["work_band"], "down": c["down"]} for c in hot[:top]],
        "open_work": {"total": work["total"], "overdue": work["facets"].get("overdue", 0),
                      "by_stage": work["facets"].get("stage", {}),
                      "top": [{"title": r["title"], "kind": r["kind_label"], "stage": r["stage"], "holder": r["holder"],
                               "overdue": r["overdue"], "value_usd": r.get("value_usd"),
                               "about": [names.get(s, s) for s in (r.get("subjects") or [])[:3]],
                               "case": r.get("item_id")} for r in work["rows"][:top]]},
        "down_now": {"total": len(down), "items": [{"id": k, "since": v["start"], "reason": v["reason"]}
                                                    for k, v in list(down.items())[:top]]},
        "planned_next_days": {"days": days_ahead, "total": len(planned),
                              "items": [{"title": i["title"], "start": i["start_at"].date().isoformat(), "kind": i["kind"],
                                         "needs_shutdown": i["needs_shutdown"],
                                         "clash": any(c["kind"] != "bundle" for c in i["clashes"])} for i in planned[:top]]},
        "as_of": now.isoformat(timespec="minutes"),
    }
