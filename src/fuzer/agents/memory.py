"""What the assistant learns from conversations (#93): short, dated facts about Fuzer entities.

The shape follows Graphiti / Zep (arXiv 2501.13956), in Postgres:

- **Episodes** are the answers in assistant_answer (question, answer, tools, figures, asker, ratings), kept once.
- **Facts** (assistant_fact) are extracted from an episode when it is rated useful, or when an engineer marks a
  statement "remember this". Each names the entities it is about, the period it holds for in the plant
  (valid_from / valid_to, often open), when it was learned, and its sources (the answer and the records it
  cited).
- **Invalidation:** a newer fact that contradicts a current one on the same entity and subject ends the old one
  (status 'ended', valid_to, invalidated_by). It is never deleted, so both stay visible with their dates.
  Claude makes that call in the same request that extracts the facts: it sees the current facts on the
  answer's entities and says which ones each new fact replaces. Only a replaced fact that shares an entity
  with the new one is ended.

Facts that read as reusable lessons (what to check or do for a failure mode on a model or class) also go to
the lessons review queue as suggestions, not straight into anyone's practice. Facts from an answer rated not
useful are never kept. People stay in charge: an engineer can correct, end or delete any fact.

Recall: `context(conn, ids)` is the "Known from earlier conversations" block the orchestrator gets for the
entities a question names, and `recall(ref)` is the tool for more.
"""

import json
import re
from datetime import date

from psycopg.types.json import Jsonb

from ..core import llm
from ..core.pack import Pack

FUZER_ID = re.compile(r"\bfz-[a-z_]+-[0-9a-f]{12}\b")
MAX_FACTS = 8
PROMPT_VERSION = 1

SYSTEM = """\
You keep the operations assistant's memory for an oil and gas asset. You get one question and the answer the
assistant gave (an engineer rated it useful, or marked a statement in it to remember), the entities it is about,
and the facts already remembered about them.

Return the few facts worth remembering for later conversations: findings about specific equipment, wells or
units that the data alone does not make obvious (a cause that was established, a repair that was done, a
condition that was confirmed, what an engineer said is true). Not general knowledge, not numbers anyone can
look up again, not advice. When a statement is given, extract only from it. At most 8 facts; none is fine.

For each fact:
- text: one short, self-contained sentence naming the entity ("J07-03's gas lift valve was replaced by slickline
  in December 2024."). No hedging words the answer did not use.
- subject: a short lowercase topic that a later fact about the same thing would share ("gas lift valve
  condition", "trip cause", "seal condition").
- refs: the tags, wells or Fuzer ids it is about, written as in the answer.
- valid_from / valid_to: the dates (YYYY-MM-DD) it holds for in the plant, when the answer gives them; null when
  open or unknown.
- replaces: ids of remembered facts this one contradicts or updates (same entity and subject, no longer true).
  Leave out facts it merely adds to.
- sources: the record ids or source keys the answer cited for it.
- lesson: when the fact is a reusable lesson for a failure mode (what to check or do next time on this kind of
  equipment), {failure_mode: the ISO 14224 failure mode code if the answer names one or else a short name,
  title, steps: [short checks or actions]}; otherwise null."""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["facts"],
    "properties": {"facts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["text", "subject", "refs", "valid_from", "valid_to", "replaces", "sources", "lesson"],
        "properties": {
            "text": {"type": "string"}, "subject": {"type": "string"},
            "refs": {"type": "array", "items": {"type": "string"}},
            "valid_from": {"type": ["string", "null"]}, "valid_to": {"type": ["string", "null"]},
            "replaces": {"type": "array", "items": {"type": "integer"}},
            "sources": {"type": "array", "items": {"type": "string"}},
            "lesson": {"anyOf": [{"type": "null"}, {
                "type": "object", "additionalProperties": False, "required": ["failure_mode", "title", "steps"],
                "properties": {"failure_mode": {"type": "string"}, "title": {"type": "string"},
                               "steps": {"type": "array", "items": {"type": "string"}}}}]},
        }}}},
}

COLUMNS = ("id", "text", "subject", "entity_ids", "valid_from", "valid_to", "learned_at", "episode", "sources",
           "status", "invalidated_by", "by_user", "edited_by", "ended_by", "ended_at")


# ---- reading


def facts(conn, ids: list[str] | None = None, status: str | None = "current", limit: int = 50) -> list[dict]:
    """Facts about any of these entities (all with ids=None), newest first, with entity labels."""
    rows = conn.execute(
        f"""
        select {', '.join('f.' + c for c in COLUMNS)}, a.question as episode_question, a.created_at as episode_at,
               (select coalesce(jsonb_agg(jsonb_build_object('id', x, 'label', l.label)), '[]')
                from unnest(f.entity_ids) x left join entity_label l on l.fuzer_id = x) as entities
        from assistant_fact f left join assistant_answer a on a.id = f.episode
        where (%(ids)s::text[] is null or f.entity_ids && %(ids)s) and (%(status)s::text is null or f.status = %(status)s)
        order by f.status = 'current' desc, f.learned_at desc limit %(limit)s
        """, {"ids": ids, "status": status, "limit": limit}).fetchall()
    return rows


def units(conn, fuzer_id: str) -> list[str]:
    """The entity, what is installed at it (or where it is installed) and the units it is part of: facts about
    any of them are worth knowing when the entity comes up."""
    from ..graph import knowledge

    return [k for k, (_, how) in knowledge.scope(conn, fuzer_id).items() if how in ("this", "installed", "parent")]


def context(conn, ids: list[str], limit: int = 12) -> dict | None:
    """The "Known from earlier conversations" block for these entities and their units ({text, ids}), or None."""
    scope = sorted({u for i in ids for u in units(conn, i)})
    found = facts(conn, scope, "current", limit) if scope else []
    if not found:
        return None
    lines = ["Known from earlier conversations (learned from answers engineers rated useful, not source records; "
             "check them against the data and cite them as `fact:<id>`, learned from a conversation):"]
    for f in found:
        when = _period(f)
        lines.append(f"- fact:{f['id']}: {f['text']}{f' ({when})' if when else ''} "
                     f"[learned {f['learned_at']:%Y-%m-%d} from answer {f['episode'] or 'deleted'}]")
    return {"text": "\n".join(lines), "ids": [f["id"] for f in found]}


def _period(f: dict) -> str:
    a, b = f.get("valid_from"), f.get("valid_to")
    if a and b:
        return f"{a} to {b}"
    return f"since {a}" if a else (f"until {b}" if b else "")


# ---- learning


def entities_of(conn, pack: Pack, answer: dict) -> list[str]:
    """The entities an answer is about: the Fuzer ids it names, the tags and wells in its question and answer, and
    the refs its tools were called with."""
    from ..graph import queries

    ids = list(dict.fromkeys(FUZER_ID.findall(answer.get("answer") or "")))
    text = f"{answer.get('question') or ''}\n{answer.get('answer') or ''}"
    for m in queries.link_text(conn, pack, text):
        if m["fuzer_id"] and m["fuzer_id"] not in ids:
            ids.append(m["fuzer_id"])
    for t in answer.get("tools") or []:
        ref = (t.get("args") or {}).get("ref")
        if isinstance(ref, str) and ref:
            try:
                fid = queries.resolve_ref(conn, pack, ref)
            except Exception:  # ambiguous: not worth guessing
                fid = None
            if fid and fid not in ids:
                ids.append(fid)
    return ids[:30]


def learn(conn, pack: Pack, answer_id: str, statement: str | None = None, by: str = "anonymous",
          client=None, model: str | None = None) -> dict:
    """Extract facts from one answer (or one statement in it) and store them, ending the facts they replace.
    Skipped for an answer rated not useful by anyone."""
    answer = conn.execute("select * from assistant_answer where id = %s", (answer_id,)).fetchone()
    if answer is None or not answer["answer"]:
        return {"error": "unknown answer"}
    if _rated_not_useful(conn, answer_id):
        return {"skipped": "rated not useful", "facts": []}
    ids = entities_of(conn, pack, answer)
    scope = sorted({u for i in ids for u in units(conn, i)})
    known = facts(conn, scope, "current", 40) if scope else []
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (ids,))}
    payload = {
        "date": str(answer["created_at"].date()),
        "question": answer["question"], "answer": answer["answer"],
        **({"statement": statement} if statement else {}),
        "entities": [{"id": i, "label": labels.get(i)} for i in ids],
        "remembered": [{"id": f["id"], "text": f["text"], "subject": f["subject"],
                        "entities": [e["label"] or e["id"] for e in f["entities"]],
                        "valid_from": _iso(f["valid_from"]), "valid_to": _iso(f["valid_to"])} for f in known],
    }
    message = llm.call(client or _client(), {
        "model": model or llm.model(), "max_tokens": 4000,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}})
    if message.stop_reason in ("refusal", "max_tokens"):
        return {"error": f"stopped: {message.stop_reason}", "facts": []}
    out = json.loads(next(b.text for b in message.content if b.type == "text"))
    known_ids = {f["id"]: f for f in known}
    stored, ended, lessons = [], [], []
    for f in (out.get("facts") or [])[:MAX_FACTS]:
        entity_ids = _resolve(conn, pack, f.get("refs") or [], labels) or ids[:1]
        if not entity_ids or not (f.get("text") or "").strip():
            continue
        row = conn.execute(
            """
            insert into assistant_fact (text, subject, entity_ids, valid_from, valid_to, episode, sources, by_user)
            values (%s, %s, %s, %s, %s, %s, %s, %s) returning id
            """,
            (f["text"].strip(), (f.get("subject") or "").strip().lower(), entity_ids, _date(f.get("valid_from")),
             _date(f.get("valid_to")), answer_id, Jsonb([f"answer:{answer_id}", *(f.get("sources") or [])]), by),
        ).fetchone()
        stored.append(row["id"])
        for old in f.get("replaces") or []:
            if old in known_ids and set(known_ids[old]["entity_ids"]) & set(entity_ids):
                end(conn, old, by="assistant", valid_to=_date(f.get("valid_from")), invalidated_by=row["id"],
                    commit=False)
                ended.append(old)
        if f.get("lesson") and (lesson := _suggest_lesson(conn, entity_ids, f, answer_id)):
            lessons.append(lesson)
    conn.commit()
    return {"facts": stored, "ended": ended, "lessons": lessons, "usage": llm.usage(message)}


def _client():
    import anthropic

    return anthropic.Anthropic()


def learn_later(answer_id: str, statement: str | None = None, by: str = "anonymous") -> None:
    """learn() on its own connection, for a background task after a rating; without a key it does nothing."""
    import logging
    import os

    from ..core import db
    from . import mcp_server

    if not os.environ.get("ANTHROPIC_API_KEY") and _client is _DEFAULT_CLIENT:
        return
    try:
        with db.connect() as conn:
            learn(conn, mcp_server.PACK, answer_id, statement, by)
    except Exception as e:  # a rating must never fail because memory did
        logging.getLogger(__name__).warning("learning from answer %s failed: %s", answer_id, e)


def _rated_not_useful(conn, answer_id: str) -> bool:
    return conn.execute("select 1 from feedback where target = %s and not useful limit 1",
                        (f"answer:{answer_id}",)).fetchone() is not None


_DEFAULT_CLIENT = _client


def forget_episode(conn, answer_id: str) -> int:
    """An answer was rated not useful: drop the facts learned from it that no engineer has touched. Facts they
    ended stay ended."""
    n = conn.execute("delete from assistant_fact where episode = %s and edited_by is null and status = 'current'",
                     (answer_id,)).rowcount
    conn.commit()
    return n


def _resolve(conn, pack: Pack, refs: list[str], labels: dict[str, str]) -> list[str]:
    from ..graph import queries

    by_label = {v.upper(): k for k, v in labels.items() if v}
    out = []
    for r in refs:
        fid = by_label.get(r.strip().upper())
        if fid is None:
            try:
                fid = queries.resolve_ref(conn, pack, r)
            except Exception:
                fid = None
        if fid and fid not in out:
            out.append(fid)
    return out


def _suggest_lesson(conn, entity_ids: list[str], f: dict, answer_id: str) -> int | None:
    """A reusable lesson goes to the lessons review queue, scoped to the entity's model (or class)."""
    lesson = f["lesson"]
    scope = conn.execute(
        """
        select k.kind, k.key from edge e join entity_key k on k.fuzer_id = e.dst
        where e.src = any(%s) and e.rel in ('instance_of', 'classified_as')
          and k.kind in ('equipment_model', 'equipment_class')
        order by k.kind = 'equipment_model' desc limit 1
        """, (entity_ids,)).fetchone()
    if scope is None:
        return None
    draft = "\n".join([f"From a conversation with the assistant ({f['text']})", *(f"- {s}" for s in lesson["steps"])])
    row = conn.execute(
        """
        insert into lesson (scope_kind, scope_key, failure_mode, title, draft, evidence, stats)
        values (%s, %s, %s, %s, %s, %s, %s) on conflict (scope_kind, scope_key, failure_mode) do nothing returning id
        """,
        (scope["kind"], scope["key"], lesson["failure_mode"], lesson["title"], draft,
         Jsonb([{"id": f"answer:{answer_id}", "label": "assistant answer rated useful"}]),
         Jsonb({"incidents": 0, "from_conversation": True})),
    ).fetchone()
    return row["id"] if row else None


# ---- people in charge


def correct(conn, fact_id: int, by: str, text: str | None = None, valid_from=None, valid_to=None,
            clear: tuple[str, ...] = ()) -> dict | None:
    """An engineer's correction: new wording and / or dates (`clear` names dates to empty)."""
    sets, args = ["edited_by = %(by)s", "updated_at = now()"], {"id": fact_id, "by": by}
    for col, v in (("text", text), ("valid_from", _date(valid_from)), ("valid_to", _date(valid_to))):
        if v is not None:
            sets.append(f"{col} = %({col})s")
            args[col] = v
    for col in clear:
        if col in ("valid_from", "valid_to"):
            sets.append(f"{col} = null")
    row = conn.execute(f"update assistant_fact set {', '.join(sets)} where id = %(id)s returning id", args).fetchone()
    conn.commit()
    return get(conn, fact_id) if row else None


def end(conn, fact_id: int, by: str, valid_to=None, invalidated_by: int | None = None, commit: bool = True) -> bool:
    """No longer true: status 'ended', valid_to set (the given date, or today when the fact had none). Kept."""
    n = conn.execute(
        """
        update assistant_fact set status = 'ended', ended_by = %s, ended_at = now(), invalidated_by = %s,
               valid_to = coalesce(%s, valid_to, current_date), updated_at = now(),
               edited_by = case when %s = 'assistant' then edited_by else %s end
        where id = %s and status = 'current'
        """, (by, invalidated_by, _date(valid_to), by, by, fact_id)).rowcount
    if commit:
        conn.commit()
    return bool(n)


def delete(conn, fact_id: int) -> bool:
    conn.execute("update assistant_fact set invalidated_by = null where invalidated_by = %s", (fact_id,))
    n = conn.execute("delete from assistant_fact where id = %s", (fact_id,)).rowcount
    conn.commit()
    return bool(n)


def get(conn, fact_id: int) -> dict | None:
    return conn.execute(f"select {', '.join(COLUMNS)} from assistant_fact where id = %s", (fact_id,)).fetchone()


def _date(v):
    if v in (None, ""):
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _iso(v):
    return v.isoformat() if v else None
