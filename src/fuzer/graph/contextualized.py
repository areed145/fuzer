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
    unmatched    records that matched nothing, from a source whose records otherwise match (`EXPECTS_MATCH`)
    pinned       a steward already decided (not counted against the source: it is a decision, not a question)

Every automatic match is listed with its confidence and basis; "Not this record" on the entity page reverses one, and
the reversal is a pinned decision the next rebuild keeps (graph.curation).

    report(conn, source)     the summary and the automatic decisions
    TARGET                   the share needing a steward a new source should stay under (P2.1: 10%)
"""

TARGET = 0.10
EXPECTS_MATCH = 0.5  # a source most of whose records join entities other sources hold expects every record to
LISTED = 200


def _stats(conn, source: str) -> dict:
    row = conn.execute("select stats, built_at from source_match_stat where source = %s order by built_at desc "
                       "limit 1", (source,)).fetchone()
    return {**(row["stats"] or {}), "built_at": row["built_at"]} if row else {}


def report(conn, source: str) -> dict:
    """What the last rebuild decided about `source`'s records, and what it left for a steward."""
    rows = conn.execute(
        """
        select c.source, c.record_type, c.source_key, c.fuzer_id, c.method, c.confidence, e.kind, l.label,
               (select array_agg(distinct o.source order by o.source) from crosswalk o
                 where o.fuzer_id = c.fuzer_id and o.source <> c.source) as others
        from crosswalk c join entity e on e.fuzer_id = c.fuzer_id left join entity_label l on l.fuzer_id = c.fuzer_id
        where c.source = %s order by c.method, c.confidence, c.source_key
        """, (source,)).fetchall()
    stats = _stats(conn, source)
    n = len(rows)
    shared = sum(bool(r["others"]) for r in rows)
    expects = bool(n) and shared / n >= EXPECTS_MATCH
    decided = {"key": 0, "score": 0, "pinned": 0, "new": 0}
    unmatched = []
    for r in rows:
        if r["method"] == "pinned":
            decided["pinned"] += 1
        elif r["others"] and r["method"] in ("key", "score"):
            decided[r["method"]] += 1
        else:
            decided["new"] += 1
            if expects and not r["others"]:
                unmatched.append(r)
    reviews = stats.get("open_reviews") or {}
    coverage = stats.get("coverage") or {}
    unresolved = sum(v.get("rows", 0) for v in (coverage.get("unresolved") or {}).values())
    steward = {"to_confirm": reviews.get("match", 0), "disagree": reviews.get("conflict", 0),
               "unresolved": unresolved, "unmatched": len(unmatched)}
    need = min(n, sum(steward.values())) if n else 0
    auto = [r for r in rows if r["others"] and r["method"] in ("key", "score")]
    return {
        "source": source, "records": n, "built_at": stats.get("built_at"), "decided": decided,
        "expects_match": expects, "links": {k: coverage.get(k) for k in ("pct", "resolved", "total")},
        "steward": steward, "needs_steward": need, "share": round(need / n, 4) if n else None, "target": TARGET,
        "automatic": [{"ref": f"{r['source']}:{r['record_type']}:{r['source_key']}", "source_key": r["source_key"],
                       "fuzer_id": r["fuzer_id"], "label": r["label"] or r["fuzer_id"], "kind": r["kind"],
                       "method": r["method"], "confidence": round(float(r["confidence"]), 4),
                       "basis": "same key as the register" if r["method"] == "key" else "matched on its values",
                       "with": r["others"]} for r in auto[:LISTED]],
        "unmatched": [{"source_key": r["source_key"], "fuzer_id": r["fuzer_id"], "label": r["label"] or r["source_key"],
                       "kind": r["kind"]} for r in unmatched[:LISTED]],
    }
