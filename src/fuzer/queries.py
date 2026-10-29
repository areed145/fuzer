"""Read queries shared by the REST API and the MCP server."""

from collections import defaultdict
from datetime import date

from . import nel
from .pack import Pack


class AmbiguousRef(LookupError):
    def __init__(self, ref: str, candidates: list[dict]):
        super().__init__(f"{ref!r} matches {len(candidates)} entities")
        self.candidates = candidates


def stats(conn, pack: Pack) -> dict:
    kinds = conn.execute(
        "select kind, count(*) as n from entity where merged_into is null group by kind order by n desc"
    ).fetchall()
    reviews = conn.execute(
        "select kind, count(*) as n from review_item where status = 'open' group by kind"
    ).fetchall()
    return {
        "pack": pack.name,
        "facility": pack.facility,
        "entities": {r["kind"]: r["n"] for r in kinds},
        "open_reviews": {r["kind"]: r["n"] for r in reviews},
        "records": conn.execute("select count(*) as n from source_record").fetchone()["n"],
        "edges": conn.execute("select count(*) as n from edge").fetchone()["n"],
    }


def search(conn, pack: Pack, q: str = "", kind: str | None = None, limit: int = 50) -> list[dict]:
    keys = [q.upper()]
    if tag := pack.tags.parse(q):
        keys.append(tag)
    if well := pack.wells.parse(q):
        keys.append(well)
    return conn.execute(
        """
        select distinct e.fuzer_id, e.kind, l.label, g.value as description
        from entity e
        left join entity_label l using (fuzer_id)
        left join golden g on g.fuzer_id = e.fuzer_id and g.field = 'description'
        where e.merged_into is null
          and (%(kind)s::text is null or e.kind = %(kind)s)
          and (%(q)s = ''
               or e.fuzer_id in (select fuzer_id from entity_key where key = any(%(keys)s))
               or l.label ilike %(like)s
               or g.value::text ilike %(like)s
               or e.fuzer_id in (select fuzer_id from crosswalk where source_key ilike %(like)s))
        order by l.label
        limit %(limit)s
        """,
        {"q": q, "kind": kind, "keys": keys, "like": f"%{q}%", "limit": limit},
    ).fetchall()


def resolve_ref(conn, pack: Pack, ref: str) -> str | None:
    """Turn a Fuzer ID, tag, well, PI point or source key into one Fuzer ID.

    Returns None when nothing matches; raises AmbiguousRef when several do.
    """
    ref = ref.strip()
    if ref.startswith("fz-"):
        row = conn.execute("select fuzer_id, merged_into from entity where fuzer_id = %s", (ref,)).fetchone()
        return (row["merged_into"] or row["fuzer_id"]) if row else None

    keys = {ref.upper()}
    if tag := pack.tags.parse(ref):
        keys.add(tag)
    if well := pack.wells.parse(ref):
        keys.add(well)
    rows = conn.execute(
        """
        select distinct fuzer_id from entity_key where key = any(%(keys)s) and kind <> 'tag_noarea'
        union
        select distinct fuzer_id from crosswalk where upper(source_key) = %(ref)s
        """,
        {"keys": list(keys), "ref": ref.upper()},
    ).fetchall()
    ids = [r["fuzer_id"] for r in rows]
    if not ids:
        # A tag written without its area ("PT-2001") may exist in several areas.
        short = [t.short_key for t in pack.tags.find_all(ref)]
        ids = [r["fuzer_id"] for r in conn.execute(
            "select fuzer_id from entity_key where kind = 'tag_noarea' and key = any(%s)", (short,))]
    if len(ids) > 1:
        # An exact label match wins (e.g. "20-V-2001" the tag over its PI points).
        exact = [r["fuzer_id"] for r in conn.execute(
            "select fuzer_id from entity_label where fuzer_id = any(%s) and label = any(%s)",
            (ids, list(keys)))]
        if len(exact) == 1:
            return exact[0]
        cands = conn.execute(
            "select e.fuzer_id, e.kind, l.label from entity e left join entity_label l using (fuzer_id) "
            "where e.fuzer_id = any(%s) order by l.label", (ids,)).fetchall()
        raise AmbiguousRef(ref, cands)
    return ids[0] if ids else None


def entity(conn, fuzer_id: str, as_of: date | None = None) -> dict | None:
    ent = conn.execute("select * from entity where fuzer_id = %s", (fuzer_id,)).fetchone()
    if not ent:
        return None
    if ent["merged_into"]:
        return {"fuzer_id": fuzer_id, "merged_into": ent["merged_into"]}

    golden = {r["field"]: {"value": r["value"], "source": r["source"], "rule": r["rule"]}
              for r in conn.execute("select * from golden where fuzer_id = %s", (fuzer_id,))}
    values = defaultdict(list)
    for a in conn.execute("select field, value, source, source_key from assertion where fuzer_id = %s "
                          "order by field, source", (fuzer_id,)):
        values[a["field"]].append({"source": a["source"], "source_key": a["source_key"], "value": a["value"]})
    keys = conn.execute("select kind, key from entity_key where fuzer_id = %s", (fuzer_id,)).fetchall()
    records = conn.execute(
        "select source, record_type, source_key, method, confidence from crosswalk where fuzer_id = %s",
        (fuzer_id,),
    ).fetchall()
    edges = conn.execute(
        """
        select case when e.src = %(id)s then 'out' else 'in' end as direction, e.rel,
               case when e.src = %(id)s then e.dst else e.src end as other,
               o.kind as other_kind, l.label as other_label, g.value as other_description,
               e.valid_from, e.valid_to, e.sources, e.confidence, e.props
        from edge e
        join entity o on o.fuzer_id = case when e.src = %(id)s then e.dst else e.src end
        left join entity_label l on l.fuzer_id = o.fuzer_id
        left join golden g on g.fuzer_id = o.fuzer_id and g.field = 'description'
        where (e.src = %(id)s or e.dst = %(id)s)
          and (%(as_of)s::date is null
               or ((e.valid_from is null or e.valid_from <= %(as_of)s::date)
                   and (e.valid_to is null or e.valid_to > %(as_of)s::date)))
        order by direction, e.rel, l.label
        """,
        {"id": fuzer_id, "as_of": as_of},
    ).fetchall()
    label = conn.execute("select label from entity_label where fuzer_id = %s", (fuzer_id,)).fetchone()
    return {"fuzer_id": fuzer_id, "kind": ent["kind"], "label": label["label"] if label else None,
            "keys": keys, "golden": golden, "values": dict(values), "records": records, "edges": edges}


def link_text(conn, pack: Pack, text: str) -> list[dict]:
    out = []
    for m in nel.find_mentions(text, pack):
        ids = [r["fuzer_id"] for r in conn.execute(
            "select fuzer_id from entity_key where kind = %s and key = %s", (m.kind, m.key))]
        out.append({**m.__dict__, "fuzer_id": ids[0] if len(ids) == 1 else None,
                    "candidates": ids if len(ids) > 1 else []})
    return out


def lookup_source(conn, source: str, key: str) -> list[dict]:
    return conn.execute("select * from crosswalk where source = %s and source_key = %s", (source, key)).fetchall()


def reviews(conn, status: str = "open", kind: str | None = None, limit: int = 100) -> list[dict]:
    return conn.execute(
        """
        select r.*, l.label, g.value as description
        from review_item r
        left join entity_label l on l.fuzer_id = r.payload->>'fuzer_id'
        left join golden g on g.fuzer_id = r.payload->>'fuzer_id' and g.field = 'description'
        where r.status = %s and (%s::text is null or r.kind = %s)
        order by r.id
        limit %s
        """,
        (status, kind, kind, limit),
    ).fetchall()


def ontology(conn) -> dict:
    """The data model as it actually exists: entity kinds, which link types join which kinds,
    which fields each kind carries and which sources feed it."""
    kinds = conn.execute(
        "select kind, count(*) as n from entity where merged_into is null group by kind order by n desc"
    ).fetchall()
    relations = conn.execute(
        """
        select s.kind as src_kind, e.rel, d.kind as dst_kind, count(distinct e.id) as n,
               count(distinct e.id) filter (where e.valid_from is not null or e.valid_to is not null) as dated,
               array_agg(distinct s_src.name) as sources
        from edge e
        join entity s on s.fuzer_id = e.src
        join entity d on d.fuzer_id = e.dst
        cross join lateral unnest(e.sources) as s_src(name)
        group by s.kind, e.rel, d.kind
        order by n desc
        """
    ).fetchall()
    fields = conn.execute(
        """
        select en.kind, g.field, count(*) as n
        from golden g join entity en using (fuzer_id)
        where en.merged_into is null
        group by en.kind, g.field
        order by en.kind, n desc, g.field
        """
    ).fetchall()
    sources = conn.execute(
        """
        select en.kind, c.source, count(*) as n
        from crosswalk c join entity en using (fuzer_id)
        where en.merged_into is null
        group by en.kind, c.source
        order by en.kind, n desc
        """
    ).fetchall()
    out_fields, out_sources = defaultdict(list), defaultdict(list)
    for r in fields:
        out_fields[r["kind"]].append({"field": r["field"], "count": r["n"]})
    for r in sources:
        out_sources[r["kind"]].append({"source": r["source"], "records": r["n"]})
    return {
        "kinds": [{"kind": k["kind"], "count": k["n"], "fields": out_fields[k["kind"]],
                   "sources": out_sources[k["kind"]]} for k in kinds],
        "relations": relations,
    }


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
