"""A drafted change to a skill as work for its owner (P6.4c, #805).

An agent drafts a change to a skill's playbook from where the skill fell short (fuzer.agents.skill_drafts). The draft
opens here as `skill_change` work for the engineer who owns the skill, keyed by the skill and the draft's version,
with the cases behind it and the eval questions drawn from them. The agent never publishes: the work closes when a
person publishes the draft or sets it aside.

    open_review(conn, skill, version, title, by, cases, questions, note)   the work, opened (or updated)
    close_review(conn, skill, version, by, reason)                         the work, cleared
"""

from ..core.exceptions import TYPES, clear_exception, exception_type, raise_exception
from ..core.findings import DETERMINISTIC, Finding, fact


def _type():
    return TYPES.get("skill_change") or exception_type(
        "skill_change", "Skill change to review", role="role:engineer", audience=("ioc",), joins=False,
        description="A change an agent drafted to a skill's playbook, from the briefs people did not follow and the "
                    "answers they rated not useful, with the cases behind it and new eval questions drawn from them. "
                    "Read it against the cases, then publish it or set it aside. Publishing runs the pack's eval.")


SKILL_CHANGE = _type().name


def key_of(skill: str, version: int) -> str:
    return f"{skill} v{version}"


def open_review(conn, skill: str, version: int, title: str, by: str, cases: list[dict] | None = None,
                questions: list[dict] | None = None, note: str | None = None) -> dict:
    cases, questions = cases or [], questions or []
    found = Finding(
        "skills.change", DETERMINISTIC, method="Shortfalls gathered per skill, a change drafted by its agent",
        data={"skill": skill, "version": version, "cases": cases, "questions": questions},
        items=(fact("Skill", skill), fact("Draft version", version), fact("Why", note),
               fact("Cases behind it", len(cases) or None), fact("New eval questions", len(questions) or None),
               *(fact(f"Case: {c.get('kind')} {c.get('case') or c.get('answer') or ''}".strip(),
                      c.get("note") or c.get("title") or c.get("question")) for c in cases[:6])))
    return raise_exception(conn, SKILL_CHANGE, key_of(skill, version), title, by, source="skills", refs=[],
                           priority=0.5, finding=found)


def close_review(conn, skill: str, version: int, by: str, reason: str) -> None:
    clear_exception(conn, SKILL_CHANGE, key_of(skill, version), by, reason=reason)
