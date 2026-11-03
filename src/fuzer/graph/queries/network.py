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


def network(conn, rel: str = "flows_to", as_of: date | None = None, max_edges: int = 5000) -> dict:
    """Every link of one type (e.g. the whole flow / allocation network)."""
    rows = conn.execute(
        f"""
        select e.id, e.src, e.rel, e.dst, e.valid_from, e.valid_to, e.sources
        from edge e
        where e.rel = %(rel)s and {_EDGE_DATES}
        order by e.id
        limit %(limit)s
        """,
        {"rel": rel, "as_of": as_of, "limit": max_edges + 1},
    ).fetchall()
    truncated = len(rows) > max_edges
    rows = rows[:max_edges]
    return {"nodes": _subgraph(conn, rows), "edges": rows, "truncated": truncated}


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
