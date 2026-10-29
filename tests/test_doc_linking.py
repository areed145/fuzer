"""Section-level linking: failure modes from rules and Claude, suggested reading for incidents."""

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from fuzer import db, fakedata, ingest, knowledge, llm_docs, llm_links, pipeline, queries
from fuzer.pack import load_pack
from fuzer.taxonomy import failure_modes_in

sys.path.insert(0, str(Path(__file__).parent))
from test_llm_links import FakeClaude  # noqa: E402

PACK = load_pack()


class FakeDocReader:
    def __init__(self):
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        sections = json.loads(kw["messages"][0]["content"])["sections"]
        out = []
        for s in sections:
            modes = ["PLU"] if "strainer" in s["text"].lower() else []  # the rules have no word for this here
            tag = next((c["id"] for c in s["candidates"] if c["key"] == "20-LT-2001"), None)
            specific = [tag] if tag and "20-LT-2001" in s["text"] else []
            out.append({"section_id": s["section_id"], "failure_modes": modes, "applies_to": specific + ["c999"]})
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=500, output_tokens=50),
                               content=[SimpleNamespace(type="text", text=json.dumps({"sections": out}))])


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    fakedata.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c


def test_keyword_rules():
    assert failure_modes_in("High vibration: check coupling alignment", PACK) == ["VIB"]
    assert set(failure_modes_in("Seal leakage after the pump fails to start", PACK)) >= {"ELP", "FTS"}
    assert failure_modes_in("Safety: isolate before work", PACK) == []


def test_troubleshooting_sections_carry_failure_modes(conn):
    rows = conn.execute(
        "select g.value from golden g join golden h on h.fuzer_id = g.fuzer_id and h.field = 'heading' "
        "where g.field = 'failure_modes' and h.value #>> '{}' like '%%Troubleshooting%%'").fetchall()
    assert rows and all(r["value"] for r in rows)
    assert any("VIB" in r["value"] for r in rows)


def test_search_boost_by_failure_mode(conn):
    plain = knowledge.search(conn, "check alignment", limit=20)
    boosted = knowledge.search(conn, "check alignment", limit=20, failure_modes=["VIB"])
    assert boosted and "VIB" in boosted[0]["failure_modes"]
    assert boosted[0]["score"] >= plain[0]["score"]


def test_incident_suggested_reading(conn):
    llm_links.run(conn, PACK, client=FakeClaude(), workers=1, sources=["oplog"])
    pipeline.rebuild(conn, PACK)
    jan = next(i for i in queries.incidents(conn, queries.resolve_ref(conn, PACK, "20-P-1001B"))["items"]
               if i["start"] == "2024-01-01")
    reading = knowledge.for_incident(conn, jan["id"])
    assert reading and all("VIB" in r["failure_modes"] for r in reading)
    assert any(r["heading"].endswith("Troubleshooting") for r in reading)


def test_claude_refines_sections(conn):
    fake = FakeDocReader()
    out = llm_docs.run(conn, PACK, client=fake, workers=1)
    assert out["stored"] == out["sections"] > 0 and out["unknown_candidate"] == out["sections"]
    pipeline.rebuild(conn, PACK)
    lt = queries.resolve_ref(conn, PACK, "20-LT-2001")
    claude_links = conn.execute(
        "select 1 from edge e join entity s on s.fuzer_id = e.src and s.kind = 'document_section' "
        "where e.dst = %s and e.rel = 'applies_to' and 'claude' = any(e.sources)", (lt,)).fetchall()
    assert claude_links  # the guide section about 20-LT-2001 now applies to it specifically
    assert llm_docs.run(conn, PACK, client=fake)["sections"] == 0  # cached
