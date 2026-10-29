"""REST API: search, entity detail, lookup, flow tracing and the review queue."""

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import db, graph, nel, pipeline
from .pack import load_pack

app = FastAPI(title="Fuzer", version="0.1.0")
PACK = load_pack()
UI_DIST = Path(__file__).resolve().parents[2] / "ui" / "dist"

if (UI_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")


def get_conn():
    with db.connect() as conn:
        yield conn


@app.get("/", include_in_schema=False)
def root():
    """The web UI once built (cd ui && npm run build), otherwise the API docs."""
    index = UI_DIST / "index.html"
    return FileResponse(index) if index.exists() else RedirectResponse("/docs")


@app.get("/health")
def health():
    return {"ok": True, "pack": PACK.name}


@app.get("/stats")
def stats(conn=Depends(get_conn)):
    kinds = conn.execute(
        "select kind, count(*) as n from entity where merged_into is null group by kind order by n desc"
    ).fetchall()
    reviews = conn.execute(
        "select kind, count(*) as n from review_item where status = 'open' group by kind"
    ).fetchall()
    return {
        "pack": PACK.name,
        "facility": PACK.facility,
        "entities": {r["kind"]: r["n"] for r in kinds},
        "open_reviews": {r["kind"]: r["n"] for r in reviews},
        "records": conn.execute("select count(*) as n from source_record").fetchone()["n"],
        "edges": conn.execute("select count(*) as n from edge").fetchone()["n"],
    }


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
               or g.value::text ilike %(like)s
               or e.fuzer_id in (select fuzer_id from crosswalk where source_key ilike %(like)s))
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
            "keys": keys, "golden": golden, "values": values, "records": records, "edges": edges}


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
def review_list(status: str = "open", kind: str | None = None, limit: int = 100, conn=Depends(get_conn)):
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
