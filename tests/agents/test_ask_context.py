"""P4.5a (#750): Ask opened from a case or an asset carries it, so the person never restates it."""

import asyncio
import os
import tempfile

import pytest
from psycopg.types.json import Jsonb

from fuzer import agents
from fuzer.agents import brief, context, history, service
from fuzer.core import db


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        with c.transaction():
            c.execute("insert into entity (fuzer_id, kind) values ('fz-pump-1', 'equipment')")
        yield c


def _case(conn) -> int:
    with conn.transaction():
        item = conn.execute("insert into work_item (type, title, subjects, payload, created_by) values ('machine_health', "
                            "'Pump vibration rising', '{fz-pump-1}', %s, 'test') returning id",
                            (Jsonb({"detector": "vibration", "peak_mm_s": 7.1}),)).fetchone()["id"]
        conn.execute("insert into work_event (item_id, kind, actor, element, data) values (%s, 'agent_result', "
                     "'agent:maintenance-integrity', 'brief', %s)", (item, Jsonb({"step": "brief", "data": {"brief": {"brief": {
                         "what_happened": "Vibration on the drive end doubled in a week.",
                         "proposal": {"disposition": "check the coupling alignment", "why": "1x dominant"},
                         "could_not_check": ["no spectrum"]}}}})))
    return item


def test_a_case_context_carries_its_subjects_what_raised_it_and_its_brief(conn):
    item = _case(conn)
    assert brief.latest(conn, item)["brief"]["what_happened"]
    ctx = context.about(conn, item)
    assert ctx["item_id"] == item and ctx["subject"] == "fz-pump-1" and ctx["label"] == f"case {item}: Pump vibration rising"
    for said in ("Pump vibration rising", "fz-pump-1", "peak_mm_s", "doubled in a week", "check the coupling alignment",
                 "a person decides", "no spectrum"):
        assert said in ctx["text"], said
    asset = context.about(conn, subject="fz-pump-1")
    assert asset["item_id"] is None and "fz-pump-1" in asset["text"] and "equipment" in asset["text"]
    assert context.about(conn) is None and context.framed("why?", None) == "why?"
    with pytest.raises(context.Unknown):
        context.about(conn, 99999999)
    with pytest.raises(context.Unknown):
        context.about(conn, subject="fz-nothing")


def test_the_first_question_carries_the_case_and_a_follow_up_keeps_the_conversation(conn, monkeypatch):
    item = _case(conn)

    class Team:
        def create_session(self, **kwargs):
            return object()

    sent = []

    async def ask(question, *args):
        sent.append(question)
        return {"answer": "ok", "usage": {}, "seconds": 0, "specialists": [], "tools": [], "recalled": []}

    monkeypatch.setattr(service, "_jobs", {})
    monkeypatch.setattr(service, "_tasks", set())
    monkeypatch.setattr(service, "team", lambda: Team())
    monkeypatch.setattr(service, "_purge", lambda conn: None)
    monkeypatch.setattr(service, "_save", lambda job, by: None)
    monkeypatch.setattr(agents, "ask", ask)

    async def exercise():
        first = service.start("Is it the bearing?", by="user:test", item_id=item)
        await asyncio.gather(*service._tasks)
        again = service.start("And the seal?", first["conversation_id"], by="user:test", item_id=item)
        await asyncio.gather(*service._tasks)
        return first, again

    first, again = asyncio.run(exercise())
    assert again["conversation_id"] == first["conversation_id"]
    assert "Pump vibration rising" in sent[0] and sent[0].endswith("Question: Is it the bearing?")
    assert sent[1] == "And the seal?"  # the history already holds the context
    assert service.status(first["job_id"])["question"] == "Is it the bearing?"  # the page shows what was asked
    saved = history.conversation(conn, first["conversation_id"])
    assert saved["item_id"] == item and saved["subject"] == "fz-pump-1" and saved["title"] == "Is it the bearing?"
    with pytest.raises(context.Unknown):
        service.start("Lost?", by="user:test", item_id=99999999)


def test_a_person_attaches_an_answer_and_its_charts_to_the_case_in_one_action(conn):
    """P4.5b (#761)."""
    item = _case(conn)
    with conn.transaction():
        history.ensure(conn, "conv-attach", "user:ana", "Is it the bearing?", item, "fz-pump-1")
        conn.execute("insert into assistant_answer (id, conversation_id, question, answer, by_user) values "
                     "('ans-1', 'conv-attach', 'Is it the bearing?', %s, 'user:ana')",
                     ("The 1x peak points at the coupling.\n\n[[figure:f_0a1b]]\n\nAgain: [[figure:f_0a1b]] [[figure:f_2c3d]]",))
        conn.execute("insert into assistant_answer (id, conversation_id, question, error) values "
                     "('ans-failed', 'conv-attach', 'And?', 'timed out')")
    out = context.attach(conn, "ans-1", item, "user:ana")
    assert out["figures"] == ["f_0a1b", "f_2c3d"] and out["conversation_id"] == "conv-attach"
    e = conn.execute("select actor, data from work_event where item_id = %s and kind = 'attached'", (item,)).fetchall()
    assert len(e) == 1 and e[0]["actor"] == "user:ana" and "coupling" in e[0]["data"]["answer"]
    assert e[0]["data"]["question"] == "Is it the bearing?"
    for who in ("agent:maintenance-integrity", "system", "anonymous"):
        with pytest.raises(context.Refused):
            context.attach(conn, "ans-1", item, who)
    with pytest.raises(context.Unknown):
        context.attach(conn, "ans-failed", item, "user:ana")  # nothing to attach from a failed answer
    with pytest.raises(context.Unknown):
        context.attach(conn, "ans-1", 99999999, "user:ana")
    state = conn.execute("select state from work_item where id = %s", (item,)).fetchone()["state"]
    assert state == "open"  # attaching changes nothing else on the case
    conn.commit()  # the route's connection commits after it


def test_a_case_and_its_asset_reach_their_incidents_studies_and_lessons(okan_db):
    """P4.5c: incidents on the asset, studies run on it, lessons for its equipment model or class."""
    from fuzer.core.pack import load_pack
    from fuzer.graph import knowledge, queries

    c = okan_db.conn
    k = queries.resolve_ref(c, load_pack("okan"), "20-K-2200B")
    key = c.execute("select kind, key from entity_key where fuzer_id = any(%s) and kind in ('equipment_model', "
                    "'equipment_class') limit 1", (list(knowledge.scope(c, k)),)).fetchone()
    assert key, "the compressor has a model or class to scope lessons by"
    try:
        c.execute("insert into lesson (scope_kind, scope_key, failure_mode, title, draft, status) values "
                  "(%s, %s, 'VIB', 'Check the coupling first', '- check alignment', 'approved')", (key["kind"], key["key"]))
        c.execute("insert into lesson (scope_kind, scope_key, failure_mode, title, draft) values "
                  "('equipment_model', 'nothing-like-it', 'VIB', 'Elsewhere', '-')")
        c.execute("insert into study (kind, params, status, summary, created_by) values ('correlate', %s, 'done', "
                  "'suction pressure leads vibration', 'user:ana')", (Jsonb({"target": k}),))
        r = context.related(c, [k, k, ""])
        assert r["subjects"] == [k]
        assert len(r["incidents"]) >= 1 and len({i["id"] for i in r["incidents"]}) == len(r["incidents"])
        starts = [str(i["start"]) for i in r["incidents"]]
        assert starts == sorted(starts, reverse=True)
        assert [s["summary"] for s in r["studies"]] == ["suction pressure leads vibration"]
        assert [lsn["title"] for lsn in r["lessons"]] == ["Check the coupling first"]
        assert context.related(c, []) == {"subjects": [], "incidents": [], "studies": [], "lessons": []}
    finally:
        c.rollback()
