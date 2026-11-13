"""The eval gate on a skill version (P6.4d, #805): a draft goes live only if the pack's eval does not regress.

Publishing asks the same questions of the live version and of the candidate: the pack's answer keys for this
project's bundle (evals/<eval>.yaml in the bundle), the questions every version that went live drew from its cases,
and the draft's own. Each answer is
scored as the assistant eval scores one (fuzer.evals.evaluate: the share of the facts it must mention, ignoring case,
and that the specialists it expects answered; an answer from the wrong specialist counts half). A candidate that scores lower than the live version is refused with both scores; one
that scores the same or higher is published with them. A skill with no questions at all is published with the
evaluation saying it was not evaluated. Every question costs a model call per version.

    questions(conn, skill, version)        what the gate asks
    evaluate(conn, skill, version, answer) both versions' scores, and whether the candidate may go live
    publish(conn, skill, version, by)      the gate, then skill_versions.publish with the scores kept on the version

`answer(question, skill, playbook) -> {answer, specialists, tools, seconds, usage}` defaults to asking the operations
assistant with the skill loaded at that playbook (skill_versions.trying).
"""

from . import packs, skill_versions


class GateRefused(Exception):
    """A candidate that scores lower than the live version."""

    def __init__(self, result: dict):
        self.result = result
        super().__init__(f"the candidate scores {result['candidate']['score']:.2f} against the live version's "
                         f"{result['live']['score']:.2f} on {result['questions']} questions: it cannot be published")


def _ask(question: str, skill: str, playbook: str) -> dict:
    import asyncio

    from .. import agents

    with skill_versions.trying(skill, playbook):
        team = agents.team()
    return asyncio.run(agents.ask(question, team))


def questions(conn, skill: str, version: int) -> list[dict]:
    from ..core.pack import resolve

    versions = skill_versions.history(conn, skill)
    if not any(v["version"] == version for v in versions):
        raise ValueError(f"{skill} has no version {version}")
    keys = []
    if skill in packs.shipped():
        try:
            bundle = resolve(conn).name
        except Exception:  # a database with no project: no bundle, so none of the pack's answer keys apply
            conn.rollback()
            bundle = None
        keys = list((packs.load(skill).get("questions") or {}).get(bundle) or [])
    # a question drawn from a case stays in the eval once its version went live, so a later draft cannot lose it
    for v in sorted(versions, key=lambda v: v["version"]):
        if v["version"] == version or v.get("published_at"):
            keys += [q for q in v.get("questions") or [] if q not in keys]
    return keys


def _mentions(text: str, want: str | list[str]) -> bool:
    norm = text.lower().replace("³", "3")
    return any(w.lower().replace("³", "3") in norm for w in ([want] if isinstance(want, str) else want))


def score_answer(q: dict, out: dict) -> dict:
    """One answer against its key, as the assistant eval scores it: facts named, and the right specialist."""
    text, must = out.get("answer") or "", q.get("must_mention") or []
    missing = [m for m in must if not _mentions(text, m)]
    return {"question": q["question"], "mentions": round(1 - len(missing) / len(must), 2) if must else None,
            "missing": missing, "routed": set(q.get("specialists") or []) <= set(out.get("specialists") or []),
            "cost_usd": (out.get("usage") or {}).get("cost_usd") or 0}


def _score(qs: list[dict], skill: str, playbook: str, answer) -> dict:
    results = [score_answer(q, answer(q["question"], skill, playbook)) for q in qs]
    n = len(results) or 1
    score = sum((r["mentions"] or 0) * (1 if r["routed"] else 0.5) for r in results) / n
    return {"score": round(score, 3), "mentions": round(sum(r["mentions"] or 0 for r in results) / n, 3),
            "routed": round(sum(r["routed"] for r in results) / n, 3),
            "cost_usd": round(sum(r["cost_usd"] or 0 for r in results), 2),
            "missing": [{"question": r["question"], "missing": r["missing"]} for r in results if r["missing"]]}


def evaluate(conn, skill: str, version: int, answer=None) -> dict:
    """Ask the gate's questions of the live version and of `version`; `allowed` when the candidate scores no lower."""
    answer = answer or _ask
    qs = questions(conn, skill, version)
    live = skill_versions.live(conn, skill)
    cand = next(v for v in skill_versions.history(conn, skill) if v["version"] == version)
    conn.commit()  # end the reads before the model calls
    if not qs:
        return {"skill": skill, "version": version, "live_version": live["version"], "questions": 0,
                "allowed": True, "note": "not evaluated: the skill has no eval questions and the draft added none"}
    now, new = _score(qs, skill, live["playbook"], answer), _score(qs, skill, cand["playbook"], answer)
    return {"skill": skill, "version": version, "live_version": live["version"], "questions": len(qs),
            "live": now, "candidate": new, "allowed": new["score"] >= now["score"]}


def publish(conn, skill: str, version: int, by: str, answer=None) -> dict:
    """The gate, then the publish. Refused (GateRefused, with both scores) when the candidate scores lower."""
    skill_versions.check_publish(conn, skill, version, by)  # a machine or a non-draft is refused before any model call
    result = evaluate(conn, skill, version, answer)
    if not result["allowed"]:
        raise GateRefused(result)
    return skill_versions.publish(conn, skill, version, by, evaluation=result) | {"evaluation": result}
