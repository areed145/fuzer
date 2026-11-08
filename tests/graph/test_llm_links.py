"""Claude log reading, with a fake client standing in for the API (no network, no cost)."""

import json
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb

from fuzer.core.pack import load_pack
from fuzer.graph import llm_links, queries
from fuzer.ops import pipeline

PACK = load_pack("okan")


class FakeClaude:
    """Answers like Claude would for a few known entries; records every request."""

    def __init__(self):
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        assert kw["fallbacks"] == "default" and kw["output_config"]["format"]["type"] == "json_schema"
        entries = json.loads(kw["messages"][0]["content"])["entries"]
        out = []
        for e in entries:
            ids = {c["key"]: c["id"] for c in e["candidates"]}
            mentions, events = [], []
            if "20-K-2200B" in e["text"] and "20-K-2200B" in ids:
                mentions = [{"text": "20-K-2200B", "candidate_id": ids["20-K-2200B"], "confidence": 0.95,
                             "note": "Okan train B second-stage compressor"}]
                events = [{"type": "trip", "summary": "20-K-2200B tripped on high vibration",
                           "involves": [{"candidate_id": ids["20-K-2200B"], "role": "subject"},
                                        {"candidate_id": "c999", "role": "location"}],
                           "details": [{"name": "vibration_mm_s", "value": "9"}]}]
            out.append({"entry_id": e["entry_id"], "mentions": mentions, "events": events})
        text = json.dumps({"entries": out})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0))


pytestmark = pytest.mark.sequence  # (#137) the reader's cache over time: read, cached, changed, refused, removed


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    yield c


def test_candidates_cover_ambiguous_tags_and_area_names(conn):
    reg = llm_links.load_register(conn)
    cands = llm_links.candidates_for("PT-2001 on GGCP inlet separator reading erratic", PACK, reg)
    keys = {k for _, k in cands}
    assert {"10-PT-2001", "20-PT-2001", "30-PT-2001", "40-PT-2001", "50-PT-2001", "20-V-2001"} <= keys
    cands = llm_links.candidates_for("20-K-2200B on GGCP tripped", PACK, reg)
    assert ("tag", "20-K-2200B") in cands and ("area", "OKAN-20") in cands


def test_dry_run_calls_nothing(conn):
    fake = FakeClaude()
    out = llm_links.run(conn, PACK, client=fake, dry_run=True, sources=["oplog"])
    assert out["entries"] == 2428 and out["requests"] > 0 and out["estimated_input_tokens"] > 0
    assert fake.requests == []


def test_run_links_and_events_reach_the_graph(conn):
    fake = FakeClaude()
    out = llm_links.run(conn, PACK, client=fake, workers=1, sources=["oplog"])
    assert out["stored"] == out["entries"] and out["failed_requests"] == 0 and out["unknown_candidate"] > 0
    pipeline.rebuild(conn, PACK)

    compressor = queries.resolve_ref(conn, PACK, "20-K-2200B")
    evs = queries.events(conn, compressor)
    assert evs and all(e["type"] == "trip" for e in evs)
    assert evs[0]["details"]["vibration_mm_s"] == "9"
    linked = conn.execute("select sources from edge where rel = 'mentions' and dst = %s", (compressor,)).fetchall()
    assert any("claude" in r["sources"] for r in linked)

    o = queries.ontology(conn)
    assert any(k["kind"] == "event" for k in o["kinds"])
    assert any(r["rel"] == "involves" for r in o["relations"])


def test_cached_results_are_not_requested_again(conn):
    fake = FakeClaude()
    assert llm_links.run(conn, PACK, client=fake, sources=["oplog"])["entries"] == 0
    assert fake.requests == []


def test_changed_text_is_read_again(conn):
    conn.execute(
        """
        update source_record set payload = payload || %s
        where source = 'oplog' and source_key = (select min(source_key) from source_record where source = 'oplog')
        """,
        (Jsonb({"text": "LT-2002 on test separator high level, drained."}),),
    )
    conn.commit()
    fake = FakeClaude()
    assert llm_links.run(conn, PACK, client=fake, sources=["oplog"])["entries"] == 1
    assert len(fake.requests) == 1


def test_refusal_is_reported_not_stored(conn):
    class Refuses(FakeClaude):
        def create(self, **kw):
            return SimpleNamespace(stop_reason="refusal", content=[], usage=None)

    out = llm_links.run(conn, PACK, client=Refuses(), force=True, limit=5, sources=["oplog"])
    assert out["failed_requests"] == 1 and out["stored"] == 0 and "declined" in out["errors"][0]


def test_removed_results_retire_their_events(conn):
    conn.execute("delete from llm_extraction")
    conn.commit()
    pipeline.rebuild(conn, PACK)
    assert not any(k["kind"] == "event" for k in queries.ontology(conn)["kinds"])
    assert conn.execute("select count(*) as n from entity where kind = 'event' and retired_at is not null").fetchone()["n"] > 0


def test_other_text_sources_are_read_and_cached_separately(conn):
    fake = FakeClaude()
    out = llm_links.run(conn, PACK, client=fake, sources=["ec_downtime", "ec_daily"], dry_run=True)
    assert out["entries"] > 60  # daily reports plus downtime comments
    sent = llm_links.run(conn, PACK, client=fake, sources=["ec_downtime"], workers=1)
    assert sent["stored"] == sent["entries"] > 0
    names = {r["source"] for r in conn.execute("select distinct source from llm_extraction")}
    assert "ec_downtime" in names
