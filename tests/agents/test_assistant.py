"""Troubleshooting brief: one call, everything relevant, every item with its source."""

import asyncio

import pytest
from mcp import Client
from test_llm_links import (
    FakeClaude,  # a test module: tests/conftest.py puts every test folder on the path
)

from fuzer.agents import assistant
from fuzer.core.pack import load_pack
from fuzer.graph import lessons, llm_links, queries
from fuzer.ops import pipeline

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    lessons.MIN_INCIDENTS = 1
    pipeline.rebuild(c, PACK)  # the reader picks candidates from the register
    llm_links.run(c, PACK, client=FakeClaude(), workers=1, sources=["oplog"])
    pipeline.rebuild(c, PACK)
    yield c
    lessons.MIN_INCIDENTS = 2


def test_brief_for_the_okan_compressor_incident(conn):
    march = next(i for i in queries.incidents(conn, queries.resolve_ref(conn, PACK, "20-K-2200B"))["items"]
                 if i["start"].startswith("2024-03"))
    b = assistant.brief(conn, PACK, march["id"])
    assert b["about"]["label"] == "20-K-2200B" and b["incident"]["timeline"]
    assert "UST" in b["incident"]["failure_modes"]
    assert len(b["spares"]) == 3 and b["documents"]
    assert b["process_values"]["series"] and b["process_values"]["provider"] == "synthetic"


def test_brief_suggests_matching_okan_guide(conn):
    incident = next(i for i in queries.incidents(conn, queries.resolve_ref(conn, PACK, "20-V-2001"))["items"]
                    if i["start"] == "2024-11-05")
    b = assistant.brief(conn, PACK, incident["id"])
    assert b["suggested_reading"] and all("AIR" in r["failure_modes"] for r in b["suggested_reading"])
    assert any("Troubleshooting" in r["heading"] for r in b["suggested_reading"])
    assert b["suggested_checks"] and all(c["from"] for c in b["suggested_checks"])


def test_brief_for_a_tag_uses_its_latest_incident(conn):
    b = assistant.brief(conn, PACK, queries.resolve_ref(conn, PACK, "20-K-2200B"))
    assert b["incident"] and b["about"]["label"] == "20-K-2200B"


def test_mcp_tool_and_prompt(conn):
    from fuzer.agents.mcp_server import server

    async def go():
        async with Client(server, raise_exceptions=True) as client:
            brief = (await client.call_tool("troubleshooting_brief", {"ref": "20-K-2200B"})).structured_content
            prompts = [p.name for p in (await client.list_prompts()).prompts]
            return brief, prompts

    brief, prompts = asyncio.run(go())
    assert brief["about"]["label"] == "20-K-2200B" and "troubleshoot" in prompts


def test_feedback(conn):
    from fastapi.testclient import TestClient

    from fuzer.api import app
    client = TestClient(app)
    assert client.post("/feedback", json={"target": "brief:x", "useful": True, "by": "tester"}).status_code == 200
    summary = client.get("/feedback/summary").json()
    assert summary[0]["kind"] == "brief" and summary[0]["ratings"] >= 1

    # the rating set: unrated incidents first; a rated one moves to the end with its count
    first = client.get("/feedback/briefs").json()
    assert first["incidents"] > 0 and first["items"][0]["ratings"] == 0
    inc = first["items"][0]["id"]
    client.post("/feedback", json={"target": f"brief:{inc}", "useful": False, "comment": "missing the seal checks"})
    after = client.get("/feedback/briefs").json()
    rated = next(i for i in after["items"] if i["id"] == inc)
    assert rated["ratings"] == 1 and rated["useful"] == 0 and rated["last_comment"] == "missing the seal checks"
    assert after["items"][-1]["id"] == inc or after["items"][-1]["ratings"] > 0
