"""Graph edges: resolve candidate edges to Fuzer IDs and store them with dates."""

from collections import Counter
from datetime import date

from psycopg.types.json import Jsonb

from .model import Candidate, Edge


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def build(conn, cands: list[Candidate], extra: list[Edge], assigned: dict, key_index: dict) -> dict:
    def endpoint(ep):
        kind, key = ep
        if kind == "ref":
            return assigned.get(key), None
        if key is None:
            return None, f"unreadable {kind}"
        ids = key_index.get((kind, key), set())
        if len(ids) == 1:
            return next(iter(ids)), None
        return None, f"ambiguous {kind}" if ids else f"unknown {kind}"

    rows, unresolved = [], Counter()
    for e in [e for c in cands for e in c.edges] + extra:
        (src, why_s), (dst, why_d) = endpoint(e.src), endpoint(e.dst)
        if src and dst:
            rows.append((src, e.rel, dst, _date(e.valid_from), _date(e.valid_to), [e.source],
                         e.confidence, Jsonb(e.props) if e.props else None))
        else:
            unresolved[f"{e.rel}: {why_s or why_d}"] += 1

    conn.execute("delete from edge")
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into edge (src, rel, dst, valid_from, valid_to, sources, confidence, props)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (src, rel, dst, valid_from) do update
            set sources = array(select distinct unnest(edge.sources || excluded.sources)),
                confidence = greatest(edge.confidence, excluded.confidence)
            """,
            rows,
        )
    return {"edges": len(rows), "unresolved_edges": dict(unresolved)}


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
