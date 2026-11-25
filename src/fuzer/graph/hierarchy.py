"""The register's containment as a tree: what an entity sits in, and what sits in it.

    CONTAINMENT              the links that place one entity inside another: part_of, installed_at, located_at
    up(conn, fid)            what it sits in, nearest first: [{id, kind, label}], up to the top
    subtree(conn, fid)       everything beneath it, each with its parent and depth: {id: {parent, depth, kind, label}}
    beneath(conn, fid, n)    the first `n` levels beneath it as a tree, each node with how many sit beneath it

A schedule's activities and a document's sections are part_of too, but they are not places or things in the plant,
so they are left out of the asset tree (OFF_TREE). An entity has one place in it: where the register gives it two
parents, the one reached first wins, so nothing is counted twice when totals roll up.
"""

CONTAINMENT = ("part_of", "installed_at", "located_at")
# What work can be about: the assets a case is filed under (ops.worklist, nearest well or line first, then the tag, then
# equipment) and the only kinds a person may match work to (a message's entity, P5.26f). Places, records and readings
# (a work order, a log entry, a time series) are not.
WORK_KINDS = ("well", "pipeline", "pipeline_segment", "tag", "equipment")
OFF_TREE = ("activity", "document_section", "timeseries")
MAX_DEPTH = 8
PER_LEVEL = 200  # children listed under one node; the rest are counted


def up(conn, fid: str) -> list[dict]:
    """What `fid` sits in, nearest first, up to the top of the register."""
    rows = conn.execute(
        """
        with recursive up (id, depth) as (
            select %(fid)s::text, 0
            union
            select e.dst, up.depth + 1 from up
            join edge e on e.src = up.id and e.rel = any(%(rels)s) and (e.valid_to is null or e.valid_to >= current_date)
            where up.depth < %(max)s)
        select up.id, min(up.depth) as depth, n.kind, coalesce(l.label, up.id) as label
        from up join entity n on n.fuzer_id = up.id and n.merged_into is null
        left join entity_label l on l.fuzer_id = up.id
        where up.id <> %(fid)s
        group by up.id, n.kind, l.label
        order by depth, label
        """, {"fid": fid, "rels": list(CONTAINMENT), "max": MAX_DEPTH}).fetchall()
    out, seen = [], set()
    for r in rows:  # one entity per level: the chain, not every parent
        if r["depth"] in seen:
            continue
        seen.add(r["depth"])
        out.append({"id": r["id"], "kind": r["kind"], "label": r["label"]})
    return out


def subtree(conn, fid: str, max_depth: int = MAX_DEPTH) -> dict[str, dict]:
    """Everything beneath `fid` (not itself), each with the parent it was reached through and its depth."""
    rows = conn.execute(
        """
        with recursive down (id, parent, depth) as (
            select %(fid)s::text, null::text, 0
            union
            select e.src, down.id, down.depth + 1 from down
            join edge e on e.dst = down.id and e.rel = any(%(rels)s) and (e.valid_to is null or e.valid_to >= current_date)
            join entity n on n.fuzer_id = e.src and n.merged_into is null and n.retired_at is null
                 and not n.kind = any(%(off)s)
            where down.depth < %(max)s)
        select distinct on (d.id) d.id, d.parent, d.depth, n.kind, coalesce(l.label, d.id) as label,
               g.value #>> '{}' as description
        from down d join entity n on n.fuzer_id = d.id
        left join entity_label l on l.fuzer_id = d.id
        left join golden g on g.fuzer_id = d.id and g.field = 'description'
        where d.depth > 0
        order by d.id, d.depth, d.parent
        """, {"fid": fid, "rels": list(CONTAINMENT), "off": list(OFF_TREE), "max": max_depth}).fetchall()
    return {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in rows if r["id"] != fid}


def beneath(conn, fid: str, levels: int = 2, tree: dict[str, dict] | None = None) -> dict:
    """The first `levels` levels beneath `fid`: {children: [{id, kind, label, description, beneath, children}],
    total, kinds}. `beneath` is how many sit under a node at any depth; `kinds` counts everything under `fid` by kind.
    Children come places first, then by name."""
    tree = subtree(conn, fid) if tree is None else tree
    kids: dict[str, list[str]] = {}
    for cid, node in tree.items():
        kids.setdefault(node["parent"], []).append(cid)
    size: dict[str, int] = {}

    def count(nid: str) -> int:
        if nid not in size:
            size[nid] = 0  # guards a loop
            size[nid] = sum(1 + count(c) for c in kids.get(nid, []))
        return size[nid]

    def order(ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda i: (KIND_RANK.get(tree[i]["kind"], 50), tree[i]["label"]))

    def node(nid: str, level: int) -> dict:
        n = tree[nid]
        under = order(kids.get(nid, []))
        return {"id": nid, "kind": n["kind"], "label": n["label"], "description": n["description"],
                "beneath": count(nid),
                "children": [node(c, level + 1) for c in under[:PER_LEVEL]] if level < levels else [],
                "more": max(len(under) - PER_LEVEL, 0) if level < levels else 0}

    top = order(kids.get(fid, []))
    kinds: dict[str, int] = {}
    for n in tree.values():
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    return {"children": [node(c, 1) for c in top[:PER_LEVEL]], "more": max(len(top) - PER_LEVEL, 0),
            "total": len(tree), "kinds": dict(sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])))}


# Places before the things in them, machines before the instruments on them.
KIND_RANK = {"facility": 0, "area": 1, "well": 2, "pipeline": 3, "equipment": 4, "allocation_node": 5, "tag": 6}
