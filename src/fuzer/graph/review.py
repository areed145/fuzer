"""The review queue's answers (P2.3): one domain function the API, a case and a group answer all use.

    decide(conn, item_id, decision, value, by)   apply one answer: pin a match, choose a value, accept a mapping...

An answer is kept where the next rebuild reads it (a pinned link, a review assertion, a mapping version), so it
survives rebuilds; the question is marked decided, and its case in the steward's queue closes (graph.data_work).
"""

import re

from psycopg.types.json import Jsonb

DECISIONS = ("accept", "reject", "choose", "field")
FIELD_KINDS = ("conflict", "pid_mismatch")  # questions "check it in the field" can answer (P2.4)


class ReviewError(ValueError):
    """An answer the question can't take."""


def decide(conn, item_id: int, decision: str, value, by: str, extra: dict | None = None, sync: bool = True) -> dict:
    """Answer one open question. Returns {decided, kind, rebuild}: `rebuild` when the answer lands with a rebuild
    (a merge or split, an accepted curve or drawing line). The caller commits."""
    if decision not in DECISIONS:
        raise ReviewError(f"a decision is one of {', '.join(DECISIONS)}")
    item = conn.execute("select * from review_item where id = %s", (item_id,)).fetchone()
    if not item:
        raise LookupError("unknown review item")
    p = item["payload"]
    record = {"decision": decision, "value": value, "by": by, **(extra or {})}
    if decision == "field":  # neither record can be trusted: the plant itself is looked at (P2.4, ops.corrections)
        if item["kind"] not in FIELD_KINDS:
            raise ReviewError("only conflicting values and drawing disagreements can be sent for a field check")
    elif item["kind"] in FIELD_KINDS:
        record.update(_wrong(item["kind"], p, decision, value))

    if item["kind"] == "mapping":
        if decision != "reject":
            from ..sources import suggest

            try:  # a new version; the next ingest loads the table and rebuilds use it
                suggest.save(conn, p["dataset"], p["yaml"] if decision == "accept" else value, by)
            except ValueError as e:
                raise ReviewError(str(e)) from e
    elif item["kind"] == "schema":
        if decision == "choose":
            raise ReviewError("schema items take accept (load with the new columns) or reject (keep holding)")
        if decision == "accept":
            # The new columns become the expected ones; the next ingest loads the dataset.
            conn.execute("update source_dataset set schema = %s where name = %s", (Jsonb(p["new"]), p["dataset"]))
    elif item["kind"] == "curve":  # a datasheet Claude read: accept as read, or choose with corrections
        if decision != "reject":
            from ..sources import llm_curves
            if decision == "choose" and not isinstance(value, dict):
                raise ReviewError("choose needs the corrected reading")
            llm_curves.accept(conn, item, by, value if decision == "choose" else None)
    elif item["kind"] == "check":  # a held load (#225): fixed at the source, then loaded again from its dataset
        if decision == "choose":
            raise ReviewError("held-load items take accept (seen) or reject")
    elif item["kind"] == "pid_connection":  # an off-page line one drawing shows: accept links it, reject leaves it
        if decision == "choose":
            raise ReviewError("drawing connections take accept (link them) or reject")
    elif item["kind"] == "pid_mismatch":  # a drawing and the DCS, AF or SAP disagree (#475): which needs changing
        if decision == "field":
            pass
        elif decision == "accept" or (decision == "choose" and value not in ("drawing", "system")):
            raise ReviewError("drawing disagreements take choose with drawing (the system needs changing) or "
                              "system (the drawing needs a redline), or reject (not a real disagreement)")
    elif item["kind"] == "role":  # an uncertain measurement role (P1.4b): confirm it, or choose another from the list
        from . import roles

        role = p.get("proposed") if decision == "accept" else value
        if decision not in ("accept", "choose") or not role:
            raise ReviewError("measurement roles take accept (the proposed role) or choose with a role")
        try:
            roles.confirm(conn, item["subject"], role, by)
        except ValueError as e:
            raise ReviewError(str(e)) from e
    elif item["kind"] == "match":
        if decision == "choose":
            raise ReviewError("match items take accept or reject")
        rule = "must" if decision == "accept" else "cannot"
        conn.execute(
            """
            insert into pinned_link (left_ref, right_ref, rule, decided_by) values (%s, %s, %s, %s)
            on conflict (left_ref, right_ref) do update set rule = excluded.rule,
                decided_by = excluded.decided_by, decided_at = now()
            """,
            (p["left"]["ref"], p["right"]["ref"], rule, by),
        )
    elif decision == "field":
        pass  # nothing changes until the field check comes back
    else:
        if decision == "reject":
            raise ReviewError("conflict items take accept (keep chosen) or choose (with value)")
        value = p["chosen"] if decision == "accept" else value
        if value is None:
            raise ReviewError("choose needs a value")
        conn.execute("delete from assertion where fuzer_id = %s and field = %s and source = 'review'",
                     (p["fuzer_id"], p["field"]))
        conn.execute(
            "insert into assertion (fuzer_id, field, value, source, source_key) values (%s, %s, %s, 'review', %s)",
            (p["fuzer_id"], p["field"], Jsonb(value), by),
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
        "update review_item set status = 'decided', decision = %s, decided_by = %s, decided_at = now() where id = %s",
        (Jsonb(record), by, item_id),
    )
    if sync:
        from . import data_work

        data_work.sync(conn, by)  # the answer closes its case in the steward's queue
    rebuild = item["kind"] == "match" or (item["kind"] in ("curve", "pid_connection") and decision != "reject")
    return {"decided": item_id, "kind": item["kind"], "rebuild": rebuild}


def _wrong(kind: str, p: dict, decision: str, value) -> dict:
    """Which source the answer says is wrong, kept on the decision so its correction is drafted for that source's
    owner (P2.4, ops.corrections). A value written the same way but for case and punctuation is not wrong."""
    if kind == "conflict" and decision in ("accept", "choose"):
        right = p.get("chosen") if decision == "accept" else value
        wrong = sorted({v["source"] for v in p.get("values") or [] if v.get("source") not in (None, "review")
                        and right is not None and _spelling(v.get("value")) != _spelling(right)})
        return {"wrong": wrong, "right": right} if wrong else {}
    if kind == "pid_mismatch" and decision == "choose":
        if value == "system":
            return {"wrong": ["drawing"]}
        if p.get("check") == "placement":
            return {"wrong": sorted({s for x in p.get("register") or [] for s in x.get("sources") or []})}
        return {"wrong": ["alarm configuration"]}
    return {}


# ---- like questions answered once (P2.3c) ---------------------------------------------------------------------
#
# Questions are like each other when the same answer is right for all of them for the same reason: conflicts on one
# field between the same systems that differ only in how the value is written; possible matches between the same two
# registers on the same evidence; drawing disagreements of one check. One answer decides each of them (each pinned
# where the next rebuild reads it, as a single answer is), and a standing answer also decides new ones like them as
# they arrive, at each rebuild, by the person who set it. A standing answer can be withdrawn; what it decided stays.

def _spelling(v) -> str:
    return re.sub(r"[\W_]+", "", str(v).lower())


def _source(ref: str | None) -> str:
    return (ref or "").partition(":")[0]


def group_of(kind: str, p: dict) -> tuple[str, str] | None:
    """The group a question belongs to, (key, what its members share in words), or None when it stands alone."""
    if kind == "conflict":
        values = p.get("values") or []
        sources = sorted({v.get("source") for v in values if v.get("source")})
        if len(sources) < 2:
            return None
        spelling = len({_spelling(v.get("value")) for v in values}) == 1
        field = p.get("field", "a field")
        return (f"conflict|{field}|{'+'.join(sources)}|{'spelling' if spelling else 'values'}",
                f"{field}: {' and '.join(sources)} " + ("write the same value differently" if spelling
                                                         else "give different values"))
    if kind == "match":
        a, b = sorted((_source(p.get("left", {}).get("ref")), _source(p.get("right", {}).get("ref"))))
        why = p.get("why") or ""
        basis = ("the maker is unknown on one side" if "manufacturer unknown" in why
                 else "more than one item fits" if "more than one possible match" in why else
                 f"scoring {float(p.get('score') or 0):.1f}")
        return f"match|{a}+{b}|{basis}", f"{a} and {b} records at the same tag where {basis}"
    if kind == "pid_mismatch":
        check = p.get("check") or "check"
        what = {"placement": "placement", "twin": "what their DEXPI file shows"}.get(check, check)
        return f"pid_mismatch|{check}", f"drawings that disagree on {what}"
    if kind == "pid_connection":
        return "pid_connection|all", "lines read from drawings"
    return None


def _options(kind: str, members: list[dict]) -> list[dict]:
    if kind == "conflict":
        sources = sorted({v.get("source") for m in members for v in m["payload"].get("values") or [] if v.get("source")})
        return [{"decision": "choose", "source": s, "label": f"Use {s}'s value"} for s in sources] + [
            {"decision": "accept", "label": "Keep the value in use"}]
    if kind == "match":
        return [{"decision": "accept", "label": "The same item, each pair"},
                {"decision": "reject", "label": "Keep each pair apart"}]
    if kind == "pid_mismatch":
        return [{"decision": "choose", "value": "drawing", "label": "The systems need changing"},
                {"decision": "choose", "value": "system", "label": "The drawings need redlines"},
                {"decision": "reject", "label": "Not real disagreements"}]
    return [{"decision": "accept", "label": "Accept each"}, {"decision": "reject", "label": "Reject each"}]


def groups(conn, kind: str | None = None, least: int = 2) -> list[dict]:
    """Open questions that share an answer, largest first: {kind, key, label, count, items, options, standing}."""
    out: dict[str, dict] = {}
    for r in conn.execute("select id, kind, payload from review_item where status = 'open' and (%s::text is null "
                          "or kind = %s) order by id", (kind, kind)):
        g = group_of(r["kind"], r["payload"] or {})
        if g:
            entry = out.setdefault(g[0], {"kind": r["kind"], "key": g[0], "label": g[1], "members": []})
            entry["members"].append(dict(r))
    standing = {r["group_key"]: r for r in conn.execute("select * from review_rule where withdrawn_at is null")}
    rows = []
    for g in out.values():
        if len(g["members"]) >= least:
            rule = standing.get(g["key"])
            rows.append({"kind": g["kind"], "key": g["key"], "label": g["label"], "count": len(g["members"]),
                         "items": [m["id"] for m in g["members"]], "options": _options(g["kind"], g["members"]),
                         "standing": {k: rule[k] for k in ("decision", "source", "value", "set_by")} if rule else None})
    return sorted(rows, key=lambda g: (-g["count"], g["key"]))


def _value_from(p: dict, source: str):
    for v in p.get("values") or []:
        if v.get("source") == source:
            return v.get("value")
    return None


def decide_group(conn, key: str, decision: str, by: str, *, source: str | None = None, value=None,
                 standing: bool = False) -> dict:
    """Answer every open question in a group the same way; with `standing`, new ones like them too. Returns
    {decided, skipped, rebuild, standing}. The caller commits."""
    kind = key.partition("|")[0]
    if kind == "conflict" and decision == "choose" and not source:
        raise ReviewError("a group of conflicts is answered by naming the system whose value is right")
    members = [r for r in conn.execute("select id, kind, payload from review_item where status = 'open' and kind = %s "
                                       "order by id", (kind,)) if (group_of(r["kind"], r["payload"] or {}) or ("",))[0] == key]
    if not members and not standing:
        raise LookupError(f"no open questions in {key}")
    decided, skipped, rebuild = [], [], False
    for m in members:
        v = _value_from(m["payload"], source) if kind == "conflict" and source else value
        if kind == "conflict" and source and v is None:
            skipped.append({"id": m["id"], "why": f"{source} gives no value here"})
            continue
        try:
            with conn.transaction():
                out = decide(conn, m["id"], decision, v, by, extra={"group": key, **({"source": source} if source else {})},
                             sync=False)
        except ReviewError as e:
            skipped.append({"id": m["id"], "why": str(e)})
            continue
        decided.append(m["id"])
        rebuild |= out["rebuild"]
    if decided:
        from . import data_work

        data_work.sync(conn, by)  # their cases close in the steward's queue
    if standing:
        conn.execute("update review_rule set withdrawn_at = now(), withdrawn_by = %s where group_key = %s "
                     "and withdrawn_at is null", (by, key))
        conn.execute("insert into review_rule (group_key, kind, decision, source, value, set_by) values "
                     "(%s, %s, %s, %s, %s, %s)", (key, kind, decision, source, Jsonb(value), by))
    return {"decided": decided, "skipped": skipped, "rebuild": rebuild, "standing": standing}


def withdraw(conn, key: str, by: str) -> bool:
    """Stop a standing answer deciding new questions; what it decided stays decided."""
    return conn.execute("update review_rule set withdrawn_at = now(), withdrawn_by = %s where group_key = %s "
                        "and withdrawn_at is null", (by, key)).rowcount > 0


def apply_standing(conn) -> dict:
    """Decide the open questions a standing answer covers, as the person who set it. Run before the steward's queue
    is synced, at each rebuild. Returns {group key: questions decided}."""
    out = {}
    for rule in conn.execute("select * from review_rule where withdrawn_at is null order by id").fetchall():
        res = decide_group(conn, rule["group_key"], rule["decision"], rule["set_by"], source=rule["source"],
                           value=rule["value"]) if any(
            (group_of(r["kind"], r["payload"] or {}) or ("",))[0] == rule["group_key"]
            for r in conn.execute("select kind, payload from review_item where status = 'open' and kind = %s",
                                  (rule["kind"],))) else {"decided": []}
        if res["decided"]:
            out[rule["group_key"]] = len(res["decided"])
    return out
