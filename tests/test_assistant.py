"""Troubleshooting brief: one call, everything relevant, every item with its source."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest
from mcp import Client

from fakeplant import offshore
from fuzer import assistant, db, ingest, lessons, llm_links, pipeline, queries
from fuzer.pack import load_pack

sys.path.insert(0, str(Path(__file__).parent))
from test_llm_links import FakeClaude  # noqa: E402

PACK = load_pack()


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    offshore.generate(data)
    lessons.MIN_INCIDENTS = 1
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)  # the reader picks candidates from the register
        llm_links.run(c, PACK, client=FakeClaude(), workers=1, sources=["oplog"])
        pipeline.rebuild(c, PACK)
        yield c
    lessons.MIN_INCIDENTS = 2


def test_brief_for_the_vibration_incident(conn):
    jan = next(i for i in queries.incidents(conn, queries.resolve_ref(conn, PACK, "20-P-1001B"))["items"]
               if i["start"] == "2024-01-01")
    b = assistant.brief(conn, PACK, jan["id"])
    assert b["about"]["label"] == "20-P-1001B" and b["incident"]["timeline"]
    assert "VIB" in b["incident"]["failure_modes"]
    assert any("Troubleshooting" in r["heading"] for r in b["suggested_reading"])
    assert len(b["spares"]) == 3 and b["documents"]
    assert b["suggested_checks"] and all(c["from"] for c in b["suggested_checks"])
    assert b["process_values"]["series"] and b["process_values"]["provider"] == "synthetic"


def test_brief_for_a_tag_uses_its_latest_incident(conn):
    b = assistant.brief(conn, PACK, queries.resolve_ref(conn, PACK, "23-K-1001A"))
    assert b["incident"] and b["about"]["label"] == "23-K-1001A"


def test_mcp_tool_and_prompt(conn):
    from fuzer.mcp_server import server

    async def go():
        async with Client(server, raise_exceptions=True) as client:
            brief = (await client.call_tool("troubleshooting_brief", {"ref": "20-P-1001B"})).structured_content
            prompts = [p.name for p in (await client.list_prompts()).prompts]
            return brief, prompts

    brief, prompts = asyncio.run(go())
    assert brief["about"]["label"] == "20-P-1001B" and "troubleshoot" in prompts


def test_feedback(conn):
    from fastapi.testclient import TestClient
    from fuzer.api import app
    client = TestClient(app)
    assert client.post("/feedback", json={"target": "brief:x", "useful": True, "by": "tester"}).status_code == 200
    summary = client.get("/feedback/summary").json()
    assert summary[0]["kind"] == "brief" and summary[0]["ratings"] >= 1
