"""An agent drafts a change to a skill from where it fell short (P6.4c, #805).

A skill with `MIN_SHORT` or more shortfalls (fuzer.agents.shortfalls) since its live version went live, and no draft
waiting already, is due a change. Its owning specialist reads the live playbook and each shortfall (the case or the
question, what was proposed or answered and the person's note) and writes the revised playbook, the eval questions
the cases suggest, and why. The draft is kept as a skill version (skill_versions.draft: it cannot name a tool the
skill's agents lack) and opens as `skill_change` work for the skill's owner (ops.skill_review). The agent never
publishes; a person does, behind the pack's eval (P6.4d).

    due(conn)                     skills due a change, the most shortfalls first: {skill, since, shortfalls}
    prepare(conn, skill, ask)     draft the change and open its work; or why there is none
    next_one(conn, ask)           draft for the first skill due, one worker per skill; None when none is due

The agent worker drafts when it has no agent step or case brief to write, when `agents.skill_drafts` is on.
"""

import json
import re

from . import ledger, shortfalls, skill_versions
from .capabilities import catalog

MIN_SHORT = 2  # shortfalls since the live version before a change is drafted: one is the person's call, not a pattern
SHOWN = 12  # shortfalls the specialist reads, the newest first
_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _waiting(conn, skill: str) -> bool:
    return conn.execute("select 1 from skill_version where skill = %s and status = 'draft' limit 1",
                        (skill,)).fetchone() is not None


def due(conn) -> list[dict]:
    """Skills with `MIN_SHORT` or more shortfalls since their live version went live and no draft waiting."""
    out, read = [], {}
    for s in catalog.SKILL_CATALOG:
        if s.shared or _waiting(conn, s.path):
            continue
        since = skill_versions.live(conn, s.path)["published_at"]
        if since not in read:
            read[since] = {x["skill"]: x for x in shortfalls.gather(conn, since)}
        got = read[since].get(s.path)
        if got and len(got["shortfalls"]) >= MIN_SHORT:
            out.append({"skill": s.path, "owner": s.owner, "since": since, **got})
    return sorted(out, key=lambda x: (-len(x["shortfalls"]), x["skill"]))


def _said(x: dict) -> str:
    if x["kind"] == "brief":
        return (f"case {x['case']} ({x['type']}: {x['title']}): the brief proposed {x.get('proposed') or 'nothing'}; "
                f"{x['by']} {'partly followed' if x['followed'] == 'partly' else 'did not follow'} it"
                + (f": \"{x['note']}\"" if x.get("note") else ""))
    return (f"answer {x['answer']} to \"{x['question']}\": {x['by']} rated it not useful"
            + (f": \"{x['note']}\"" if x.get("note") else ""))


def prompt(skill: str, owner: str, playbook: str, short: list[dict]) -> str:
    return (
        f"You are the {owner} specialist. Your `{skill.rsplit('/', 1)[-1]}` skill fell short {len(short)} times. "
        "Read each shortfall, decide what in the playbook led to it, and revise the playbook so it would not happen "
        "again. Keep everything that still holds, keep its format, and name only tools you already have. Draw one "
        "eval question from each case worth testing, with the words a good answer must mention. Change nothing for "
        "a shortfall that was the person's call on facts you could not see; say so instead.\n\n"
        "Shortfalls, newest first:\n" + "".join(f"- {_said(x)}\n" for x in short[:SHOWN]) +
        f"\nThe live playbook:\n<playbook>\n{playbook}\n</playbook>\n\n"
        "End with one ```json block with these keys: playbook (the whole revised SKILL.md), questions (list of "
        f"{{question, must_mention}}), why (two or three sentences: what changed and which cases drove it).")


def parse(answer: str, owner: str) -> dict:
    blocks = _JSON.findall(answer or "")
    try:
        raw = json.loads(blocks[-1]) if blocks else {}
    except json.JSONDecodeError:
        raw = {}
    qs = [{"question": str(q["question"]), "must_mention": list(q.get("must_mention") or []), "specialists": [owner]}
          for q in raw.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    return {"playbook": str(raw.get("playbook") or ""), "questions": qs, "why": str(raw.get("why") or "")}


def _cases(short: list[dict]) -> list[dict]:
    keep = ("kind", "case", "answer", "title", "question", "followed", "note", "by", "at")
    return [{k: x[k] for k in keep if x.get(k) is not None} for x in short[:SHOWN]]


def prepare(conn, skill: str, ask=None) -> dict:
    """Draft a change to `skill` from its shortfalls and open it as work for its owner; or say why there is none."""
    from ..ops import skill_review

    s = next((x for x in due(conn) if x["skill"] == skill), None)
    if s is None:
        return {"skill": skill, "missing": f"no draft is due: fewer than {MIN_SHORT} shortfalls since its live version, "
                                           "or a draft is already waiting"}
    live = skill_versions.live(conn, skill)
    conn.commit()  # end the reads before the model call
    if ask is None:
        from . import ask_sync as ask
    try:
        answer = ask(prompt(skill, s["owner"], live["playbook"], s["shortfalls"]))
    except Exception as e:
        return {"skill": skill, "missing": f"the {s['owner']} specialist could not be reached: {e}"}
    led = ledger.record(s["owner"], answer)
    got = parse(answer["answer"], s["owner"])
    if not got["playbook"].strip() or got["playbook"].strip() == live["playbook"].strip():
        return {"skill": skill, "missing": "the specialist proposed no change", "why": got["why"], "ledger": led}
    by = f"agent:{s['owner']}"
    cases = _cases(s["shortfalls"])
    try:
        v = skill_versions.draft(conn, skill, got["playbook"], by, cases, got["questions"], got["why"])
    except ValueError as e:  # a playbook that names a tool its agents lack never becomes a draft
        return {"skill": skill, "missing": f"the draft was refused: {e}", "ledger": led}
    work = skill_review.open_review(
        conn, skill, v["version"], f"{skill}: a change drafted from {len(s['shortfalls'])} shortfalls", by,
        cases, got["questions"], got["why"])
    conn.commit()
    return {"skill": skill, "version": v["version"], "item_id": work["item_id"], "exception_id": work["id"],
            "cases": len(cases), "questions": len(got["questions"]), "why": got["why"], "ledger": led}


def next_one(conn, ask=None) -> dict | None:
    """Draft for the first skill due; a skill another worker is drafting is skipped. None when none is due."""
    for s in due(conn):
        got = conn.execute("select pg_try_advisory_lock(hashtext('skill_draft'), hashtext(%s)) as ok",
                           (s["skill"],)).fetchone()
        conn.commit()
        if not got["ok"]:
            continue
        try:
            return prepare(conn, s["skill"], ask)
        finally:
            conn.execute("select pg_advisory_unlock(hashtext('skill_draft'), hashtext(%s))", (s["skill"],))
            conn.commit()
    return None
