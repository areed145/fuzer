"""REST API: search, entity detail, lookup, flow tracing, the review queue and the work engine."""

from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import graph, knowledge, lessons, pipeline, queries
from .core import db
from .core.pack import load_pack
from .workflow import engine as work


@asynccontextmanager
async def lifespan(app):
    # Bring an older database up to date (idempotent), as every CLI command does; otherwise pages
    # that read newer tables fail on a database made by an earlier version.
    with db.connect() as conn:
        db.init_schema(conn)
        work.load_defaults(conn)
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
    from .sources import timeseries
    return timeseries.around(conn, fuzer_id, at, min(hours_before, 24 * 30), min(hours_after, 24 * 30),
                             direct=direct, pack=PACK)


@app.get("/entities/{fuzer_id}/limits")
def entity_limits(fuzer_id: str, at: str | None = None, conn=Depends(get_conn)):
    """Operating limits on a point or on every point of a unit: alarm limits by source, IOWs, the learned
    normal range, checks that fail and the latest excursions (#101)."""
    from . import limits
    return limits.for_entity(conn, PACK, fuzer_id, at)


@app.get("/limits/issues")
def limit_issues(conn=Depends(get_conn)):
    """Limits that look wrong or are missing across the asset (#101)."""
    from . import limits
    return limits.issues(conn, PACK)


@app.get("/excursions")
def excursions(since: str | None = None, basis: str | None = None, limit: int = 200, conn=Depends(get_conn)):
    """Recorded excursions outside alarm limits, IOWs and learned ranges, newest first (#101)."""
    from . import limits
    return limits.recent(conn, since, basis, min(limit, 2000))


@app.get("/entities/{fuzer_id}/measurements")
def entity_measurements(fuzer_id: str, quantity: str | None = None, since: str | None = None,
                        until: str | None = None, conn=Depends(get_conn)):
    """Landed measurements (well tests, lab results, wall thickness) on an entity, per quantity and location."""
    from . import measurements
    return measurements.for_entity(conn, fuzer_id, quantity, since, until)


@app.get("/entities/{fuzer_id}/curves")
def entity_curves(fuzer_id: str, conn=Depends(get_conn)):
    """The OEM curves for an equipment model, or for the model of an equipment item or tag."""
    from . import curves
    model = curves.model_of(conn, fuzer_id)
    return (curves.for_model(conn, model) if model else None) or {"model_id": model, "series": {},
                                                                  "waiting": curves.waiting(conn, model) if model else []}


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


@app.get("/entities/{fuzer_id}/drawings")
def entity_drawings(fuzer_id: str, conn=Depends(get_conn)):
    """The P&ID sheets that show an entity, and the entity ids to highlight on them."""
    return queries.drawings_for(conn, fuzer_id)


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


@app.get("/value")
def value_ranking(kind: list[str] | None = Query(None), by: Literal["value", "per_effort"] = "value",
                  ref: str | None = None, as_of: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    """Upsets, work, plans and opportunities ranked by the shared value / risk score (#100)."""
    from . import value
    fid = queries.resolve_ref(conn, PACK, ref) if ref else None
    return value.ranked(conn, PACK, tuple(kind or value.KINDS), as_of, by, fid, min(limit, 500))


@app.get("/board")
def board_page(kind: str | None = None, area: str | None = None, unit: str | None = None, text: str | None = None,
               since: str | None = None, owner: str | None = None, page: int = 1, per_page: int = 50,
               facets: bool = False, conn=Depends(get_conn)):
    """The work board (#99): every outstanding piece of work, ranked by value, one page at a time; with
    `facets`, counts by kind, area and owner for the views."""
    from . import board
    area_id = area if area and area.startswith("fz-") else queries.resolve_ref(conn, PACK, area) if area else None
    unit_id = queries.resolve_ref(conn, PACK, unit) if unit else None
    return board.cards(conn, kind, area_id, unit_id, text, since, max(1, page), min(per_page, 200), owner, facets)


@app.get("/board/history")
def board_history(key: str, conn=Depends(get_conn)):
    from . import board
    return board.history(conn, key)


class Pin(BaseModel):
    key: str
    reason: str = ""
    rank: int | None = None
    value_usd: float | None = None
    by: str = "anonymous"


@app.post("/board/pins")
def board_pin(p: Pin, conn=Depends(get_conn)):
    """Pin a card's rank or value with a reason (both empty: unpin). Applied at the next refresh, which
    this runs."""
    from . import board
    try:
        out = board.pin(conn, p.key, p.by, p.reason, p.rank, p.value_usd)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    board.refresh(conn, PACK)
    return out


class Project(BaseModel):
    title: str
    unit: str | None = None
    owner: str | None = None
    due: str | None = None
    benefit_usd: float | None = None
    effort_usd: float | None = None
    scope: str | None = None
    by: str = "anonymous"


@app.post("/board/projects")
def board_project(p: Project, conn=Depends(get_conn)):
    """Add a project to the board."""
    from . import board
    unit_id = queries.resolve_ref(conn, PACK, p.unit) if p.unit else None
    out = board.add_project(conn, p.title, p.by, unit_id, p.owner, p.due, p.benefit_usd, p.effort_usd, p.scope)
    board.refresh(conn, PACK)
    return out


@app.get("/reports/daily")
def daily_reports(limit: int = 30, conn=Depends(get_conn)):
    """Days with a daily operations report, newest first (#98)."""
    from . import daily_report
    return daily_report.days(conn, min(limit, 365))


@app.get("/reports/daily/{day}")
def daily_report_day(day: str, version: int | None = None, conn=Depends(get_conn)):
    from . import daily_report
    out = daily_report.get(conn, day, version)
    if out is None:
        raise HTTPException(404, f"no report for {day}")
    return out


@app.get("/opportunities")
def opportunity_list(status: str | None = "open", limit: int = 100, conn=Depends(get_conn)):
    """Opportunities from the daily scan (#102)."""
    from . import opportunities
    return opportunities.listing(conn, status, min(limit, 500))


class OpportunityDecision(BaseModel):
    decision: Literal["accepted", "rejected", "project"]
    by: str = "anonymous"
    reason: str | None = None


@app.post("/opportunities/{key:path}/decision")
def opportunity_decide(key: str, d: OpportunityDecision, conn=Depends(get_conn)):
    """Accept, reject (a reason is required) or turn an opportunity into a board project."""
    from . import board, opportunities
    try:
        out = opportunities.decide(conn, key, d.decision, d.by, d.reason)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    board.refresh(conn, PACK)
    return out


class StudyIn(BaseModel):
    kind: Literal["correlate", "correlation_matrix", "modes", "entities", "events", "spe"]
    params: dict[str, Any]
    by: str = "anonymous"
    now: bool | None = None


@app.post("/studies")
def study_start(s: StudyIn, conn=Depends(get_conn)):
    """Run a correlation or clustering study now when it is small, else queue it (#106)."""
    from . import study
    params = dict(s.params)
    for k in ("target", "scope"):
        if isinstance(params.get(k), str) and params[k] != "facility" and not params[k].startswith("fz-"):
            params[k] = queries.resolve_ref(conn, PACK, params[k])
    try:
        return study.start(conn, PACK, s.kind, params, s.by, s.now)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/studies")
def study_list(limit: int = 50, conn=Depends(get_conn)):
    from . import study
    return study.listing(conn, min(limit, 200))


@app.get("/studies/{sid}")
def study_get(sid: int, conn=Depends(get_conn)):
    from . import study
    out = study.get(conn, sid)
    if out is None:
        raise HTTPException(404, f"no study {sid}")
    return out


@app.post("/studies/{sid}/rerun")
def study_rerun(sid: int, by: str = "anonymous", conn=Depends(get_conn)):
    from . import study
    try:
        return study.rerun(conn, PACK, sid, by)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class Ranking(BaseModel):
    items: list[str]  # fuzer ids (or opportunity ids), most valuable first
    comment: str | None = None
    by: str = "anonymous"


@app.post("/value/rankings")
def value_rank(r: Ranking, conn=Depends(get_conn)):
    """An engineer's own ordering of a sample, to calibrate the score against."""
    row = conn.execute("insert into value_ranking (items, comment, by_user) values (%s, %s, %s) returning id",
                       (Jsonb(r.items), r.comment, r.by)).fetchone()
    conn.commit()
    return row


@app.get("/value/calibration")
def value_calibration(as_of: str | None = None, conn=Depends(get_conn)):
    """How well the score's ranking agrees with engineers' orderings (Kendall's tau, top-3 overlap)."""
    from . import value
    return value.calibrate(conn, PACK, as_of)


class Image(BaseModel):
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    data: str  # base64


class Question(BaseModel):
    question: str
    conversation_id: str | None = None
    by: str = "anonymous"
    images: list[Image] = []  # screenshots, photos, datasheet pages the user attaches


MAX_IMAGES, MAX_IMAGE_BYTES = 4, 5 * 1024 * 1024


@app.post("/assistant/ask")
async def assistant_ask(q: Question):
    """Ask the operations assistant (Claude agents). Returns a job id at once; poll /assistant/jobs/{id}."""
    import base64
    import binascii

    from .agents import service

    if not q.question.strip():
        raise HTTPException(400, "empty question")
    if len(q.images) > MAX_IMAGES:
        raise HTTPException(400, f"at most {MAX_IMAGES} images")
    try:
        images = [(i.media_type, base64.b64decode(i.data, validate=True)) for i in q.images]
    except binascii.Error:
        raise HTTPException(400, "images must be base64")
    if any(len(b) > MAX_IMAGE_BYTES for _, b in images):
        raise HTTPException(400, "each image at most 5 MB")
    try:
        return service.start(q.question.strip(), q.conversation_id, q.by, images)
    except service.Unavailable as e:
        raise HTTPException(503, str(e))


@app.get("/assistant/jobs/{job_id}")
def assistant_job(job_id: str):
    """Progress (which agent is calling which tool) and, when done, the answer with its cost."""
    from .agents import service
    if (out := service.status(job_id)) is None:
        raise HTTPException(404, "unknown job (the server may have restarted)")
    return out


@app.get("/figures/{figure_id}.html", include_in_schema=False)
def figure_page(figure_id: str, conn=Depends(get_conn)):
    """One chart as a page of its own (for sharing and for MCP clients)."""
    from . import plots
    if (row := plots.get(conn, figure_id)) is None:
        raise HTTPException(404, "unknown figure")
    return HTMLResponse(plots.html(row["id"], row["title"], row["spec"]))


@app.get("/figures/{figure_id}.png", include_in_schema=False)
def figure_png(figure_id: str, conn=Depends(get_conn)):
    """A plain image of a chart (what look_at_figure shows the assistant)."""
    from . import plots
    if (row := plots.get(conn, figure_id)) is None:
        raise HTTPException(404, "unknown figure")
    return Response(plots.png(row["spec"]), media_type="image/png")


@app.get("/figures/{figure_id}")
def figure(figure_id: str, conn=Depends(get_conn)):
    """A chart the assistant made: the Plotly figure, its summary and sources."""
    from . import plots
    if (row := plots.get(conn, figure_id)) is None:
        raise HTTPException(404, "unknown figure")
    return {k: row[k] for k in ("id", "kind", "title", "spec", "summary", "sources", "by_source", "created_at")}


@app.get("/assistant/answers")
def assistant_answers(limit: int = 50, conn=Depends(get_conn)):
    """Recent questions and answers with their ratings."""
    return conn.execute(
        """
        select a.id, a.conversation_id, a.question, a.answer, a.error, a.specialists, a.usage, a.seconds,
               a.by_user, a.created_at, count(f.id) as ratings, count(f.id) filter (where f.useful) as useful
        from assistant_answer a left join feedback f on f.target = 'answer:' || a.id
        group by a.id order by a.created_at desc limit %s
        """,
        (min(limit, 500),),
    ).fetchall()


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
def network(rel: Literal["flows_to", "lift_gas_to", "pipes_to"] = "flows_to", as_of: date | None = None,
            conn=Depends(get_conn)):
    """flows_to: production / allocation flow (Energy Components); lift_gas_to: the gas lift network
    (Energy Components); pipes_to: piping from P&IDs."""
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

            from .sources import mappings

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
    elif item["kind"] == "curve":  # a datasheet Claude read: accept as read, or choose with corrections
        if d.decision != "reject":
            from .sources import llm_curves
            if d.decision == "choose" and not isinstance(d.value, dict):
                raise HTTPException(400, "choose needs the corrected reading")
            llm_curves.accept(conn, item, d.by, d.value if d.decision == "choose" else None)
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
    if item["kind"] == "match" or (item["kind"] == "curve" and d.decision != "reject"):
        # Merging or splitting records touches IDs, links and values; an accepted curve lands with the
        # rebuild. Do it in the background.
        return {"decided": item_id, "rebuild": rebuilder.request()}
    return {"decided": item_id}


@app.post("/rebuild")
def rebuild():
    """Queue a rebuild; GET /rebuild for progress."""
    return rebuilder.request()


@app.get("/rebuild")
def rebuild_status():
    return rebuilder.status()


# --- work engine (#97) ---------------------------------------------------------------------------

@app.exception_handler(work.WorkError)
def work_error(request, e: work.WorkError):
    body = {"detail": str(e)}
    if isinstance(e, work.CheckFailed):
        body["problems"] = [vars(p) for p in e.problems]
    return JSONResponse(body, status_code=e.status)


class WorkBy(BaseModel):
    by: str


class WorkDefinitionIn(BaseModel):
    xml: str
    by: str


class WorkStart(BaseModel):
    process_key: str
    by: str
    data: dict[str, Any] = {}
    title: str | None = None
    subjects: list[str] | None = None
    priority: float | None = None
    correlation_key: str | None = None
    correlate: bool = False   # send to the open process with the same correlation key instead, if there is one


class WorkComplete(BaseModel):
    by: str
    data: dict[str, Any] = {}


class WorkHandOff(BaseModel):
    by: str
    to: str                   # a person, role:<role> or agent:<role>
    reason: str | None = None


class WorkComment(BaseModel):
    by: str
    text: str
    attachments: list[dict[str, Any]] = []


class WorkTransition(BaseModel):
    by: str
    state: str
    reason: str | None = None


class WorkItemIn(BaseModel):
    type: str
    title: str
    by: str
    subjects: list[str] = []
    priority: float | None = None
    assignee: str | None = None
    role: str | None = None
    due: datetime | None = None
    source: str | None = None
    payload: dict[str, Any] = {}


class WorkMessage(BaseModel):
    name: str
    correlation_key: str
    by: str = work.SYSTEM
    payload: dict[str, Any] = {}
    process_key: str | None = None


class WorkDraft(BaseModel):
    process_key: str
    by: str
    from_id: int | None = None  # copy this version (default: the published one) unless a draft is open


class WorkMigrate(BaseModel):
    by: str
    definition_id: int
    mapping: dict[str, str] = {}


class WorkBulk(BaseModel):
    by: str
    task_ids: list[int]
    action: str               # the label of a bulk action the tasks' form declares
    reason: str | None = None


class WorkPerson(BaseModel):
    roles: list[str]
    by: str = work.SYSTEM


@app.get("/work/items")
def work_items(state: str | None = "open", type: str | None = None, assignee: str | None = None,
               role: list[str] | None = Query(None), subject: str | None = None, overdue: bool | None = None,
               q: str | None = None, limit: int = 50, offset: int = 0, conn=Depends(get_conn)):
    """Work items ranked by priority, with filters, the total and how many are overdue or due soon."""
    return work.list_items(conn, None if state == "all" else state, type, assignee, role, subject, overdue, q,
                           min(limit, 1000), offset)


@app.post("/work/items")
def work_item_create(i: WorkItemIn, conn=Depends(get_conn)):
    """A work item without a process (a plain task with an owner, a due date and a history)."""
    return work.create_item(conn, i.type, i.title, i.by, i.subjects, i.priority, i.assignee, i.role, i.due,
                            i.source, i.payload)


@app.get("/work/items/{item_id}")
def work_item(item_id: int, conn=Depends(get_conn)):
    """An item with its processes (path taken, open steps) and every event in its history."""
    return work.item(conn, item_id)


@app.post("/work/items/{item_id}/comment")
def work_item_comment(item_id: int, c: WorkComment, conn=Depends(get_conn)):
    return work.comment(conn, item_id, c.by, c.text, c.attachments)


@app.post("/work/items/{item_id}/transition")
def work_item_transition(item_id: int, t: WorkTransition, conn=Depends(get_conn)):
    return work.transition(conn, item_id, t.state, t.by, t.reason)


@app.post("/work/items/{item_id}/hand-off")
def work_item_hand_off(item_id: int, h: WorkHandOff, conn=Depends(get_conn)):
    return work.assign_item(conn, item_id, h.by, h.to, h.reason)


@app.get("/work/inbox")
def work_inbox(person: str, role: list[str] | None = Query(None), conn=Depends(get_conn)):
    """A person's tasks ('mine') and their roles' unclaimed queue, with overdue and due-soon counts.
    Roles default to the ones the person holds."""
    return work.my_work(conn, person, role)


@app.get("/work/agents/{role}")
def work_agent_queue(role: str, conn=Depends(get_conn)):
    return work.agent_work(conn, role)


@app.get("/work/tasks/{task_id}")
def work_task(task_id: int, conn=Depends(get_conn)):
    """One step with its item, process data, form-js form, bulk actions and evidence."""
    return work.task(conn, task_id)


@app.post("/work/tasks/bulk-complete")
def work_bulk_complete(b: WorkBulk, conn=Depends(get_conn)):
    """Complete several steps with a bulk action their form declares (never requiresHuman steps)."""
    return work.bulk_complete(conn, b.task_ids, b.by, b.action, b.reason)


@app.post("/work/tasks/{task_id}/claim")
def work_claim(task_id: int, b: WorkBy, conn=Depends(get_conn)):
    return work.claim(conn, task_id, b.by)


@app.post("/work/tasks/{task_id}/release")
def work_release(task_id: int, b: WorkBy, conn=Depends(get_conn)):
    return work.release(conn, task_id, b.by)


@app.post("/work/tasks/{task_id}/complete")
def work_complete(task_id: int, c: WorkComplete, conn=Depends(get_conn)):
    """Complete a step with its form data. A requiresHuman step refuses agents (403)."""
    return work.complete(conn, task_id, c.by, c.data)


@app.post("/work/tasks/{task_id}/hand-off")
def work_task_hand_off(task_id: int, h: WorkHandOff, conn=Depends(get_conn)):
    return work.hand_off(conn, task_id, h.by, h.to, h.reason)


@app.post("/work/processes")
def work_start(s: WorkStart, conn=Depends(get_conn)):
    """Start a published process on a new work item (or, with `correlate`, add to the open one)."""
    if s.correlate:
        return work.start_or_correlate(conn, s.process_key, s.data, s.by, title=s.title, subjects=s.subjects,
                                       priority=s.priority)
    return work.start(conn, s.process_key, s.data, s.by, s.title, s.subjects, s.priority, s.correlation_key)


@app.get("/work/processes/{process_id}")
def work_process(process_id: int, conn=Depends(get_conn)):
    return work.process(conn, process_id)


@app.post("/work/processes/{process_id}/migrate")
def work_migrate(process_id: int, m: WorkMigrate, conn=Depends(get_conn)):
    """Move one running process to another published version (explicit, per process)."""
    return work.migrate(conn, process_id, m.definition_id, m.by, m.mapping)


@app.post("/work/processes/{process_id}/cancel")
def work_cancel(process_id: int, t: WorkTransition, conn=Depends(get_conn)):
    return work.cancel(conn, process_id, t.by, t.reason)


@app.post("/work/messages")
def work_message(m: WorkMessage, conn=Depends(get_conn)):
    """Send a message (new evidence, a notification raised, a comment) to the open process with its correlation key."""
    return work.send_message(conn, m.name, m.correlation_key, m.payload, m.by, m.process_key)


@app.get("/work/definitions")
def work_definitions(process_key: str | None = None, conn=Depends(get_conn)):
    return work.definitions(conn, process_key)


@app.post("/work/definitions")
def work_definition_import(d: WorkDefinitionIn, conn=Depends(get_conn)):
    """Import a .bpmn file as draft versions (one per process in it)."""
    return work.import_bpmn(conn, d.xml, d.by)


@app.post("/work/definitions/draft")
def work_definition_draft(d: WorkDraft, conn=Depends(get_conn)):
    """The draft to edit in the modeler: the open one, or a new one copied from a version."""
    return work.draft(conn, d.process_key, d.by, d.from_id)


@app.put("/work/definitions/{definition_id}")
def work_definition_save(definition_id: int, d: WorkDefinitionIn, conn=Depends(get_conn)):
    """Save the modeler's diagram over a draft (published versions never change)."""
    return work.save_draft(conn, definition_id, d.xml, d.by)


@app.get("/work/definitions/{definition_id}/compare")
def work_definition_compare(definition_id: int, against: int | None = None, conn=Depends(get_conn)):
    """Elements added, removed or changed against another version (default: the published one)."""
    return work.compare(conn, definition_id, against)


@app.get("/work/monitor/{process_key}")
def work_monitor(process_key: str, definition_id: int | None = None, days: int = 30, conn=Depends(get_conn)):
    """Steps waiting and finished at each element of a process, for the diagram overlay."""
    return work.monitor(conn, process_key, definition_id, min(max(days, 1), 365))


@app.get("/work/roles")
def work_roles():
    """The person roles and agent roles a process may name."""
    return work.roles()


@app.post("/work/definitions/{definition_id}/check")
def work_definition_check(definition_id: int, b: WorkBy, conn=Depends(get_conn)):
    return work.check_definition(conn, definition_id, b.by)


@app.post("/work/definitions/{definition_id}/publish")
def work_definition_publish(definition_id: int, b: WorkBy, conn=Depends(get_conn)):
    """Publish a version (workflow_admin role). Refused with 422 and each problem's element if a check fails."""
    return work.publish(conn, definition_id, b.by)


@app.get("/work/definitions/{definition_id}/bpmn")
def work_definition_export(definition_id: int, conn=Depends(get_conn)):
    return Response(work.export_bpmn(conn, definition_id), media_type="application/xml")


@app.get("/work/forms")
def work_forms(conn=Depends(get_conn)):
    return work.forms(conn)


@app.get("/work/forms/{key}")
def work_form(key: str, conn=Depends(get_conn)):
    if (f := work.form(conn, key)) is None:
        raise HTTPException(404, f"no form '{key}'")
    return f


@app.post("/work/forms")
def work_form_import(schema: dict[str, Any], by: str, conn=Depends(get_conn)):
    """A form-js schema as the next version of the form its `id` names."""
    return work.import_form(conn, schema, by)


@app.get("/work/people")
def work_people(conn=Depends(get_conn)):
    return work.people(conn)


@app.put("/work/people/{name}")
def work_person(name: str, p: WorkPerson, conn=Depends(get_conn)):
    return work.set_person(conn, name, p.roles, p.by)


@app.post("/work/tick")
def work_tick(conn=Depends(get_conn)):
    """Fire due timers and escalate overdue tasks now (the Dagster schedule does this every few minutes)."""
    return work.tick(conn)
