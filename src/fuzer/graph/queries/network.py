"""The graph around an entity, the allocation network and how it changed, and members of a class or model."""

from datetime import date

_EDGE_DATES = """
    (%(as_of)s::date is null
     or ((e.valid_from is null or e.valid_from <= %(as_of)s::date)
         and (e.valid_to is null or e.valid_to > %(as_of)s::date)))
"""


def _subgraph(conn, edges: list[dict], extra_nodes: set[str] = frozenset()) -> list[dict]:
    ids = {e["src"] for e in edges} | {e["dst"] for e in edges} | set(extra_nodes)
    return conn.execute(
        """
        select en.fuzer_id as id, en.kind, l.label, g.value as description
        from entity en
        left join entity_label l using (fuzer_id)
        left join golden g on g.fuzer_id = en.fuzer_id and g.field = 'description'
        where en.fuzer_id = any(%s)
        """,
        (list(ids),),
    ).fetchall()


def neighborhood(conn, fuzer_id: str, depth: int = 1, rels: list[str] | None = None,
                 as_of: date | None = None, max_nodes: int = 250) -> dict:
    """Entities within `depth` links of one entity, for graph exploration."""
    seen, frontier, edges, truncated = {fuzer_id}, {fuzer_id}, {}, False
    for _ in range(depth):
        rows = conn.execute(
            f"""
            select e.id, e.src, e.rel, e.dst, e.valid_from, e.valid_to, e.sources
            from edge e
            where (e.src = any(%(f)s) or e.dst = any(%(f)s))
              and (%(rels)s::text[] is null or e.rel = any(%(rels)s))
              and {_EDGE_DATES}
            order by e.rel, e.id
            """,
            {"f": list(frontier), "rels": rels, "as_of": as_of},
        ).fetchall()
        nxt = set()
        for r in rows:
            for n in (r["src"], r["dst"]):
                if n not in seen:
                    if len(seen) >= max_nodes:
                        truncated = True
                        break
                    seen.add(n)
                    nxt.add(n)
            if r["src"] in seen and r["dst"] in seen:
                edges[r["id"]] = r
        frontier = nxt
        if not frontier:
            break
    edge_list = list(edges.values())
    return {"root": fuzer_id, "nodes": _subgraph(conn, edge_list, {fuzer_id}), "edges": edge_list,
            "truncated": truncated}


def network_rels(conn) -> list[str]:
    """The link types drawn as the network when none are asked for: every relation the ontology puts in its
    flow view (production/allocation, gas lift, piping, and any a pack or a person adds there)."""
    return [r["name"] for r in conn.execute(
        "select name from ontology_relation where view = 'flow' order by name").fetchall()]


def network(conn, rels: str | list[str] | None = None, as_of: date | None = None, max_edges: int = 5000) -> dict:
    """Every link of the given types, whatever entities they join: one network for flow, gas lift, piping or any
    other relation. `rels` defaults to the ontology's flow view."""
    if isinstance(rels, str):
        rels = [rels]
    rels = list(dict.fromkeys(rels or network_rels(conn)))
    rows = conn.execute(
        f"""
        select e.id, e.src, e.rel, e.dst, e.valid_from, e.valid_to, e.sources
        from edge e
        where e.rel = any(%(rels)s) and {_EDGE_DATES}
        order by e.id
        limit %(limit)s
        """,
        {"rels": rels, "as_of": as_of, "limit": max_edges + 1},
    ).fetchall()
    truncated = len(rows) > max_edges
    rows = rows[:max_edges]
    return {"rels": rels, "nodes": _subgraph(conn, rows), "edges": rows, "truncated": truncated}


def members(conn, fuzer_id: str, kind: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    """Tags / equipment in a class (including its subtypes) or instances of a model, paged."""
    rows = conn.execute(
        """
        with recursive classes(id) as (
            select %(id)s::text
            union
            select e.src from edge e join classes c on e.dst = c.id
            where e.rel = 'is_a' and e.src in (select fuzer_id from entity where kind = 'equipment_class')
        ), hits as (
            select distinct e.src as id from edge e
            where (e.rel = 'classified_as' and e.dst in (select id from classes))
               or (e.rel = 'instance_of' and e.dst = %(id)s)
        )
        select h.id as fuzer_id, en.kind, l.label, g.value #>> '{}' as description, count(*) over () as total
        from hits h
        join entity en on en.fuzer_id = h.id
        left join entity_label l on l.fuzer_id = h.id
        left join golden g on g.fuzer_id = h.id and g.field = 'description'
        where en.merged_into is null and en.retired_at is null and (%(kind)s::text is null or en.kind = %(kind)s)
        order by l.label
        limit %(limit)s offset %(offset)s
        """,
        {"id": fuzer_id, "kind": kind, "limit": limit, "offset": offset},
    ).fetchall()
    total = rows[0]["total"] if rows else 0
    return {"total": total, "offset": offset, "items": [{k: r[k] for k in ("fuzer_id", "kind", "label", "description")}
                                                        for r in rows]}


def compare_networks(conn, depth: int = 6, limit: int = 100) -> dict:
    """Energy Components' flow (flows_to) vs. P&ID piping (pipes_to), between tags both know:
    EC flows with no piping path on the P&IDs, and piping between EC nodes with no EC flow path."""
    def missing(rel_a, rel_b):
        return conn.execute(
            f"""
            with recursive pairs as (
                select a.src, a.dst from edge a
                join entity s on s.fuzer_id = a.src and s.kind = 'tag'
                join entity d on d.fuzer_id = a.dst and d.kind = 'tag'
                where a.rel = '{rel_a}'
                  and exists (select 1 from edge x where x.rel = '{rel_b}' and (x.src = a.src or x.dst = a.src))
                  and exists (select 1 from edge x where x.rel = '{rel_b}' and (x.src = a.dst or x.dst = a.dst))
            ), walk(start, goal, at, n) as (
                select src, dst, src, 0 from pairs
                union
                select w.start, w.goal, e.dst, w.n + 1 from walk w
                join edge e on e.src = w.at and e.rel = '{rel_b}'
                where w.n < %(depth)s and w.at <> w.goal
            )
            select p.src, p.dst, ls.label as from_label, ld.label as to_label
            from pairs p
            left join entity_label ls on ls.fuzer_id = p.src
            left join entity_label ld on ld.fuzer_id = p.dst
            where not exists (select 1 from walk w where w.start = p.src and w.goal = p.dst and w.at = p.dst)
            order by 3, 4 limit %(limit)s
            """,
            {"depth": depth, "limit": limit},
        ).fetchall()
    return {"in_ec_not_on_pids": missing("flows_to", "pipes_to"), "on_pids_not_in_ec": missing("pipes_to", "flows_to")}


def _reach(adj: dict[str, set[str]], start: str, depth: int, stop: set[str] = frozenset()) -> list[list[str]]:
    """Breadth-first layers downstream of `start` (start excluded); a node in `stop` is reached but not walked past."""
    seen, layer, out = {start}, [start], []
    for _ in range(depth):
        nxt = sorted({b for a in layer if a == start or a not in stop for b in adj.get(a, ()) if b not in seen})
        if not nxt:
            break
        seen |= set(nxt)
        out.append(nxt)
        layer = nxt
    return out


def flow_disagreements(conn, area_of, depth: int = 12) -> list[dict]:
    """Where the flow network (flows_to, Energy Components' allocation) and the drawings' piping (pipes_to) disagree
    inside one plant area (`area_of(label)` gives a tag's area; links between areas are pipelines, which the P&IDs
    don't draw). Two ways:

        flow_only    the flow network sends A to B, but no drawn line leads from A to B (B may be on no drawing)
        drawn_only   a drawn line leads from A to B, both on the flow network, but the flow network never sends A to B

    The flow network may skip equipment the drawings show (an allocation network goes from one metered node to the
    next): a drawn path through other items still counts as agreeing. Each comes with the drawn path downstream of A
    in layers, and the sheets and systems behind each side. Nothing is decided here: the drawing disagreements turn
    these into questions (sources.llm_pids.mismatches)."""
    rows = conn.execute(
        """
        select e.rel, e.src, e.dst, e.sources, e.props, ls.label as a, ld.label as b from edge e
        join entity s on s.fuzer_id = e.src and s.kind = 'tag' and s.merged_into is null
        join entity d on d.fuzer_id = e.dst and d.kind = 'tag' and d.merged_into is null
        join entity_label ls on ls.fuzer_id = e.src join entity_label ld on ld.fuzer_id = e.dst
        where e.rel in ('flows_to', 'pipes_to') and e.src <> e.dst and e.valid_to is null
          and (e.valid_from is null or e.valid_from <= now())
        """).fetchall()
    flows: dict[str, set[str]] = {}
    pipes: dict[str, set[str]] = {}
    label, ids, sheets, systems = {}, {}, {}, {}
    for r in rows:
        label[r["src"]], label[r["dst"]] = r["a"], r["b"]
        ids[r["a"]], ids[r["b"]] = r["src"], r["dst"]
        if r["rel"] == "flows_to":
            flows.setdefault(r["src"], set()).add(r["dst"])
            systems[(r["src"], r["dst"])] = sorted(set(r["sources"] or []))
        else:
            pipes.setdefault(r["src"], set()).add(r["dst"])
            p = r["props"] or {}
            for x in (r["src"], r["dst"]):
                sheets.setdefault(x, set()).update(s for s in [p.get("sheet"), p.get("pfd"), *(p.get("sheets") or [])]
                                                   if s)
    on_flow = set(flows) | {b for bs in flows.values() for b in bs}
    piped = set(pipes) | {b for bs in pipes.values() for b in bs}
    area = {x: area_of(label[x]) for x in label}
    drawn_areas = {area[x] for x in piped if area[x]}
    names = lambda layers: " → ".join(", ".join(label[x] for x in layer) for layer in layers)

    def item(kind, a, b, path, flow_systems):
        return {"kind": kind, "from": label[a], "to": label[b], "from_id": a, "to_id": b, "area": area[a],
                "drawn_path": [[label[x] for x in layer] for layer in path], "drawn": names(path),
                "on_drawings": b in piped, "sheets": sorted(sheets.get(a, set()) | sheets.get(b, set())),
                "systems": flow_systems}

    out = []
    for a in sorted(flows, key=label.get):
        for b in sorted(flows[a], key=label.get):
            if not area[a] or area[a] != area[b] or area[a] not in drawn_areas:
                continue
            path = _reach(pipes, a, depth)
            if b not in {x for layer in path for x in layer}:
                out.append(item("flow_only", a, b, path, systems[(a, b)]))
    for a in sorted(on_flow & set(pipes), key=label.get):
        if not area[a]:
            continue
        fed = {x for layer in _reach(flows, a, depth) for x in layer}
        path = _reach(pipes, a, depth, stop=on_flow)  # up to the next items on the flow network, not past them
        for b in sorted({x for layer in path for x in layer} & on_flow, key=label.get):
            if area[b] == area[a] and b not in fed and b != a:
                upto = []
                for layer in path:  # the drawn path, cut at b's layer
                    upto.append(layer)
                    if b in layer:
                        break
                out.append(item("drawn_only", a, b, upto, sorted({s for k, v in systems.items() if a in k for s in v})))
    return out
