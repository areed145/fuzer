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
        "select kind, count(*) as n from entity where merged_into is null and retired_at is null group by kind order by n desc"
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


SEARCH_FIELDS = ("description", "manufacturer", "model", "serial", "short_text", "text", "title", "summary")


def search(conn, pack: Pack, q: str = "", kind: str | None = None, limit: int = 50) -> list[dict]:
    """Exact key matches first (any tag / well style), then anything whose label, source key or
    searchable field contains the text. Each part stops early, so common words stay fast on large
    registers."""
    params = {"kind": kind, "limit": limit}
    if not q:
        return conn.execute(
            """
            select e.fuzer_id, e.kind, l.label, g.value as description
            from entity_label l
            join entity e on e.fuzer_id = l.fuzer_id
            left join golden g on g.fuzer_id = e.fuzer_id and g.field = 'description'
            where e.merged_into is null and e.retired_at is null and (%(kind)s::text is null or e.kind = %(kind)s)
            order by l.label limit %(limit)s
            """,
            params,
        ).fetchall()
    keys = [q.upper()]
    if tag := pack.tags.parse(q):
        keys.append(tag)
    if well := pack.wells.parse(q):
        keys.append(well)
    params |= {"keys": keys, "like": f"%{q}%", "fields": list(SEARCH_FIELDS), "probe": limit * 10}
    # With a kind filter, filter before the early stop so it can't fill up on other kinds.
    kf = "and fuzer_id in (select fuzer_id from entity where kind = %(kind)s)" if kind else ""
    return conn.execute(
        f"""
        with hits as (
            (select fuzer_id, 0 as rank from entity_key where key = any(%(keys)s))
            union all
            (select fuzer_id, 1 from entity_label where label ilike %(like)s {kf} limit %(probe)s)
            union all
            (select fuzer_id, 1 from crosswalk where source_key ilike %(like)s {kf} limit %(probe)s)
            union all
            (select fuzer_id, 2 from golden where field = any(%(fields)s) and value::text ilike %(like)s {kf}
             limit %(probe)s)
        ), best as (
            select fuzer_id, min(rank) as rank from hits group by fuzer_id
        )
        select e.fuzer_id, e.kind, l.label, g.value as description
        from best b
        join entity e on e.fuzer_id = b.fuzer_id
        left join entity_label l on l.fuzer_id = e.fuzer_id
        left join golden g on g.fuzer_id = e.fuzer_id and g.field = 'description'
        where e.merged_into is null and e.retired_at is null and (%(kind)s::text is null or e.kind = %(kind)s)
        order by b.rank, l.label
        limit %(limit)s
        """,
        params,
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


def source_values(conn, pack: Pack, fuzer_id: str) -> dict[str, list[dict]]:
    """What each source says about one entity: its few records re-extracted on demand, plus
    reviewer choices and derived values. Nothing per-source is stored for the whole register."""
    from .extract import extract

    values = defaultdict(list)
    for rec in conn.execute(
        """
        select r.source, r.record_type, r.source_key, r.payload
        from crosswalk c join source_record r using (source, record_type, source_key)
        where c.fuzer_id = %s
        """,
        (fuzer_id,),
    ):
        for cand in extract(rec, pack)[0]:
            for field, value in cand.fields.items():
                values[field].append({"source": cand.source, "source_key": cand.source_key, "value": value})
    for a in conn.execute(
        """
        select field, value, source, source_key from assertion where fuzer_id = %(id)s
        union all
        select field, value, source, null from golden where fuzer_id = %(id)s and rule = 'derived'
        """,
        {"id": fuzer_id},
    ):
        values[a["field"]].append({"source": a["source"], "source_key": a["source_key"], "value": a["value"]})
    return {f: sorted(v, key=lambda x: x["source"]) for f, v in sorted(values.items())}


def entity(conn, fuzer_id: str, as_of: date | None = None, per_group: int = 100,
           pack: Pack | None = None) -> dict | None:
    ent = conn.execute("select * from entity where fuzer_id = %s", (fuzer_id,)).fetchone()
    if not ent:
        return None
    if ent["merged_into"]:
        return {"fuzer_id": fuzer_id, "merged_into": ent["merged_into"]}

    golden = {r["field"]: {"value": r["value"], "source": r["source"], "rule": r["rule"]}
              for r in conn.execute("select * from golden where fuzer_id = %s", (fuzer_id,))}
    values = source_values(conn, pack, fuzer_id) if pack else defaultdict(list)
    keys = conn.execute("select kind, key from entity_key where fuzer_id = %s", (fuzer_id,)).fetchall()
    records = conn.execute(
        "select source, record_type, source_key, method, confidence from crosswalk where fuzer_id = %s",
        (fuzer_id,),
    ).fetchall()
    # Hubs (a class, an area, the facility) can have tens of thousands of links: return the first
    # `per_group` of each relationship and the true totals.
    edges = conn.execute(
        """
        with e as (
            select case when e.src = %(id)s then 'out' else 'in' end as direction, e.rel,
                   case when e.src = %(id)s then e.dst else e.src end as other,
                   e.valid_from, e.valid_to, e.sources, e.confidence, e.props
            from edge e
            where (e.src = %(id)s or e.dst = %(id)s)
              and (%(as_of)s::date is null
                   or ((e.valid_from is null or e.valid_from <= %(as_of)s::date)
                       and (e.valid_to is null or e.valid_to > %(as_of)s::date)))
        ), ranked as (
            select e.*, l.label as other_label,
                   row_number() over (partition by e.direction, e.rel order by l.label, e.other) as n,
                   count(*) over (partition by e.direction, e.rel) as total
            from e left join entity_label l on l.fuzer_id = e.other
        )
        select r.direction, r.rel, r.other, o.kind as other_kind, r.other_label, g.value as other_description,
               r.valid_from, r.valid_to, r.sources, r.confidence, r.props, r.total
        from ranked r
        join entity o on o.fuzer_id = r.other
        left join golden g on g.fuzer_id = r.other and g.field = 'description'
        where r.n <= %(per_group)s
        order by r.direction, r.rel, r.other_label
        """,
        {"id": fuzer_id, "as_of": as_of, "per_group": per_group},
    ).fetchall()
    edge_counts = {f"{r['direction']}:{r['rel']}": r["total"] for r in edges}
    label = conn.execute("select label from entity_label where fuzer_id = %s", (fuzer_id,)).fetchone()
    return {"fuzer_id": fuzer_id, "kind": ent["kind"], "label": label["label"] if label else None,
            "keys": keys, "golden": golden, "values": dict(values), "records": records, "edges": edges,
            "edge_counts": edge_counts}


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


def ontology(conn, cached: bool = True) -> dict:
    """The data model as it actually exists: entity kinds, which link types join which kinds,
    which fields each kind carries and which sources feed it. Computed once per rebuild."""
    if cached and (row := conn.execute("select value from register_summary where name = 'ontology'").fetchone()):
        return row["value"]
    kinds = conn.execute(
        "select kind, count(*) as n from entity where merged_into is null and retired_at is null group by kind order by n desc"
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
        where en.merged_into is null and en.retired_at is null
        group by en.kind, g.field
        order by en.kind, n desc, g.field
        """
    ).fetchall()
    sources = conn.execute(
        """
        select en.kind, c.source, count(*) as n
        from crosswalk c join entity en using (fuzer_id)
        where en.merged_into is null and en.retired_at is null
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


def events(conn, fuzer_id: str | None = None, since: str | None = None, until: str | None = None,
           event_type: str | None = None, limit: int = 100) -> list[dict]:
    """Events read from the operator logs, newest first: facility-wide, or those involving one
    entity (or reported by one log entry). since / until are ISO dates or timestamps."""
    rows = conn.execute(
        """
        select * from (
            select en.fuzer_id as id, jsonb_object_agg(g.field, g.value) as fields
            from entity en join golden g using (fuzer_id)
            where en.kind = 'event' and en.merged_into is null and en.retired_at is null
              and (%(id)s::text is null or en.fuzer_id in (
                    select src from edge where rel = 'involves' and dst = %(id)s
                    union select dst from edge where rel = 'reports' and src = %(id)s))
            group by en.fuzer_id
        ) x
        where (%(type)s::text is null or fields->>'event_type' = %(type)s)
          and (%(since)s::text is null or fields->>'timestamp' >= %(since)s)
          and (%(until)s::text is null or fields->>'timestamp' < %(until)s)
        order by fields->>'timestamp' desc nulls last, id
        limit %(limit)s
        """,
        {"id": fuzer_id, "type": event_type, "since": since, "until": until, "limit": limit},
    ).fetchall()
    ids = [r["id"] for r in rows]
    links = conn.execute(
        """
        select e.rel, e.src, e.dst, e.props->>'role' as role,
               o.fuzer_id as other, o.kind, l.label, g.value #>> '{}' as text
        from edge e
        join entity o on o.fuzer_id = case when e.rel = 'involves' then e.dst else e.src end
        left join entity_label l on l.fuzer_id = o.fuzer_id
        left join golden g on g.fuzer_id = o.fuzer_id and g.field = 'text'
        where (e.rel = 'involves' and e.src = any(%(ids)s)) or (e.rel = 'reports' and e.dst = any(%(ids)s))
        """,
        {"ids": ids},
    ).fetchall()
    involves, logs = defaultdict(list), {}
    for link in links:
        if link["rel"] == "involves":
            involves[link["src"]].append({"id": link["other"], "label": link["label"], "kind": link["kind"],
                                          "role": link["role"]})
        else:
            logs[link["dst"]] = {"id": link["other"], "label": link["label"], "text": link["text"]}
    out = []
    for r in rows:
        f = dict(r["fields"])
        out.append({
            "id": r["id"], "type": f.pop("event_type", None), "summary": f.pop("summary", None),
            "timestamp": f.pop("timestamp", None), "details": {k: v for k, v in f.items() if k != "description"},
            "involves": involves[r["id"]], "log": logs.get(r["id"]),
        })
    return out


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



def maintenance(conn, fuzer_id: str, since: str | None = None, limit: int = 50) -> dict:
    """Notifications (with their work orders) for a tag, a piece of equipment, every instance of a
    model, or every member of a class including subtypes; plus counts by ISO 14224 failure mode."""
    params = {"id": fuzer_id, "since": since, "limit": limit}
    scope = """
        with recursive classes(id) as (
            select %(id)s::text
            union
            select e.src from edge e join classes c on e.dst = c.id
            where e.rel = 'is_a' and e.src in (select fuzer_id from entity where kind = 'equipment_class')
        ), targets as (
            select %(id)s::text as id
            union select src from edge where rel = 'instance_of' and dst = %(id)s
            union select src from edge where rel = 'classified_as' and dst in (select id from classes)
        ), notifs as (
            select distinct e.src as id
            from edge e join entity en on en.fuzer_id = e.src and en.kind = 'notification' and en.retired_at is null
            where e.rel = 'concerns' and e.dst in (select id from targets)
        ), n as (
            select notifs.id, jsonb_object_agg(g.field, g.value) as f
            from notifs join golden g on g.fuzer_id = notifs.id
            group by notifs.id
        )
    """
    since_clause = "where (%(since)s::text is null or n.f->>'created' >= %(since)s)"
    modes = conn.execute(scope + f"""
        select n.f->>'failure_mode' as code, n.f->>'failure_mode_name' as name, count(*) as count
        from n {since_clause}
        group by 1, 2 order by count desc, code
    """, params).fetchall()
    recent = conn.execute(scope + f"""
        select n.id, n.f from n {since_clause}
        order by n.f->>'created' desc nulls last, n.id
        limit %(limit)s
    """, params).fetchall()
    ids = [r["id"] for r in recent]
    orders = defaultdict(list)
    for r in conn.execute(
        """
        select e.dst as notification, w.fuzer_id as id, l.label, jsonb_object_agg(g.field, g.value) as f
        from edge e
        join entity w on w.fuzer_id = e.src
        left join entity_label l on l.fuzer_id = w.fuzer_id
        join golden g on g.fuzer_id = w.fuzer_id
        where e.rel = 'for_notification' and e.dst = any(%s)
        group by e.dst, w.fuzer_id, l.label
        """,
        (ids,),
    ):
        f = r["f"]
        orders[r["notification"]].append({"id": r["id"], "label": r["label"], "order_type": f.get("order_type"),
                                          "activity": f.get("activity_name"), "start": f.get("start"),
                                          "finish": f.get("finish"), "cost": f.get("cost"), "text": f.get("text")})
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (ids,))}
    return {
        "total": sum(m["count"] for m in modes),
        "failure_modes": modes,
        "recent": [{"id": r["id"], "label": labels.get(r["id"]), "created": r["f"].get("created"),
                    "type": r["f"].get("notif_type"), "priority": r["f"].get("priority"),
                    "failure_mode": r["f"].get("failure_mode"), "failure_mode_name": r["f"].get("failure_mode_name"),
                    "failure_mechanism_name": r["f"].get("failure_mechanism_name"),
                    "text": r["f"].get("short_text"), "work_orders": orders[r["id"]]} for r in recent],
    }



_DOWNTIME = """
    with recursive classes(id) as (
        select %(id)s::text
        union
        select e.src from edge e join classes c on e.dst = c.id
        where e.rel = 'is_a' and e.src in (select fuzer_id from entity where kind = 'equipment_class')
    ), targets as (
        select %(id)s::text as id
        union select src from edge where rel = 'instance_of' and dst = %(id)s
        union select src from edge where rel = 'classified_as' and dst in (select id from classes)
    ), scoped as (
        select en.fuzer_id as id from entity en
        where en.kind = 'downtime' and en.retired_at is null
          and (%(id)s::text is null or exists (
                select 1 from edge e where e.src = en.fuzer_id and e.rel in ('affects', 'caused_by')
                  and e.dst in (select id from targets)))
    ), d as (
        select s.id, jsonb_object_agg(g.field, g.value) as f
        from scoped s join golden g on g.fuzer_id = s.id
        group by s.id
    ), dd as (
        select * from d
        where (%(since)s::text is null or f->>'start' >= %(since)s)
          and (%(until)s::text is null or f->>'start' < %(until)s)
    )
"""


def downtime(conn, fuzer_id: str | None = None, since: str | None = None, until: str | None = None,
             limit: int = 50) -> dict:
    """Downtime affecting a well or the facility, or caused by a tag / equipment / model / class."""
    params = {"id": fuzer_id, "since": since, "until": until, "limit": limit}
    totals = conn.execute(_DOWNTIME + """
        select count(*) as events, coalesce(sum((f->>'lost_oil_sm3')::numeric), 0) as lost_oil_sm3,
               coalesce(sum((f->>'duration_h')::numeric), 0) as hours
        from dd
    """, params).fetchone()
    rows = conn.execute(_DOWNTIME + """
        select dd.id, dd.f, l.label from dd left join entity_label l on l.fuzer_id = dd.id
        order by dd.f->>'start' desc, dd.id limit %(limit)s
    """, params).fetchall()
    ids = [r["id"] for r in rows]
    links = defaultdict(lambda: {"affects": [], "caused_by": []})
    for r in conn.execute(
        """
        select e.src, e.rel, e.dst, l.label from edge e left join entity_label l on l.fuzer_id = e.dst
        where e.src = any(%s) and e.rel in ('affects', 'caused_by')
        """,
        (ids,),
    ):
        links[r["src"]][r["rel"]].append({"id": r["dst"], "label": r["label"]})
    return {
        "events": totals["events"], "lost_oil_sm3": float(totals["lost_oil_sm3"]), "hours": float(totals["hours"]),
        "recent": [{"id": r["id"], "label": r["label"], **{k: r["f"].get(k) for k in (
            "start", "end", "duration_h", "reason", "reason_category", "lost_oil_sm3", "comment", "reason_check")},
                    **links[r["id"]]} for r in rows],
    }


def downtime_ranking(conn, by: str = "tag", since: str | None = None, until: str | None = None,
                     limit: int = 20) -> list[dict]:
    """Lost oil ranked by causing tag, by the class of the causing tag, or by reason."""
    params = {"id": None, "since": since, "until": until, "limit": limit}
    if by == "reason":
        sql = """
            select coalesce(f->>'reason', 'Unknown') as label, null::text as description, null::text as fuzer_id,
                   count(*) as events, sum((f->>'lost_oil_sm3')::numeric) as lost_oil_sm3,
                   sum((f->>'duration_h')::numeric) as hours
            from dd group by 1 order by lost_oil_sm3 desc nulls last limit %(limit)s
        """
    elif by in ("tag", "class"):
        via = ("join edge k on k.src = c.dst and k.rel = 'classified_as'" if by == "class" else "")
        target = "k.dst" if by == "class" else "c.dst"
        sql = f"""
            select l.label, gd.value #>> '{{}}' as description, {target} as fuzer_id, count(distinct dd.id) as events,
                   sum((dd.f->>'lost_oil_sm3')::numeric) as lost_oil_sm3, sum((dd.f->>'duration_h')::numeric) as hours
            from dd
            join edge c on c.src = dd.id and c.rel = 'caused_by'
            {via}
            left join entity_label l on l.fuzer_id = {target}
            left join golden gd on gd.fuzer_id = {target} and gd.field = 'description'
            group by l.label, gd.value, {target} order by lost_oil_sm3 desc nulls last limit %(limit)s
        """
    else:
        raise ValueError("by must be tag, class or reason")
    return [{**r, "lost_oil_sm3": float(r["lost_oil_sm3"] or 0), "hours": float(r["hours"] or 0)}
            for r in conn.execute(_DOWNTIME + sql, params)]


def downtime_reason_checks(conn, limit: int = 100) -> list[dict]:
    """Downtime whose comment names failed equipment but whose reason code says otherwise."""
    return conn.execute(
        """
        select g.fuzer_id, l.label, g.value #>> '{}' as issue, s.value #>> '{}' as start
        from golden g
        left join entity_label l using (fuzer_id)
        left join golden s on s.fuzer_id = g.fuzer_id and s.field = 'start'
        where g.field = 'reason_check'
        order by start desc limit %s
        """,
        (limit,),
    ).fetchall()


def incidents(conn, fuzer_id: str | None = None, since: str | None = None, until: str | None = None,
              limit: int = 50) -> dict:
    """Incidents facility-wide, or about a tag (an instrument counts toward its equipment), a piece of
    equipment (via where it's installed), every instance of a model, or a class including subtypes."""
    params = {"id": fuzer_id, "since": since, "until": until, "limit": limit}
    rows = conn.execute(
        """
        with recursive classes(id) as (
            select %(id)s::text
            union
            select e.src from edge e join classes c on e.dst = c.id
            where e.rel = 'is_a' and e.src in (select fuzer_id from entity where kind = 'equipment_class')
        ), base as (
            select %(id)s::text as id
            union select src from edge where rel = 'instance_of' and dst = %(id)s
            union select src from edge where rel = 'classified_as' and dst in (select id from classes)
        ), targets as (
            select id from base
            union select dst from edge where rel in ('installed_at', 'part_of') and src in (select id from base)
        ), inc as (
            select en.fuzer_id as id, jsonb_object_agg(g.field, g.value) as f
            from entity en join golden g using (fuzer_id)
            where en.kind = 'incident' and en.retired_at is null
              and (%(id)s::text is null or en.fuzer_id in (
                    select src from edge where rel = 'about' and dst in (select id from targets)))
            group by en.fuzer_id
        )
        select inc.id, inc.f, count(*) over () as total from inc
        where (%(since)s::text is null or inc.f->>'start' >= %(since)s)
          and (%(until)s::text is null or inc.f->>'start' < %(until)s)
        order by inc.f->>'start' desc, inc.id
        limit %(limit)s
        """,
        params,
    ).fetchall()
    return {"total": rows[0]["total"] if rows else 0,
            "items": [{"id": r["id"], **{k: v for k, v in r["f"].items() if k != "timestamp"}} for r in rows]}


def incident_timeline(conn, fuzer_id: str) -> list[dict]:
    """The signals in one incident, in time order."""
    rows = conn.execute(
        """
        select s.fuzer_id as id, s.kind, l.label, jsonb_object_agg(g.field, g.value) as f
        from edge e
        join entity s on s.fuzer_id = e.dst
        left join entity_label l on l.fuzer_id = s.fuzer_id
        join golden g on g.fuzer_id = s.fuzer_id
        where e.src = %s and e.rel = 'includes'
        group by s.fuzer_id, s.kind, l.label
        """,
        (fuzer_id,),
    ).fetchall()
    out = []
    for r in rows:
        f = r["f"]
        out.append({"id": r["id"], "kind": r["kind"], "label": r["label"],
                    "at": f.get("timestamp") or f.get("start") or f.get("created"),
                    "text": f.get("summary") or f.get("comment") or f.get("short_text") or f.get("description"),
                    "detail": f.get("event_type") or f.get("reason") or f.get("failure_mode_name"),
                    "lost_oil_sm3": f.get("lost_oil_sm3")})
    return sorted(out, key=lambda x: (x["at"] or "", x["kind"]))


def similar_incidents(conn, fuzer_id: str, limit: int = 10) -> list[dict]:
    """Past incidents most like this one: same unit, same equipment class, shared failure modes and
    event types. Candidates come only from incidents on units of the same class."""
    me = conn.execute(
        """
        select a.dst as unit, c.dst as class, jsonb_object_agg(g.field, g.value) as f
        from edge a
        left join edge c on c.src = a.dst and c.rel = 'classified_as'
        join golden g on g.fuzer_id = a.src
        where a.src = %s and a.rel = 'about'
        group by a.dst, c.dst
        """,
        (fuzer_id,),
    ).fetchone()
    if not me:
        return []
    rows = conn.execute(
        """
        select a.src as id, a.dst as unit, jsonb_object_agg(g.field, g.value) as f
        from edge a
        join golden g on g.fuzer_id = a.src
        where a.rel = 'about' and a.src <> %(me)s
          and (a.dst = %(unit)s or a.dst in (select src from edge where rel = 'classified_as' and dst = %(class)s))
        group by a.src, a.dst
        """,
        {"me": fuzer_id, "unit": me["unit"], "class": me["class"]},
    ).fetchall()

    def jaccard(a, b):
        a, b = set(a or []), set(b or [])
        return len(a & b) / len(a | b) if a | b else 0.0

    scored = []
    for r in rows:
        score = (1.0 if r["unit"] == me["unit"] else 0.0) + 0.5 \
            + jaccard(me["f"].get("failure_modes"), r["f"].get("failure_modes")) \
            + jaccard(me["f"].get("event_types"), r["f"].get("event_types"))
        scored.append({"id": r["id"], "score": round(score, 2), "same_unit": r["unit"] == me["unit"],
                       **{k: r["f"].get(k) for k in ("description", "start", "end", "lost_oil_sm3", "failure_modes",
                                                    "event_types")}})
    return sorted(scored, key=lambda x: (-x["score"], x["start"] or ""), reverse=False)[:limit]



def documents_with_several_current_revisions(conn, limit: int = 50) -> list[dict]:
    """Document numbers the EDMS lists as current in more than one revision."""
    return conn.execute(
        """
        select n.value #>> '{}' as doc_number, array_agg(l.label order by l.label) as revisions
        from golden n
        join golden st on st.fuzer_id = n.fuzer_id and st.field = 'status' and st.value #>> '{}' = 'current'
        join entity e on e.fuzer_id = n.fuzer_id and e.kind = 'document' and e.retired_at is null
        left join entity_label l on l.fuzer_id = n.fuzer_id
        where n.field = 'doc_number'
        group by 1 having count(*) > 1
        order by 1 limit %s
        """,
        (limit,),
    ).fetchall()



def record_history(conn, pack: Pack, fuzer_id: str, limit: int = 50) -> list[dict]:
    """How the source records behind an entity changed: per change, the source record, when, and the
    fields that changed (from -> to), newest first."""
    rows = conn.execute(
        """
        with recs as (
            select source, record_type, source_key from crosswalk where fuzer_id = %(id)s
        )
        select h.source, h.record_type, h.source_key, h.payload, h.replaced_at as at, h.change, h.id
        from source_record_history h join recs using (source, record_type, source_key)
        union all
        select r.source, r.record_type, r.source_key, r.payload, null, 'current', null
        from source_record r join recs using (source, record_type, source_key)
        order by source, record_type, source_key, at nulls last, id
        """,
        {"id": fuzer_id},
    ).fetchall()
    changes, prev = [], {}
    for r in rows:
        key = (r["source"], r["record_type"], r["source_key"])
        if key in prev:
            before, when, kind = prev[key]
            fields = sorted(set(before) | set(r["payload"]))
            diff = {f: {"from": before.get(f), "to": r["payload"].get(f)} for f in fields
                    if before.get(f) != r["payload"].get(f)}
            changes.append({"record": ":".join(key), "at": when, "change": kind, "fields": diff})
        if r["change"] == "removed":
            changes.append({"record": ":".join(key), "at": r["at"], "change": "removed", "fields": {}})
            prev.pop(key, None)
        elif r["change"] != "current":
            prev[key] = (r["payload"], r["at"], r["change"])
    return sorted(changes, key=lambda c: c["at"], reverse=True)[:limit]
