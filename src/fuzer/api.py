"""REST API: search, entity detail, lookup, flow tracing and the review queue."""

from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import db, graph, pipeline, queries
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
    return queries.stats(conn, PACK)


@app.get("/entities")
def search(q: str = "", kind: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    return queries.search(conn, PACK, q, kind, limit)


@app.get("/entities/{fuzer_id}")
def entity(fuzer_id: str, as_of: date | None = None, conn=Depends(get_conn)):
    if (ent := queries.entity(conn, fuzer_id, as_of)) is None:
        raise HTTPException(404, "unknown fuzer_id")
    return ent


@app.get("/entities/{fuzer_id}/trace")
def trace(fuzer_id: str, rel: str = "flows_to", direction: Literal["up", "down"] = "up",
          as_of: date | None = None, depth: int = 10, conn=Depends(get_conn)):
    return graph.trace(conn, fuzer_id, rel, direction, as_of, depth)


@app.get("/lookup")
def lookup(source: str | None = None, key: str | None = None, text: str | None = None,
           conn=Depends(get_conn)):
    """Find Fuzer IDs from a source record key, or from free text."""
    if source and key:
        return queries.lookup_source(conn, source, key)
    if not text:
        raise HTTPException(400, "give source+key or text")
    return queries.link_text(conn, PACK, text)


@app.get("/review")
def review_list(status: str = "open", kind: str | None = None, limit: int = 100, conn=Depends(get_conn)):
    return queries.reviews(conn, status, kind, limit)


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
