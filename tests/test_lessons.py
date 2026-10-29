"""Lessons learned: drafted from incidents + corrective work orders, owned by engineers once reviewed."""

import os
import tempfile

import pytest

from fakeplant import offshore
from fuzer import db, ingest, knowledge, lessons, pipeline, queries
from fuzer.pack import load_pack

PACK = load_pack()


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    offshore.generate(data)
    lessons.MIN_INCIDENTS = 1  # the small asset has few repeats
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
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
