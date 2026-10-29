"""Graph edges: resolve candidate edges to Fuzer IDs and store them with dates."""

from collections import Counter
from datetime import date

from psycopg.types.json import Jsonb

from . import db
from .model import Candidate, Edge


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def build(conn, cands: list[Candidate], extra: list[Edge], assigned: dict, key_index: dict) -> dict:
    def endpoint(ep):
        kind, key = ep
        if kind == "ref":
            return assigned.get(key), None
        if kind == "id":  # already a Fuzer ID (derived links)
            return key, None
        if key is None:
            return None, f"unreadable {kind}"
        ids = key_index.get((kind, key), set())
        if len(ids) == 1:
            return next(iter(ids)), None
        return None, f"ambiguous {kind}" if ids else f"unknown {kind}"

    merged, unresolved = {}, Counter()
    for e in [e for c in cands for e in c.edges] + extra:
        (src, why_s), (dst, why_d) = endpoint(e.src), endpoint(e.dst)
        if not (src and dst):
            unresolved[f"{e.rel}: {why_s or why_d}"] += 1
            continue
        # The same link stated by several sources becomes one edge listing all of them.
        key = (src, e.rel, dst, _date(e.valid_from))
        if key in merged:
            row = merged[key]
            row["sources"].add(e.source)
            row["confidence"] = max(row["confidence"], e.confidence)
        else:
            merged[key] = {"valid_to": _date(e.valid_to), "sources": {e.source}, "confidence": e.confidence,
                           "props": e.props}

    rows = [(src, rel, dst, valid_from, r["valid_to"], sorted(r["sources"]), r["confidence"],
             Jsonb(r["props"]) if r["props"] else None)
            for (src, rel, dst, valid_from), r in merged.items()]
    sync = db.sync_table(conn, "edge", ["src", "rel", "dst", "valid_from"],
                         ["valid_to", "sources", "confidence", "props"], rows, nullable_keys=("valid_from",))
    return {"edges": len(rows), "unresolved_edges": dict(unresolved),
            "edge_changes": {k: v for k, v in sync.items() if k != "rows"}}


def _trace_sql(direction: str) -> str:
    this, nxt = ("dst", "src") if direction == "up" else ("src", "dst")
    return f"""
    with recursive walk (fuzer_id, depth, path, valid_from, valid_to) as (
        select %(start)s::text, 0, array[%(start)s::text], null::date, null::date
        union all
        select e.{nxt}, w.depth + 1, w.path || e.{nxt}, e.valid_from, e.valid_to
        from walk w
        join edge e on e.{this} = w.fuzer_id
        where e.rel = %(rel)s
          and w.depth < %(depth)s
          and not e.{nxt} = any(w.path)
          and (%(as_of)s::date is null
               or ((e.valid_from is null or e.valid_from <= %(as_of)s::date)
                   and (e.valid_to is null or e.valid_to > %(as_of)s::date)))
    )
    select w.fuzer_id, w.depth, w.valid_from, w.valid_to, en.kind, l.label
    from walk w
    join entity en using (fuzer_id)
    left join entity_label l using (fuzer_id)
    where w.depth > 0
    order by w.depth, l.label
    """


def trace(conn, start: str, rel: str = "flows_to", direction: str = "up",
          as_of: date | None = None, depth: int = 10) -> list[dict]:
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    params = {"start": start, "rel": rel, "depth": depth, "as_of": as_of}
    return conn.execute(_trace_sql(direction), params).fetchall()


def link_maintenance_to_events(conn, days: int = 3) -> int:
    """A notification on a tag within `days` of a logged event on that tag is probably the same
    problem: link them (set-based, one statement)."""
    return conn.execute(
        """
        insert into edge (src, rel, dst, sources, confidence, props)
        select distinct on (n.src, ev.src) n.src, 'related_to', ev.src, array['fuzer'], 0.6,
               jsonb_build_object('days_apart', abs(left(gn.value #>> '{}', 10)::date - left(ge.value #>> '{}', 10)::date))
        from edge n
        join entity en on en.fuzer_id = n.src and en.kind = 'notification'
        join edge ev on ev.rel = 'involves' and ev.dst = n.dst
        join golden gn on gn.fuzer_id = n.src and gn.field = 'created'
        join golden ge on ge.fuzer_id = ev.src and ge.field = 'timestamp'
        where n.rel = 'concerns'
          and abs(left(gn.value #>> '{}', 10)::date - left(ge.value #>> '{}', 10)::date) <= %s
        on conflict do nothing
        """,
        (days,),
    ).rowcount
