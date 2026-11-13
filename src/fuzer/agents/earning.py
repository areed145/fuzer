"""How the specialists and their skills are performing (P7.4b, #955): which skills earn their keep, and which need a
change. Per detector is `ops.earning` (P7.4a).

Per skill (each one a specialist owns; shared skills are loaded by every agent, so nothing counts against them):

- **briefs**: the case briefs its pack wrote (P4.3), and their agent cost per case;
- **followed**: the latest verdict on each brief, whether the person who decided followed its proposal: yes, partly or
  no;
- **rated**: assistant answers engineers rated, counted against the skills of the specialists that worked on them, as
  the shortfalls are (`shortfalls`, P6.4a): useful or not;
- **edits**: its published versions (P6.4b), when the latest went live, and a drafted change waiting for review, with the
  case that holds that review (P6.4c).

A skill **needs a change** when at least `MIN_RATED` of its briefs and answers have a verdict and half or more fell short
(a brief partly or not followed, an answer rated not useful). It is **earning its keep** when fewer than a quarter fell
short. Too few verdicts say nothing yet.

Per specialist: its agent steps, the cases they were on, what they cost and the cost per case, from the ledgers (P4.4).

    skills(conn)    {"skills": [...], "specialists": [...]}
"""

from ..ops import skill_review
from . import ledger, shortfalls
from .capabilities import catalog

MIN_RATED = 5  # briefs and answers with a verdict before a skill's shares say anything
SHORT = 0.5  # the share fallen short at which a skill needs a change
GOOD = 0.25  # under this share fallen short, it earns its keep

_PRODUCED = """
    select e.data -> 'data' -> 'brief' ->> 'pack' as skill, count(*) as briefs, count(distinct e.item_id) as cases,
           coalesce(sum((e.data -> 'data' -> 'brief' -> 'ledger' ->> 'cost_usd')::numeric), 0) as usd
    from work_event e
    where e.kind = 'agent_result' and e.element = 'brief' and e.data -> 'data' -> 'brief' ? 'brief'
    group by 1
"""


def verdict(short: int, rated: int) -> str:
    """`needs_change`, `earning`, `mixed`, or `too_few` verdicts to say."""
    if rated < MIN_RATED:
        return "too_few"
    share = short / rated
    return "needs_change" if share >= SHORT else "earning" if share < GOOD else "mixed"


def _edits(conn) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in conn.execute("select skill, status, version, published_at from skill_version order by skill, version"):
        e = out.setdefault(r["skill"], {"published": 0, "live_version": None, "live_since": None, "waiting": None})
        if r["published_at"] is not None:
            e["published"] += 1
        if r["status"] == "published":
            e["live_version"], e["live_since"] = r["version"], r["published_at"]
        elif r["status"] == "draft":
            e["waiting"] = r["version"]
    reviews = {r["key"]: r["item_id"] for r in conn.execute(
        "select key, item_id from exception_record where type = %s and state = 'open'", (skill_review.SKILL_CHANGE,))}
    for path, e in out.items():
        e["review_case"] = reviews.get(skill_review.key_of(path, e["waiting"])) if e["waiting"] else None
    return out


def skills(conn) -> dict:
    """Per skill: briefs, how far they were followed, answer ratings, edits and brief cost per case; per specialist:
    agent steps, cases and cost per case."""
    owned = [s for s in catalog.SKILL_CATALOG if not s.shared]
    rows = {s.path: {"skill": s.path, "name": s.name, "owner": s.owner, "briefs": 0, "brief_cases": 0, "brief_usd": 0.0,
                     "followed": {"yes": 0, "partly": 0, "no": 0}, "rated": {"useful": 0, "not_useful": 0}}
            for s in owned}

    def row(path: str) -> dict:  # a pack not in the catalog (an installed one) still counts
        return rows.setdefault(path, {"skill": path, "name": path.rsplit("/", 1)[-1], "owner": path.split("/", 1)[0],
                                      "briefs": 0, "brief_cases": 0, "brief_usd": 0.0,
                                      "followed": {"yes": 0, "partly": 0, "no": 0},
                                      "rated": {"useful": 0, "not_useful": 0}})

    for r in conn.execute(_PRODUCED):
        if r["skill"]:
            x = row(r["skill"])
            x["briefs"], x["brief_cases"], x["brief_usd"] = r["briefs"], r["cases"], float(r["usd"])
    for r in conn.execute(shortfalls._BRIEFS, {"since": None}):
        if r["followed"] in ("yes", "partly", "no"):
            row(r["skill"])["followed"][r["followed"]] += 1
    for r in conn.execute(shortfalls._ANSWERS, {"since": None}):
        for path in shortfalls._owned(r["specialists"]):
            row(path)["rated"]["useful" if r["useful"] else "not_useful"] += 1
    edits = _edits(conn)
    out = []
    for x in rows.values():
        f, a = x["followed"], x["rated"]
        rated = sum(f.values()) + sum(a.values())
        short = f["partly"] + f["no"] + a["not_useful"]
        judged = sum(f.values())
        out.append({**x, "brief_usd": round(x["brief_usd"], 4),
                    "brief_cost_per_case": round(x["brief_usd"] / x["brief_cases"], 4) if x["brief_cases"] else None,
                    "followed_rate": round(f["yes"] / judged, 3) if judged else None,
                    "useful_rate": round(a["useful"] / sum(a.values()), 3) if sum(a.values()) else None,
                    "verdicts": rated, "short": short, "verdict": verdict(short, rated),
                    "edits": edits.get(x["skill"], {"published": 0, "live_version": None, "live_since": None,
                                                    "waiting": None, "review_case": None})})
    out.sort(key=lambda r: (-(r["briefs"] + r["verdicts"]), r["owner"], r["skill"]))
    spend = ledger.spend(conn)["by_agent"]
    specialists = [{"specialist": s["agent"], "steps": s["steps"], "cases": s["cases"], "cost_usd": s["cost_usd"],
                    "cost_per_case": round(s["cost_usd"] / s["cases"], 4) if s["cases"] else None} for s in spend]
    return {"skills": out, "specialists": specialists, "min_rated": MIN_RATED}
