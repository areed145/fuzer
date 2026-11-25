"""Graph and network views, lookup, the review queue and rebuilds."""

import logging
import threading
import time
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import db, ontology
from ..graph import queries
from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

log = logging.getLogger(__name__)

router = APIRouter(route_class=AuthenticatedRoute)


@router.get("/graph/{fuzer_id}")
def neighborhood(fuzer_id: str, depth: int = 1, rels: str | None = None, as_of: date | None = None,
                 max_nodes: int = 250, conn=Depends(get_conn)):
    """Entities within `depth` links; `rels` is a comma-separated list of link types."""
    rel_list = [r for r in rels.split(",") if r] if rels else None
    return queries.neighborhood(conn, fuzer_id, min(depth, 3), rel_list, as_of, min(max_nodes, 1000))


@router.get("/network")
def network(rels: str | None = None, rel: str | None = None, as_of: date | None = None, within: str | None = None,
            conn=Depends(get_conn)):
    """One network of any link types: `rels` is a comma-separated list of relations from the ontology (default:
    its flow view, e.g. flows_to production/allocation, lift_gas_to gas lift and pipes_to P&ID piping). `rel`
    names a single one, for older links. `within` keeps an entity, everything beneath it and what they link to
    directly (an entity page's Connections)."""
    wanted = [r for r in (rels or "").split(",") if r] + ([rel] if rel else [])
    known = {r["name"] for r in ontology.relations(conn)}
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise HTTPException(422, f"unknown link type: {', '.join(unknown)}")
    out = queries.network(conn, wanted or None, as_of)
    if within:
        from ..graph import hierarchy

        inside = {within, *hierarchy.subtree(conn, within)}
        edges = [e for e in out["edges"] if e["src"] in inside or e["dst"] in inside]
        ends = {x for e in edges for x in (e["src"], e["dst"])} | (inside & {n["id"] for n in out["nodes"]})
        out = {**out, "nodes": [n for n in out["nodes"] if n["id"] in ends], "edges": edges}
    return out


@router.get("/scene")
def scene(at: str | None = None, rels: str | None = None,
          layout: Literal["schematic", "geo"] = "schematic", as_of: datetime | None = None, conn=Depends(get_conn)):
    """A schematic scene of a facility or area (P5.16a): its items as generic shapes on plates and the links between
    them as pipe runs. `at` is a facility, an area or anything inside one; `rels` as for the network. `layout=geo`
    places the areas by where they are, compacted. `as_of` draws the links as they were then (a case's time)."""
    from ..graph import scene as scenes

    wanted = [r for r in (rels or "").split(",") if r]
    unknown = sorted(set(wanted) - {r["name"] for r in ontology.relations(conn)})
    if unknown:
        raise HTTPException(422, f"unknown link type: {', '.join(unknown)}")
    return scenes.scene(conn, at, wanted or None, deps.pack(), layout, as_of)


SCENE_STATUS_SECONDS = 60  # how long one read of a scene's status serves everyone looking at it (#1069)
_scene_status: dict[tuple, tuple[float, dict]] = {}
_scene_status_lock = threading.Lock()


@router.get("/scene/status")
def scene_status(at: str | None = None, rels: str | None = None, conn=Depends(get_conn)):
    """Open work, active alarms and down now for every item in the scene `at` names (P5.16b), in one read: the same
    reads as each entity's status card. Reading the whole facility takes seconds on a shared server, and a shift
    opening the 3D view together would each pay it, so one read serves the project for SCENE_STATUS_SECONDS; `as_of`
    says when it was read."""
    from ..graph import scene as scenes
    from ..ops import status_now

    key = (db.database_url(), db.scope().schema, at, rels)
    now = time.monotonic()
    with _scene_status_lock:
        for k in [k for k, (t, _) in _scene_status.items() if now - t >= SCENE_STATUS_SECONDS]:
            del _scene_status[k]
        if hit := _scene_status.get(key):
            return hit[1]
    wanted = [r for r in (rels or "").split(",") if r]
    ids = [i["id"] for i in scenes.scene(conn, at, wanted or None, deps.pack())["items"]]
    out = {**status_now.status(conn, ids), "as_of": datetime.now(UTC).isoformat(timespec="seconds")}
    with _scene_status_lock:
        _scene_status[key] = (now, out)
    return out


@router.get("/scene/trip")
def scene_trip(item: str, hours: float = 24, conn=Depends(get_conn)):
    """If this trips (P5.16d): what goes down with it and the production at risk over `hours`, an estimate."""
    from ..ops import at_risk

    if not 0 < hours <= 24 * 30:
        raise HTTPException(422, "hours is between 0 and 720")
    return at_risk.trip(conn, item, hours)


@router.get("/network/compare")
def network_compare(conn=Depends(get_conn)):
    return queries.compare_networks(conn)


@router.get("/lookup")
def lookup(source: str | None = None, key: str | None = None, text: str | None = None,
           conn=Depends(get_conn)):
    """Find Fuzer IDs from a source record key, or from free text."""
    if source and key:
        return queries.lookup_source(conn, source, key)
    if not text:
        raise HTTPException(400, "give source+key or text")
    return queries.link_text(conn, deps.pack(), text)


@router.get("/review")
def review_list(status: str = "open", kind: str | None = None, limit: int = 100, item: int | None = None,
                conn=Depends(get_conn)):
    return queries.reviews(conn, status, kind, limit, item)


class Decision(BaseModel):
    decision: Literal["accept", "reject", "choose", "field"]
    value: Any = None
    by: str = "anonymous"


@router.get("/review/groups")
def review_groups(kind: str | None = None, conn=Depends(get_conn)):
    """Open questions that share an answer (P2.3c), largest first, each with the answers it can take."""
    from ..graph import review

    return review.groups(conn, kind)


class GroupDecision(BaseModel):
    key: str
    decision: Literal["accept", "reject", "choose"]
    source: str | None = None
    value: Any = None
    standing: bool = False
    by: str = "anonymous"


@router.post("/review/groups")
def review_group_decide(d: GroupDecision, conn=Depends(get_conn)):
    """Answer every question in a group the same way; `standing` answers new ones like them at each rebuild too."""
    from ..graph import review
    from ..ops import corrections, messages

    try:
        out = review.decide_group(conn, d.key, d.decision, d.by, source=d.source, value=d.value, standing=d.standing)
        corrections.sync(conn, d.by)
        messages.sync_answers(conn, d.by)
    except LookupError as e:
        conn.rollback()
        raise HTTPException(404, str(e)) from e
    except review.ReviewError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e
    conn.commit()
    return {**out, "rebuild": deps.rebuilder.request() if out["rebuild"] else None}


@router.delete("/review/groups")
def review_group_withdraw(key: str, by: str = "anonymous", conn=Depends(get_conn)):
    """Withdraw a standing answer: new questions like these wait for a person again."""
    from ..graph import review

    if not review.withdraw(conn, key, by):
        raise HTTPException(404, f"no standing answer for {key}")
    conn.commit()
    return {"withdrawn": key}


@router.post("/review/{item_id}")
def review_decide(item_id: int, d: Decision, conn=Depends(get_conn)):
    from ..graph import review
    from ..ops import corrections, messages

    try:
        out = review.decide(conn, item_id, d.decision, d.value, d.by)
        corrections.sync(conn, d.by)  # an answer that says a source is wrong drafts its correction (P2.4)
        messages.sync_answers(conn, d.by)  # a message the steward matched becomes its case (P2.8)
    except LookupError as e:
        conn.rollback()
        raise HTTPException(404, str(e)) from e
    except review.ReviewError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e
    conn.commit()
    if out["kind"] == "schema" and d.decision == "accept":
        return {"decided": item_id, "loaded": _load_accepted(conn, item_id)}
    if out["rebuild"]:
        # Merging or splitting records touches IDs, links and values; an accepted curve lands with the
        # rebuild. Do it in the background.
        return {"decided": item_id, "rebuild": deps.rebuilder.request()}
    return {"decided": item_id}


def _load_accepted(conn, item_id: int) -> dict | None:
    """A held load whose new columns were accepted loads again now (#1145), so the steward sees it fixed instead of
    waiting for the next scheduled load; if that fails, the schedule tries again and Sources shows why."""
    from ..core import projects
    from ..sources import ingest

    name = conn.execute("select subject from review_item where id = %s", (item_id,)).fetchone()["subject"]
    try:
        report = ingest.ingest(conn, projects.field("data") or "data", exports=[name], trigger="review")
        conn.commit()
        return {"dataset": name, **{k: report.get(k) for k in ("loaded", "held", "missing")}}
    except Exception:  # the answer stands; the load is retried on schedule
        conn.rollback()
        log.warning("Loading %s after its columns were accepted failed", name, exc_info=True)
        return None


@router.get("/review/{item_id}/corrections")
def review_corrections(item_id: int, conn=Depends(get_conn)):
    """What an answer drafted for the source it said was wrong, and how each came back (P2.4)."""
    from ..core.jsonsafe import plain
    from ..ops import corrections

    return plain(corrections.listing(conn, item_id))


@router.post("/rebuild")
def rebuild():
    """Queue a rebuild; GET /rebuild for progress."""
    return deps.rebuilder.request()


@router.get("/rebuild")
def rebuild_status():
    return deps.rebuilder.status()
