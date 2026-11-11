"""The assistant proposes; a person creates. A draft becomes at most one work item."""

import threading

import pytest

from fuzer.agents import service
from fuzer.agents.capabilities import ASKER
from fuzer.agents.capabilities.operations import draft_work_item
from fuzer.core import db
from fuzer.workflow import engine as work
from fuzer.workflow import work_drafts


@pytest.fixture
def conn(okan_db):
    with db.connect() as c:
        c.execute("delete from work_draft")
        c.commit()
        work.load_defaults(c)  # a confirmed draft runs as the standard task process (#509)
        yield c


def draft(conn, role="production_engineer", title="Review deferment", summary="Rate fell.", evidence=("fz-a",)):
    return work_drafts.create(conn, title, role, summary, list(evidence), "ana", ["fz-well"])


def items(conn):
    return conn.execute("select count(*) as n from work_item").fetchone()["n"]


def test_drafting_creates_no_work(conn):
    before = items(conn)
    row = draft(conn)
    assert row["item_id"] is None and row["by_user"] == "ana"
    assert items(conn) == before


def test_confirming_creates_one_item_in_the_queue_with_its_evidence(conn):
    row = draft(conn)
    out = work_drafts.confirm(conn, row["id"], "ana")
    item = conn.execute("select * from work_item where id = %s", (out["item_id"],)).fetchone()
    assert (item["role"], item["assignee"], item["created_by"], item["source"], item["subjects"]) == (
        "production_engineer", None, "ana", "assistant", ["fz-well"])
    assert {k: item["payload"]["start"][k] for k in ("summary", "evidence", "draft", "task_key")} == {
        "summary": "Rate fell.", "evidence": ["fz-a"], "draft": row["id"], "task_key": f"draft:{row['id']}"}
    step = conn.execute("select p.process_key, t.role from work_token t join work_process p on p.id = t.process_id "
                        "where t.item_id = %s and t.kind = 'user'", (item["id"],)).fetchone()
    assert step == {"process_key": "task", "role": "production_engineer"}  # the queue's SLA (#509)


def test_a_resend_returns_the_same_item(conn):
    row = draft(conn)
    before = items(conn)
    first = work_drafts.confirm(conn, row["id"], "ana")
    assert work_drafts.confirm(conn, row["id"], "ana") == {"item_id": first["item_id"], "created": False}
    assert items(conn) == before + 1


def test_two_confirms_at_once_create_one_item(conn):
    row = draft(conn)
    before = items(conn)
    results, barrier = [], threading.Barrier(4)

    def press():
        with db.connect() as c:
            barrier.wait()
            results.append(work_drafts.confirm(c, row["id"], "ana"))

    threads = [threading.Thread(target=press) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len({r["item_id"] for r in results}) == 1
    assert sum(r["created"] for r in results) == 1
    assert items(conn) == before + 1


@pytest.mark.parametrize("changes, message", [
    ({"role": "production-surveillance"}, "not a person's queue"),
    ({"role": "no_such_queue"}, "not a person's queue"),
    ({"title": "  "}, "title"),
    ({"title": "x" * 161}, "title"),
    ({"summary": ""}, "summary"),
    ({"evidence": ()}, "evidence"),
])
def test_a_bad_draft_is_refused(conn, changes, message):
    with pytest.raises(work.WorkError, match=message):
        draft(conn, **changes)


def test_a_role_removed_after_drafting_is_refused_at_confirmation(conn):
    conn.execute("insert into work_role (name, kind, updated_by) values ('short_lived', 'person', 'test')")
    conn.commit()
    row = draft(conn, role="short_lived")
    conn.execute("delete from work_role where name = 'short_lived'")
    conn.commit()
    with pytest.raises(work.WorkError, match="no longer exists"):
        work_drafts.confirm(conn, row["id"], "ana")
    assert work_drafts.get(conn, row["id"])["item_id"] is None


def ref(conn):
    return conn.execute("select fuzer_id from entity where kind = 'well' limit 1").fetchone()["fuzer_id"]


def test_the_tool_needs_someone_asking(conn):
    out = draft_work_item("Review", "production_engineer", "Rate fell.", [ref(conn)])
    assert "signed in" in out["error"]


def test_the_tool_gives_a_marker_and_writes_only_a_draft(conn):
    before = items(conn)
    token = ASKER.set("ana")
    try:
        out = draft_work_item("Review", "production_engineer", "Rate fell.", [ref(conn)])
    finally:
        ASKER.reset(token)
    assert out["marker"] == f"[[work-draft:{out['draft']}]]"
    assert work_drafts.get(conn, out["draft"])["by_user"] == "ana"
    assert items(conn) == before


def test_the_tool_reports_problems_to_the_agent(conn):
    token = ASKER.set("ana")
    try:
        assert "not a person's queue" in draft_work_item("Review", "sre", "Rate fell.", [ref(conn)])["error"]
        assert draft_work_item("Review", "production_engineer", "Rate fell.", ["no-such-record"]).get("error")
    finally:
        ASKER.reset(token)


def test_only_the_orchestrator_has_the_tool():
    from fuzer.agents.capabilities import REGISTRY

    assert REGISTRY["draft_work_item"].agents == ("orchestrator",)
    assert service.ASKER is ASKER
