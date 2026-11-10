"""Claude proposes, the steward decides (#476). Questions are built here from the register, each with numbered
options Claude must pick from (it can say "none" but cannot invent an entity or a value):

    unresolved   which entity a value means: a link value a source wrote that named nothing, or a ref on an open
                 exception that matched nothing (accepting records an alias)
    match        whether two records the matcher was unsure of are one item (accepting pins it, as the review does)
    conflict     which of the sources' values is right (accepting sets it, as the review does)
    spelling     whether two near spellings in a field are one value, and which to keep (accepting sets it on the
                 entities that use the other, as a Gaps fix)

    questions(conn, kinds)            the questions as they stand
    propose(conn, client=None, ...)   ask Claude about the ones without a current answer; cached by question hash
    listing(conn, status)             proposals for the page
    accept(conn, id, by) / reject(conn, id, by, reason)

Each answer has a confidence, a reason and the evidence it relied on. Rebuilds never call the model. A bulk accept is
offered only for proposals at or above `stewardship.bulk_accept_at` (0, the default, offers none) so a project turns
it on after its evals show the proposals can be trusted at that level.
"""

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from psycopg.types.json import Jsonb
from rapidfuzz import fuzz, process, utils

from ..core import llm, settings
from ..core.llm import call, usage
from ..sources import aliases
from ..sources._catalog_common import CatalogError
from . import gaps
from .quality import LIVE_E

PROMPT_VERSION = "2026-11-10.1"
KINDS = ("unresolved", "match", "conflict", "spelling")
MAX_OPTIONS = 8
PER_REQUEST = 15

SYSTEM_PROMPT = """\
You help a master data steward at an oil and gas asset keep the asset register matching what its systems write
(SAP, Maximo, the historian, operator logs, P&IDs). Each question comes with context and numbered options. Pick
the option the evidence supports, or "none" when no option is clearly right: a wrong pick changes the register,
so prefer "none" to a guess.

Kinds of question:
- unresolved: a value some system wrote names nothing in the register. Options are register entities (key, kind,
  description). Pick the one the value means: a typo, a different separator or padding, a legacy tag style, a
  shorthand. Tags are area-function-loop-suffix (20-P-1001B is pump B, loop 1001, area 20); A/B suffixes are
  different units, so a value naming the B pump is not the A pump.
- match: two records from different systems at the same tag; the matcher scored them in between. Options "same"
  and "different". Different serial numbers always mean different items; a manufacturer spelled differently does
  not.
- conflict: sources give different values for one field of one entity. Options are the values with their source.
  Pick the right one (a fuller, correctly spelled, more specific value over an abbreviation or a typo); "none" when
  they genuinely disagree.
- spelling: two values in one field that look alike. Options keep one of them, or say they are different values.
  Two manufacturers or models with similar names are different values.

For each question return its id, your choice (an option id, or "none"), a confidence from 0 to 1 that the choice is
right, a one-sentence reason a steward can check, and the evidence: the exact parts of the context you relied on.
"""

SCHEMA = {
    "type": "object",
    "properties": {"answers": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "choice": {"type": "string"},
                       "confidence": {"type": "number"}, "reason": {"type": "string"},
                       "evidence": {"type": "array", "items": {"type": "string"}}},
        "required": ["id", "choice", "confidence", "reason", "evidence"], "additionalProperties": False}}},
    "required": ["answers"], "additionalProperties": False,
}


def _hash(q: dict) -> str:
    return hashlib.sha256(json.dumps([q["context"], q["options"]], sort_keys=True, default=str).encode()).hexdigest()[:16]


def _compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


class _Register:
    """Entity keys with their descriptions, searched by how alike a value is (typos, separators, padding)."""

    def __init__(self, conn):
        rows = conn.execute(
            f"""
            select k.kind, k.key, k.fuzer_id, l.label, g.value #>> '{{}}' as description
            from entity_key k join entity e using (fuzer_id) left join entity_label l using (fuzer_id)
            left join golden g on g.fuzer_id = k.fuzer_id and g.field = 'description'
            where {LIVE_E} and k.kind in ('tag', 'well', 'equipment', 'floc', 'area', 'facility', 'point')
            """).fetchall()
        self.rows = [dict(r) for r in rows]
        self.keys = [_compact(r["key"]) for r in self.rows]

    def near(self, value: str, n: int = MAX_OPTIONS) -> list[dict]:
        bare = _compact(aliases.bare(value))
        if not bare or not self.rows:
            return []
        hits = process.extract(bare, self.keys, scorer=fuzz.ratio, limit=n * 3, score_cutoff=55)
        out, seen = [], set()
        for _, score, i in hits:
            r = self.rows[i]
            if r["fuzer_id"] in seen:
                continue
            seen.add(r["fuzer_id"])
            out.append({**r, "similarity": round(score / 100, 2)})
        return out[:n]


def _unresolved(conn) -> list[dict]:
    have = {r["norm"] for r in conn.execute("select norm from entity_alias where retired_at is null")}
    values: dict[str, dict] = {}
    for r in conn.execute(
            """
            select distinct on (source) source, stats->'coverage'->'unresolved' as unresolved from source_match_stat
            order by source, built_at desc
            """):
        for rel, v in (r["unresolved"] or {}).items():
            for x in v.get("values") or []:
                e = values.setdefault(str(x["value"]), {"value": str(x["value"]), "seen": [], "exceptions": []})
                e["seen"].append({"source": r["source"], "link": rel, "rows": x["rows"]})
    if conn.execute("select to_regclass('exception_record') is not null as t").fetchone()["t"]:
        for r in conn.execute(
                """
                select u.ref, array_agg(e.id order by e.id) as ids, array_agg(distinct e.source) as sources,
                       min(e.title) as title
                from exception_record e, unnest(e.unmatched) u(ref)
                where e.state = 'open' and e.joined_to is null group by u.ref
                """):
            e = values.setdefault(r["ref"], {"value": r["ref"], "seen": [], "exceptions": []})
            e["exceptions"] = list(r["ids"])
            e["seen"].append({"source": ", ".join(r["sources"]), "link": "exception ref", "rows": len(r["ids"]),
                              "example": r["title"]})
    reg = _Register(conn) if values else None
    out = []
    for v in values.values():
        if aliases._norm(aliases.bare(v["value"])) in have:
            continue
        near = reg.near(v["value"])
        if not near:
            continue
        out.append({"kind": "unresolved", "subject": v["value"], "context": {"value": v["value"], "seen": v["seen"]},
                    "options": [{"id": f"e{i + 1}", "fuzer_id": n["fuzer_id"],
                                 "text": f"{n['kind']} {n['key']}" + (f": {n['description']}" if n["description"] else ""),
                                 "similarity": n["similarity"]} for i, n in enumerate(near)],
                    "exceptions": v["exceptions"]})
    return out


def _matches(conn) -> list[dict]:
    out = []
    for r in conn.execute("select id, subject, payload from review_item where kind = 'match' and status = 'open' "
                          "order by id"):
        p = r["payload"]
        side = lambda s: {"source": s["source"], "key": s["key"], "tag": s["block"], "fields": s["fields"]}
        out.append({"kind": "match", "subject": r["subject"], "review_id": r["id"],
                    "context": {"left": side(p["left"]), "right": side(p["right"]), "score": p.get("score"),
                                "matcher": p.get("why")},
                    "options": [{"id": "same", "text": "the same item"}, {"id": "different", "text": "different items"}]})
    return out


def _conflicts(conn) -> list[dict]:
    out = []
    for c in gaps.conflicts(conn, limit=500):
        opts, seen = [], set()
        for v in c["values"]:
            key = json.dumps(v["value"], sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            opts.append({"id": f"v{len(opts) + 1}", "value": v["value"],
                         "text": f"{v['value']} (from {v['source']})"})
        out.append({"kind": "conflict", "subject": f"{c['fuzer_id']}:{c['field']}", "review_id": c["id"],
                    "fuzer_id": c["fuzer_id"], "field": c["field"],
                    "context": {"entity": c["label"] or c["fuzer_id"], "field": c["field"],
                                "values": [{"source": v["source"], "value": v["value"]} for v in c["values"]]},
                    "options": opts})
    return out


def _spellings(conn, min_ratio: int = 85) -> list[dict]:
    """Near spellings the Gaps rules don't group (a dropped or swapped letter), per vocabulary field."""
    by_field: dict[str, dict[str, list[str]]] = {}
    for r in conn.execute(
            f"select g.field, g.value #>> '{{}}' as v, g.fuzer_id from golden g join entity e using (fuzer_id) "
            f"where {LIVE_E} and jsonb_typeof(g.value) = 'string' and g.rule <> 'derived'"):
        by_field.setdefault(r["field"], {}).setdefault(r["v"], []).append(r["fuzer_id"])
    out = []
    for field, vals in by_field.items():
        entities = sum(len(v) for v in vals.values())
        if len(vals) > 0.5 * entities or len(vals) > 2000:
            continue
        names = sorted(vals)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if gaps._norm(a) == gaps._norm(b):
                    continue  # the Gaps tab groups these already
                if fuzz.ratio(utils.default_process(a), utils.default_process(b)) < min_ratio:
                    continue
                out.append({"kind": "spelling", "subject": f"{field}|{a}|{b}", "field": field,
                            "context": {"field": field, "values": [{"value": a, "entities": len(vals[a])},
                                                                   {"value": b, "entities": len(vals[b])}]},
                            "options": [{"id": "keep_a", "text": f"one value: keep {a!r}", "keep": a,
                                         "change": vals[b][:500]},
                                        {"id": "keep_b", "text": f"one value: keep {b!r}", "keep": b,
                                         "change": vals[a][:500]},
                                        {"id": "different", "text": "different values"}]})
    return out[:200]


BUILDERS = {"unresolved": _unresolved, "match": _matches, "conflict": _conflicts, "spelling": _spellings}


def questions(conn, kinds=KINDS) -> list[dict]:
    out = []
    for k in kinds:
        out += BUILDERS[k](conn)
    for q in out:
        q["input_hash"] = _hash(q)
    return out


def _sync(conn, qs: list[dict], kinds=KINDS) -> None:
    """Store the questions: a changed question loses its answer; one no longer asked goes stale."""
    by_kind: dict[str, set] = {}
    for q in qs:
        by_kind.setdefault(q["kind"], set()).add(q["subject"])
        conn.execute(
            """
            insert into steward_proposal (kind, subject, question, input_hash) values (%s, %s, %s, %s)
            on conflict (kind, subject) do update set question = excluded.question, input_hash = excluded.input_hash,
                answer = case when steward_proposal.input_hash = excluded.input_hash then steward_proposal.answer end,
                status = case when steward_proposal.status = 'stale' then 'open' else steward_proposal.status end
            where steward_proposal.status in ('open', 'stale')
            """, (q["kind"], q["subject"], Jsonb(q), q["input_hash"]))
    for kind in kinds:
        conn.execute("update steward_proposal set status = 'stale' where kind = %s and status = 'open' "
                     "and subject <> all(%s)", (kind, list(by_kind.get(kind, ()))))


def _for_claude(q: dict, qid: str) -> dict:
    return {"id": qid, "kind": q["kind"], "context": q["context"],
            "options": [{"id": o["id"], "text": o["text"]} for o in q["options"]]}


def _request(model: str, effort: str, batch: list[dict]) -> dict:
    return {
        "model": model, "max_tokens": 8000,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": json.dumps({"questions": batch}, ensure_ascii=False, default=str)}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
    }


def _parse(message, asked: dict[str, dict]) -> dict[str, dict]:
    if message.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("answer cut off at max_tokens; ask fewer questions per request")
    data = json.loads(next(b.text for b in message.content if b.type == "text"))
    out = {}
    for a in data.get("answers", []):
        q = asked.get(a.get("id"))
        if q is None:
            continue
        choice = a["choice"] if a["choice"] in {o["id"] for o in q["options"]} else "none"
        out[a["id"]] = {"choice": choice, "confidence": max(0.0, min(1.0, float(a["confidence"]))),
                        "reason": str(a["reason"])[:600], "evidence": [str(e)[:300] for e in a["evidence"][:6]]}
    return out


def propose(conn, *, client=None, kinds=KINDS, model: str | None = None, effort: str | None = None,
            limit: int | None = 100, force: bool = False, dry_run: bool = False, workers: int = 4) -> dict:
    """Ask Claude about the questions with no current answer (same question and prompt version are cached)."""
    model, effort = model or llm.model(), effort or llm.effort()
    qs = questions(conn, kinds)
    _sync(conn, qs, kinds)
    rows = conn.execute(
        "select id, question, answer, prompt_version from steward_proposal where status = 'open' and kind = any(%s) "
        "order by id", (list(kinds),)).fetchall()
    todo = [r for r in rows if force or r["answer"] is None or r["prompt_version"] != PROMPT_VERSION][:limit]
    stats = {"questions": len(rows), "asked": len(todo), "cached": len(rows) - len(todo), "model": model,
             "provider": llm.provider()}
    if dry_run or not todo:
        conn.commit()
        return stats
    client = client or llm.client()
    asked = {f"q{r['id']}": r["question"] for r in todo}
    chunks = [list(asked)[i:i + PER_REQUEST] for i in range(0, len(asked), PER_REQUEST)]
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "answered": 0, "failed_requests": 0}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(call, client, _request(model, effort, [_for_claude(asked[i], i) for i in ids])): ids
                   for ids in chunks}
        for fut in as_completed(futures):
            try:
                message = fut.result()
                answers = _parse(message, {i: asked[i] for i in futures[fut]})
            except Exception as e:  # one bad request shouldn't lose the rest
                totals["failed_requests"] += 1
                errors.append(f"{type(e).__name__}: {e}")
                continue
            for k, v in usage(message).items():
                totals[k] += v
            for qid, a in answers.items():
                conn.execute("update steward_proposal set answer = %s, model = %s, prompt_version = %s where id = %s",
                             (Jsonb(a), model, PROMPT_VERSION, int(qid[1:])))
                totals["answered"] += 1
    conn.commit()
    return {**stats, **totals, **llm.cost_report(model, totals), "errors": errors[:5]}


def bulk_threshold(conn) -> float:
    return float(settings.get(conn, "stewardship.bulk_accept_at") or 0)


def listing(conn, status: str = "open", kind: str | None = None, limit: int = 300) -> list[dict]:
    rows = conn.execute(
        """
        select * from steward_proposal where status = %s and (%s::text is null or kind = %s)
        order by (answer->>'confidence')::float desc nulls last, id limit %s
        """, (status, kind, kind, limit)).fetchall()
    out = []
    for r in rows:
        q, a = r["question"], r["answer"]
        pick = next((o for o in q["options"] if a and o["id"] == a["choice"]), None)
        out.append({"id": r["id"], "kind": r["kind"], "subject": r["subject"], "context": q["context"],
                    "options": [{"id": o["id"], "text": o["text"], **({"fuzer_id": o["fuzer_id"]} if "fuzer_id" in o
                                                                     else {})} for o in q["options"]],
                    "answer": a, "pick": pick and pick["text"], "status": r["status"], "model": r["model"],
                    "decided_by": r["decided_by"], "decided_at": r["decided_at"], "result": r["result"],
                    "created_at": r["created_at"]})
    return out


def _get(conn, proposal_id: int) -> dict:
    r = conn.execute("select * from steward_proposal where id = %s", (proposal_id,)).fetchone()
    if r is None:
        raise CatalogError(f"no proposal {proposal_id}")
    if r["status"] != "open":
        raise CatalogError(f"proposal {proposal_id} is {r['status']}")
    return dict(r)


def _decide_review(conn, review_id: int, decision: dict) -> None:
    conn.execute("update review_item set status = 'decided', decision = %s, decided_by = %s, decided_at = now() "
                 "where id = %s and status = 'open'", (Jsonb(decision), decision["by"], review_id))


def accept(conn, proposal_id: int, by: str, choice: str | None = None) -> dict:
    """Apply a proposal the way the steward would by hand (an alias, a pin, a chosen value, a spelling fix). `choice`
    takes another of its options instead of Claude's."""
    p = _get(conn, proposal_id)
    q, a = p["question"], p["answer"] or {}
    choice = choice or a.get("choice")
    opt = next((o for o in q["options"] if o["id"] == choice), None)
    if opt is None:
        raise CatalogError("Claude picked none of the options; choose one to accept")
    why = f"Claude proposal {proposal_id}" + (f": {a['reason']}" if a.get("reason") and choice == a.get("choice") else "")
    result: dict = {"choice": choice}
    if p["kind"] == "unresolved":
        result["alias"] = aliases.add(conn, q["subject"], opt["fuzer_id"], by, reason=why)["id"]
        result["fuzer_id"], result["exceptions"] = opt["fuzer_id"], q.get("exceptions") or []
    elif p["kind"] == "match":
        left, right = q["subject"].split("|", 1)
        rule = "must" if choice == "same" else "cannot"
        conn.execute("insert into pinned_link (left_ref, right_ref, rule, decided_by, reason) values (%s, %s, %s, %s, %s) "
                     "on conflict (left_ref, right_ref) do update set rule = excluded.rule, decided_by = "
                     "excluded.decided_by, decided_at = now(), reason = excluded.reason", (left, right, rule, by, why))
        _decide_review(conn, q["review_id"], {"decision": "accept" if rule == "must" else "reject", "by": by,
                                              "via": f"proposal {proposal_id}"})
        result["rebuild"] = True
    elif p["kind"] == "conflict":
        fid, field = q["fuzer_id"], q["field"]
        conn.execute("delete from assertion where fuzer_id = %s and field = %s and source = 'review'", (fid, field))
        conn.execute("insert into assertion (fuzer_id, field, value, source, source_key) values (%s, %s, %s, 'review', "
                     "%s)", (fid, field, Jsonb(opt["value"]), by))
        conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, %s, %s, 'review', 'review') "
                     "on conflict (fuzer_id, field) do update set value = excluded.value, source = excluded.source, "
                     "rule = excluded.rule", (fid, field, Jsonb(opt["value"])))
        _decide_review(conn, q["review_id"], {"decision": "choose", "value": opt["value"], "by": by,
                                              "via": f"proposal {proposal_id}"})
    elif p["kind"] == "spelling":
        if choice != "different":
            result["fix"] = gaps.set_values(conn, q["field"], opt["keep"], opt["change"], by, reason=why)["batch"]
    conn.execute("update steward_proposal set status = 'accepted', decided_by = %s, decided_at = now(), result = %s "
                 "where id = %s", (by, Jsonb(result), proposal_id))
    return {"id": proposal_id, "kind": p["kind"], **result}


def reject(conn, proposal_id: int, by: str, reason: str | None = None) -> dict:
    _get(conn, proposal_id)
    conn.execute("update steward_proposal set status = 'rejected', decided_by = %s, decided_at = now(), result = %s "
                 "where id = %s", (by, Jsonb({"reason": reason}), proposal_id))
    return {"id": proposal_id, "status": "rejected"}


def accept_confident(conn, by: str, kind: str | None = None) -> list[dict]:
    """Accept every open proposal at or above the bulk threshold (none when the setting is 0)."""
    at = bulk_threshold(conn)
    if at <= 0:
        raise CatalogError("bulk accept is off (stewardship.bulk_accept_at is 0)")
    done = []
    for r in conn.execute(
            "select id from steward_proposal where status = 'open' and (%s::text is null or kind = %s) and "
            "answer->>'choice' <> 'none' and (answer->>'confidence')::float >= %s order by id",
            (kind, kind, at)).fetchall():
        done.append(accept(conn, r["id"], by))
    return done
