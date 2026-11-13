"""Graph and network views, lookup, the review queue and rebuilds."""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import ontology
from ..graph import queries
from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


@router.get("/graph/{fuzer_id}")
def neighborhood(fuzer_id: str, depth: int = 1, rels: str | None = None, as_of: date | None = None,
                 max_nodes: int = 250, conn=Depends(get_conn)):
    """Entities within `depth` links; `rels` is a comma-separated list of link types."""
    rel_list = [r for r in rels.split(",") if r] if rels else None
    return queries.neighborhood(conn, fuzer_id, min(depth, 3), rel_list, as_of, min(max_nodes, 1000))


@router.get("/network")
def network(rels: str | None = None, rel: str | None = None, as_of: date | None = None, conn=Depends(get_conn)):
    """One network of any link types: `rels` is a comma-separated list of relations from the ontology (default:
    its flow view, e.g. flows_to production/allocation, lift_gas_to gas lift and pipes_to P&ID piping). `rel`
    names a single one, for older links."""
    wanted = [r for r in (rels or "").split(",") if r] + ([rel] if rel else [])
    known = {r["name"] for r in ontology.relations(conn)}
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise HTTPException(422, f"unknown link type: {', '.join(unknown)}")
    return queries.network(conn, wanted or None, as_of)


@router.get("/scene")
def scene(at: str | None = None, rels: str | None = None, conn=Depends(get_conn)):
    """A schematic scene of a facility or area (P5.16a): its items as generic shapes on plates and the links between
    them as pipe runs. `at` is a facility, an area or anything inside one; `rels` as for the network."""
    from ..graph import scene as scenes

    wanted = [r for r in (rels or "").split(",") if r]
    unknown = sorted(set(wanted) - {r["name"] for r in ontology.relations(conn)})
    if unknown:
        raise HTTPException(422, f"unknown link type: {', '.join(unknown)}")
    return scenes.scene(conn, at, wanted or None, deps.pack())


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
def review_list(status: str = "open", kind: str | None = None, limit: int = 100, conn=Depends(get_conn)):
    return queries.reviews(conn, status, kind, limit)


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
    from ..ops import corrections

    try:
        out = review.decide_group(conn, d.key, d.decision, d.by, source=d.source, value=d.value, standing=d.standing)
        corrections.sync(conn, d.by)
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
    from ..ops import corrections

    try:
        out = review.decide(conn, item_id, d.decision, d.value, d.by)
        corrections.sync(conn, d.by)  # an answer that says a source is wrong drafts its correction (P2.4)
    except LookupError as e:
        conn.rollback()
        raise HTTPException(404, str(e)) from e
    except review.ReviewError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e
    conn.commit()
    if out["rebuild"]:
        # Merging or splitting records touches IDs, links and values; an accepted curve lands with the
        # rebuild. Do it in the background.
        return {"decided": item_id, "rebuild": deps.rebuilder.request()}
    return {"decided": item_id}


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
