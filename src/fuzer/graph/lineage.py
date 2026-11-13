"""Where an entity's values came from (#875), in one place for whoever doubts a make, a model or a tag: the systems
whose records make it up and how each record was matched to it, which values each system supplies and the rule that
picked them, the reviewer choices and steward fixes on it, the merges and splits that formed it, and the work and
write-backs that touched it. Read only from what the register already keeps (crosswalk, golden, review_item,
steward_fix, pinned_link, id_event, work_item, writeback); nothing here is inferred."""

from collections import defaultdict

METHOD_SAYS = {"key": "same key", "pinned": "a steward's match", "must": "a steward's match"}


def _ref(r) -> str:
    return f"{r['source']}:{r['record_type']}:{r['source_key']}"


def lineage(conn, fuzer_id: str, work_limit: int = 10) -> dict | None:
    """The lineage of one entity, or None when there is no such entity."""
    ent = conn.execute("select fuzer_id, kind, merged_into from entity where fuzer_id = %s", (fuzer_id,)).fetchone()
    if ent is None:
        return None
    records = conn.execute(
        "select source, record_type, source_key, method, confidence, updated_at from crosswalk where fuzer_id = %s "
        "order by source, record_type, source_key", (fuzer_id,)).fetchall()
    golden = conn.execute("select field, value, source, rule from golden where fuzer_id = %s order by field",
                          (fuzer_id,)).fetchall()
    chosen: dict[str, list[str]] = defaultdict(list)
    for g in golden:
        chosen[g["source"]].append(g["field"])
    systems: dict[str, dict] = {}
    for r in records:
        s = systems.setdefault(r["source"], {"source": r["source"], "records": [], "chosen_fields": []})
        s["records"].append({"record": _ref(r), "record_type": r["record_type"], "key": r["source_key"],
                             "matched_by": METHOD_SAYS.get(r["method"], r["method"].replace("_", " ")),
                             "confidence": round(float(r["confidence"]), 2)})
    for src, fields in chosen.items():
        systems.setdefault(src, {"source": src, "records": [], "chosen_fields": []})["chosen_fields"] = fields
    rules = defaultdict(int)
    for g in golden:
        rules[g["rule"]] += 1

    decided = {r["subject"].split(":", 1)[1]: r for r in conn.execute(
        "select subject, decision, decided_by, decided_at from review_item where kind = 'conflict' "
        "and status = 'decided' and subject like %s", (f"{fuzer_id}:%",))}
    reviewed = [{"field": g["field"], "value": g["value"],
                 "by": (decided.get(g["field"]) or {}).get("decided_by"),
                 "at": (decided.get(g["field"]) or {}).get("decided_at")}
                for g in golden if g["rule"] == "review"]
    fixes = [dict(r) for r in conn.execute(
        "select field, old_value, new_value, created_by as by, created_at as at, reason from steward_fix "
        "where fuzer_id = %s and undone_at is null order by created_at desc, id desc limit 20", (fuzer_id,))]

    refs = [_ref(r) for r in records]
    pins = [dict(r) for r in conn.execute(
        "select left_ref, right_ref, rule, decided_by as by, decided_at as at, reason from pinned_link "
        "where left_ref = any(%s) or right_ref = any(%s) order by decided_at desc", (refs, refs))] if refs else []
    identity = [{"event": r["event"], "other": r["other"], "other_label": r["other_label"], "at": r["at"]}
                for r in conn.execute(
        """
        select e.event, case when e.event = 'merge' then e.fuzer_id else e.other_id end as other, l.label as other_label,
               e.at
        from id_event e
        left join entity_label l on l.fuzer_id = case when e.event = 'merge' then e.fuzer_id else e.other_id end
        where (e.event = 'merge' and e.other_id = %(f)s) or (e.event = 'split' and e.fuzer_id = %(f)s)
        order by e.at desc, e.id desc limit 20
        """, {"f": fuzer_id})]

    n_work = conn.execute("select count(*) as n from work_item where %s = any(subjects)", (fuzer_id,)).fetchone()["n"]
    work = [dict(r) for r in conn.execute(
        "select id, type, title, state, created_at, closed_at from work_item where %s = any(subjects) "
        "order by created_at desc, id desc limit %s", (fuzer_id, work_limit))]
    writebacks = [dict(r) for r in conn.execute(
        """
        select w.id, w.system, w.kind, w.record, w.action, w.state, w.approved_by, w.approved_at, w.confirmed_at,
               w.item_id
        from writeback w join work_item i on i.id = w.item_id
        where %s = any(i.subjects) order by w.drafted_at desc, w.id desc limit 20
        """, (fuzer_id,))]

    return {"fuzer_id": fuzer_id, "kind": ent["kind"], "merged_into": ent["merged_into"],
            "systems": sorted(systems.values(), key=lambda s: (-len(s["chosen_fields"]), s["source"])),
            "rules": dict(rules), "reviewed": reviewed, "fixes": fixes, "pins": pins, "identity": identity,
            "work": {"count": n_work, "recent": work}, "writebacks": writebacks,
            "note": "Each value's own records, mapping versions and loads are under why? on Values."}
