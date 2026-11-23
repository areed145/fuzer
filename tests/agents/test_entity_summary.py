"""An entity page's summary: written once from the rolled-up facts on a small model, kept a day, read by everyone."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.agents import summary
from fuzer.api import entities
from fuzer.ops import beneath

pytestmark = pytest.mark.integration


class FakeSummariser:
    def __init__(self):
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        facts = json.loads(kw["messages"][0]["content"])
        out = {"headline": f"{facts['label']}: {facts['open_work']['total']} open",
               "points": [{"text": "Most work sits on the compression trains.", "cites": [facts["id"]]}]}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(out))],
                               usage=SimpleNamespace(input_tokens=900, output_tokens=60, cache_read_input_tokens=0))


def _id(conn, label):
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def test_the_facts_roll_up_what_is_beneath(okan_db):
    conn = okan_db.conn
    facts = beneath.picture(conn, _id(conn, "OKAN-20"))
    assert facts["kind"] == "area" and facts["sits_in"] == ["OKAN"]
    assert facts["beneath"].get("tag") and facts["open_work"]["total"] >= len(facts["open_work"]["top"])
    assert len(json.dumps(facts, default=str)) < 12_000, "small enough for one short call"
    conn.rollback()


def test_a_summary_is_written_once_and_kept_for_a_day(okan_db):
    conn = okan_db.conn
    ggcp = _id(conn, "OKAN-20")
    fake = FakeSummariser()
    assert summary.fresh(conn, ggcp) is None
    out = summary.write(conn, ggcp, "user:test", client=fake)
    assert out["headline"].startswith("OKAN-20") and out["points"][0]["cites"] == [ggcp]
    assert fake.requests[0]["model"] == "claude-haiku-5-5"
    assert fake.requests[0]["output_config"]["effort"] == "low"
    assert summary.fresh(conn, ggcp)["headline"] == out["headline"]
    conn.execute("update entity_summary set created_at = created_at - %s", (timedelta(hours=25),))
    conn.commit()
    assert summary.fresh(conn, ggcp) is None and summary.latest(conn, ggcp)["stale"]


def test_reading_never_calls_a_model_and_a_fresh_one_is_not_rewritten(okan_db, monkeypatch):
    app = FastAPI()
    app.include_router(entities.router)
    client = TestClient(app)
    conn = okan_db.conn
    well = _id(conn, "OK-J03-01")
    monkeypatch.setattr(summary.llm, "client", lambda *a, **k: pytest.fail("reading called a model"))
    assert client.get(f"/entities/{well}/summary").json()["summary"] is None
    summary.write(conn, well, "user:test", client=FakeSummariser())
    r = client.post(f"/entities/{well}/summary/generate").json()
    assert r["written"] is False and r["summary"]["headline"].startswith("OK-J03-01")
