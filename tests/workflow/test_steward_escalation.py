"""Overdue steward work escalates inside the steward team, never to the ops lead's queue (#1346), and steward
questions that escalated there before go back to the steward queue when the schema is applied."""

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from fuzer.core import db
from fuzer.workflow import engine, roles

pytestmark = pytest.mark.sequence

T0 = datetime(2026, 11, 20, 8, tzinfo=UTC)


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        engine._parsed.clear()
        roles._seen = None
        engine.load_defaults(c)
        engine.set_person(c, "casey", ["data_steward"])
        engine.set_person(c, "riley", ["ops_lead"])
        c.commit()
        yield c


def question(conn) -> int:
    n = conn.execute("select count(*) as n from work_item").fetchone()["n"]
    data = {"role": "data_steward", "case_type": "curve_to_confirm", "exception_id": n + 1}
    return engine.start(conn, "queue_item", data, "stewardship",
                        title="Confirm the CC30 curve", now=T0)["item_id"]


def step(conn, item_id):
    return conn.execute("select t.*, i.role as item_role from work_token t join work_item i on i.id = t.item_id "
                        "where t.item_id = %s and t.kind = 'user' and t.state = 'waiting'", (item_id,)).fetchone()


def test_an_overdue_steward_question_stays_with_the_stewards(conn):
    item = question(conn)
    assert step(conn, item)["due_at"] == T0 + timedelta(days=1)  # the steward role's P1D
    engine.tick(conn, now=T0 + timedelta(days=2))
    t = step(conn, item)
    assert (t["role"], t["item_role"]) == ("data_steward", "data_steward") and t["escalated_at"] is not None


def test_steward_questions_escalated_to_the_ops_lead_go_back(conn):
    # as a deploy before #1346 left it: the seeded steward role escalating to the ops lead, and questions moved there
    conn.execute("update work_role set escalate_to = 'ops_lead', updated_by = 'seed' where name = 'data_steward'")
    conn.commit()
    left, taken = question(conn), question(conn)
    engine.tick(conn, now=T0 + timedelta(days=2))
    assert step(conn, left)["role"] == step(conn, taken)["role"] == "ops_lead"
    conn.execute("update work_token set assignee = 'riley' where id = %s", (step(conn, taken)["id"],))  # riley took it
    conn.commit()

    conn.execute(db.schema_sql())  # what the deploy with this change runs
    conn.commit()
    assert roles.role(conn, "data_steward")["escalate_to"] is None
    t = step(conn, left)
    assert (t["role"], t["item_role"], t["assignee"]) == ("data_steward", "data_steward", None)
    assert t["escalated_at"] is not None  # still marked escalated
    assert step(conn, taken)["role"] == "ops_lead"  # one the ops lead took stays theirs
    engine.tick(conn, now=T0 + timedelta(days=5))
    assert step(conn, left)["role"] == "data_steward"


def test_a_site_that_chose_the_ops_lead_keeps_it(conn):
    roles.set_role(conn, "data_steward", "olga", escalate_to="ops_lead")
    conn.commit()
    conn.execute(db.schema_sql())
    conn.commit()
    assert roles.role(conn, "data_steward")["escalate_to"] == "ops_lead"
