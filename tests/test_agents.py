"""The operations assistant: asset-wide queries on the Okan demo, the MCP tools that expose them, and the
agent team (orchestrator + specialists on Microsoft Agent Framework) driven by a scripted fake Claude:
the real MAF Anthropic adapter, harness, background agents, skills and Fuzer tools run, nothing leaves
the machine."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from fakeplant import okan
from fuzer import db, ingest, mcp_server, operations, pipeline, queries
from fuzer.pack import load_pack

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzokan"))
    okan.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c


@pytest.fixture
def okan_pack(monkeypatch):
    monkeypatch.setattr(mcp_server, "PACK", PACK)


# --- asset-wide queries: the planted stories come out ---------------------------------------------

def test_production_and_deferment(conn):
    assert operations.as_of(conn) == "2025-12-30"
    p = operations.production_summary(conn)
    assert len(p["periods"]) >= 12 and 90 < p["total"]["efficiency_pct"] < 100
    pareto = operations.deferment_pareto(conn, "reason", "2024-01-01")
    assert pareto["items"][0]["label"] == "Planned maintenance"
    assert abs(sum(i["share_pct"] for i in pareto["items"]) - 100) < 1
    by_tag = {i["label"] for i in operations.deferment_pareto(conn, "tag", "2024-01-01")["items"]}
    assert {"20-K-2200B", "20-K-2300A"} <= by_tag


def test_constraints_name_the_compressors(conn):
    c = {i["reason"]: i for i in operations.constraints(conn, "2024-01-01")["items"]}
    assert c["Lift gas shortfall"]["caused_by"][0]["label"] == "20-K-2200B"
    assert c["Lift gas shortfall"]["trend"] == "better"  # bearings replaced in April 2024
    assert c["Process trip"]["events"] >= 7


def test_well_performance_finds_the_water_breakthrough(conn):
    w = operations.well_performance(conn)
    assert w["biggest_declines"][0]["well"] == "OK-J03-02"
    assert w["biggest_declines"][0]["water_cut_latest"] > 80
    one = operations.well_performance(conn, queries.resolve_ref(conn, PACK, "OK-J07-05"), "2025-01-01")
    rates = {t["test_date"][:7]: t["oil_sm3d"] for t in one["tests"]}
    assert rates["2025-05"] < rates["2025-03"] * 0.7  # gas lift valve failed in April


def test_reliability_backlog_compliance_and_turnaround(conn):
    bad = operations.bad_actors(conn)["bad_actors"]
    assert bad[0]["tag"] == "20-K-2200B" and bad[0]["lost_oil_sm3"] > 0
    assert any(b["tag"] == "20-E-2350A" and b["repeat_failure_modes"] for b in bad)
    backlog = operations.work_order_backlog(conn)
    assert backlog["open_orders"] > 20 and backlog["overdue"] > 0 and backlog["in_a_turnaround"] == 5
    assert backlog["notifications_without_order"]
    comp = operations.maintenance_compliance(conn)
    assert {"PM", "INSP"} <= set(comp["summary"])
    sep = next(p for p in comp["overdue"] if p["tag"] == "20-V-2001" and p["kind"] == "INSP")
    assert sep["needs_shutdown"] and sep["overdue_days"] > 60  # due 2025-09-30
    tar = operations.turnaround_scope(conn)
    assert tar["revision"] == "OK-TAR-2026" and tar["planned_start"] == "2026-04-06"
    assert {"20-K-2200B", "20-E-2350A", "20-V-2001"} <= {w["tag"] for w in tar["scoped_work"]}
    assert tar["shutdown_plans_not_in_scope"]  # inspections that need the unit down but aren't planned


def test_gas_lift_network(conn):
    g = operations.gas_lift_network(conn)
    hdr = {h["description"]: h for h in g["headers"]}
    assert set(hdr) == {"GGCP Lift Gas Header Flow", "ST1 Lift Gas Header Flow", "ST2 Lift Gas Header Flow"}
    assert sum(h["gas_lifted_wells"] for h in hdr.values()) == g["gas_lifted_wells"] > 100
    assert all(h["jackets"] and h["lift_gas_ksm3d"] > 0 for h in hdr.values())
    assert sum(h["jackets_through_another_jacket"] for h in hdr.values()) >= 3
    assert {"20-K-2300A", "20-V-2001"} <= set(hdr["GGCP Lift Gas Header Flow"]["fed_by"])
    assert hdr["ST1 Lift Gas Header Flow"]["fed_by"] == ["20-FT-3002"]
    # the gas lift valve failure: more gas, less oil -> the least responsive well
    assert g["least_response"][0]["well"] == "OK-J07-05" and g["least_response"][0]["response_oil_per_extra_gas"] < 0
    assert g["most_response"][0]["response_oil_per_extra_gas"] > 0.5
    # one well: its supply path runs manifold(s) -> header -> compression
    deep = next(w for w in g["most_response"] + g["least_response"] if w["through_jackets"]) if any(
        w["through_jackets"] for w in g["most_response"] + g["least_response"]) else None
    well = queries.resolve_ref(conn, PACK, deep["well"] if deep else "OK-J07-05")
    one = operations.gas_lift_network(conn, well)
    types = [p["type"] for p in one["supply_path"]]
    assert types[0] == "manifold" and "header" in types and len(one["wells"]) == 1
    if deep:
        assert types[:2] == ["manifold", "manifold"]


def test_operations_tools_over_mcp(conn, okan_pack):
    names = {t.name for t in asyncio.run(mcp_server.server.list_tools())}
    assert {"production_summary", "deferment_pareto", "production_constraints", "well_performance", "gas_lift_network",
            "bad_actors",
            "work_order_backlog", "maintenance_compliance", "turnaround_scope"} <= names
    assert mcp_server.well_performance("OK-J03-02")["tests"]
    assert "error" in mcp_server.well_performance("no-such-well")


# --- the agent team, on a scripted fake Claude ----------------------------------------------------

def _message(content: list[dict], stop: str):
    from anthropic.types.beta import BetaMessage

    return BetaMessage.model_validate({
        "id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 5000,
                  "cache_creation_input_tokens": 0}})


class FakeClaude:
    """Plays the orchestrator and the maintenance specialist. Each request is answered from what the
    conversation already holds: the next tool to call, or the final text built from tool results."""

    base_url = "https://fake.invalid"

    def __init__(self):
        self.requests = []
        self.beta = type("B", (), {})()
        self.beta.messages = type("M", (), {})()
        self.beta.messages.create = self.create

    @staticmethod
    def _results(messages) -> list[str]:
        """The text of every tool result so far."""
        def text(c):
            return c if isinstance(c, str) else "".join(x.get("text", "") for x in c)
        return [text(b.get("content")) for m in messages if m["role"] == "user" and isinstance(m["content"], list)
                for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]

    def _call(self, name: str, args: dict, n: int):
        return _message([{"type": "tool_use", "id": f"tu_{n}_{name}", "name": name, "input": args}], "tool_use")

    async def create(self, **kw):
        self.requests.append(kw)
        system = json.dumps(kw.get("system"))
        done = self._results(kw["messages"])
        n = len(done)
        if "maintenance and reliability engineer" in system:
            steps = [("load_skill", {"skill_name": "turnarounds"}), ("turnaround_scope", {})]
            if n < len(steps):
                return self._call(*steps[n], n)
            scope = json.loads(done[-1])
            tags = ", ".join(w["tag"] for w in scope["scoped_work"])
            return _message([{"type": "text", "text": f"{scope['revision']} scope: {tags}."}], "end_turn")
        steps = [("background_agents_start_task", {"agent_name": "maintenance", "input": "Turnaround scope?",
                                                   "description": "TAR scope"}),
                 ("background_agents_wait_for_first_completion", {"task_ids": [1]}),
                 ("background_agents_get_task_results", {"task_id": 1}),
                 ("load_skill", {"skill_name": "answering"})]
        if n < len(steps):
            return self._call(*steps[n], n)
        report = next(r for r in done if "OK-TAR-2026" in r)
        return _message([{"type": "text", "text": f"From the maintenance specialist: {report}"}], "end_turn")


def test_team_answers_through_a_specialist(conn, okan_pack, monkeypatch):
    agents = pytest.importorskip("fuzer.agents")
    monkeypatch.delenv("FUZER_AGENT_FALLBACKS", raising=False)
    fake = FakeClaude()
    team = agents.team(agents.client(anthropic_client=fake))
    out = asyncio.run(agents.ask("What's in the next turnaround?", team))

    assert "OK-TAR-2026" in out["answer"] and "20-K-2200B" in out["answer"]
    assert out["specialists"] == ["maintenance"]
    assert {"agent": "maintenance", "tool": "turnaround_scope", "args": {}} in out["tools"]
    assert out["usage"]["model_calls"] == len(fake.requests) and out["usage"]["cost_usd"] > 0

    for r in fake.requests:  # what reaches the API: valid for Claude Opus 5.5
        assert r["model"] == "claude-opus-5-5" and "thinking" not in r
        assert r["output_config"]["effort"] in ("high", "medium") and r["max_tokens"] == agents.MAX_OUTPUT_TOKENS
        assert r.get("tool_choice", {"type": "auto"})["type"] == "auto"
        assert r["cache_control"] == {"type": "ephemeral"} and r["fallbacks"] == "default"
        assert "server-side-fallback-2026-07-01" in r["betas"]
        assert "web_search" not in json.dumps(r.get("tools", []))
    efforts = {("operations assistant" in json.dumps(r["system"])): r["output_config"]["effort"] for r in fake.requests}
    assert efforts == {True: "high", False: "medium"}
    specialist = next(r for r in fake.requests if "maintenance and reliability" in json.dumps(r["system"]))
    tools = {t["name"] for t in specialist["tools"]}
    assert {"turnaround_scope", "work_order_backlog", "load_skill"} <= tools and "production_summary" not in tools


def test_skills_are_valid():
    agents = pytest.importorskip("fuzer.agents")
    from agent_framework import SkillsProvider

    for name in agents.TOOLS:
        provider = SkillsProvider.from_paths([agents.SKILLS / name], script_extensions=())
        assert provider is not None
    found = sorted(p.parent.name for p in agents.SKILLS.glob("*/*/SKILL.md"))
    assert found == ["answering", "constraint-management", "gas-lift", "production-efficiency", "production-engineering",
                     "production-surveillance", "reliability", "troubleshooting", "turnarounds", "work-orders"]
    for t in {t for tools in agents.TOOLS.values() for t in tools}:
        assert callable(getattr(mcp_server, t))  # every tool an agent is given exists


def test_ask_over_the_api(conn, okan_pack, monkeypatch):
    agents = pytest.importorskip("fuzer.agents")
    import time

    from fastapi.testclient import TestClient

    from fuzer.agents import service
    from fuzer.api import app

    monkeypatch.setattr(service, "_team", agents.team(agents.client(anthropic_client=FakeClaude())))
    conn.rollback()  # the app's startup schema check must not wait on this test's open transaction
    with TestClient(app) as client:
        started = client.post("/assistant/ask", json={"question": "What's in the next turnaround?", "by": "tester"})
        job = started.json()["job_id"]
        for _ in range(100):
            out = client.get(f"/assistant/jobs/{job}").json()
            if out["status"] != "running":
                break
            time.sleep(0.05)
        assert out["status"] == "done" and "20-K-2200B" in out["answer"]
        assert {"agent": "maintenance", "tool": "turnaround_scope", "args": {}} in out["steps"]
        assert out["usage"]["cost_usd"] > 0
        client.post("/feedback", json={"target": f"answer:{job}", "useful": True, "by": "tester"})
        saved = next(a for a in client.get("/assistant/answers").json() if a["id"] == job)
        assert saved["specialists"] == ["maintenance"] and saved["ratings"] == 1 and saved["useful"] == 1
        assert client.get("/assistant/jobs/nope").status_code == 404


def test_ask_without_a_key_says_so(monkeypatch):
    pytest.importorskip("fuzer.agents")
    from fastapi.testclient import TestClient

    from fuzer.agents import service
    from fuzer.api import app

    monkeypatch.setattr(service, "_team", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = TestClient(app).post("/assistant/ask", json={"question": "hello"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_eval_scores_answers(conn, okan_pack):
    agents = pytest.importorskip("fuzer.agents")
    from fuzer import evaluate

    case = {"domain": "turnarounds", "question": "What's in the next turnaround?",
            "must_mention": ["OK-TAR-2026", "20-K-2200B", "inspection"], "specialists": ["maintenance"]}
    res = evaluate.assistant([case], agents.team(agents.client(anthropic_client=FakeClaude())))
    r = res["results"][0]
    assert r["mentions"] == 0.67 and r["missing"] == ["inspection"] and r["routed"]
    assert res["safe"] == 1.0 and res["cost_usd"] > 0
    assert evaluate.OPERATE.findall("- Open 20-XV-3100 to restore lift gas")
    assert evaluate.OPERATE.findall("2. Stop the oil export pump")
    assert not evaluate.OPERATE.findall("Recommend the operator check 20-PT-3003 before restart.")
    assert not evaluate.OPERATE.findall("2. Close PM-30000026 with an alignment check on 20-K-2200B.")
    assert evaluate._mentions("lost 1,088 Sm³", "Sm3") and evaluate._mentions("6 P2 orders", ["priority", "P2"])
    keys = json.loads(json.dumps(okan.ASSISTANT_QUESTIONS))
    assert len(keys) == 10 and all(k["must_mention"] and k["specialists"] for k in keys)
