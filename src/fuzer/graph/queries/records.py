"""Entities and records: counts, search, refs, an entity with its sources, record history, the ontology and the review queue."""

from collections import defaultdict
from datetime import date

from ...core import nel
from ...core.pack import Pack


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
    from ...sources import mappings
    from ...sources.extract import extract

    maps = mappings.load(conn)
    values = defaultdict(list)
    for rec in conn.execute(
        """
        select r.source, r.record_type, r.source_key, r.payload
        from crosswalk c join source_record r using (source, record_type, source_key)
        where c.fuzer_id = %s
        """,
        (fuzer_id,),
    ):
        for cand in extract(rec, pack, maps)[0]:
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


def names(conn, ids: list[str]) -> dict:
    """What to call each entity: its label and description. Ids that aren't entities are left out."""
    rows = conn.execute(
        """select e.fuzer_id as id, l.label, d.value #>> '{}' as description
           from entity e left join entity_label l using (fuzer_id)
           left join golden d on d.fuzer_id = e.fuzer_id and d.field = 'description'
           where e.fuzer_id = any(%s)""", (ids,)).fetchall()
    return {r["id"]: {"label": r["label"], "description": r["description"]} for r in rows}
