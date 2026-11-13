"""P6.4c: a skill that fell short twice gets a change drafted by its specialist, as work for the skill's owner. The
agent never publishes; a person publishes or sets the draft aside, and the work clears."""

import json
import os
import tempfile

import pytest
from psycopg.types.json import Jsonb

from fuzer.agents import skill_drafts, skill_versions
from fuzer.core import db

SKILL = "maintenance-integrity/compression"
STRAINER = "\n\nBefore a surge diagnosis, check the suction strainer differential pressure.\n"


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        from fuzer.workflow import engine

        engine.load_defaults(c)
        c.commit()
        yield c


@pytest.fixture(autouse=True)
def fresh(conn):
    conn.execute("truncate skill_version, feedback, assistant_answer, exception_record, work_item cascade")
    conn.commit()


def _short(conn, n: int) -> None:
    for i in range(n):
        with conn.transaction():
            item = conn.execute("insert into work_item (type, title, subjects, created_by) values ('machine_health', "
                                "%s, '{unit-co}', 'test') returning id", (f"surge on K-{i}",)).fetchone()["id"]
            conn.execute("insert into work_event (item_id, kind, actor, data) values (%s, 'brief_verdict', 'olga', %s)",
                         (item, Jsonb({"pack": SKILL, "followed": "no", "note": "it was the suction strainer",
                                       "disposition": "plan an anti-surge valve check"})))


def _ask(playbook: str, questions=({"question": "Why is K-1 surging?", "must_mention": ["strainer"]},)):
    asked = []

    def ask(q):
        asked.append(q)
        return {"answer": "Revised.\n```json\n" + json.dumps({"playbook": playbook, "questions": list(questions),
                                                             "why": "Two briefs missed a blocked strainer."}) + "\n```",
                "usage": {}}
    return ask, asked


def test_a_skill_that_fell_short_twice_gets_a_drafted_change_as_work_for_its_owner(conn):
    _short(conn, 1)
    assert SKILL not in {s["skill"] for s in skill_drafts.due(conn)}  # once is the person's call
    _short(conn, 1)
    assert SKILL in {s["skill"] for s in skill_drafts.due(conn)}

    ask, asked = _ask(skill_versions.shipped(SKILL) + STRAINER)
    out = skill_drafts.prepare(conn, SKILL, ask)
    assert out["version"] == 1 and out["cases"] == 2 and out["questions"] == 1 and out["item_id"], out
    assert "it was the suction strainer" in asked[0] and "<playbook>" in asked[0]
    v = skill_versions.history(conn, SKILL)[0]
    assert v["status"] == "draft" and v["created_by"] == "agent:maintenance-integrity" and len(v["cases"]) == 2
    assert v["questions"][0]["specialists"] == ["maintenance-integrity"] and skill_versions.live(conn, SKILL)["version"] == 0
    work = conn.execute("select * from exception_record where type = 'skill_change' and state = 'open'").fetchone()
    assert work["key"] == f"{SKILL} v1" and work["subjects"] == []
    assert conn.execute("select role from work_item where id = %s", (work["item_id"],)).fetchone()["role"] == "engineer"

    assert SKILL not in {s["skill"] for s in skill_drafts.due(conn)}  # a draft is waiting
    assert skill_drafts.prepare(conn, SKILL, ask)["missing"].startswith("no draft is due")
    with pytest.raises(skill_versions.VersionRefused):  # the agent never publishes
        skill_versions.publish(conn, SKILL, 1, "agent:maintenance-integrity")
    skill_versions.publish(conn, SKILL, 1, "olga")
    assert conn.execute("select state from exception_record where id = %s", (work["id"],)).fetchone()["state"] != "open"
    assert SKILL not in {s["skill"] for s in skill_drafts.due(conn)}  # the shortfalls came before the live version


def test_a_draft_set_aside_clears_its_work_and_a_bad_draft_never_lands(conn):
    _short(conn, 2)
    ask, _ = _ask(skill_versions.shipped(SKILL) + "\n\nCall `plot_decline(well)` first.\n")
    out = skill_drafts.prepare(conn, SKILL, ask)
    assert out["missing"].startswith("the draft was refused") and skill_versions.history(conn, SKILL)[0]["version"] == 0
    same, _ = _ask(skill_versions.shipped(SKILL))
    assert skill_drafts.prepare(conn, SKILL, same)["missing"] == "the specialist proposed no change"

    ask, _ = _ask(skill_versions.shipped(SKILL) + STRAINER)
    out = skill_drafts.next_one(conn, ask)
    assert out["skill"] == SKILL and out["version"] == 1
    skill_versions.set_aside(conn, SKILL, 1, "olga", "the strainer was a one-off")
    assert skill_versions.history(conn, SKILL)[0]["status"] == "refused"
    assert conn.execute("select count(*) as n from exception_record where type = 'skill_change' and state = 'open'"
                        ).fetchone()["n"] == 0
