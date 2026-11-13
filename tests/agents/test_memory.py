"""The assistant's memory (#93): dated facts learned from rated answers, ended (never deleted) by a newer fact
that contradicts them, recalled for later questions, and corrected by engineers. Claude is a scripted fake."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from fuzer.agents import mcp_server, memory
from fuzer.core.pack import load_pack
from fuzer.graph import queries

PACK = load_pack("okan")
pytestmark = pytest.mark.sequence  # a story: later tests read the facts earlier ones learned


@pytest.fixture(scope="module")
def conn(okan_db):
    yield okan_db.conn


@pytest.fixture
def okan_pack(monkeypatch):
    monkeypatch.setattr(mcp_server, "PACK", PACK)


class FakeExtractor:
    """Answers each extraction request with the next scripted fact list; `replaces` may be a callable that picks
    ids from the remembered facts it was shown."""

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        payload = json.loads(kw["messages"][0]["content"])
        facts = []
        for f in self.scripts.pop(0):
            f = {"valid_from": None, "valid_to": None, "replaces": [], "sources": [], "lesson": None, **f}
            if callable(f["replaces"]):
                f["replaces"] = f["replaces"](payload["remembered"])
            facts.append(f)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(
            {"facts": facts}))], usage=SimpleNamespace(input_tokens=500, output_tokens=80, cache_read_input_tokens=0))


class PlainClaude:
    """The orchestrator answering straight away, recording what it was sent."""

    base_url = "https://fake.invalid"

    def __init__(self):
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        from anthropic.types.beta import BetaMessage

        self.requests.append(kw)
        return BetaMessage.model_validate({
            "id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "Noted."}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}})


def _answer(conn, aid: str, question: str, answer: str, ref: str | None = None):
    tools = [{"agent": "production", "tool": "well_performance", "args": {"ref": ref}}] if ref else []
    conn.execute("insert into assistant_answer (id, conversation_id, question, answer, tools, by_user) "
                 "values (%s, %s, %s, %s, %s, 'tester') on conflict (id) do nothing",
                 (aid, f"c-{aid}", question, answer, json.dumps(tools)))
    conn.commit()


def _well(conn) -> str:
    return queries.resolve_ref(conn, PACK, "OK-J07-05")


def test_learn_dated_facts_and_end_the_contradicted_one(conn):
    well = _well(conn)
    assert well
    _answer(conn, "m-a1", "Why is OK-J07-05 down on rate?", "OK-J07-05's gas lift valve is failed open (well test 2024-11-02).",
            "OK-J07-05")
    first = FakeExtractor([{"text": "OK-J07-05's gas lift valve failed open in November 2024.",
                            "subject": "Gas lift valve condition", "refs": ["OK-J07-05"], "valid_from": "2024-11-02",
                            "sources": ["EC:WT-OK-J07-05-20241102"]}])
    out = memory.learn(conn, PACK, "m-a1", by="tester", client=first)
    assert len(out["facts"]) == 1 and out["ended"] == []
    old = memory.get(conn, out["facts"][0])
    assert old["entity_ids"] == [well] and old["subject"] == "gas lift valve condition"
    assert str(old["valid_from"]) == "2024-11-02" and old["valid_to"] is None and old["status"] == "current"
    assert old["sources"][0] == "answer:m-a1" and old["episode"] == "m-a1"
    request = first.requests[0]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert json.loads(request["messages"][0]["content"])["entities"][0]["id"] == well

    # a later answer: the valve was replaced; Claude is shown the remembered fact and says it is replaced
    _answer(conn, "m-a2", "Is OK-J07-05 back?", "Slickline replaced OK-J07-05's valve on 2024-12-14.", "OK-J07-05")
    second = FakeExtractor([{"text": "OK-J07-05's gas lift valve was replaced by slickline on 2024-12-14.",
                             "subject": "gas lift valve condition", "refs": ["OK-J07-05"], "valid_from": "2024-12-14",
                             "replaces": lambda remembered: [f["id"] for f in remembered]}])
    out2 = memory.learn(conn, PACK, "m-a2", client=second)
    assert json.loads(second.requests[0]["messages"][0]["content"])["remembered"][0]["id"] == old["id"]
    assert out2["ended"] == [old["id"]]
    ended, new = memory.get(conn, old["id"]), memory.get(conn, out2["facts"][0])
    assert ended["status"] == "ended" and str(ended["valid_to"]) == "2024-12-14"
    assert ended["invalidated_by"] == new["id"] and ended["ended_by"] == "assistant"
    both = memory.facts(conn, [well], None)
    assert {f["id"] for f in both} >= {old["id"], new["id"]}  # both kept, with their dates

    # recall: only the current fact, for the well and anything that names it
    block = memory.context(conn, [well])
    assert f"fact:{new['id']}" in block["text"] and f"fact:{old['id']}" not in block["text"]
    assert "not source records" in block["text"] and block["ids"] == [new["id"]]


def test_a_replaced_fact_on_another_entity_is_left_alone(conn):
    other = queries.resolve_ref(conn, PACK, "20-K-2200B")
    _answer(conn, "m-b1", "Train B trips?", "20-K-2200B tripped on bearing wear.", "20-K-2200B")
    a = memory.learn(conn, PACK, "m-b1", client=FakeExtractor([{"text": "20-K-2200B trips were bearing wear.",
                                                                 "subject": "trip cause", "refs": ["20-K-2200B"]}]))
    fact = a["facts"][0]
    _answer(conn, "m-b2", "OK-J07-05?", "OK-J07-05 note.", "OK-J07-05")
    b = memory.learn(conn, PACK, "m-b2", client=FakeExtractor([{"text": "OK-J07-05 note.", "subject": "trip cause",
                                                                 "refs": ["OK-J07-05"], "replaces": [fact]}]))
    assert b["ended"] == [] and memory.get(conn, fact)["status"] == "current"
    assert memory.get(conn, fact)["entity_ids"] == [other]


def test_not_useful_answers_are_never_kept(conn):
    _answer(conn, "m-c1", "OK-J07-05 rate?", "Something wrong.", "OK-J07-05")
    conn.execute("insert into feedback (target, useful, by_user) values ('answer:m-c1', false, 'tester')")
    conn.commit()
    fake = FakeExtractor([{"text": "x", "subject": "y", "refs": ["OK-J07-05"]}])
    assert memory.learn(conn, PACK, "m-c1", client=fake)["skipped"] and fake.requests == []

    _answer(conn, "m-c2", "OK-J07-05 again?", "Another.", "OK-J07-05")
    out = memory.learn(conn, PACK, "m-c2", client=FakeExtractor([{"text": "OK-J07-05 z.", "subject": "z",
                                                                   "refs": ["OK-J07-05"]}]))
    assert memory.forget_episode(conn, "m-c2") == 1 and memory.get(conn, out["facts"][0]) is None


def test_lessons_go_to_the_review_queue(conn):
    rows = conn.execute("select e.src from edge e join entity_label l on l.fuzer_id = e.src "  # labelled equipment
                        "where e.rel = 'instance_of' order by e.src limit 1").fetchall()
    equipment = rows[0]["src"]
    label = conn.execute("select label from entity_label where fuzer_id = %s", (equipment,)).fetchone()["label"]
    _answer(conn, "m-d1", f"{label} seal?", f"{label} ({equipment}) leaks at the seal.")
    out = memory.learn(conn, PACK, "m-d1", client=FakeExtractor([{
        "text": f"{label} leaked at the mechanical seal.", "subject": "seal condition", "refs": [equipment],
        "lesson": {"failure_mode": "ELP-memory-test", "title": "Seal leaks after restarts",
                   "steps": ["Check seal flush flow", "Check alignment"]}}]))
    assert len(out["lessons"]) == 1
    lesson = conn.execute("select * from lesson where id = %s", (out["lessons"][0],)).fetchone()
    assert lesson["status"] == "suggested" and lesson["scope_kind"] in ("equipment_model", "equipment_class")
    assert "- Check alignment" in lesson["draft"] and lesson["stats"]["from_conversation"]


def test_recall_tool(conn, okan_pack):
    out = mcp_server.recall("OK-J07-05")
    assert out["facts"] and "ended" not in out
    with_ended = mcp_server.recall("OK-J07-05", include_ended=True)
    assert with_ended["ended"] and all("id" not in f for f in with_ended["ended"])  # history, never cited
    assert any(f["replaced_by"] and f["replaced_by"].startswith("fact:") for f in with_ended["ended"])
    assert "learned from conversations" in out["note"]


def test_a_new_question_gets_the_remembered_facts(conn, okan_pack):
    agents = pytest.importorskip("fuzer.agents")
    fake = PlainClaude()
    team = agents.team(agents.client(anthropic_client=fake))
    out = asyncio.run(agents.ask("What's going on with OK-J07-05?", team))
    current = [f["id"] for f in memory.facts(conn, memory.units(conn, _well(conn)), "current")]
    assert out["recalled"] and set(out["recalled"]) <= set(current)
    turn = fake.requests[0]["messages"][-1]["content"]
    assert turn[0]["text"] == "What's going on with OK-J07-05?"  # the question first, the facts after it
    assert "Known from earlier conversations" in turn[-1]["text"] and f"fact:{out['recalled'][0]}" in turn[-1]["text"]
    out = asyncio.run(agents.ask("What's going on with OK-J07-05?", team, recall=False))
    assert out["recalled"] == []


def test_people_stay_in_charge_over_the_api(conn, okan_pack, monkeypatch):
    from fastapi.testclient import TestClient

    from fuzer.api import app

    _answer(conn, "m-e1", "OK-J07-05 choke?", "OK-J07-05's choke was set to 40/64 on 2025-02-01.", "OK-J07-05")
    monkeypatch.setattr(memory, "_client", lambda: FakeExtractor(
        [{"text": "OK-J07-05's choke was set to 40/64.", "subject": "choke setting", "refs": ["OK-J07-05"],
          "valid_from": "2025-02-01"}],
        [{"text": "OK-J07-05 flows to the test separator.", "subject": "routing", "refs": ["OK-J07-05"]}]))
    conn.rollback()
    with TestClient(app) as client:
        assert client.post("/feedback", json={"target": "answer:m-e1", "useful": True, "by": "tester"}).status_code == 200
        for _ in range(50):  # learned after the response
            mine = [f for f in client.get("/assistant/facts", params={"ref": "OK-J07-05"}).json() if f["episode"] == "m-e1"]
            if mine:
                break
            time.sleep(0.05)
        fact = mine[0]
        assert fact["text"] == "OK-J07-05's choke was set to 40/64." and fact["by_user"] == "tester"

        fixed = client.patch(f"/assistant/facts/{fact['id']}", json={"text": "OK-J07-05's choke was set to 42/64.",
                                                                     "by": "engineer"}).json()
        assert fixed["text"].endswith("42/64.") and fixed["edited_by"] == "engineer"
        ended = client.post(f"/assistant/facts/{fact['id']}/end", json={"valid_to": "2025-06-01", "by": "engineer"}).json()
        assert ended["status"] == "ended" and ended["valid_to"] == "2025-06-01"
        assert client.post(f"/assistant/facts/{fact['id']}/end", json={"by": "engineer"}).status_code == 404

        monkeypatch.setattr(memory, "_client", lambda: FakeExtractor(
            [{"text": "OK-J07-05 flows to the test separator.", "subject": "routing", "refs": ["OK-J07-05"]}]))
        r = client.post("/assistant/answers/m-e1/remember", json={"statement": "OK-J07-05 is on the test separator.",
                                                                  "by": "engineer"}).json()
        assert r["items"][0]["text"] == "OK-J07-05 flows to the test separator."
        assert client.post("/assistant/answers/nope/remember", json={"statement": "x"}).status_code == 404
        assert client.delete(f"/assistant/facts/{r['facts'][0]}").status_code == 200
        assert client.delete(f"/assistant/facts/{r['facts'][0]}").status_code == 404

        # rated not useful later: the untouched facts from it go, the engineer's ended one stays
        client.post("/feedback", json={"target": "answer:m-e1", "useful": False, "by": "other"})
        assert client.get("/assistant/facts", params={"ref": "OK-J07-05", "status": "ended"}).json()


class CitingClaude(PlainClaude):
    """The orchestrator citing the first remembered fact it was given."""

    async def create(self, **kw):
        import re

        from anthropic.types.beta import BetaMessage

        self.requests.append(kw)
        last = kw["messages"][-1]["content"]
        ids = re.findall(r"fact:(\d+)", " ".join(b.get("text", "") for b in last if isinstance(b, dict)))
        text = f"Per fact:{ids[0]} slickline replaced it on 2025-12-14; check the orifice." if ids else "No idea."
        return BetaMessage.model_validate({
            "id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}})


def test_memory_eval(conn, okan_pack):
    agents = pytest.importorskip("fuzer.agents")
    from fakeplant import okan
    from fuzer.evals import evaluate

    extractor = FakeExtractor(
        [{"text": "OK-J07-05's gas lift valve failed open.", "subject": "gas lift valve condition",
          "refs": ["OK-J07-05"], "valid_from": "2025-03-10"}],
        [{"text": "Slickline replaced OK-J07-05's gas lift valve on 2025-12-14.", "subject": "gas lift valve condition",
          "refs": ["OK-J07-05"], "valid_from": "2025-12-14",
          "replaces": lambda remembered: [f["id"] for f in remembered if "failed open" in f["text"]]}],
        [{"text": "20-K-2200B's thrust bearing was starved by the wrong lube oil orifice plate.",
          "subject": "trip cause", "refs": ["20-K-2200B"], "valid_from": "2024-03-01"}])
    before = conn.execute("select count(*) as n from assistant_fact").fetchone()["n"]
    team = agents.team(agents.client(anthropic_client=CitingClaude()))
    res = evaluate.memory(conn, PACK, okan.MEMORY_QUESTIONS, team=team, learn_client=extractor)
    valve, train = res["results"]
    assert valve["learned"] == 2 and valve["ended"] >= 1 and valve["recalled"] and valve["used"]
    assert valve["cited_ended"] == [] and train["recalled"] and train["used"]
    assert res["recalled"] == 1.0 and res["cited_ended"] == 0
    # nothing the eval stored is left behind
    assert conn.execute("select count(*) as n from assistant_fact").fetchone()["n"] == before
    assert not conn.execute("select 1 from assistant_answer where id like 'eval-%'").fetchone()
