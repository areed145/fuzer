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
the reversal is a pinned decision the next rebuild keeps (graph.curation).

    report(conn, source)     the summary and the automatic decisions
    TARGET                   the share needing a steward a new source should stay under (P2.1: 10%)
"""

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
               exists (select 1 from asked where r = m.source || ':' || m.record_type || ':' || m.source_key) as asked
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
            if r["kind"] in expects and not r["others"] and not r["asked"]:
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
        "expects_match": sorted(expects), "links": {k: coverage.get(k) for k in ("pct", "resolved", "total")},
        "steward": steward, "needs_steward": need, "share": round(need / n, 4) if n else None, "target": TARGET,
        "automatic": [{"ref": f"{r['source']}:{r['record_type']}:{r['source_key']}", "source_key": r["source_key"],
                       "fuzer_id": r["fuzer_id"], "label": r["label"] or r["fuzer_id"], "kind": r["kind"],
                       "method": r["method"], "confidence": round(float(r["confidence"]), 4),
                       "basis": "same key as the register" if r["method"] == "key" else "matched on its values",
                       "with": r["others"]} for r in auto[:LISTED]],
        "unmatched": [{"source_key": r["source_key"], "fuzer_id": r["fuzer_id"], "label": r["label"] or r["source_key"],
                       "kind": r["kind"]} for r in unmatched[:LISTED]],
    }
