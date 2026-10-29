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
rebuilder = pipeline.BackgroundRebuild(PACK)
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
    return {**queries.stats(conn, PACK), "rebuild": rebuilder.status()}


@app.get("/entities")
def search(q: str = "", kind: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    return queries.search(conn, PACK, q, kind, limit)


@app.get("/entities/{fuzer_id}")
def entity(fuzer_id: str, as_of: date | None = None, per_group: int = 100, conn=Depends(get_conn)):
    if (ent := queries.entity(conn, fuzer_id, as_of, min(per_group, 1000), PACK)) is None:
        raise HTTPException(404, "unknown fuzer_id")
    return ent


@app.get("/entities/{fuzer_id}/members")
def entity_members(fuzer_id: str, kind: str | None = None, limit: int = 100, offset: int = 0,
                   conn=Depends(get_conn)):
    """Members of a class (including subtypes) or instances of a model."""
    return queries.members(conn, fuzer_id, kind, min(limit, 1000), offset)


@app.get("/entities/{fuzer_id}/maintenance")
def entity_maintenance(fuzer_id: str, since: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    """Notifications and work orders for a tag, equipment, model or class, with failure-mode counts."""
    return queries.maintenance(conn, fuzer_id, since, min(limit, 500))


@app.get("/entities/{fuzer_id}/trace")
def trace(fuzer_id: str, rel: str = "flows_to", direction: Literal["up", "down"] = "up",
          as_of: date | None = None, depth: int = 10, conn=Depends(get_conn)):
    return graph.trace(conn, fuzer_id, rel, direction, as_of, depth)


@app.get("/events")
def all_events(since: str | None = None, until: str | None = None, type: str | None = None, limit: int = 100,
               conn=Depends(get_conn)):
    return queries.events(conn, None, since, until, type, min(limit, 1000))


@app.get("/entities/{fuzer_id}/events")
def entity_events(fuzer_id: str, since: str | None = None, until: str | None = None, type: str | None = None,
                  limit: int = 100, conn=Depends(get_conn)):
    return queries.events(conn, fuzer_id, since, until, type, min(limit, 1000))


@app.get("/documents/{fuzer_id}/image", include_in_schema=False)
def document_image(fuzer_id: str, conn=Depends(get_conn)):
    """The scanned drawing behind a P&ID document, if there is one."""
    row = conn.execute(
        """
        select r.payload->>'image_path' as path
        from crosswalk c join source_record r using (source, record_type, source_key)
        where c.fuzer_id = %s and c.source = 'pid' and r.payload ? 'image_path'
        """,
        (fuzer_id,),
    ).fetchone()
    if not row or not Path(row["path"]).is_file():
        raise HTTPException(404, "no image for this document")
    return FileResponse(row["path"], media_type="image/png")


@app.get("/downtime")
def all_downtime(since: str | None = None, until: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    return queries.downtime(conn, None, since, until, min(limit, 1000))


@app.get("/downtime/ranking")
def downtime_ranking(by: Literal["tag", "class", "reason"] = "tag", since: str | None = None,
                     until: str | None = None, limit: int = 20, conn=Depends(get_conn)):
    return queries.downtime_ranking(conn, by, since, until, min(limit, 200))


@app.get("/downtime/reason-checks")
def downtime_reason_checks(limit: int = 100, conn=Depends(get_conn)):
    return queries.downtime_reason_checks(conn, min(limit, 1000))


@app.get("/entities/{fuzer_id}/downtime")
def entity_downtime(fuzer_id: str, since: str | None = None, until: str | None = None, limit: int = 50,
                    conn=Depends(get_conn)):
    return queries.downtime(conn, fuzer_id, since, until, min(limit, 1000))


@app.get("/ontology")
def ontology(conn=Depends(get_conn)):
    return queries.ontology(conn)


@app.get("/graph/{fuzer_id}")
def neighborhood(fuzer_id: str, depth: int = 1, rels: str | None = None, as_of: date | None = None,
                 max_nodes: int = 250, conn=Depends(get_conn)):
    """Entities within `depth` links; `rels` is a comma-separated list of link types."""
    rel_list = [r for r in rels.split(",") if r] if rels else None
    return queries.neighborhood(conn, fuzer_id, min(depth, 3), rel_list, as_of, min(max_nodes, 1000))


@app.get("/network")
def network(rel: str = "flows_to", as_of: date | None = None, conn=Depends(get_conn)):
    return queries.network(conn, rel, as_of)


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
        # A chosen value only changes this one golden field, so apply it now; no rebuild needed.
        conn.execute(
            """
            insert into golden (fuzer_id, field, value, source, rule) values (%s, %s, %s, 'review', 'review')
            on conflict (fuzer_id, field) do update
            set value = excluded.value, source = excluded.source, rule = excluded.rule
            """,
            (p["fuzer_id"], p["field"], Jsonb(value)),
        )

    conn.execute(
        """
        update review_item set status = 'decided', decision = %s, decided_by = %s, decided_at = now()
        where id = %s
        """,
        (Jsonb(d.model_dump()), d.by, item_id),
    )
    conn.commit()
    if item["kind"] == "match":
        # Merging or splitting records touches IDs, links and values; do it in the background.
        return {"decided": item_id, "rebuild": rebuilder.request()}
    return {"decided": item_id}


@app.post("/rebuild")
def rebuild():
    """Queue a rebuild; GET /rebuild for progress."""
    return rebuilder.request()


@app.get("/rebuild")
def rebuild_status():
    return rebuilder.status()
