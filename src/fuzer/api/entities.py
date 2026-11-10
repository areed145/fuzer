"""Entities and what hangs off them: values, limits, events, maps, drawings, documents, downtime, incidents."""

from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import (
    FileResponse,
)

from ..graph import edges, knowledge, queries
from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


@router.get("/entities")
def search(q: str = "", kind: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    return queries.search(conn, deps.pack(), q, kind, limit)


@router.get("/entities/names")
def entity_names(id: list[str] = Query(default=[], max_length=200), conn=Depends(get_conn)):
    """What to call entities cited in text: each id's label and description. Unknown ids are left out."""
    return queries.names(conn, id)


@router.get("/entities/{fuzer_id}")
def entity(fuzer_id: str, as_of: date | None = None, per_group: int = 100, conn=Depends(get_conn)):
    if (ent := queries.entity(conn, fuzer_id, as_of, min(per_group, 1000), deps.pack())) is None:
        raise HTTPException(404, "unknown fuzer_id")
    return ent


@router.get("/entities/{fuzer_id}/members")
def entity_members(fuzer_id: str, kind: str | None = None, limit: int = 100, offset: int = 0,
                   conn=Depends(get_conn)):
    """Members of a class (including subtypes) or instances of a model."""
    return queries.members(conn, fuzer_id, kind, min(limit, 1000), offset)


@router.get("/entities/{fuzer_id}/maintenance")
def entity_maintenance(fuzer_id: str, since: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    """Notifications and work orders for a tag, equipment, model or class, with failure-mode counts."""
    return queries.maintenance(conn, fuzer_id, since, min(limit, 500))


@router.get("/entities/{fuzer_id}/history")
def entity_history(fuzer_id: str, limit: int = 50, conn=Depends(get_conn)):
    """How the source records behind this entity changed over time."""
    return queries.record_history(conn, deps.pack(), fuzer_id, min(limit, 500))


@router.get("/entities/{fuzer_id}/spares")
def entity_spares(fuzer_id: str, conn=Depends(get_conn)):
    """Spare parts (SAP materials from equipment BOMs) with stock and the manual sections naming them."""
    return queries.spares(conn, fuzer_id)


@router.get("/entities/{fuzer_id}/values")
def entity_values(fuzer_id: str, at: str | None = None, hours_before: int = 48, hours_after: int = 24,
                  direct: bool = False, conn=Depends(get_conn)):
    """Process values on a unit (or an incident's unit), or of one timeseries, around a time, from the
    time-series provider. `direct`: only the entity's own points, not its instruments'."""
    from ..analysis import values

    return values.around(conn, fuzer_id, at, min(hours_before, 24 * 30), min(hours_after, 24 * 30),
                         direct=direct, pack=deps.pack())


@router.get("/entities/{fuzer_id}/limits")
def entity_limits(fuzer_id: str, at: str | None = None, conn=Depends(get_conn)):
    """Operating limits on a point or on every point of a unit: alarm limits by source, IOWs, the learned
    normal range, checks that fail and the latest excursions (#101)."""
    from ..analysis import limits
    return limits.for_entity(conn, deps.pack(), fuzer_id, at)


@router.get("/limits/issues")
def limit_issues(conn=Depends(get_conn)):
    """Limits that look wrong or are missing across the asset (#101)."""
    from ..analysis import limits
    return limits.issues(conn, deps.pack())


@router.get("/excursions")
def excursions(since: str | None = None, basis: str | None = None, limit: int = 200, conn=Depends(get_conn)):
    """Recorded excursions outside alarm limits, IOWs and learned ranges, newest first (#101)."""
    from ..analysis import limits
    return limits.recent(conn, since, basis, min(limit, 2000))


@router.get("/entities/{fuzer_id}/measurements")
def entity_measurements(fuzer_id: str, quantity: str | None = None, since: str | None = None,
                        until: str | None = None, conn=Depends(get_conn)):
    """Landed measurements (well tests, lab results, wall thickness) on an entity, per quantity and location."""
    from ..graph import measurements
    return measurements.for_entity(conn, fuzer_id, quantity, since, until)


@router.get("/entities/{fuzer_id}/curves")
def entity_curves(fuzer_id: str, conn=Depends(get_conn)):
    """The OEM curves for an equipment model, or for the model of an equipment item or tag."""
    from ..sources import curves
    model = curves.model_of(conn, fuzer_id)
    return (curves.for_model(conn, model) if model else None) or {"model_id": model, "series": {},
                                                                  "waiting": curves.waiting(conn, model) if model else []}


@router.get("/entities/{fuzer_id}/trace")
def trace(fuzer_id: str, rel: str = "flows_to", direction: Literal["up", "down"] = "up",
          as_of: date | None = None, depth: int = 10, conn=Depends(get_conn)):
    return edges.trace(conn, fuzer_id, rel, direction, as_of, depth)


@router.get("/events")
def all_events(since: str | None = None, until: str | None = None, type: str | None = None, limit: int = 100,
               conn=Depends(get_conn)):
    return queries.events(conn, None, since, until, type, min(limit, 1000))


@router.get("/entities/{fuzer_id}/events")
def entity_events(fuzer_id: str, since: str | None = None, until: str | None = None, type: str | None = None,
                  limit: int = 100, conn=Depends(get_conn)):
    return queries.events(conn, fuzer_id, since, until, type, min(limit, 1000))


@router.get("/map")
def map_features(bbox: str | None = Query(None, description="min lon,min lat,max lon,max lat"),
                 kind: list[str] | None = Query(None), limit: int = 20000, conn=Depends(get_conn)):
    """Everything with a location, as GeoJSON (facilities, pipelines, wells, ...)."""
    from ..graph import spatial

    box = tuple(float(x) for x in bbox.split(",")) if bbox else None
    if box is not None and len(box) != 4:
        raise HTTPException(400, "bbox is min lon,min lat,max lon,max lat")
    return spatial.features(conn, box, kind, min(limit, 50000))


@router.get("/entities/{fuzer_id}/map")
def entity_map(fuzer_id: str, conn=Depends(get_conn)):
    """An entity's location and the located things linked to it, as GeoJSON (`focus` is the place shown)."""
    from ..graph import spatial

    return spatial.around(conn, fuzer_id)


@router.get("/entities/{fuzer_id}/drawings")
def entity_drawings(fuzer_id: str, conn=Depends(get_conn)):
    """The P&ID sheets that show an entity, and the entity ids to highlight on them."""
    return queries.drawings_for(conn, fuzer_id)


@router.get("/documents/{fuzer_id}/drawing")
def document_drawing(fuzer_id: str, conn=Depends(get_conn)):
    """A P&ID drawing with what was read from it placed on the sheet (tags, line numbers, piping)."""
    d = queries.drawing(conn, fuzer_id)
    if d is None:
        raise HTTPException(404, "no drawing for this document")
    return d


@router.get("/documents/{fuzer_id}/image", include_in_schema=False)
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


@router.get("/downtime")
def all_downtime(since: str | None = None, until: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    return queries.downtime(conn, None, since, until, min(limit, 1000))


@router.get("/downtime/ranking")
def downtime_ranking(by: Literal["tag", "class", "reason"] = "tag", since: str | None = None,
                     until: str | None = None, limit: int = 20, conn=Depends(get_conn)):
    return queries.downtime_ranking(conn, by, since, until, min(limit, 200))


@router.get("/downtime/reason-checks")
def downtime_reason_checks(limit: int = 100, conn=Depends(get_conn)):
    return queries.downtime_reason_checks(conn, min(limit, 1000))


@router.get("/entities/{fuzer_id}/downtime")
def entity_downtime(fuzer_id: str, since: str | None = None, until: str | None = None, limit: int = 50,
                    conn=Depends(get_conn)):
    return queries.downtime(conn, fuzer_id, since, until, min(limit, 1000))


@router.get("/incidents")
def all_incidents(since: str | None = None, until: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    return queries.incidents(conn, None, since, until, min(limit, 1000))


@router.get("/upsets")
def all_upsets(status: str | None = "detected", kind: str | None = "process", limit: int = 50,
               conn=Depends(get_conn)):
    """Newest upsets, detected by default."""
    from ..ops import sre
    return sre.upsets(conn, status=status, kind=kind, limit=min(limit, 1000))


@router.get("/entities/{fuzer_id}/incidents")
def entity_incidents(fuzer_id: str, since: str | None = None, until: str | None = None, limit: int = 50,
                     conn=Depends(get_conn)):
    return queries.incidents(conn, fuzer_id, since, until, min(limit, 1000))


@router.get("/incidents/{fuzer_id}/timeline")
def incident_timeline(fuzer_id: str, conn=Depends(get_conn)):
    return queries.incident_timeline(conn, fuzer_id)


@router.get("/incidents/{fuzer_id}/reading")
def incident_reading(fuzer_id: str, limit: int = 5, conn=Depends(get_conn)):
    """Document sections in scope of the incident's unit that cover its failure modes."""
    return knowledge.for_incident(conn, fuzer_id, min(limit, 50))


@router.get("/incidents/{fuzer_id}/similar")
def similar_incidents(fuzer_id: str, limit: int = 10, conn=Depends(get_conn)):
    return queries.similar_incidents(conn, fuzer_id, min(limit, 100))


@router.get("/documents/{fuzer_id}/file", include_in_schema=False)
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


@router.get("/knowledge/search")
def knowledge_search(q: str, ref: str | None = None, limit: int = 10, include_superseded: bool = False,
                     failure_mode: list[str] = Query(default=[]), conn=Depends(get_conn)):
    """Cited passages from manuals, guides, procedures and past incidents, optionally scoped to an
    entity (a Fuzer ID or any tag / well name) and what it relates to."""
    fid = queries.resolve_ref(conn, deps.pack(), ref) if ref else None
    if ref and not fid:
        raise HTTPException(404, f"nothing found for {ref!r}")
    return knowledge.search(conn, q, fid, min(limit, 50), include_superseded, failure_mode)


@router.get("/entities/{fuzer_id}/documents")
def entity_documents(fuzer_id: str, conn=Depends(get_conn)):
    return knowledge.documents_for(conn, fuzer_id)
