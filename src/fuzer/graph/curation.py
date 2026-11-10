"""Steward corrections to entity resolution (#476): two entities are the same thing, or a record is not this thing.

    preview_merge(conn, a, b) / merge(conn, a, b, by, reason)          "Same as…": one entity from now on
    preview_split(conn, fid, ref) / split(conn, fid, ref, by, reason)  "Not this record": the record stands apart
    undo(conn, batch, by, reason)                                      reverse either

Both are written as pinned links (must / cannot), the same decisions the match review makes, so they hold on every
rebuild and show in the decision log. A merge or split takes effect on the next rebuild (the API queues one). Each
decision's pins share a batch id: one undo removes them all.
"""

import uuid

from ..sources._catalog_common import CatalogError, _audit


def _ref(r) -> str:
    return f"{r['source']}:{r['record_type']}:{r['source_key']}"


def _entity(conn, fid: str) -> dict:
    e = conn.execute(
        "select e.fuzer_id, e.kind, e.merged_into, e.retired_at, l.label from entity e "
        "left join entity_label l using (fuzer_id) where e.fuzer_id = %s", (fid,)).fetchone()
    if e is None:
        raise CatalogError(f"no entity {fid}")
    if e["merged_into"]:
        raise CatalogError(f"{fid} was merged into {e['merged_into']}")
    if e["retired_at"]:
        raise CatalogError(f"{fid} is retired (no record maps to it)")
    e = dict(e)
    e["records"] = [dict(r, ref=_ref(r)) for r in conn.execute(
        "select source, record_type, source_key, method from crosswalk where fuzer_id = %s "
        "order by source, record_type, source_key", (fid,))]
    e["keys"] = [dict(r) for r in conn.execute(
        "select kind, key from entity_key where fuzer_id = %s and kind <> 'tag_noarea' order by kind, key", (fid,))]
    return e


def _golden(conn, fid: str) -> dict:
    return {r["field"]: r["value"] for r in conn.execute("select field, value from golden where fuzer_id = %s", (fid,))}


def _pins(conn, batch: str, pairs: list[tuple[str, str]], rule: str, by: str, reason: str | None) -> None:
    for a, b in pairs:
        left, right = sorted((a, b))
        conn.execute("delete from pinned_link where (left_ref, right_ref) in ((%s, %s), (%s, %s))",
                     (left, right, right, left))
        conn.execute("insert into pinned_link (left_ref, right_ref, rule, decided_by, batch, reason) "
                     "values (%s, %s, %s, %s, %s, %s)", (left, right, rule, by, batch, reason))


def preview_merge(conn, a: str, b: str) -> dict:
    """What "a is the same as b" would do: the two, their records and keys, and where their values disagree."""
    if a == b:
        raise CatalogError("pick a different entity to merge with")
    ea, eb = _entity(conn, a), _entity(conn, b)
    if ea["kind"] != eb["kind"]:
        raise CatalogError(f"{ea['label'] or a} is a {ea['kind']} and {eb['label'] or b} is a {eb['kind']}; "
                           "only things of the same kind can be one")
    if not ea["records"] or not eb["records"]:
        raise CatalogError("both need a source record to pin the merge to")
    blocked = conn.execute(
        "select left_ref, right_ref from pinned_link where rule = 'cannot' and "
        "((left_ref = any(%(a)s) and right_ref = any(%(b)s)) or (left_ref = any(%(b)s) and right_ref = any(%(a)s)))",
        {"a": [r["ref"] for r in ea["records"]], "b": [r["ref"] for r in eb["records"]]}).fetchall()
    ga, gb = _golden(conn, a), _golden(conn, b)
    differ = [{"field": f, "a": ga[f], "b": gb[f]} for f in sorted(set(ga) & set(gb)) if ga[f] != gb[f]]
    keys = {(k["kind"], k["key"]) for k in ea["keys"] + eb["keys"]}
    return {"a": ea, "b": eb, "differ": differ, "keys": [{"kind": k, "key": v} for k, v in sorted(keys)],
            "records": len(ea["records"]) + len(eb["records"]),
            "replaces": [f"{p['left_ref']} is not {p['right_ref']}" for p in blocked],
            "note": "One entity after the next rebuild: it keeps the ID most of its records already had; the other ID "
                    "points at it. Where values disagree, the field rules pick, and the conflict goes to review."}


def merge(conn, a: str, b: str, by: str, reason: str | None = None) -> dict:
    p = preview_merge(conn, a, b)
    batch = f"merge-{uuid.uuid4().hex[:10]}"
    ra, rb = [r["ref"] for r in p["a"]["records"]], [r["ref"] for r in p["b"]["records"]]
    conn.execute("delete from pinned_link where rule = 'cannot' and ((left_ref = any(%(a)s) and right_ref = any(%(b)s))"
                 " or (left_ref = any(%(b)s) and right_ref = any(%(a)s)))", {"a": ra, "b": rb})
    _pins(conn, batch, [(ra[0], rb[0])], "must", by, reason)
    _audit(conn, by, "pin", {"name": batch}, "create", new={"merge": [a, b], "refs": [ra[0], rb[0]]}, reason=reason)
    return {"batch": batch, "decision": "merge", "a": a, "b": b, "preview": p}


def preview_split(conn, fid: str, ref: str) -> dict:
    """What "this record is not this entity" would do: the record, what stays, and the values it brings."""
    e = _entity(conn, fid)
    refs = [r["ref"] for r in e["records"]]
    if ref not in refs:
        raise CatalogError(f"{ref} is not one of {e['label'] or fid}'s records")
    if len(refs) < 2:
        raise CatalogError("this is the entity's only record; there is nothing to split it from")
    source, record_type, key = ref.split(":", 2)
    brings = [dict(r) for r in conn.execute(
        "select field, value from assertion where fuzer_id = %s and source = %s and source_key = %s order by field",
        (fid, source, key))]
    return {"entity": e, "ref": ref, "record": {"source": source, "record_type": record_type, "source_key": key},
            "stays": [r for r in refs if r != ref], "brings": brings,
            "note": "After the next rebuild the record is its own entity (a new ID), and Fuzer won't match it to "
                    "these records again unless the decision is undone."}


def split(conn, fid: str, ref: str, by: str, reason: str | None = None) -> dict:
    p = preview_split(conn, fid, ref)
    batch = f"split-{uuid.uuid4().hex[:10]}"
    _pins(conn, batch, [(ref, r) for r in p["stays"]], "cannot", by, reason)
    _audit(conn, by, "pin", {"name": batch}, "create", new={"split": fid, "ref": ref, "from": p["stays"]},
           reason=reason)
    return {"batch": batch, "decision": "split", "fuzer_id": fid, "ref": ref, "preview": p}


def undo(conn, batch: str, by: str, reason: str | None = None) -> dict:
    rows = conn.execute("delete from pinned_link where batch = %s returning *", (batch,)).fetchall()
    if not rows:
        raise CatalogError(f"no current decision {batch}")
    _audit(conn, by, "pin", {"name": batch}, "delete",
           old={"pins": [f"{r['left_ref']} {r['rule']} {r['right_ref']}" for r in rows]}, reason=reason)
    return {"undone": batch, "pins": len(rows)}
