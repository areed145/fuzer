"""What each agent step did, kept so any past answer traces to what produced it (P4.4).

An agent step's result carries a `ledger`: the agents that worked on it, the skill versions they held (a hash of each
skill's files, so a changed playbook or threshold shows as a new version), the models that served it, the tools they
called with their arguments, the seconds and the cost. It is kept with the step's result, in the `agent_result` work
event, so the case's history holds it without a second store.

    record("maintenance-integrity", answer)   the ledger for one step, from what `agents.ask` returned
    skill_versions(["maintenance-integrity"])  {"maintenance-integrity/...": "3f9c0a1b2c4d", ...}
    spend(conn, since="2026-11-01")           agent spend by agent, work type and asset, from the ledgers
    case_spend(conn, item_id)                  what agents have spent on one case so far

A case's agent spend is capped by the setting `agents.case_cap_usd`: the worker checks it before an agent step runs,
and over the cap the step goes to a person with the spend and the cap on the case.
"""

import functools
import hashlib
from datetime import datetime

from . import SKILLS
from .capabilities import AGENT_BY_ID


@functools.cache
def _skill_hash(path: str) -> str:
    """A short hash of every file in one skill's folder, in name order."""
    h = hashlib.sha256()
    root = SKILLS / path
    for f in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:12]


def skill_versions(agents: list[str]) -> dict[str, str]:
    """The skills the named agents hold, each with its version hash."""
    paths = {p for a in agents if a in AGENT_BY_ID for p in AGENT_BY_ID[a].skill_paths}
    return {p: _skill_hash(p) for p in sorted(paths)}


def record(role: str, answer: dict) -> dict:
    """The ledger for one agent step: who worked, with which skill and model versions, which tools, at what cost."""
    usage = answer.get("usage") or {}
    worked = sorted({role, *answer.get("specialists", []), *(t.get("agent") for t in answer.get("tools", [])
                                                              if t.get("agent"))} - {None})
    return {"agent": role, "agents": worked, "skills": skill_versions(worked),
            "models": sorted((usage.get("models") or {}).keys()), "model_calls": usage.get("model_calls", 0),
            "tools": [{k: t[k] for k in ("agent", "tool", "args") if k in t} for t in answer.get("tools", [])],
            "seconds": answer.get("seconds"), "cost_usd": round(float(usage.get("cost_usd") or 0), 4)}


# Each agent step's ledger, with the case it was on: the step's result sits under the event's "data", by step key.
_LEDGERS = """
    select e.item_id, i.type, i.subjects, e.at, l.value -> 'ledger' as ledger
    from work_event e join work_item i on i.id = e.item_id, jsonb_each(coalesce(e.data -> 'data', '{}'::jsonb)) l
    where e.kind = 'agent_result' and jsonb_typeof(l.value) = 'object' and l.value ? 'ledger'
"""


def case_spend(conn, item_id: int) -> float:
    """What agents have spent on one case so far (USD)."""
    r = conn.execute(f"select coalesce(sum((ledger ->> 'cost_usd')::numeric), 0) as usd from ({_LEDGERS}) s "
                     "where item_id = %s", (item_id,)).fetchone()
    return round(float(r["usd"]), 4)


def spend(conn, since: str | datetime | None = None, until: str | datetime | None = None) -> dict:
    """Agent spend in a window, by agent, by work type and by asset (each case's subjects; a case about two assets
    counts for both, so the asset rows can add up to more than the total)."""
    rows = conn.execute(f"select * from ({_LEDGERS}) s where (%s::timestamptz is null or at >= %s::timestamptz) "
                        "and (%s::timestamptz is null or at < %s::timestamptz)", (since, since, until, until)).fetchall()
    by: dict[str, dict[str, dict]] = {"agent": {}, "work_type": {}, "asset": {}}

    def add(kind: str, key: str, usd: float, item: int) -> None:
        row = by[kind].setdefault(key, {kind: key, "cost_usd": 0.0, "steps": 0, "cases": set()})
        row["cost_usd"] += usd
        row["steps"] += 1
        row["cases"].add(item)
    for r in rows:
        usd = float((r["ledger"] or {}).get("cost_usd") or 0)
        add("agent", r["ledger"].get("agent") or "unknown", usd, r["item_id"])
        add("work_type", r["type"], usd, r["item_id"])
        for subject in r["subjects"] or ["(no asset)"]:
            add("asset", subject, usd, r["item_id"])

    def table(kind: str) -> list[dict]:
        return sorted(({**v, "cost_usd": round(v["cost_usd"], 4), "cases": len(v["cases"])} for v in by[kind].values()),
                      key=lambda v: -v["cost_usd"])
    return {"window": [str(since) if since else None, str(until) if until else None],
            "total_usd": round(sum(float((r["ledger"] or {}).get("cost_usd") or 0) for r in rows), 4),
            "steps": len(rows), "by_agent": table("agent"), "by_work_type": table("work_type"),
            "by_asset": table("asset")}
