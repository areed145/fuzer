"""The agent brief on a case (P4.3): before a person opens a case, the specialist whose pack owns it reads it.

A pack owns a case when the case's work type is one the pack `briefs` and one of the case's subjects is of a class
the pack covers (ISO 14224). The pack's agent writes the brief: what happened in plain words, the evidence, what the
standard analysis shows, similar past cases and what fixed them, a proposed disposition with its confidence, and
what it could not check. The brief proposes; a person decides (the gate policy, P5.10, always stops an agent's
proposed disposition at a person).

A brief is kept on the case as an `agent_result` event for the step `brief`, with its ledger (P4.4), so its cost
counts toward the case's agent spend and it shows in the agent work card. A case that gets no brief records why in
the same place: no pack owns it, the case's agent spend reached its cap, or the specialist could not be reached.

It is due within the case's cadence tier: the cadence of the detector that raises its work type (30 minutes,
6 hours or a day), 6 hours when no detector raises it. A brief may cost up to `agents.brief_cap_usd`; one that costs
more is kept and says so.

    owner(conn, item)            the pack that briefs this case, or None
    due(item)                    when its brief is due
    prepare(conn, item_id, ask)  write the brief, or record why there is none; returns what was recorded
    latest(conn, item_id)        the case's brief, or its reason for having none
    pending(conn)                open cases with no brief yet, oldest first
"""

import json
import re
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from ..core import detectors, settings
from . import ledger, packs

STEP = "brief"
SECTIONS = ("what_happened", "evidence", "analysis", "similar", "proposal", "could_not_check")
TIERS = {"30min": timedelta(minutes=30), "6h": timedelta(hours=6), "daily": timedelta(days=1)}
DEFAULT_TIER = timedelta(hours=6)
_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def owner(conn, item: dict) -> dict | None:
    """The pack whose `briefs` include the case's type and whose classes include one of its subjects' classes."""
    classes = set(detectors.classes_of(conn, item.get("subjects") or []).values())
    for path in packs.shipped():
        p = packs.load(path)
        if item["type"] in (p.get("briefs") or ()) and classes & set(p.get("classes") or ()):
            return p
    return None


def tier(work_type: str) -> timedelta:
    """The case type's cadence tier: the shortest cadence of the detectors that raise it."""
    found = [TIERS[d.cadence] for d in detectors.registry().values() if work_type in d.raises and d.cadence in TIERS]
    return min(found, default=DEFAULT_TIER)


def due(item: dict) -> datetime:
    return item["created_at"] + tier(item["type"])


def prompt(item: dict, pack: dict) -> str:
    return (
        f"Brief case {item['id']} ({item['type']}): {item['title']}. Subjects: {', '.join(item['subjects']) or 'none'}.\n"
        f"What raised it: {json.dumps(item.get('payload') or {}, default=str)[:4000]}\n"
        f"Ask the {pack['owner']} specialist to load the `{pack['path'].rsplit('/', 1)[-1]}` skill and brief the person "
        "who will decide this case, before they open it. Use the skill's checks and cite each record and method used. "
        "Find similar past cases on the same unit or class and what fixed them. Propose a disposition for the person "
        "to decide; take no action. Say plainly what could not be checked and why.\n"
        "End with one ```json block with these keys: what_happened (plain words, two or three sentences), "
        "evidence (list of {ref, says}), analysis (list of {method, says}), similar (list of {case, fixed_by}), "
        "proposal ({disposition, confidence between 0 and 1, why}), could_not_check (list of strings).")


def parse(answer: str) -> dict:
    """The brief's sections from the answer's last JSON block; a section the agent left out is named as missing."""
    blocks = _JSON.findall(answer or "")
    try:
        raw = json.loads(blocks[-1]) if blocks else {}
    except json.JSONDecodeError:
        raw = {}
    brief = {k: raw.get(k) for k in SECTIONS}
    missing = [k for k in SECTIONS if raw.get(k) in (None, "", [], {})]
    if missing and "could_not_check" not in missing:
        brief["could_not_check"] = [*(brief["could_not_check"] or []),
                                   *(f"the brief has no {k.replace('_', ' ')}" for k in missing)]
    elif missing:
        brief["could_not_check"] = [f"the brief has no {k.replace('_', ' ')}" for k in missing if k != "could_not_check"]
    p = brief.get("proposal") or {}
    if isinstance(p, dict) and p.get("confidence") is not None:
        try:
            p["confidence"] = max(0.0, min(1.0, float(p["confidence"])))
        except (TypeError, ValueError):
            p["confidence"] = None
    return brief


def _record(conn, item: dict, actor: str, entry: dict) -> dict:
    conn.commit()  # end the reads before it, so the record below commits as its own unit of work
    with conn.transaction():
        conn.execute("insert into work_event (item_id, kind, actor, element, data) values (%s, 'agent_result', %s, "
                     "%s, %s)", (item["id"], actor, STEP, Jsonb({"data": {STEP: entry}, "step": STEP})))
    return entry


def latest(conn, item_id: int) -> dict | None:
    r = conn.execute("select data -> 'data' -> %s as entry from work_event where item_id = %s and kind = 'agent_result' "
                     "and element = %s order by id desc limit 1", (STEP, item_id, STEP)).fetchone()
    return r["entry"] if r else None


def prepare(conn, item_id: int, ask=None, now: datetime | None = None) -> dict:
    """Write the case's brief, or record why it has none. `ask(question) -> {answer, usage, ...}` defaults to the
    operations assistant. A case that already has a brief keeps it."""
    if (had := latest(conn, item_id)) is not None:
        return had
    item = dict(conn.execute("select * from work_item where id = %s", (item_id,)).fetchone())
    conn.commit()  # end the read before the model call
    now = now or datetime.now(item["created_at"].tzinfo)
    when = {"due": due(item).isoformat(), "at": now.isoformat()}
    pack = owner(conn, item)
    if pack is None:
        return _record(conn, item, "system", {"missing": f"no specialist pack briefs a {item['type']} case on these "
                                                         "subjects' equipment classes", **when})
    actor = f"agent:{pack['owner']}"
    cap = float(settings.get(conn, "agents.case_cap_usd") or 0)
    if cap and (spent := ledger.case_spend(conn, item_id)) >= cap:
        return _record(conn, item, actor, {"missing": f"agent spend on this case reached its cap: ${spent:.2f} of "
                                                      f"${cap:.2f} (setting agents.case_cap_usd)", **when})
    if ask is None:
        from . import ask_sync as ask
    try:
        answer = ask(prompt(item, pack))
    except Exception as e:  # the case still needs a person; the reason goes on it
        return _record(conn, item, actor, {"missing": f"the {pack['owner']} specialist could not be reached: {e}",
                                           **when})
    led = ledger.record(pack["owner"], answer)
    budget = float(settings.get(conn, "agents.brief_cap_usd") or 0)
    return _record(conn, item, actor, {
        "pack": pack["path"], "brief": parse(answer["answer"]), "answer": answer["answer"],
        "usage": answer.get("usage") or {}, "ledger": led, "budget_usd": budget,
        "over_budget": bool(budget) and led["cost_usd"] > budget, "late": now > due(item), **when})


def briefed_types() -> set[str]:
    return {t for path in packs.shipped() for t in (packs.load(path).get("briefs") or ())}


def pending(conn, limit: int = 20) -> list[int]:
    """Open cases of a type some pack briefs, with no brief and no recorded reason for none, oldest first."""
    return [r["id"] for r in conn.execute(
        "select i.id from work_item i where i.state = 'open' and i.type = any(%s) and not exists (select 1 "
        "from work_event e where e.item_id = i.id and e.kind = 'agent_result' and e.element = %s) "
        "order by i.created_at, i.id limit %s", (sorted(briefed_types()), STEP, limit))]
