"""Entities and records: counts, search, refs, an entity with its sources, record history, the ontology and the review queue."""

from collections import defaultdict
from datetime import date

from ...core import nel
from ...core.pack import Pack
from ..hierarchy import CONTAINMENT, WORK_KINDS


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


# The order kinds are listed in (#1075): the places and drawings people look for first, instrument tags last, so
# "flare" finds the flare system area and its P&IDs before thirty flare tags, and Browse opens on facilities.
KIND_ORDER = ["facility", "area", "document", "equipment", "well", "pipeline", "work_order", "notification", "incident"]
LAST_KINDS = ["timeseries", "tag"]
_KIND_RANK = ("coalesce(array_position(%(order)s::text[], e.kind), "
              "case when e.kind = any(%(last)s::text[]) then 100 + array_position(%(last)s::text[], e.kind) else 50 end)")


# What each row sits in (the nearest container), so a list of tags says which area each is on.
_PARENT = """left join lateral (
                select x.dst as id, coalesce(pl.label, x.dst) as label from edge x
                left join entity_label pl on pl.fuzer_id = x.dst
                where x.src = e.fuzer_id and x.rel = any(%(rels)s) and x.valid_to is null
                order by x.rel, x.dst limit 1) p on true
            left join lateral (
                select tl.label from edge x join entity_label tl on tl.fuzer_id = x.dst
                where x.src = e.fuzer_id and x.rel = 'installed_at' and x.valid_to is null
                order by tl.label limit 1) t on e.kind = 'equipment'"""


def search(conn, pack: Pack, q: str = "", kind: str | None = None, limit: int = 50, total: bool = False):
    """Exact key matches first (any tag / well style), then labels that start with the text, shortest first (so part
    of a tag finds the tag before the incidents named after it), then anything whose label, source key or searchable
    field contains the text, places and drawings before equipment and instrument tags (KIND_ORDER). Each part stops
    early, so common words stay fast on large registers. A trailing separator is part of a tag still being typed
    ("20-P-" finds what "20-P" does, #1382). With `total`, {hits, total}: how many match, so a picker that shows the
    first few says "12 of N"; each part then probes up to TOTAL_PROBE rows, the most N says."""
    if total:
        hits = search(conn, pack, q, kind, TOTAL_PROBE)
        return {"hits": hits[:limit], "total": len(hits)}
    kinds = [k for k in (kind or "").split(",") if k] or None
    if kinds == ["work"] or (kinds and set(kinds) == set(WORK_KINDS)):
        # what work can be about (a message case's entity, #1382): the one list, held there, each thing once
        return _once(conn, _search(conn, pack, q, list(WORK_KINDS), limit))
    return _search(conn, pack, q, kinds, limit)


def _once(conn, rows: list[dict]) -> list[dict]:
    """Each real thing listed once where work is pinned to it (C9): a vessel was listed three ways, as its tag, the
    equipment installed at that tag, and a second system's record of the same equipment the matcher left for a steward
    to confirm. Of an equipment and the tag it is installed at, or two entities an open possible match pairs, the one
    listed first stays."""
    ids = [r["fuzer_id"] for r in rows]
    if len(ids) < 2:
        return rows
    pairs = conn.execute(
        """
        select src as a, dst as b from edge where rel = 'installed_at' and valid_to is null
          and src = any(%(ids)s) and dst = any(%(ids)s)
        union all
        select min(c.fuzer_id), max(c.fuzer_id) from review_item i
        cross join lateral unnest(string_to_array(i.subject, '|')) r
        join crosswalk c on c.source || ':' || c.record_type || ':' || c.source_key = r
        where i.kind = 'match' and i.status = 'open' and c.fuzer_id = any(%(ids)s)
        group by i.id having count(distinct c.fuzer_id) = 2
        """, {"ids": ids}).fetchall()
    group = {i: i for i in ids}  # one group per real thing, joined through each pair

    def top(i: str) -> str:
        while group[i] != i:
            i = group[i]
        return i

    for p in pairs:
        group[top(p["a"])] = top(p["b"])
    seen: set[str] = set()
    out = []
    for r in rows:
        if (g := top(r["fuzer_id"])) not in seen:
            seen.add(g)
            out.append(r)
    return out


def _search(conn, pack: Pack, q: str, kinds: list[str] | None, limit: int):
    q = q.strip()
    q = q.rstrip("-_./: ") or q
    params ={"kinds": kinds, "limit": limit, "order": KIND_ORDER, "last": LAST_KINDS, "rels": list(CONTAINMENT)}
    if not q:
        return conn.execute(
            """
            select e.fuzer_id, e.kind, l.label, g.value as description, p.id as parent, p.label as parent_label, t.label as at
            from entity_label l
            join entity e on e.fuzer_id = l.fuzer_id
            left join golden g on g.fuzer_id = e.fuzer_id and g.field = 'description'
            """ + _PARENT + """
            where e.merged_into is null and e.retired_at is null and (%(kinds)s::text[] is null or e.kind = any(%(kinds)s))
            order by """ + _KIND_RANK + """, l.label limit %(limit)s
            """,
            params,
        ).fetchall()
    keys = [q.upper()]
    if tag := pack.tags.parse(q):
        keys.append(tag)
    if well := pack.wells.parse(q):
        keys.append(well)
    params |= {"keys": keys, "like": f"%{q}%", "prefix": f"{q}%", "fields": list(SEARCH_FIELDS), "probe": limit * 10}
    # With a kind filter, filter before the early stop so it can't fill up on other kinds.
    kf = "and fuzer_id in (select fuzer_id from entity where kind = any(%(kinds)s))" if kinds else ""
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
        select e.fuzer_id, e.kind, l.label, g.value as description, p.id as parent, p.label as parent_label, t.label as at
        from best b
        join entity e on e.fuzer_id = b.fuzer_id
        left join entity_label l on l.fuzer_id = e.fuzer_id
        left join golden g on g.fuzer_id = e.fuzer_id and g.field = 'description'
        {_PARENT}
        where e.merged_into is null and e.retired_at is null and (%(kinds)s::text[] is null or e.kind = any(%(kinds)s))
        order by b.rank > 0, case when l.label ilike %(prefix)s then length(l.label) end nulls last, {_KIND_RANK},
                 b.rank, l.label
        limit %(limit)s
        """,
        params,
    ).fetchall()


TOTAL_PROBE = 500  # the most a search counts for "12 of N"


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


def reviews(conn, status: str = "open", kind: str | None = None, limit: int = 100,
            item: int | None = None) -> list[dict]:
    rows = conn.execute(
        """
        select r.*, l.label, g.value as description
        from review_item r
        left join entity_label l on l.fuzer_id = r.payload->>'fuzer_id'
        left join golden g on g.fuzer_id = r.payload->>'fuzer_id' and g.field = 'description'
        where r.status = %s and (%s::text is null or r.kind = %s) and (%s::bigint is null or r.id = %s)
        order by r.id
        limit %s
        """,
        (status, kind, kind, item, item, limit),
    ).fetchall()
    return _message_choices(conn, rows)


def _message_choices(conn, rows: list[dict]) -> list[dict]:
    """A message question's choices as the steward reads them (P5.26f): only the candidates work can be about, each with
    its kind, description and the place it sits in, and `kinds`, what the register search for another is limited to."""
    asked = [r for r in rows if r["kind"] == "message"]
    ids = list({c["fuzer_id"] for r in asked for c in (r["payload"] or {}).get("candidates") or []})
    if not asked:
        return rows
    found = {r["fuzer_id"]: r for r in conn.execute(
        """
        select e.fuzer_id, e.kind, l.label, g.value #>> '{}' as description, p.label as parent_label, t.label as at
        from entity e
        left join entity_label l on l.fuzer_id = e.fuzer_id
        left join golden g on g.fuzer_id = e.fuzer_id and g.field = 'description'
        """ + _PARENT + """
        where e.fuzer_id = any(%(ids)s) and e.kind = any(%(kinds)s) and e.merged_into is null and e.retired_at is null
        """, {"ids": ids, "kinds": list(WORK_KINDS), "rels": list(CONTAINMENT)})}
    for r in asked:
        p = r["payload"] = dict(r["payload"] or {})
        p["candidates"] = [dict(found[c["fuzer_id"]]) for c in p.get("candidates") or [] if c["fuzer_id"] in found]
        p["kinds"] = list(WORK_KINDS)
    return rows


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
    """What to call each entity: its label and description, and for a physical item (equipment, labelled by its
    serial number) the tag it is installed at (`at`), which is what people in the field know it by. An id a rebuild
    merged into another (a case or a citation keeps the id it was given) reads as the entity it is now, so it is never
    shown as its id (#1383). Ids that aren't entities are left out."""
    rows = conn.execute(
        """with recursive now (id, cur, depth) as (
               select fuzer_id, fuzer_id, 0 from entity where fuzer_id = any(%s)
               union all
               select n.id, e.merged_into, n.depth + 1 from now n join entity e on e.fuzer_id = n.cur
               where e.merged_into is not null and n.depth < 10
           ), live as (select distinct on (id) id, cur from now order by id, depth desc)
           select n.id, l.label, d.value #>> '{}' as description, t.label as at
           from live n join entity e on e.fuzer_id = n.cur left join entity_label l on l.fuzer_id = e.fuzer_id
           left join golden d on d.fuzer_id = e.fuzer_id and d.field = 'description'
           left join lateral (select tl.label from edge x join entity_label tl on tl.fuzer_id = x.dst
                              where x.src = e.fuzer_id and x.rel = 'installed_at' and x.valid_to is null
                              order by tl.label limit 1) t on e.kind = 'equipment'""", (ids,)).fetchall()
    return {r["id"]: {"label": r["label"], "description": r["description"], **({"at": r["at"]} if r["at"] else {})}
            for r in rows}


def called(conn, ids: list[str]) -> dict[str, str]:
    """What text written on the server calls each entity, as `display` in ui/src/entityRefs.ts does on a page: equipment
    by the tag it is installed at and its description ("20-GT-2000A Gas Turbine A"), else the label, else the
    description, else the id. A case's history read "S/N 76031, S/N 80853, S/N 85143" (tour D8)."""
    got = names(conn, ids)

    def one(i: str) -> str:
        n = got.get(i) or {}
        if n.get("at"):
            return " ".join(x for x in (n["at"], n.get("description")) if x)
        return n.get("label") or n.get("description") or i
    return {i: one(i) for i in ids}


def conversations(conn, fuzer_id: str, limit: int = 20) -> list[dict]:
    """The chat threads about an asset (P5.8b, #745): each conversation whose summary names it, or names the tag it is
    installed at or the equipment installed at it, newest first, with the problem, what was tried, the fix and the
    link back to where it was said. A summary and a link, never the chat (sources/teams.py)."""
    rows = conn.execute(
        """
        with targets as (
            select %(id)s::text as id
            union select dst from edge where rel = 'installed_at' and src = %(id)s and valid_to is null
            union select src from edge where rel = 'installed_at' and dst = %(id)s and valid_to is null
        )
        select c.fuzer_id as id, (select jsonb_object_agg(g.field, g.value) from golden g where g.fuzer_id = c.fuzer_id) as f
        from entity c where c.kind = 'conversation' and c.retired_at is null and c.merged_into is null
          and exists (select 1 from edge m where m.src = c.fuzer_id and m.rel = 'mentions' and m.dst in (select id from targets))
        """, {"id": fuzer_id}).fetchall()
    out = []
    for r in rows:
        f = r["f"] or {}
        tried = f.get("tried")
        out.append({"id": r["id"], "subject": f.get("subject"), "channel": f.get("channel"), "state": f.get("state"),
                    "started_at": f.get("started_at"), "last_post_at": f.get("last_post_at"),
                    "problem": f.get("problem"), "tried": tried if isinstance(tried, list) else [t for t in [tried] if t],
                    "fix": f.get("fix"), "participants": f.get("participants") or [], "web_url": f.get("web_url")})
    out.sort(key=lambda c: str(c["started_at"] or ""), reverse=True)
    return out[:limit]
