"""MCP tools, called through an in-memory MCP client against the fake asset."""

import asyncio
import os
import tempfile

import pytest
from mcp import Client

from fakeplant import offshore
from fuzer import db, ingest, pipeline
from fuzer.pack import load_pack


@pytest.fixture(scope="module")
def call():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    offshore.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, load_pack())

    from fuzer.mcp_server import server

    async def _call(name, args):
        async with Client(server, raise_exceptions=True) as client:
            result = await client.call_tool(name, args)
            assert not result.is_error, result.content
            out = result.structured_content
            # List-returning tools are wrapped as {"result": [...]}.
            return out["result"] if set(out) == {"result"} else out

    return lambda name, **args: asyncio.run(_call(name, args))


def test_tools_are_read_only():
    from fuzer.mcp_server import server

    async def _list():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(_list())
    assert {"search", "get_entity", "trace_flow", "equipment_history", "link_text", "get_events"} <= {t.name for t in tools}
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


def test_get_entity_accepts_any_tag_style(call):
    by_pi_style = call("get_entity", ref="20V2001")
    assert by_pi_style["label"] == "20-V-2001"
    assert by_pi_style["values"]["description"]["source"] == "sap"
    assert {"sap:floc:OSB-20-V2001", "ec:node:SEP-PROD"} <= set(by_pi_style["source_records"])
    assert any(x["label"] == "A-03" for x in call("trace_flow", ref="20-V-2001", direction="up", max_depth=3)["path"])


def test_ambiguous_ref_returns_candidates(call):
    out = call("get_entity", ref="PT-2001")
    assert {c["label"] for c in out["candidates"]} == {"20-PT-2001", "23-PT-2001", "27-PT-2001", "44-PT-2001"}
    assert call("get_entity", ref="LT-2002")["label"] == "20-LT-2002"  # only one area has it


def test_trace_flow_as_of(call):
    before = call("trace_flow", ref="20-V-2001", as_of="2024-01-01")
    after = call("trace_flow", ref="20-V-2001", as_of="2024-06-01")
    labels = lambda r: {p["label"] for p in r["path"]}
    assert "A-03" not in labels(before) and "A-03" in labels(after)


def test_equipment_history_and_changes(call):
    hist = call("equipment_history", tag="20-P-1001B")["installs"]
    assert [(h["from"], h["to"]) for h in hist] == [("2018-01-01", "2023-06-01"), ("2023-06-01", "now")]
    assert all(h["serial"].startswith("SN") for h in hist)
    changes = call("equipment_changes", since="2023-01-01")
    assert {c["tag"] for c in changes} == {"20-P-1001B"}


def test_link_text(call):
    out = call("link_text", text="Lined up A-03 to test sep")
    assert [(m["key"], bool(m["fuzer_id"])) for m in out] == [("A-03", True), ("20-V-2002", True)]


def test_get_ontology(call):
    o = call("get_ontology")
    assert any(r["rel"] == "installed_at" and r["src_kind"] == "equipment" for r in o["relations"])


def test_get_events_without_claude_results(call):
    assert call("get_events", ref="20-P-1001B")["events"] == []  # nothing until `fuzer llm-link` runs


def test_list_reviews(call):
    kinds = {r["kind"] for r in call("list_reviews")}
    assert kinds == {"match", "conflict"}
