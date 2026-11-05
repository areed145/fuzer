"""Ontology, graph and network views, lookup, the review queue and rebuilds."""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..graph import queries
from . import deps
from .deps import get_conn

router = APIRouter()


@router.get("/ontology")
def ontology(conn=Depends(get_conn)):
    return queries.ontology(conn)


@router.get("/graph/{fuzer_id}")
def neighborhood(fuzer_id: str, depth: int = 1, rels: str | None = None, as_of: date | None = None,
                 max_nodes: int = 250, conn=Depends(get_conn)):
    """Entities within `depth` links; `rels` is a comma-separated list of link types."""
    rel_list = [r for r in rels.split(",") if r] if rels else None
    return queries.neighborhood(conn, fuzer_id, min(depth, 3), rel_list, as_of, min(max_nodes, 1000))


@router.get("/network")
def network(rel: Literal["flows_to", "lift_gas_to", "pipes_to"] = "flows_to", as_of: date | None = None,
            conn=Depends(get_conn)):
    """flows_to: production / allocation flow (Energy Components); lift_gas_to: the gas lift network
    (Energy Components); pipes_to: piping from P&IDs."""
    return queries.network(conn, rel, as_of)


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
    return queries.link_text(conn, deps.PACK, text)


@router.get("/review")
def review_list(status: str = "open", kind: str | None = None, limit: int = 100, conn=Depends(get_conn)):
    return queries.reviews(conn, status, kind, limit)


class Decision(BaseModel):
    decision: Literal["accept", "reject", "choose"]
    value: Any = None
    by: str = "anonymous"


@router.post("/review/{item_id}")
def review_decide(item_id: int, d: Decision, conn=Depends(get_conn)):
    item = conn.execute("select * from review_item where id = %s", (item_id,)).fetchone()
    if not item:
        raise HTTPException(404, "unknown review item")
    p = item["payload"]

    if item["kind"] == "mapping":
        if d.decision != "reject":
            from ..sources import suggest

            try:  # a new version; the next ingest loads the table and rebuilds use it
                suggest.save(conn, p["dataset"], p["yaml"] if d.decision == "accept" else d.value, d.by)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
    elif item["kind"] == "schema":
        if d.decision == "choose":
            raise HTTPException(400, "schema items take accept (load with the new columns) or reject (keep holding)")
        if d.decision == "accept":
            # The new columns become the expected ones; the next ingest loads the dataset.
            conn.execute("update source_dataset set schema = %s where name = %s", (Jsonb(p["new"]), p["dataset"]))
    elif item["kind"] == "curve":  # a datasheet Claude read: accept as read, or choose with corrections
        if d.decision != "reject":
            from ..sources import llm_curves
            if d.decision == "choose" and not isinstance(d.value, dict):
                raise HTTPException(400, "choose needs the corrected reading")
            llm_curves.accept(conn, item, d.by, d.value if d.decision == "choose" else None)
    elif item["kind"] == "check":  # a held load (#225): fixed at the source, then loaded again from its dataset
        if d.decision == "choose":
            raise HTTPException(400, "held-load items take accept (seen) or reject")
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
        return {"decided": item_id, "rebuild": deps.rebuilder.request()}
    return {"decided": item_id}


@router.post("/rebuild")
def rebuild():
    """Queue a rebuild; GET /rebuild for progress."""
    return deps.rebuilder.request()


@router.get("/rebuild")
def rebuild_status():
    return deps.rebuilder.status()
