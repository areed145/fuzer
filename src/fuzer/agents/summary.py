"""An entity page's summary (Explore): the summary specialist reads the facts about an entity and everything beneath
it (`ops.beneath.picture`) and says, in a headline and a few points, what matters there now.

One short call on a small model (`agents.summary_model`, low effort), kept for `agents.summary_hours` and shared with
everyone who opens the page: the next visit by someone with AI access after that writes a new one. The facts it was
written from are kept with it, so every point can be checked against them; a point cites the ids it rests on.

    latest(conn, fid)                 the kept summary, fresh or not, with its age; None if there has never been one
    fresh(conn, fid)                  the kept summary if it is younger than agents.summary_hours, else None
    write(conn, fid, by, client=None) a new summary from the facts now; refuses nothing, so callers check fresh() first
"""

import json
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from ..core import llm, settings

SYSTEM = """You are the summary specialist of an oil and gas integrated operations centre. You are given the facts
about one entity in the asset register (a facility, an area, a well, a machine or an instrument) and everything that
sits beneath it, rolled up: open work, what is down now, the jobs planned in the next days, and the children that
carry the work.

Write what an IOC operator or a planner opening this entity's page needs to know first, from these facts only:
- headline: one sentence, at most 20 words, on how it stands now.
- points: two to five short sentences, most important first: what is down, what is overdue or most valuable, where
  the work concentrates beneath it, what is coming up (a shutdown, a clash).
Say numbers as given, with units. Name entities by their labels. Never advise how to operate the plant. If the facts
show nothing open, down or planned, say so in the headline and give one point at most. For each point list in
`cites` the ids from the facts it rests on (entity ids, or case numbers as "case:<n>"), or none."""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["headline", "points"],
    "properties": {
        "headline": {"type": "string"},
        "points": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "cites"],
            "properties": {"text": {"type": "string"}, "cites": {"type": "array", "items": {"type": "string"}}}}},
    },
}


def _row(r: dict | None, hours: int) -> dict | None:
    if r is None:
        return None
    age = datetime.now(UTC) - r["created_at"]
    return {"headline": r["headline"], "points": r["points"], "facts_as_of": (r["facts"] or {}).get("as_of"),
            "written_at": r["created_at"].isoformat(timespec="seconds"), "by": r["by_user"],
            "model": (r["usage"] or {}).get("model"), "cost_usd": (r["usage"] or {}).get("cost_usd"),
            "stale": age >= timedelta(hours=hours)}


def latest(conn, fid: str) -> dict | None:
    hours = int(settings.get(conn, "agents.summary_hours"))
    return _row(conn.execute(
        "select * from entity_summary where fuzer_id = %s order by created_at desc limit 1", (fid,)).fetchone(), hours)


def fresh(conn, fid: str) -> dict | None:
    kept = latest(conn, fid)
    return kept if kept and not kept["stale"] else None


def write(conn, fid: str, by: str, client=None) -> dict | None:
    """Write and keep a new summary of `fid`; None for an unknown entity."""
    from ..ops import beneath

    facts = beneath.picture(conn, fid)
    if facts is None:
        return None
    model = settings.get(conn, "agents.summary_model") or settings.get(conn, "agents.model")
    message = llm.call(client or llm.client("agents"), {
        "model": model, "max_tokens": 1200,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": json.dumps(facts, ensure_ascii=False, default=str)}],
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}},
        scope="agents")
    if message.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"The summary stopped early ({message.stop_reason}); nothing was kept.")
    out = json.loads(next(b.text for b in message.content if b.type == "text"))
    measured = llm.usage(message)
    usage = {"model": model, **measured, **llm.cost_report(model, measured, "agents")}
    points = [{"text": p["text"].strip(), "cites": [str(c) for c in p.get("cites") or []][:6]}
              for p in (out.get("points") or [])[:5] if (p.get("text") or "").strip()]
    conn.execute(
        "insert into entity_summary (fuzer_id, headline, points, facts, usage, by_user) values (%s, %s, %s, %s, %s, %s)",
        (fid, (out.get("headline") or "").strip(), Jsonb(points), Jsonb(json.loads(json.dumps(facts, default=str))),
         Jsonb(usage), by))
    conn.commit()
    return latest(conn, fid)
