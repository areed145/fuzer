"""Where a skill fell short (P6.4a, #805): the signals that motivate a change to its playbook, gathered per skill.

    briefs    a case's specialist brief whose proposal the person who decided partly followed or did not follow
              (P4.3). It counts against the pack that wrote the brief.
    answers   an assistant answer an engineer rated not useful (`feedback` on answer:<id>). It counts against the
              skills owned by the specialists that worked on it. An answer the orchestrator gave alone counts
              against its answering skill.

    gather(conn, since=None)    every skill with a shortfall, the most first: its counts, the share of its briefs and
                                rated answers that fell short, and each shortfall with its case or answer and the
                                person's note

A shortfall is a signal for the skill's owner to read, not a verdict on the skill: one brief the person did not follow
may be the person's call on facts the specialist could not see.
"""

from .capabilities import catalog

_BRIEFS = """
    select e.item_id, e.actor, e.at, e.data ->> 'pack' as skill, e.data ->> 'followed' as followed,
           e.data ->> 'note' as note, e.data ->> 'disposition' as disposition, i.type, i.title
    from work_event e join work_item i on i.id = e.item_id
    where e.kind = 'brief_verdict' and e.data ->> 'pack' is not null
      and (%(since)s::timestamptz is null or e.at >= %(since)s)
      and e.id = (select max(v.id) from work_event v where v.item_id = e.item_id and v.kind = 'brief_verdict')
"""

_ANSWERS = """
    select a.id, a.question, a.specialists, f.useful, f.comment, f.by_user, f.created_at as at
    from feedback f join assistant_answer a on f.target = 'answer:' || a.id
    where (%(since)s::timestamptz is null or f.created_at >= %(since)s)
"""


def _owned(specialists: list[str]) -> list[str]:
    """The skills an answer counts against: those owned by the specialists that worked on it, else the answering
    skill of the orchestrator that gave it alone."""
    mine = [s.path for s in catalog.SKILL_CATALOG if not s.shared and s.owner in set(specialists or [])]
    return mine or [s.path for s in catalog.SKILL_CATALOG if s.owner == "orchestrator"]


def gather(conn, since: str | None = None) -> list[dict]:
    """Every skill with a shortfall since `since` (all time when None), the most shortfalls first."""
    skills: dict[str, dict] = {}

    def skill(path: str) -> dict:
        return skills.setdefault(path, {"skill": path, "briefs": 0, "briefs_short": 0, "answers": 0,
                                        "answers_short": 0, "shortfalls": []})

    for r in conn.execute(_BRIEFS, {"since": since}):
        s = skill(r["skill"])
        s["briefs"] += 1
        if r["followed"] in ("partly", "no"):
            s["briefs_short"] += 1
            s["shortfalls"].append({"kind": "brief", "case": r["item_id"], "title": r["title"], "type": r["type"],
                                    "followed": r["followed"], "proposed": r["disposition"], "note": r["note"],
                                    "by": r["actor"], "at": r["at"].isoformat()})
    for r in conn.execute(_ANSWERS, {"since": since}):
        for path in _owned(r["specialists"]):
            s = skill(path)
            s["answers"] += 1
            if not r["useful"]:
                s["answers_short"] += 1
                s["shortfalls"].append({"kind": "answer", "answer": r["id"], "question": r["question"],
                                        "note": r["comment"], "by": r["by_user"], "at": r["at"].isoformat()})
    out = []
    for s in skills.values():
        if not s["shortfalls"]:
            continue
        rated = s["briefs"] + s["answers"]
        s["short_pct"] = round((s["briefs_short"] + s["answers_short"]) / rated * 100, 1)
        s["shortfalls"].sort(key=lambda x: x["at"], reverse=True)
        out.append(s)
    return sorted(out, key=lambda s: (-len(s["shortfalls"]), s["skill"]))
