"""MCP tools, called through an in-memory MCP client against the fake asset."""

import asyncio

import pytest
from mcp import Client


@pytest.fixture(scope="module")
def call(okan_db):  # the tools open their own connections, to this module's copy
    from fuzer.agents.mcp_server import server

    async def _call(name, args):
        async with Client(server, raise_exceptions=True) as client:
            result = await client.call_tool(name, args)
            assert not result.is_error, result.content
            out = result.structured_content
            # List-returning tools are wrapped as {"result": [...]}.
            return out["result"] if set(out) == {"result"} else out

    return lambda name, **args: asyncio.run(_call(name, args))


def test_tools_are_read_only_except_work():
    from fuzer.agents.mcp_server import server

    async def _list():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(_list())
    assert {"search", "get_entity", "trace_flow", "equipment_history", "link_text", "get_events"} <= {t.name for t in tools}
    writes = {"claim_work", "complete_work", "hand_off", "send_evidence"}
    assert all(t.annotations and t.annotations.read_only_hint for t in tools if t.name not in writes)
    assert all(not t.annotations.read_only_hint and not t.annotations.destructive_hint
               for t in tools if t.name in writes)


def test_get_entity_accepts_any_tag_style(call):
    by_pi_style = call("get_entity", ref="20-V-2001")
    assert by_pi_style["label"] == "20-V-2001"
    assert by_pi_style["values"]["description"]["source"] == "sap"
    assert any(x.startswith("sap:floc:OKAN-20-V2001") for x in by_pi_style["source_records"])
    assert call("trace_flow", ref="20-V-2001", direction="up", max_depth=3)["path"]


def test_ambiguous_ref_returns_candidates(call):
    out = call("get_entity", ref="PT-2001")
    assert {"10-PT-2001", "20-PT-2001", "30-PT-2001", "40-PT-2001", "50-PT-2001"} <= {
        c["label"] for c in out["candidates"]}
    assert call("get_entity", ref="20-PT-3003")["label"] == "20-PT-3003"


def test_trace_flow_as_of(call):
    before = call("trace_flow", ref="OK-NWP-G2", direction="down", as_of="2024-01-01")
    after = call("trace_flow", ref="OK-NWP-G2", direction="down", as_of="2024-06-01")
    labels = lambda r: {p["label"] for p in r["path"]}
    assert labels(before) and labels(after)


def test_equipment_history_and_changes(call):
    hist = call("equipment_history", tag="20-K-2200B")["installs"]
    assert len(hist) == 1 and (hist[0]["from"], hist[0]["to"]) == ("2009-06-01", "now")
    assert all(h["serial"].startswith("SN") for h in hist)
    changes = call("equipment_changes", since="2000-01-01")
    assert "72-HCV-1002" in {c["tag"] for c in changes}


def test_link_text(call):
    out = call("link_text", text="Train B down on OK_GGCP, lift gas short to OKA_ST2")
    assert {"OKAN-20", "OKAN-40"} <= {m["key"] for m in out}
    assert all(m["fuzer_id"] for m in out)


def test_get_ontology(call):
    o = call("get_ontology")
    assert any(r["rel"] == "installed_at" and r["src_kind"] == "equipment" for r in o["relations"])
    tag = next(k for k in o["kinds"] if k["kind"] == "tag")
    assert tag["description"].startswith("A functional position")  # declared meaning, for Claude (#241)
    assert o["link_types"]["installed_at"]["dated"] and o["undefined"] == []


def test_get_events_without_claude_results(call):
    assert call("get_events", ref="20-K-2200B")["events"] == []  # nothing until `fuzer llm-link` runs


def test_list_reviews(call):
    kinds = {r["kind"] for r in call("list_reviews")}
    # the legacy registers (JDE, Maximo) raise both; the drawings disagree with the DCS, AF and SAP (#475)
    assert kinds == {"match", "conflict", "pid_mismatch"}
