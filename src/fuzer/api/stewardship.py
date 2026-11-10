"""Master data stewardship (#476): the queues that keep the register matching the work, and the decisions made on it.

    GET  /stewardship/unmatched     refs on open exceptions that matched nothing, grouped, most open work first
    GET  /stewardship/unresolved    link values each source wrote that named nothing, from the last rebuild
    GET  /stewardship/decisions     every alias and pinned match, with who, when and why (searchable)
    POST /stewardship/aliases       record a value as another name for an entity
    POST /stewardship/aliases/{id}/retire, POST /stewardship/pins/undo   reverse a decision
    GET  /stewardship/merge/preview, POST /stewardship/merge    "Same as…" from the entity page
    GET  /stewardship/split/preview, POST /stewardship/split    "Not this record" from the entity page
    GET  /stewardship/completeness, /consistency, /conflicts     gaps in the register, each with where to fix it
    POST /stewardship/fixes, POST /stewardship/fixes/{batch}/undo   one value for many entities, and back
    POST /exceptions/{id}/resolve   (api/ops.py) pick the asset an exception's unmatched ref meant
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(prefix="/stewardship", route_class=AuthenticatedRoute)


def _do(conn, fn, *args, **kwargs):
    from ..core.jsonsafe import plain
    from ..sources.catalog import CatalogError

    try:
        out = fn(*args, **kwargs)
    except CatalogError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e
    conn.commit()
    return plain(out)


@router.get("/unmatched")
def unmatched(conn=Depends(get_conn)):
    """Each ref open exceptions carry that matched nothing: how many exceptions, from which sources, and which."""
    return [dict(r) for r in conn.execute(
        """
        select u.ref, count(*) as exceptions, array_agg(distinct e.source) as sources,
               jsonb_agg(jsonb_build_object('id', e.id, 'title', e.title, 'type', e.type, 'item_id', e.item_id)
                         order by e.value_usd desc nulls last, e.id) as open,
               bool_or(e.item_id is not null) as has_case
        from exception_record e, unnest(e.unmatched) u(ref)
        where e.state = 'open'
        group by u.ref order by bool_or(e.item_id is not null) desc, count(*) desc, u.ref limit 500
        """)]


@router.get("/unresolved")
def unresolved(conn=Depends(get_conn)):
    """Per source and link type, the values its records wrote that named nothing in the register (last rebuild)."""
    from ..sources import aliases

    have = {a["norm"] for a in aliases.listing(conn, limit=10000)}
    out = []
    for r in conn.execute(
        """
        select distinct on (source) source, built_at, stats->'coverage'->'unresolved' as unresolved
        from source_match_stat order by source, built_at desc
        """):
        for rel, v in (r["unresolved"] or {}).items():
            for x in v.get("values") or []:
                out.append({"source": r["source"], "rel": rel, "value": x["value"], "rows": x["rows"],
                            "aliased": " ".join(str(x["value"]).split()).lower() in have, "built_at": r["built_at"]})
    return sorted(out, key=lambda x: (x["aliased"], -x["rows"], x["source"], x["value"]))


@router.get("/decisions")
def decisions(q: str | None = None, current: bool = True, limit: int = Query(200, le=1000), conn=Depends(get_conn)):
    """Aliases and pinned match decisions, newest first, each reversible."""
    from ..sources import aliases

    rows = [{"kind": "alias", "id": a["id"], "what": f"{a['value']} = {a['kind']}:{a['key']}", "fuzer_id": a["fuzer_id"],
             "source": a["source"], "by": a["created_by"], "at": a["created_at"], "reason": a["reason"],
             "reversed": bool(a["retired_at"]), "reversed_by": a["retired_by"], "reversed_at": a["retired_at"]}
            for a in aliases.listing(conn, current=current, q=q, limit=limit)]
    pins = conn.execute(
        """
        select * from pinned_link where %(q)s::text is null or left_ref ilike '%%' || %(q)s || '%%'
           or right_ref ilike '%%' || %(q)s || '%%' order by decided_at desc limit %(n)s
        """, {"q": q or None, "n": limit}).fetchall()
    batches: dict[str, list] = {}
    for p in pins:
        if p["batch"]:
            batches.setdefault(p["batch"], []).append(p)
            continue
        rows.append({"kind": f"{p['rule']}_link", "id": f"{p['left_ref']}|{p['right_ref']}",
                     "what": f"{p['left_ref']} {'is' if p['rule'] == 'must' else 'is not'} {p['right_ref']}",
                     "fuzer_id": None, "source": None, "by": p["decided_by"], "at": p["decided_at"],
                     "reason": p["reason"], "reversed": False})
    for batch, ps in batches.items():  # a steward's merge or split (#476): one decision, one undo
        p = ps[0]
        if batch.startswith("split-"):
            common = set.intersection(*({x["left_ref"], x["right_ref"]} for x in ps))
            what = (f"{p['left_ref']} is not {p['right_ref']}" if len(ps) == 1 else
                    f"{min(common)} is not the same as its {len(ps)} other records")
        else:
            what = f"{p['left_ref']} is the same as {p['right_ref']}"
        rows.append({"kind": "split" if batch.startswith("split-") else "merge", "id": batch, "what": what,
                     "fuzer_id": None, "source": None, "by": p["decided_by"], "at": p["decided_at"],
                     "reason": p["reason"], "reversed": False})
    from ..graph import gaps

    rows += [{"kind": "fix", "id": f["batch"], "what": f"{f['field']} set to {f['value']!r} on {f['entities']} "
              f"entit{'y' if f['entities'] == 1 else 'ies'}", "fuzer_id": None, "source": None, "by": f["by"],
              "at": f["at"], "reason": f["reason"], "reversed": bool(f["undone_at"]), "reversed_by": f["undone_by"],
              "reversed_at": f["undone_at"]}
             for f in gaps.listing(conn, current=current, limit=limit)
             if q is None or q.lower() in f"{f['field']} {f['value']}".lower()]
    return sorted(rows, key=lambda r: r["at"], reverse=True)[:limit]


class AliasIn(BaseModel):
    value: str
    fuzer_id: str
    source: str | None = None
    reason: str | None = None
    by: str = "anonymous"


class Reason(BaseModel):
    reason: str | None = None
    by: str = "anonymous"


class PinUndo(Reason):
    left_ref: str = ""
    right_ref: str = ""
    batch: str | None = None


class FixIn(Reason):
    field: str
    value: str | float | int | bool
    fuzer_ids: list[str]


class MergeIn(Reason):
    a: str
    b: str


class SplitIn(Reason):
    fuzer_id: str
    ref: str


@router.post("/aliases")
def add_alias(body: AliasIn, conn=Depends(get_conn)):
    """Record a value as another name for an entity; it matches from now on (exceptions at once, links on the next
    rebuild)."""
    from ..sources import aliases

    return _do(conn, aliases.add, conn, body.value, body.fuzer_id, body.by, source=body.source, reason=body.reason)


@router.post("/aliases/{alias_id}/retire")
def retire_alias(alias_id: int, body: Reason, conn=Depends(get_conn)):
    """Undo an alias (the next rebuild stops using it)."""
    from ..sources import aliases

    return _do(conn, aliases.retire, conn, alias_id, body.by, reason=body.reason)


@router.post("/pins/undo")
def undo_pin(body: PinUndo, conn=Depends(get_conn)):
    """Reverse a pinned match decision (same, or not the same, or a whole merge or split by its batch); the next
    rebuild matches those records afresh."""
    from ..graph import curation
    from ..sources.catalog import _audit

    if body.batch:
        return {**_do(conn, curation.undo, conn, body.batch, body.by, reason=body.reason),
                "rebuild": deps.rebuilder.request()}
    row = conn.execute("delete from pinned_link where left_ref = %s and right_ref = %s returning *",
                       (body.left_ref, body.right_ref)).fetchone()
    if row is None:
        raise HTTPException(404, "no such pinned decision")
    _audit(conn, body.by, "pin", {"name": f"{row['left_ref']}|{row['right_ref']}"}, "delete",
           old={"rule": row["rule"], "decided_by": row["decided_by"]}, reason=body.reason)
    conn.commit()
    return {"undone": f"{row['left_ref']}|{row['right_ref']}", "rule": row["rule"]}


@router.get("/merge/preview")
def merge_preview(a: str, b: str, conn=Depends(get_conn)):
    """What "a is the same as b" would do, before it is done."""
    from ..graph import curation

    return _do(conn, curation.preview_merge, conn, a, b)


@router.post("/merge")
def merge(body: MergeIn, conn=Depends(get_conn)):
    """Two entities are one thing: pinned, and applied by a rebuild queued now. Undo it from the decision log."""
    from ..graph import curation

    return {**_do(conn, curation.merge, conn, body.a, body.b, body.by, reason=body.reason),
            "rebuild": deps.rebuilder.request()}


@router.get("/split/preview")
def split_preview(fuzer_id: str, ref: str, conn=Depends(get_conn)):
    """What "this record is not this entity" would do, before it is done."""
    from ..graph import curation

    return _do(conn, curation.preview_split, conn, fuzer_id, ref)


@router.post("/split")
def split(body: SplitIn, conn=Depends(get_conn)):
    """A record is not this entity: pinned apart, and applied by a rebuild queued now. Undo it from the decision
    log."""
    from ..graph import curation

    return {**_do(conn, curation.split, conn, body.fuzer_id, body.ref, body.by, reason=body.reason),
            "rebuild": deps.rebuilder.request()}


@router.get("/completeness")
def completeness(min_share: float = Query(0.8, ge=0.5, le=0.99), conn=Depends(get_conn)):
    """Fields most entities of a kind have, and the entities that lack them."""
    from ..core.jsonsafe import plain
    from ..graph import gaps

    return plain(gaps.completeness(conn, min_share=min_share))


@router.get("/consistency")
def consistency(conn=Depends(get_conn)):
    """Values written more than one way within a field, the usual spelling first."""
    from ..core.jsonsafe import plain
    from ..graph import gaps

    return plain(gaps.consistency(conn))


@router.get("/conflicts")
def conflicts(conn=Depends(get_conn)):
    """Open conflict reviews: sources disagree and the field rule asks a person (decided on the Review page)."""
    from ..core.jsonsafe import plain
    from ..graph import gaps

    return plain(gaps.conflicts(conn))


@router.post("/fixes")
def fix(body: FixIn, conn=Depends(get_conn)):
    """One value for a field on many entities (a reviewer's choice: it wins on every rebuild). Applied now."""
    from ..graph import gaps

    return _do(conn, gaps.set_values, conn, body.field, body.value, body.fuzer_ids, body.by, reason=body.reason)


@router.post("/fixes/{batch}/undo")
def undo_fix(batch: str, body: Reason, conn=Depends(get_conn)):
    """Put the values a fix changed back."""
    from ..graph import gaps

    return _do(conn, gaps.undo, conn, batch, body.by, reason=body.reason)
