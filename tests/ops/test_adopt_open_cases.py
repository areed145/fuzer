"""Cases opened as plain work items before every kind of work ran as a process (#509) move onto the process route()
now starts for them, still held by whoever held them and due when they were due."""

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from fuzer.core import db, work
from fuzer.core.exceptions import TYPES, ExceptionType, raise_exception
from fuzer.ops import routing


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


def _step(conn, item_id):
    return conn.execute("select p.process_key, t.role, t.assignee, t.due_at from work_token t join work_process p "
                        "on p.id = t.process_id where t.item_id = %s and t.kind = 'user' and t.state = 'waiting'",
                        (item_id,)).fetchone()


def test_open_plain_cases_move_onto_their_process(conn, monkeypatch):
    monkeypatch.setitem(TYPES, "leak", ExceptionType("leak", "Leak", role="role:operator"))
    e = raise_exception(conn, "leak", "L-1", "Drip at the manifold", "walkdown")
    conn.commit()
    # as an older Fuzer opened them: plain items, no process
    work.close_case(conn, e["item_id"], "cancelled", "test", "replaced by the plain case below")
    due = datetime(2026, 12, 1, 8, tzinfo=UTC)
    queued = work.open_case(conn, "leak", "Drip at the manifold", "walkdown", assignee="ivy", role="operator", due=due,
                            payload={"exception_id": e["id"], "drips": 12})["id"]
    conn.execute("update exception_record set item_id = %s where id = %s", (queued, e["id"]))
    asked = work.open_case(conn, "task", "Check the lube oil", "ana", role="engineer", source="assistant",
                           payload={"summary": "Oil looks dark.", "draft": "d_1"})["id"]
    named = work.open_case(conn, "lesson_review", "Review lesson 12", "system", assignee="alice")["id"]
    nobody = work.open_case(conn, "task", "Read the manual", "system", role="no_such_role")["id"]
    done = work.open_case(conn, "task", "Old", "system", role="engineer")["id"]
    work.close_case(conn, done, "done", "test")
    conn.commit()

    out = routing.adopt_open_cases(conn)
    assert out["moved"] == {"queue_item": 1, "task": 1} and not out["failed"], out
    assert out["stayed"]["it is for a named person"] == 1 and out["stayed"]["its role 'no_such_role' is no person role"] == 1

    q = _step(conn, queued)  # still ivy's, still due when it was
    assert (q["process_key"], q["role"], q["assignee"], q["due_at"]) == ("queue_item", "operator", "ivy", due)
    item = conn.execute("select type, assignee, role, due, payload from work_item where id = %s", (queued,)).fetchone()
    assert (item["type"], item["assignee"], item["role"], item["due"]) == ("leak", "ivy", "operator", due)
    assert item["payload"]["drips"] == 12  # what was raised stays on the case
    t = _step(conn, asked)  # in the engineers' queue, due by the role's SLA from now (or not at all)
    assert (t["process_key"], t["role"], t["assignee"]) == ("task", "engineer", None)
    assert t["due_at"] is None or t["due_at"] > datetime.now(UTC) - timedelta(minutes=1)
    for plain in (named, nobody, done):
        assert not conn.execute("select 1 from work_process where item_id = %s", (plain,)).fetchone()
    kinds = [r["kind"] for r in conn.execute("select kind from work_event where item_id = %s order by id", (queued,))]
    assert "adopted" in kinds

    assert routing.adopt_open_cases(conn)["moved"] == {}  # once
    # and the moved case still closes as a plain one did
    work.close_case(conn, queued, "done", "ivy", "fixed the drip")
    assert conn.execute("select state from work_item where id = %s", (queued,)).fetchone()["state"] == "done"
    conn.commit()
