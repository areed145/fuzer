"""Lessons learned: drafted from incidents + corrective work orders, owned by engineers once reviewed."""


import pytest

from fuzer.core.pack import load_pack
from fuzer.graph import knowledge, lessons, queries
from fuzer.ops import pipeline

PACK = load_pack("okan")


pytestmark = pytest.mark.sequence  # (#137) drafted, then reviewed and approved, then scoped


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    lessons.MIN_INCIDENTS = 1  # the small asset has few repeats
    pipeline.rebuild(c, PACK)
    yield c
    lessons.MIN_INCIDENTS = 2


def test_drafts_from_incidents_with_work_orders(conn):
    drafts = lessons.listing(conn, "suggested")
    assert drafts
    d = drafts[0]
    assert d["scope_kind"] in ("equipment_model", "equipment_class") and d["evidence"] and d["stats"]["incidents"] >= 1
    assert "Corrective work" in d["draft"]


def test_review_survives_rebuild_and_joins_search(conn):
    d = lessons.listing(conn, "suggested")[0]
    lessons.decide(conn, d["id"], "edit", "tester", "Check alignment first; then bearings.")
    lessons.decide(conn, d["id"], "approve", "tester")
    pipeline.rebuild(conn, PACK)
    approved = {x["id"]: x for x in lessons.listing(conn, "approved")}
    assert approved[d["id"]]["text"] == "Check alignment first; then bearings."
    hits = knowledge.search(conn, "alignment bearings", limit=20)
    assert any(h["kind"] == "lesson" for h in hits)


def test_lesson_scoped_to_its_model(conn):
    lesson = lessons.listing(conn, "approved")[0]
    scope = conn.execute("select fuzer_id from entity_key where kind = %s and key = %s",
                         (lesson["scope_kind"], lesson["scope_key"])).fetchone()["fuzer_id"]
    members = queries.members(conn, scope, limit=1)["items"]
    if members:  # any piece of equipment of that model / class finds the lesson in scoped search
        hits = knowledge.search(conn, "alignment bearings", members[0]["fuzer_id"])
        assert any(h["kind"] == "lesson" for h in hits)


def test_api(conn):
    from fastapi.testclient import TestClient

    from fuzer.api import app
    client = TestClient(app)
    rows = client.get("/lessons", params={"status": "all"}).json()
    assert rows
    assert client.post("/lessons/999999", json={"decision": "approve"}).status_code == 404


def test_three_closed_cases_of_one_failure_mode_on_one_model_draft_a_lesson(conn):
    """P6.2a: Fuzer's own closed cases are evidence too. Three cases closed as seal leaks on one compressor's model say
    what was done, how often each action held and how often the problem came back (a re-check said the fix did not
    hold on one; a repeat reopened another)."""
    tag = queries.resolve_ref(conn, PACK, "20-K-2200B")
    cases = []
    for title, activity, held in (("Seal gas leak, drive end", "1", True), ("Seal gas leak again", "1", False),
                                  ("Seal leak after restart", "2", None)):
        item = conn.execute("insert into work_item (type, title, subjects, state, created_by) values ('machine_health', "
                            "%s, %s, 'done', 'test') returning id", (title, [tag])).fetchone()["id"]
        conn.execute("insert into case_disposition (item_id, disposition, failure_mode, cause, activity, outcome, held, by) "
                     "values (%s, 'real', 'ELU', '3', %s, 'fixed', %s, 'user:ana')", (item, activity, held))
        cases.append(item)
    conn.execute("insert into case_repeat (again, first) values (%s, %s)", (cases[2], cases[2]))
    conn.execute("insert into work_item (type, title, subjects, state, created_by) values ('machine_health', 'Still open', "
                 "%s, 'open', 'test')", ([tag],))  # an open case is not evidence yet
    lessons.suggest(conn)
    (lesson,) = [x for x in lessons.listing(conn, None, 500) if x["failure_mode"] == "ELU"
                 and any(e.get("case") in cases for e in x["evidence"])]
    assert lesson["stats"]["cases"] == 3 and lesson["stats"]["fixed"] == 1
    assert "closed cases" in lesson["draft"] and "Replace (2 of 3; held in 1)" in lesson["draft"]
    assert "Repair (1 of 3; held in 0)" in lesson["draft"]
    assert {e["case"] for e in lesson["evidence"] if "case" in e} == set(cases)
    conn.commit()
