"""P6.4a: where each skill fell short, from briefs not followed and answers rated not useful."""

import os
import tempfile

import pytest
from psycopg.types.json import Jsonb

from fuzer.agents import shortfalls
from fuzer.core import db

COMPRESSION, ANSWERING = "maintenance-integrity/compression", "orchestrator/answering"


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        yield c


@pytest.fixture(autouse=True)
def fresh(conn):
    for t in ("feedback", "assistant_answer", "work_event", "work_item"):
        conn.execute(f"delete from {t}")
    conn.commit()


def _verdict(conn, pack: str, followed: str, note: str | None = None, title: str = "surge on K-1") -> int:
    with conn.transaction():
        item = conn.execute("insert into work_item (type, title, subjects, created_by) values "
                            "('machine_health', %s, '{unit-co}', 'test') returning id", (title,)).fetchone()["id"]
        conn.execute("insert into work_event (item_id, kind, actor, data) values (%s, 'brief_verdict', 'olga', %s)",
                     (item, Jsonb({"pack": pack, "followed": followed, "note": note,
                                   "disposition": "plan an anti-surge valve check"})))
    return item


def _rated(conn, aid: str, specialists: list[str], useful: bool, comment: str | None = None) -> None:
    with conn.transaction():
        conn.execute("insert into assistant_answer (id, conversation_id, question, specialists) values (%s, 'c', %s, %s)",
                     (aid, f"question {aid}", specialists))
        conn.execute("insert into feedback (target, useful, comment, by_user) values (%s, %s, %s, 'ana')",
                     (f"answer:{aid}", useful, comment))


def test_briefs_not_followed_and_answers_rated_not_useful_count_against_their_skills(conn):
    short = _verdict(conn, COMPRESSION, "no", "the valve was fine; it was the suction strainer")
    partly = _verdict(conn, COMPRESSION, "partly", title="surge on K-2")
    _verdict(conn, COMPRESSION, "yes", title="surge on K-3")
    changed = _verdict(conn, COMPRESSION, "no", title="surge on K-4")
    with conn.transaction():  # the person changed their mind: only the latest verdict on a case counts
        conn.execute("insert into work_event (item_id, kind, actor, data) values (%s, 'brief_verdict', 'olga', %s)",
                     (changed, Jsonb({"pack": COMPRESSION, "followed": "yes"})))
    _rated(conn, "a1", ["maintenance-integrity"], False, "ignored the vibration trend")
    _rated(conn, "a2", ["maintenance-integrity"], True)
    _rated(conn, "a3", [], False, "no units")  # the orchestrator answered alone

    by = {s["skill"]: s for s in shortfalls.gather(conn)}
    c = by[COMPRESSION]
    assert (c["briefs"], c["briefs_short"]) == (4, 2)
    assert [(x["case"], x["followed"]) for x in c["shortfalls"] if x["kind"] == "brief"] == [(partly, "partly"),
                                                                                            (short, "no")]
    assert next(x for x in c["shortfalls"] if x.get("case") == short)["note"].startswith("the valve was fine")
    # an answer counts against every skill its specialist owns, so the compression pack sees it too
    assert (c["answers"], c["answers_short"]) == (2, 1)
    assert next(x for x in c["shortfalls"] if x["kind"] == "answer")["note"] == "ignored the vibration trend"
    assert c["short_pct"] == 50.0
    assert by[ANSWERING]["answers_short"] == 1 and by[ANSWERING]["shortfalls"][0]["question"] == "question a3"
    assert "maintenance-integrity/reliability" in by  # another skill of the same specialist, from the answer alone
    assert "production-surveillance/production-surveillance" not in by  # nothing fell short there


def test_the_api_lists_shortfalls_by_skill(conn):
    from fastapi.testclient import TestClient

    from fuzer.api import app

    _verdict(conn, COMPRESSION, "no", title="surge on K-9")  # stands on its own, whatever ran before
    rows = TestClient(app).get("/work/skill-shortfalls").json()
    assert rows[0]["shortfalls"] and COMPRESSION in {r["skill"] for r in rows}
    assert TestClient(app).get("/work/skill-shortfalls", params={"since": "2999-01-01T00:00:00"}).json() == []
