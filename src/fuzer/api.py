"""REST API: search, entity detail, lookup, flow tracing and the review queue."""

from datetime import date
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import db, graph, nel, pipeline
from .pack import load_pack

app = FastAPI(title="Fuzer", version="0.1.0")
PACK = load_pack()


def get_conn():
    with db.connect() as conn:
        yield conn


@app.get("/health")
def health():
    return {"ok": True, "pack": PACK.name}


@app.get("/entities")
def search(q: str = "", kind: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    keys = [q.upper()]
    if tag := PACK.tags.parse(q):
        keys.append(tag)
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
               or g.value::text ilike %(like)s)
        order by l.label
        limit %(limit)s
        """,
        {"q": q, "kind": kind, "keys": keys, "like": f"%{q}%", "limit": limit},
    ).fetchall()


@app.get("/entities/{fuzer_id}")
def entity(fuzer_id: str, as_of: date | None = None, conn=Depends(get_conn)):
    ent = conn.execute("select * from entity where fuzer_id = %s", (fuzer_id,)).fetchone()
    if not ent:
        raise HTTPException(404, "unknown fuzer_id")
    if ent["merged_into"]:
        return {"fuzer_id": fuzer_id, "merged_into": ent["merged_into"]}

    golden = {r["field"]: {"value": r["value"], "source": r["source"], "rule": r["rule"]}
              for r in conn.execute("select * from golden where fuzer_id = %s", (fuzer_id,))}
    keys = conn.execute("select kind, key from entity_key where fuzer_id = %s", (fuzer_id,)).fetchall()
    records = conn.execute(
        "select source, record_type, source_key, method, confidence from crosswalk where fuzer_id = %s",
        (fuzer_id,),
    ).fetchall()
    edges = conn.execute(
        """
        select case when e.src = %(id)s then 'out' else 'in' end as direction, e.rel,
               case when e.src = %(id)s then e.dst else e.src end as other,
               o.kind as other_kind, l.label as other_label,
               e.valid_from, e.valid_to, e.sources, e.confidence, e.props
        from edge e
        join entity o on o.fuzer_id = case when e.src = %(id)s then e.dst else e.src end
        left join entity_label l on l.fuzer_id = o.fuzer_id
        where (e.src = %(id)s or e.dst = %(id)s)
          and (%(as_of)s::date is null
               or ((e.valid_from is null or e.valid_from <= %(as_of)s::date)
                   and (e.valid_to is null or e.valid_to > %(as_of)s::date)))
        order by direction, e.rel, l.label
        """,
        {"id": fuzer_id, "as_of": as_of},
    ).fetchall()
    return {"fuzer_id": fuzer_id, "kind": ent["kind"], "keys": keys, "golden": golden,
            "records": records, "edges": edges}


@app.get("/entities/{fuzer_id}/trace")
def trace(fuzer_id: str, rel: str = "flows_to", direction: Literal["up", "down"] = "up",
          as_of: date | None = None, depth: int = 10, conn=Depends(get_conn)):
    return graph.trace(conn, fuzer_id, rel, direction, as_of, depth)


@app.get("/lookup")
def lookup(source: str | None = None, key: str | None = None, text: str | None = None,
           conn=Depends(get_conn)):
    """Find Fuzer IDs from a source record key, or from free text."""
    if source and key:
        return conn.execute("select * from crosswalk where source = %s and source_key = %s",
                            (source, key)).fetchall()
    if not text:
        raise HTTPException(400, "give source+key or text")
    out = []
    for m in nel.find_mentions(text, PACK):
        ids = [r["fuzer_id"] for r in conn.execute(
            "select fuzer_id from entity_key where kind = %s and key = %s", (m.kind, m.key))]
        out.append({**m.__dict__, "fuzer_id": ids[0] if len(ids) == 1 else None,
                    "candidates": ids if len(ids) > 1 else []})
    return out


@app.get("/review")
def review_list(status: str = "open", kind: str | None = None, conn=Depends(get_conn)):
    return conn.execute(
        """
        select * from review_item
        where status = %s and (%s::text is null or kind = %s)
        order by id
        """,
        (status, kind, kind),
    ).fetchall()


class Decision(BaseModel):
    decision: Literal["accept", "reject", "choose"]
    value: Any = None
    by: str = "anonymous"


@app.post("/review/{item_id}")
def review_decide(item_id: int, d: Decision, conn=Depends(get_conn)):
    item = conn.execute("select * from review_item where id = %s", (item_id,)).fetchone()
    if not item:
        raise HTTPException(404, "unknown review item")
    p = item["payload"]

    if item["kind"] == "match":
        if d.decision == "choose":
            raise HTTPException(400, "match items take accept or reject")
        rule = "must" if d.decision == "accept" else "cannot"
        conn.execute(
            """
            insert into pinned_link (left_ref, right_ref, rule, decided_by) values (%s, %s, %s, %s)
            on conflict (left_ref, right_ref) do update set rule = excluded.rule,
                decided_by = excluded.decided_by, decided_at = now()
            """,
            (p["left"]["ref"], p["right"]["ref"], rule, d.by),
        )
    else:
        if d.decision == "reject":
            raise HTTPException(400, "conflict items take accept (keep chosen) or choose (with value)")
        value = p["chosen"] if d.decision == "accept" else d.value
        if value is None:
            raise HTTPException(400, "choose needs a value")
        conn.execute("delete from assertion where fuzer_id = %s and field = %s and source = 'review'",
                     (p["fuzer_id"], p["field"]))
        conn.execute(
            "insert into assertion (fuzer_id, field, value, source, source_key) values (%s, %s, %s, 'review', %s)",
            (p["fuzer_id"], p["field"], Jsonb(value), d.by),
        )

    conn.execute(
        """
        update review_item set status = 'decided', decision = %s, decided_by = %s, decided_at = now()
        where id = %s
        """,
        (Jsonb(d.model_dump()), d.by, item_id),
    )
    return {"decided": item_id, "rebuild": pipeline.rebuild(conn, PACK)}


@app.post("/rebuild")
def rebuild(conn=Depends(get_conn)):
    return pipeline.rebuild(conn, PACK)
