"""REST API: search, entity detail, lookup, flow tracing and the review queue."""

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import db, graph, knowledge, lessons, pipeline, queries
from .pack import load_pack

@asynccontextmanager
async def lifespan(app):
    # Bring an older database up to date (idempotent), as every CLI command does; otherwise pages
    # that read newer tables fail on a database made by an earlier version.
    with db.connect() as conn:
        db.init_schema(conn)
    yield


app = FastAPI(title="Fuzer", version="0.1.0", lifespan=lifespan)
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


@app.get("/entities/{fuzer_id}/history")
def entity_history(fuzer_id: str, limit: int = 50, conn=Depends(get_conn)):
    """How the source records behind this entity changed over time."""
    return queries.record_history(conn, PACK, fuzer_id, min(limit, 500))


@app.get("/entities/{fuzer_id}/spares")
def entity_spares(fuzer_id: str, conn=Depends(get_conn)):
    """Spare parts (SAP materials from equipment BOMs) with stock and the manual sections naming them."""
    return queries.spares(conn, fuzer_id)


@app.get("/entities/{fuzer_id}/values")
def entity_values(fuzer_id: str, at: str | None = None, hours_before: int = 48, hours_after: int = 24,
                  direct: bool = False, conn=Depends(get_conn)):
    """Process values on a unit (or an incident's unit), or of one timeseries, around a time, from the
    time-series provider. `direct`: only the entity's own points, not its instruments'."""
    from . import timeseries
    return timeseries.around(conn, fuzer_id, at, min(hours_before, 24 * 30), min(hours_after, 24 * 30),
                             direct=direct)


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


@app.get("/map")
def map_features(bbox: str | None = Query(None, description="min lon,min lat,max lon,max lat"),
                 kind: list[str] | None = Query(None), limit: int = 20000, conn=Depends(get_conn)):
    """Everything with a location, as GeoJSON (facilities, pipelines, wells, ...)."""
    from . import spatial

    box = tuple(float(x) for x in bbox.split(",")) if bbox else None
    if box is not None and len(box) != 4:
        raise HTTPException(400, "bbox is min lon,min lat,max lon,max lat")
    return spatial.features(conn, box, kind, min(limit, 50000))


@app.get("/entities/{fuzer_id}/map")
def entity_map(fuzer_id: str, conn=Depends(get_conn)):
    """An entity's location and the located things linked to it, as GeoJSON (`focus` is the place shown)."""
    from . import spatial

    return spatial.around(conn, fuzer_id)


@app.get("/documents/{fuzer_id}/drawing")
def document_drawing(fuzer_id: str, conn=Depends(get_conn)):
    """A P&ID drawing with what was read from it placed on the sheet (tags, line numbers, piping)."""
    d = queries.drawing(conn, fuzer_id)
    if d is None:
        raise HTTPException(404, "no drawing for this document")
    return d


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


@app.get("/incidents")
def all_incidents(since: str | None = None, until: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    return queries.incidents(conn, None, since, until, min(limit, 1000))


@app.get("/entities/{fuzer_id}/incidents")
def entity_incidents(fuzer_id: str, since: str | None = None, until: str | None = None, limit: int = 50,
                     conn=Depends(get_conn)):
    return queries.incidents(conn, fuzer_id, since, until, min(limit, 1000))


@app.get("/incidents/{fuzer_id}/timeline")
def incident_timeline(fuzer_id: str, conn=Depends(get_conn)):
    return queries.incident_timeline(conn, fuzer_id)


@app.get("/incidents/{fuzer_id}/reading")
def incident_reading(fuzer_id: str, limit: int = 5, conn=Depends(get_conn)):
    """Document sections in scope of the incident's unit that cover its failure modes."""
    return knowledge.for_incident(conn, fuzer_id, min(limit, 50))


@app.get("/incidents/{fuzer_id}/similar")
def similar_incidents(fuzer_id: str, limit: int = 10, conn=Depends(get_conn)):
    return queries.similar_incidents(conn, fuzer_id, min(limit, 100))


@app.get("/documents/{fuzer_id}/file", include_in_schema=False)
def document_file(fuzer_id: str, conn=Depends(get_conn)):
    """The file behind a document (manual, guide, procedure): PDF, Markdown or text."""
    row = conn.execute(
        """
        select r.payload->>'file_path' as path
        from crosswalk c join source_record r using (source, record_type, source_key)
        where c.fuzer_id = %s and c.source = 'edms' and c.record_type = 'document'
        """,
        (fuzer_id,),
    ).fetchone()
    if not row or not row["path"] or not Path(row["path"]).is_file():
        raise HTTPException(404, "no file for this document")
    media = {".pdf": "application/pdf", ".md": "text/markdown; charset=utf-8"}.get(Path(row["path"]).suffix.lower(),
                                                                                 "text/plain; charset=utf-8")
    return FileResponse(row["path"], media_type=media)


@app.get("/knowledge/search")
def knowledge_search(q: str, ref: str | None = None, limit: int = 10, include_superseded: bool = False,
                     failure_mode: list[str] = Query(default=[]), conn=Depends(get_conn)):
    """Cited passages from manuals, guides, procedures and past incidents, optionally scoped to an
    entity (a Fuzer ID or any tag / well name) and what it relates to."""
    fid = queries.resolve_ref(conn, PACK, ref) if ref else None
    if ref and not fid:
        raise HTTPException(404, f"nothing found for {ref!r}")
    return knowledge.search(conn, q, fid, min(limit, 50), include_superseded, failure_mode)


@app.get("/entities/{fuzer_id}/documents")
def entity_documents(fuzer_id: str, conn=Depends(get_conn)):
    return knowledge.documents_for(conn, fuzer_id)


@app.get("/lessons")
def lesson_list(status: str | None = "suggested", limit: int = 100, conn=Depends(get_conn)):
    return lessons.listing(conn, status if status != "all" else None, min(limit, 1000))


class LessonDecision(BaseModel):
    decision: Literal["approve", "reject", "edit"]
    text: str | None = None
    by: str = "anonymous"


@app.post("/lessons/{lesson_id}")
def lesson_decide(lesson_id: int, d: LessonDecision, conn=Depends(get_conn)):
    if (row := lessons.decide(conn, lesson_id, d.decision, d.by, d.text)) is None:
        raise HTTPException(404, "unknown lesson")
    knowledge.build(conn)  # approved lessons appear in search straight away
    conn.commit()
    return row


@app.get("/entities/{fuzer_id}/brief")
def troubleshooting_brief(fuzer_id: str, conn=Depends(get_conn)):
    """Everything that helps troubleshoot a unit or incident, with sources."""
    from . import assistant
    return assistant.brief(conn, PACK, fuzer_id)


class Feedback(BaseModel):
    target: str
    useful: bool
    comment: str | None = None
    by: str = "anonymous"


@app.post("/feedback")
def feedback(f: Feedback, conn=Depends(get_conn)):
    row = conn.execute("insert into feedback (target, useful, comment, by_user) values (%s, %s, %s, %s) returning id",
                       (f.target, f.useful, f.comment, f.by)).fetchone()
    conn.commit()
    return row


@app.get("/feedback/briefs")
def brief_ratings(limit: int = 200, conn=Depends(get_conn)):
    """The rating set for troubleshooting briefs: past incidents with how engineers rated each brief,
    not-yet-rated first, and the overall share rated useful."""
    rows = conn.execute(
        """
        with r as (
            select split_part(target, ':', 2) as id, count(*) as ratings,
                   count(*) filter (where useful) as useful,
                   (array_agg(comment order by created_at desc) filter (where comment <> ''))[1] as last_comment
            from feedback where target like 'brief:%%' group by 1
        )
        select i.fuzer_id as id, gd.value #>> '{}' as description, gs.value #>> '{}' as start,
               coalesce(r.ratings, 0) as ratings, coalesce(r.useful, 0) as useful, r.last_comment
        from entity i
        join golden gs on gs.fuzer_id = i.fuzer_id and gs.field = 'start'
        left join golden gd on gd.fuzer_id = i.fuzer_id and gd.field = 'description'
        left join r on r.id = i.fuzer_id
        where i.kind = 'incident' and i.retired_at is null and i.merged_into is null
        order by (r.ratings is not null), gs.value #>> '{}' desc
        limit %s
        """,
        (min(limit, 2000),),
    ).fetchall()
    total = conn.execute("select count(*) filter (where kind = 'incident' and retired_at is null) as n from entity"
                         ).fetchone()["n"]
    s = conn.execute("select count(distinct target) as rated, count(*) as ratings, count(*) filter (where useful) as useful "
                     "from feedback where target like 'brief:%'").fetchone()
    return {"incidents": total, "rated": s["rated"], "ratings": s["ratings"],
            "useful_share": round(s["useful"] / s["ratings"], 2) if s["ratings"] else None, "items": rows}


@app.get("/feedback/summary")
def feedback_summary(conn=Depends(get_conn)):
    return conn.execute(
        "select split_part(target, ':', 1) as kind, count(*) as ratings, "
        "round(avg(case when useful then 1 else 0 end), 2) as useful_share from feedback group by 1 order by 1"
    ).fetchall()


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
def network(rel: Literal["flows_to", "pipes_to"] = "flows_to", as_of: date | None = None, conn=Depends(get_conn)):
    """flows_to: production / allocation flow (Energy Components); pipes_to: piping from P&IDs."""
    return queries.network(conn, rel, as_of)


@app.get("/network/compare")
def network_compare(conn=Depends(get_conn)):
    return queries.compare_networks(conn)


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

    if item["kind"] == "mapping":
        if d.decision != "reject":
            import yaml

            from . import mappings

            text = p["yaml"] if d.decision == "accept" else d.value
            try:
                raw = yaml.safe_load(text or "")
                m = mappings.Mapping(raw if isinstance(raw, dict) else {})
            except (yaml.YAMLError, mappings.MappingError) as e:
                raise HTTPException(400, f"mapping is not valid: {e}")
            if m.dataset != p["dataset"]:
                raise HTTPException(400, f"mapping must keep dataset: {p['dataset']}")
            # A new version; the next ingest loads the table and rebuilds use it.
            conn.execute("insert into mapping_version (source, record_type, dataset, yaml, approved_by) "
                         "values (%s, %s, %s, %s, %s)", (m.source, m.record_type, m.dataset, text, d.by))
    elif item["kind"] == "schema":
        if d.decision == "choose":
            raise HTTPException(400, "schema items take accept (load with the new columns) or reject (keep holding)")
        if d.decision == "accept":
            # The new columns become the expected ones; the next ingest loads the dataset.
            conn.execute("update dataset_state set schema = %s where dataset = %s", (Jsonb(p["new"]), p["dataset"]))
    elif item["kind"] == "match":
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
