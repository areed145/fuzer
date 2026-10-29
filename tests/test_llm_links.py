"""Claude log reading, with a fake client standing in for the API (no network, no cost)."""

import json
import os
import tempfile
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb

from fakeplant import offshore
from fuzer import db, ingest, llm_links, pipeline, queries
from fuzer.pack import load_pack

PACK = load_pack()


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
            if "PT-2001 on production separator" in e["text"]:
                mentions = [
                    {"text": "PT-2001", "candidate_id": ids["20-PT-2001"], "confidence": 0.9,
                     "note": "the production separator is in area 20"},
                    {"text": "production separator", "candidate_id": ids["20-V-2001"], "confidence": 0.95, "note": ""},
                ]
                events = [{"type": "instrument_fault", "summary": "20-PT-2001 reading erratic, WO raised",
                           "involves": [{"candidate_id": ids["20-PT-2001"], "role": "subject"}],
                           "details": [{"name": "Work Order", "value": "raised"}]}]
            elif e["text"].startswith("20-P-1001B tripped"):
                events = [
                    {"type": "trip", "summary": "20-P-1001B tripped on high vibration",
                     "involves": [{"candidate_id": ids["20-P-1001B"], "role": "subject"}],
                     "details": [{"name": "vibration_mm_s", "value": "11"}]},
                    {"type": "start", "summary": "Started 20-P-1001A",
                     "involves": [{"candidate_id": ids["20-P-1001A"], "role": "subject"},
                                  {"candidate_id": "c999", "role": "location"}],  # not in the list
                     "details": []},
                ]
            out.append({"entry_id": e["entry_id"], "mentions": mentions, "events": events})
        text = json.dumps({"entries": out})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0))


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    offshore.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c


def test_candidates_cover_ambiguous_tags_and_area_names(conn):
    reg = llm_links.load_register(conn)
    cands = llm_links.candidates_for("PT-2001 on production separator reading erratic", PACK, reg)
    keys = {k for _, k in cands}
    assert {"20-PT-2001", "23-PT-2001", "27-PT-2001", "44-PT-2001", "20-V-2001"} <= keys
    cands = llm_links.candidates_for("B pump on water injection tripped again", PACK, reg)
    assert ("tag", "44-P-1001B") in cands and ("area", "OSB-44") in cands


def test_dry_run_calls_nothing(conn):
    fake = FakeClaude()
    out = llm_links.run(conn, PACK, client=fake, dry_run=True, sources=["oplog"])
    assert out["entries"] == 30 and out["requests"] == 2 and out["estimated_input_tokens"] > 0
    assert fake.requests == []


def test_run_links_and_events_reach_the_graph(conn):
    fake = FakeClaude()
    out = llm_links.run(conn, PACK, client=fake, workers=1, sources=["oplog"])
    assert out["stored"] == 30 and out["failed_requests"] == 0
    assert out["unknown_candidate"] == 3  # the made-up c999, once per copy of that log entry
    pipeline.rebuild(conn, PACK)

    pump = queries.resolve_ref(conn, PACK, "20-P-1001B")
    evs = queries.events(conn, pump)
    assert {e["type"] for e in evs} == {"trip"} and len(evs) == 3
    assert evs[0]["details"]["vibration_mm_s"] == "11" and evs[0]["log"]["text"].startswith("20-P-1001B tripped")

    pt = queries.resolve_ref(conn, PACK, "20-PT-2001")
    fault = queries.events(conn, pt, event_type="instrument_fault")
    assert fault and fault[0]["details"]["work_order"] == "raised"
    # The rules couldn't place "PT-2001"; Claude's link is now an edge from the log entry.
    linked = conn.execute("select sources from edge where rel = 'mentions' and dst = %s", (pt,)).fetchall()
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
    assert out["entries"] > 60  # 60 daily reports plus downtime comments
    sent = llm_links.run(conn, PACK, client=fake, sources=["ec_downtime"], workers=1)
    assert sent["stored"] == sent["entries"] > 0
    names = {r["source"] for r in conn.execute("select distinct source from llm_extraction")}
    assert "ec_downtime" in names
