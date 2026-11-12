"""How a source contextualized itself (P2.1): what the last rebuild decided about each of its records on its own, what
it left for a steward, and the share that is.

Every record a source loads is matched to the register (graph.resolve) and its link values are resolved to what they
name (graph.edges). The automatic decisions are:

    key        the record carries a canonical key (a tag, a well) the register already has: certain
    score      the matcher scored it against a record of another system above the auto-accept threshold, and the
               human gate let it through (its calibrated confidence)
    new        the record matched nothing and is a new entity, which is right for a source of new things (a schedule,
               an inspection) and suspicious for one that otherwise describes what the register has

and the steward's share is what the build could not decide:

    to confirm   a match the matcher was unsure of, or the gate stopped (open `match` questions naming the source)
    disagree     values it gives that differ from another source's (open `conflict` questions)
    unresolved   rows whose link values named nothing in the register
    unmatched    records that matched nothing, of a kind whose records from the source otherwise match
                 (`EXPECTS_MATCH`), and that are not already a match question; never the register of that kind
                 (the source holding most of them), whose own items need no other source
    pinned       a steward already decided (not counted against the source: it is a decision, not a question)

Every automatic match is listed with its confidence and basis; "Not this record" on the entity page reverses one, and
the reversal is a pinned decision the next rebuild keeps (graph.curation). Every link the source states is listed the
same way, with how it was resolved; "Not this link" leaves the source's statement of it out of every later build
(`unlinked`), while another source stating the same link keeps it.

    report(conn, source)     the summary and the automatic decisions
    sync(conn)               records that matched nothing, one `unmatched_records` case per source
    accept_new(conn, source, by, refs=None)   one answer for the group: they are new items (remembered)
    unlink(conn, source, src, rel, dst, by)   "Not this link": the source's statement of it is left out (remembered)
    TARGET                   the share needing a steward a new source should stay under (P2.1: 10%)
"""

from ..core.exceptions import clear_exception, exception_type, open_keys, raise_exception
from ..core.findings import EXTERNAL, Finding, fact, page

TARGET = 0.10
EXPECTS_MATCH = 0.5  # a kind of record most of which join entities other sources hold
LISTED = 200


def _stats(conn, source: str) -> dict:
    row = conn.execute("select stats, built_at from source_match_stat where source = %s order by built_at desc "
                       "limit 1", (source,)).fetchone()
    return {**(row["stats"] or {}), "built_at": row["built_at"]} if row else {}


def _registers(conn, source: str) -> set[str]:
    """The kinds `source` is the register of: it holds more of them than any other source, so its records are what
    the others match to, and one of its own that nobody else has is simply an item only it knows."""
    return {r["kind"] for r in conn.execute(
        """
        select distinct on (e.kind) e.kind, c.source from crosswalk c join entity e on e.fuzer_id = c.fuzer_id
        group by e.kind, c.source order by e.kind, count(*) desc, c.source
        """) if r["source"] == source}


def report(conn, source: str) -> dict:
    """What the last rebuild decided about `source`'s records, and what it left for a steward."""
    rows = conn.execute(
        """
        with mine as (select * from crosswalk where source = %(s)s),
             other as (select o.fuzer_id, array_agg(distinct o.source order by o.source) as sources from crosswalk o
                       where o.source <> %(s)s and o.fuzer_id in (select fuzer_id from mine) group by o.fuzer_id),
             asked as (select distinct r from review_item i, unnest(string_to_array(i.subject, '|')) r
                       where i.kind = 'match' and i.status = 'open' and r like %(p)s)
        select m.source, m.record_type, m.source_key, m.fuzer_id, m.method, m.confidence, e.kind, l.label,
               o.sources as others,
               exists (select 1 from asked where r = m.source || ':' || m.record_type || ':' || m.source_key) as asked,
               exists (select 1 from source_new_record n where n.source = m.source and n.record_type = m.record_type
                       and n.source_key = m.source_key) as accepted
        from mine m join entity e on e.fuzer_id = m.fuzer_id left join entity_label l on l.fuzer_id = m.fuzer_id
        left join other o on o.fuzer_id = m.fuzer_id
        order by m.method, m.confidence, m.source_key
        """, {"s": source, "p": f"{source}:%"}).fetchall()
    stats = _stats(conn, source)
    n = len(rows)
    # A kind most of whose records join what other sources hold expects each of its records to: one that matched
    # nothing (and isn't already a question) is the steward's. A kind of new things (a schedule's activities) doesn't.
    per_kind: dict[str, list[int]] = {}
    for r in rows:
        k = per_kind.setdefault(r["kind"], [0, 0])
        k[0] += bool(r["others"])
        k[1] += 1
    expects = {k for k, (shared, total) in per_kind.items() if shared / total >= EXPECTS_MATCH} - _registers(conn, source)
    decided = {"key": 0, "score": 0, "pinned": 0, "new": 0}
    unmatched = []
    for r in rows:
        if r["method"] == "pinned":
            decided["pinned"] += 1
        elif r["others"] and r["method"] in ("key", "score"):
            decided[r["method"]] += 1
        else:
            decided["new"] += 1
            if r["kind"] in expects and not r["others"] and not r["asked"] and not r["accepted"]:
                unmatched.append(r)
    reviews = stats.get("open_reviews") or {}
    coverage = stats.get("coverage") or {}
    unresolved = sum(v.get("rows", 0) for v in (coverage.get("unresolved") or {}).values())
    steward = {"to_confirm": reviews.get("match", 0), "disagree": reviews.get("conflict", 0),
               "unresolved": unresolved, "unmatched": len(unmatched)}
    need = min(n, sum(steward.values())) if n else 0
    auto = [r for r in rows if r["others"] and r["method"] in ("key", "score")]
    links = conn.execute(
        """
        select e.src, e.rel, e.dst, e.confidence, coalesce(ls.label, e.src) as src_label,
               coalesce(ld.label, e.dst) as dst_label, d.kind as dst_kind, cardinality(e.sources) > 1 as shared
        from edge e join entity d on d.fuzer_id = e.dst
        left join entity_label ls on ls.fuzer_id = e.src left join entity_label ld on ld.fuzer_id = e.dst
        where %s = any(e.sources) and e.valid_to is null
        order by e.confidence, e.rel, 5 limit %s
        """, (source, LISTED)).fetchall()
    undone = [dict(r) for r in conn.execute(
        "select u.src, u.rel, u.dst, coalesce(ls.label, u.src) as src_label, coalesce(ld.label, u.dst) as dst_label, "
        "u.decided_by, u.decided_at from unlinked u left join entity_label ls on ls.fuzer_id = u.src "
        "left join entity_label ld on ld.fuzer_id = u.dst where u.source = %s order by u.decided_at desc", (source,))]
    return {
        "source": source, "records": n, "built_at": stats.get("built_at"), "decided": decided,
        "expects_match": sorted(expects), "links": {
            **{k: coverage.get(k) for k in ("pct", "resolved", "total")}, "by_rel": coverage.get("by_rel") or {},
            "listed": [{"src": r["src"], "rel": r["rel"], "dst": r["dst"], "src_label": r["src_label"],
                        "dst_label": r["dst_label"], "dst_kind": r["dst_kind"],
                        "confidence": round(float(r["confidence"]), 4), "with_others": r["shared"],
                        "basis": "named by its key" if r["confidence"] >= 1 else "read from text"} for r in links],
            "unlinked": undone},
        "steward": steward, "needs_steward": need, "share": round(need / n, 4) if n else None, "target": TARGET,
        "automatic": [{"ref": f"{r['source']}:{r['record_type']}:{r['source_key']}", "source_key": r["source_key"],
                       "fuzer_id": r["fuzer_id"], "label": r["label"] or r["fuzer_id"], "kind": r["kind"],
                       "method": r["method"], "confidence": round(float(r["confidence"]), 4),
                       "basis": "same key as the register" if r["method"] == "key" else "matched on its values",
                       "with": r["others"]} for r in auto[:LISTED]],
        "unmatched": [{"ref": f"{r['source']}:{r['record_type']}:{r['source_key']}", "source_key": r["source_key"], "fuzer_id": r["fuzer_id"], "label": r["label"] or r["source_key"],
                       "kind": r["kind"]} for r in unmatched[:LISTED]],
    }


# ---- what matched nothing, as one case per source (P2.1b) ---------------------------------------------------------

BY = "stewardship"
TYPE = exception_type("unmatched_records", "Records that match nothing", role="role:data_steward", joins=False,
                      description="A source's records describe items the register should already have, but these "
                                  "matched nothing; say they are new, or match each to the item it means.")


def title(r: dict) -> str:
    n = r["steward"]["unmatched"]
    return f"{n} {r['source'].upper()} {'record matches' if n == 1 else 'records match'} nothing in the register"


def finding(r: dict) -> Finding:
    shown = ", ".join(u["label"] for u in r["unmatched"][:10]) + (" and more" if len(r["unmatched"]) > 10 else "")
    return Finding("stewardship.unmatched_records", EXTERNAL, method=TYPE.description, items=(
        fact("Source", r["source"].upper()),
        fact("Records", r["steward"]["unmatched"]),
        fact("Matched nothing", shown),
        fact("Of its records", r["records"]),
        page("Answer", f"#/sources?view=systems&system={r['source']}", "See them on the system's card"),
    ), data={"source": r["source"], "refs": [u["ref"] for u in r["unmatched"]]})


def sync(conn, by: str = BY) -> dict:
    """One open case per source with records that matched nothing; cleared once none are left."""
    out = {"raised": 0, "refreshed": 0, "cleared": 0}
    live = set()
    for s in [x["source"] for x in conn.execute("select distinct source from crosswalk order by 1")]:
        r = report(conn, s)
        if not r["steward"]["unmatched"]:
            continue
        live.add(s)
        row = raise_exception(conn, TYPE.name, s, title(r), by, source=BY, finding=finding(r))
        out["raised" if row.get("raised") else "refreshed"] += 1
    for key in open_keys(conn, TYPE.name) - live:
        clear_exception(conn, TYPE.name, key, by, reason="each of its records matched, or was said to be new")
        out["cleared"] += 1
    return out


def accept_new(conn, source: str, by: str, refs: list[str] | None = None, reason: str | None = None) -> dict:
    """A steward's one answer for the group: these records (all the source's unmatched ones when `refs` is None) are
    new items. Remembered, so later rebuilds don't ask again; `undo_new` takes it back."""
    pending = [u["ref"] for u in report(conn, source)["unmatched"]]
    chosen = pending if refs is None else [x for x in refs if x in pending]
    for ref in chosen:
        src, record_type, key = ref.split(":", 2)
        conn.execute("insert into source_new_record (source, record_type, source_key, decided_by, reason) "
                     "values (%s, %s, %s, %s, %s) on conflict do nothing", (src, record_type, key, by, reason))
    return {"accepted": len(chosen), "sync": sync(conn, by)}


def undo_new(conn, source: str, by: str) -> dict:
    """Take back "these are new" for a source: its records count as unmatched again."""
    n = conn.execute("delete from source_new_record where source = %s", (source,)).rowcount
    return {"undone": n, "sync": sync(conn, by)}


# ---- "Not this link" (P2.1c) --------------------------------------------------------------------------------------

def unlink(conn, source: str, src: str, rel: str, dst: str, by: str, reason: str | None = None) -> dict:
    """A link the source states is wrong: the next rebuilds leave the source's statement out (remembered)."""
    from ..sources._catalog_common import CatalogError, _audit

    if not conn.execute("select 1 from edge where src = %s and rel = %s and dst = %s and %s = any(sources)",
                        (src, rel, dst, source)).fetchone():
        raise CatalogError(f"{source} states no such link")
    conn.execute("insert into unlinked (source, src, rel, dst, decided_by, reason) values (%s, %s, %s, %s, %s, %s) "
                 "on conflict do nothing", (source, src, rel, dst, by, reason))
    _audit(conn, by, "link", {"name": f"{source}: {src} {rel} {dst}"}, "unlink",
           new={"source": source, "src": src, "rel": rel, "dst": dst}, reason=reason)
    return {"unlinked": {"source": source, "src": src, "rel": rel, "dst": dst}}


def relink(conn, source: str, src: str, rel: str, dst: str, by: str) -> dict:
    """Take back "Not this link": the next rebuild states it again."""
    from ..sources._catalog_common import _audit

    n = conn.execute("delete from unlinked where source = %s and src = %s and rel = %s and dst = %s",
                     (source, src, rel, dst)).rowcount
    if n:
        _audit(conn, by, "link", {"name": f"{source}: {src} {rel} {dst}"}, "relink",
               old={"source": source, "src": src, "rel": rel, "dst": dst})
    return {"relinked": n}
