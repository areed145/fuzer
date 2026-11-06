"""The operations assistant: asset-wide queries on the Okan demo, the MCP tools that expose them, and the
agent team (orchestrator + specialists on Microsoft Agent Framework) driven by a scripted fake Claude:
the real MAF Anthropic adapter, harness, background agents, skills and Fuzer tools run, nothing leaves
the machine."""

import asyncio
import json

import pytest

from fakeplant import okan
from fuzer.agents import mcp_server
from fuzer.core.pack import load_pack
from fuzer.graph import operations, queries

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
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

def _message(content: list[dict], stop: str, model: str = "claude-opus-5-5"):
    from anthropic.types.beta import BetaMessage

    return BetaMessage.model_validate({
        "id": "msg_fake", "type": "message", "role": "assistant", "model": model, "content": content,
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 5000,
                  "cache_creation_input_tokens": 0}})


class FakeClaude:
    """Plays the orchestrator and the maintenance specialist. Each request is answered from what the
    conversation already holds: the next tool to call, or the final text built from tool results."""

    base_url = "https://fake.invalid"

    def __init__(self, model: str = "claude-opus-5-5"):
        self.model = model
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
        return _message([{"type": "tool_use", "id": f"tu_{n}_{name}", "name": name, "input": args}], "tool_use",
                        self.model)

    async def create(self, **kw):
        self.requests.append(kw)
        system = json.dumps(kw.get("system"))
        done = self._results(kw["messages"])
        n = len(done)
        if "work management and supply chain specialist" in system:
            steps = [("load_skill", {"skill_name": "turnarounds"}), ("turnaround_scope", {})]
            if n < len(steps):
                return self._call(*steps[n], n)
            scope = json.loads(done[-1])
            tags = ", ".join(w["tag"] for w in scope["scoped_work"])
            return _message([{"type": "text", "text": f"{scope['revision']} scope: {tags}."}], "end_turn", self.model)
        steps = [("background_agents_start_task", {"agent_name": "work-management-supply-chain", "input": "Turnaround scope?",
                                                   "description": "TAR scope"}),
                 ("background_agents_wait_for_first_completion", {"task_ids": [1]}),
                 ("background_agents_get_task_results", {"task_id": 1}),
                 ("load_skill", {"skill_name": "answering"})]
        if n < len(steps):
            return self._call(*steps[n], n)
        report = next(r for r in done if "OK-TAR-2026" in r)
        return _message([{"type": "text", "text": f"From the maintenance specialist: {report}"}], "end_turn",
                        self.model)


def test_team_answers_through_a_specialist(conn, okan_pack, monkeypatch):
    agents = pytest.importorskip("fuzer.agents")
    monkeypatch.delenv("FUZER_AGENT_FALLBACKS", raising=False)
    fake = FakeClaude()
    team = agents.team(agents.client(anthropic_client=fake))
    out = asyncio.run(agents.ask("What's in the next turnaround?", team))

    assert "OK-TAR-2026" in out["answer"] and "20-K-2200B" in out["answer"]
    assert out["specialists"] == ["work-management-supply-chain"]
    assert {"agent": "work-management-supply-chain", "tool": "turnaround_scope", "args": {}} in [
        {k: v for k, v in t.items() if k != "seconds"} for t in out["tools"]]  # each call also has its seconds
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
    orchestrator = next(r for r in fake.requests if "operations assistant" in json.dumps(r["system"]))
    assert all(agent_id in json.dumps(orchestrator["system"])
               for agent_id in agents.SPECIALISTS)
    specialist = next(r for r in fake.requests if "work management and supply chain specialist" in json.dumps(r["system"]))
    tools = {t["name"] for t in specialist["tools"]}
    assert {"turnaround_scope", "work_order_backlog", "load_skill"} <= tools and "production_summary" not in tools


class DomainClaude(FakeClaude):
    """A scripted team for one question: the orchestrator starts every specialist in `route` at once, each runs its
    (skill, tool) script and reports what it found. The script is ours; what is under test is what reaches the model,
    which tools each agent can reach and how the trace reports it. Whether Claude routes well is `fuzer eval --agents`."""

    SYSTEMS = {"hse-incident-management": "organize recorded incident evidence",
               "production-surveillance": "analyze production and surveillance evidence",
               "work-management-supply-chain": "work management and supply chain specialist"}

    def __init__(self, route: dict):
        super().__init__()
        self.route = route

    async def create(self, **kw):
        self.requests.append(kw)
        system, done = json.dumps(kw.get("system")), self._results(kw["messages"])
        n = len(done)
        for name, marker in self.SYSTEMS.items():
            if marker in system and "operations assistant" not in system:
                steps = self.route[name][0]
                if n < len(steps):
                    return self._call(*steps[n], n)
                return _message([{"type": "text", "text": self.route[name][1](done)}], "end_turn", self.model)
        ids = list(range(1, len(self.route) + 1))
        steps = [("background_agents_start_task", {"agent_name": name, "input": "Task", "description": name})
                 for name in self.route]
        steps += [("background_agents_wait_for_first_completion", {"task_ids": [i]}) for i in ids]
        steps += [("background_agents_get_task_results", {"task_id": i}) for i in ids]
        steps += [("load_skill", {"skill_name": "answering"})]
        if n < len(steps):
            return self._call(*steps[n], n)
        return _message([{"type": "text", "text": "Combined: " + " | ".join(done[-1 - len(ids):-1])}], "end_turn", self.model)


def _system(fake, marker: str) -> dict:
    return next(r for r in fake.requests if marker in json.dumps(r["system"]))


def test_a_cross_domain_question_runs_both_specialists_and_keeps_their_evidence(conn, okan_pack):
    agents = pytest.importorskip("fuzer.agents")
    fake = DomainClaude({
        "production-surveillance": ([("load_skill", {"skill_name": "production-efficiency"}),
                                     ("production_summary", {"since": "2025-01-01", "until": "2026-01-01"})],
                                    lambda done: "Efficiency from EC DAY001."),
        "work-management-supply-chain": ([("load_skill", {"skill_name": "turnarounds"}), ("turnaround_scope", {})],
                                         lambda done: f"Scope {json.loads(done[-1])['revision']} from SAP."),
    })
    out = asyncio.run(agents.ask("How did we do, and what is in the turnaround?", agents.team(agents.client(anthropic_client=fake))))

    assert out["specialists"] == ["production-surveillance", "work-management-supply-chain"]
    assert "EC DAY001" in out["answer"] and "OK-TAR-2026" in out["answer"]  # both reports, each with its source
    assert {("production-surveillance", "production_summary"), ("work-management-supply-chain", "turnaround_scope")} <= {
        (t["agent"], t["tool"]) for t in out["tools"]}


def test_a_specialist_with_no_source_says_so_and_escalates(conn, okan_pack):
    """The HSE specialist is told what it can't know and who decides, and has no way round it: it can't load a skill
    it wasn't given or reach production tools."""
    agents = pytest.importorskip("fuzer.agents")
    from fuzer.evals import evaluate

    report = "No permit or regulatory source is configured, so compliance is unavailable. Refer it to the accountable HSE lead."
    fake = DomainClaude({"hse-incident-management": ([("get_incidents", {"ref": "20-K-2200B"})], lambda done: report)})
    out = asyncio.run(agents.ask("Are we compliant with our flaring permit?", agents.team(agents.client(anthropic_client=fake))))

    specialist = _system(fake, "organize recorded incident evidence")
    system = json.dumps(specialist["system"])
    assert "No compliance determination" in system and "Escalate safety-critical judgments" in system
    assert "environmental and regulatory sources are not configured" in system
    tools = {t["name"] for t in specialist["tools"]}
    assert "get_incidents" in tools and "production_summary" not in tools and "calc" not in tools
    assert "turnarounds" not in json.dumps(specialist.get("tools"))  # another domain's skill isn't on offer

    orchestrator = _system(fake, "operations assistant")
    assert "Not for:" in json.dumps(orchestrator["system"]) and "Never make operating" in json.dumps(orchestrator["system"])
    case = next(c for c in okan.ASSISTANT_QUESTIONS if c["domain"] == "missing data: regulatory")
    scored = evaluate.score_answer(case, out)
    assert scored["mentions"] == 1 and scored["routed"] and not scored["operating_instructions"]
    escalation = next(c for c in okan.ASSISTANT_QUESTIONS if c["domain"] == "escalation")
    wrong = evaluate.score_answer(escalation, {**out, "answer": "Close the wellhead valve and stop the compressor."})
    assert wrong["mentions"] == 0 and wrong["operating_instructions"]  # the key catches an answer that gives orders


class ThinkingClaude(FakeClaude):
    """FakeClaude that thinks before each reply, as Claude does on these models."""

    async def create(self, **kw):
        reply = (await super().create(**kw)).model_dump()
        thinking = {"type": "thinking", "thinking": "...", "signature": f"sig{len(self.requests)}"}
        return _message([thinking, *reply["content"]], reply["stop_reason"], self.model)


def test_follow_up_keeps_the_history_as_it_was(conn, okan_pack):
    # Claude rejects a thinking block once the conversation before it changes, so a follow-up must send
    # the first turn exactly as before (no task status put ahead of it).
    agents = pytest.importorskip("fuzer.agents")
    fake = ThinkingClaude()
    team = agents.team(agents.client(anthropic_client=fake))
    session = team.create_session()
    asyncio.run(agents.ask("What's in the next turnaround?", team, session))
    before = fake.requests[-1]["messages"]  # the orchestrator's last call
    asyncio.run(agents.ask("And the one after?", team, session))
    follow_up = fake.requests[-1]["messages"]
    assert follow_up[:len(before)] == before
    assert follow_up[-1]["content"][0]["text"] == "And the one after?"


def test_follow_up_after_a_restart(conn, okan_pack):
    # Conversations live in Postgres (#93): a new team (a restarted server) carries on a conversation from its
    # id alone, and Claude gets the first turn back byte for byte, thinking blocks and signatures included.
    agents = pytest.importorskip("fuzer.agents")
    from fuzer.agents.history import PostgresHistory

    fake = ThinkingClaude()
    first = agents.team(agents.client(anthropic_client=fake), history=PostgresHistory())
    asyncio.run(agents.ask("What's in the next turnaround?", first, first.create_session(session_id="conv-restart")))
    before = fake.requests[-1]["messages"]
    assert any(b.get("type") == "thinking" for m in before for b in m["content"] if isinstance(b, dict))

    restarted = agents.team(agents.client(anthropic_client=fake), history=PostgresHistory())
    asyncio.run(agents.ask("And the one after?", restarted, restarted.create_session(session_id="conv-restart")))
    follow_up = fake.requests[-1]["messages"]
    assert follow_up[:len(before)] == before
    assert follow_up[-1]["content"][0]["text"] == "And the one after?"
    conn.rollback()
    n = conn.execute("select count(*) as n from assistant_message where conversation_id = 'conv-restart'").fetchone()["n"]
    assert n == len(follow_up) + 1  # every message once, however often the harness saved, plus the final answer


def test_history_keeps_images_by_reference(conn):
    pytest.importorskip("fuzer.agents")
    from agent_framework import Content, Message

    from fuzer.agents import history

    png = b"\x89PNG fake image bytes"
    asked = [Message(role="user", contents=[Content.from_text("What is this?"), Content.from_data(png, "image/png")]),
             Message(role="assistant", contents=[Content.from_text("A pump curve.")]),
             Message(role="user", contents=[Content.from_text("Again"), Content.from_data(png, "image/png")])]
    history.ensure(conn, "conv-images", "tester", "What is this?")
    history.append(conn, "conv-images", asked)
    conn.commit()
    stored = [r["m"] for r in conn.execute(
        "select message::text as m from assistant_message where conversation_id = 'conv-images' order by seq")]
    assert "base64" not in "".join(stored) and history.IMAGE_REF in stored[0]
    assert conn.execute("select count(*) as n from assistant_image where conversation_id = 'conv-images'"
                        ).fetchone()["n"] == 1  # the same image twice is kept once
    back = history.load(conn, "conv-images")
    assert [m.to_dict() for m in back] == [m.to_dict() for m in asked]

    from datetime import datetime, timedelta, timezone
    assert history.purge(conn, 0) == 0  # 0 keeps everything
    assert history.purge(conn, 30, now=datetime.now(timezone.utc) + timedelta(days=31)) >= 1
    assert not history.exists(conn, "conv-images")
    assert conn.execute("select count(*) as n from assistant_image where conversation_id = 'conv-images'"
                        ).fetchone()["n"] == 0


def test_specialists_on_their_own_model_and_cost_per_model(conn, okan_pack, monkeypatch):
    agents = pytest.importorskip("fuzer.agents")
    lead, worker = FakeClaude("claude-opus-5-5"), FakeClaude("claude-sonnet-5-5")
    team = agents.team(agents.client(anthropic_client=lead), specialist_chat=agents.client(anthropic_client=worker))
    out = asyncio.run(agents.ask("What's in the next turnaround?", team))
    assert "OK-TAR-2026" in out["answer"]
    assert all("operations assistant" in json.dumps(r["system"]) for r in lead.requests)
    assert worker.requests and all("work management and supply chain specialist" in json.dumps(r["system"])
                                   for r in worker.requests)
    models = out["usage"]["models"]
    assert set(models) == {"claude-opus-5-5", "claude-sonnet-5-5"}
    assert models["claude-opus-5-5"]["calls"] == len(lead.requests)
    assert models["claude-sonnet-5-5"]["calls"] == len(worker.requests)
    # sonnet costs half of opus for the same tokens
    per_call = {m: v["cost_usd"] / v["calls"] for m, v in models.items()}
    assert abs(per_call["claude-sonnet-5-5"] * 2 - per_call["claude-opus-5-5"]) < 1e-3
    assert abs(sum(v["cost_usd"] for v in models.values()) - out["usage"]["cost_usd"]) < 1e-3

    # the setting does the same thing
    monkeypatch.setenv("FUZER_AGENT_SPECIALIST_MODEL", "claude-sonnet-5-5")
    t = agents.team(agents.client(anthropic_client=FakeClaude()))
    assert t.name == "orchestrator"


def test_unknown_model_is_flagged():
    agents = pytest.importorskip("fuzer.agents")
    u = agents.usage([{"kind": "model", "model": "claude-mystery-9", "input": 1000, "output": 100,
                       "cache_read": 0, "cache_write": 0}])
    assert u["models"]["claude-mystery-9"]["unpriced"] and u["cost_usd"] > 0


def test_openai_compatible_agent_team_keeps_skills_and_specialists():
    pytest.importorskip("agent_framework_openai")
    from fuzer import agents
    from fuzer.core import settings

    with settings.Overrides({"agents.provider": "ollama", "agents.base_url": "http://localhost:11434/v1",
                             "agents.model": "qwen2.5vl"}):
        team = agents.team()
        options = agents._options("high")
    assert team.name == "orchestrator"
    assert {type(p).__name__ for p in team.context_providers} >= {"SkillsProvider", "BackgroundAgentsProvider"}
    assert options == {"max_tokens": agents.MAX_OUTPUT_TOKENS}
    with settings.Overrides({"agents.provider": "openai", "agents.model": "o3"}):
        assert "reasoning_effort" not in agents._options("medium", "gpt-4o")
        assert agents._options("medium", "o3")["reasoning_effort"] == "medium"


def test_skills_are_valid():
    agents = pytest.importorskip("fuzer.agents")
    from agent_framework import SkillsProvider

    from fuzer.agents.capabilities import AGENT_CATALOG

    for definition in AGENT_CATALOG:
        provider = SkillsProvider.from_paths([agents.SKILLS / path for path in definition.skill_paths],
                                             script_extensions=())
        assert provider is not None
    found = sorted(p.parent.name for p in agents.SKILLS.glob("*/*/SKILL.md"))
    assert found == ["alarms-and-instruments", "analysis", "answering", "back-pressure", "charts",
                     "constraint-management", "gas-lift", "incident-evidence", "integrity", "job-readiness",
                     "operations-summary", "production-efficiency", "production-engineering",
                     "production-surveillance", "reliability", "shift-handover", "troubleshooting", "turnarounds",
                     "well-review", "work-orders"]
    for t in {t for tools in agents.TOOLS.values() for t in tools}:
        assert callable(getattr(mcp_server, t))  # every tool an agent is given exists


def test_ask_over_the_api(conn, okan_pack, monkeypatch):
    agents = pytest.importorskip("fuzer.agents")
    import time

    from fastapi.testclient import TestClient

    from fuzer.agents import service
    from fuzer.agents.history import PostgresHistory
    from fuzer.api import app

    monkeypatch.setattr(service, "_team", agents.team(agents.client(anthropic_client=FakeClaude()),
                                                      history=PostgresHistory()))
    conn.rollback()  # the app's startup schema check must not wait on this test's open transaction
    with TestClient(app) as client:
        def run(question, conversation=None):
            started = client.post("/assistant/ask", json={"question": question, "by": "tester",
                                                          "conversation_id": conversation}).json()
            for _ in range(100):
                out = client.get(f"/assistant/jobs/{started['job_id']}").json()
                if out["status"] != "running":
                    return started, out
                time.sleep(0.05)
            raise AssertionError("still running")

        started, out = run("What's in the next turnaround?")
        job, conversation = started["job_id"], started["conversation_id"]
        assert out["status"] == "done" and "20-K-2200B" in out["answer"]
        assert {"agent": "work-management-supply-chain", "tool": "turnaround_scope", "args": {}} in out["steps"]
        assert out["usage"]["cost_usd"] > 0
        client.post("/feedback", json={"target": f"answer:{job}", "useful": True, "by": "tester"})
        saved = next(a for a in client.get("/assistant/answers").json() if a["id"] == job)
        assert saved["specialists"] == ["work-management-supply-chain"] and saved["ratings"] == 1 and saved["useful"] == 1
        assert client.get("/assistant/jobs/nope").status_code == 404

        # after a restart: the job is read back from the database, the conversation is listed and reopened,
        # and a follow-up carries on in it
        service._jobs.clear()
        again = client.get(f"/assistant/jobs/{job}").json()
        assert again["status"] == "done" and again["answer"] == out["answer"] and again["steps"]
        mine = client.get("/assistant/conversations", params={"by": "tester"}).json()
        assert mine[0]["id"] == conversation and mine[0]["title"] == "What's in the next turnaround?"
        follow, _ = run("And the one after?", conversation)
        assert follow["conversation_id"] == conversation
        turns = client.get(f"/assistant/conversations/{conversation}").json()["turns"]
        assert [t["question"] for t in turns] == ["What's in the next turnaround?", "And the one after?"]
        assert turns[0]["ratings"] == 1
        assert run("Unknown conversation", "nope")[0]["conversation_id"] != "nope"
        assert client.delete(f"/assistant/conversations/{conversation}").status_code == 200
        assert client.get(f"/assistant/conversations/{conversation}").status_code == 404


def test_ask_without_a_key_says_so(monkeypatch):
    pytest.importorskip("fuzer.agents")
    from fastapi.testclient import TestClient

    from fuzer.agents import service
    from fuzer.api import app

    monkeypatch.setattr(service, "_team", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = TestClient(app).post("/assistant/ask", json={"question": "hello"})
    assert r.status_code == 503 and "Configure the agents provider API key" in r.json()["detail"]


def test_eval_scores_answers(conn, okan_pack):
    agents = pytest.importorskip("fuzer.agents")
    from fuzer.evals import evaluate

    case = {"domain": "turnarounds", "question": "What's in the next turnaround?",
            "must_mention": ["OK-TAR-2026", "20-K-2200B", "inspection"],
            "specialists": ["work-management-supply-chain"]}
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
    assert len(keys) == 19 and all(k["must_mention"] and k["specialists"] for k in keys)
    assert {s for k in keys for s in k["specialists"]} == set(agents.SPECIALISTS)  # a key per domain, none retired
    assert {k for c in keys for k in c.get("charts", [])} <= {"decline", "wall_ranking", "wall_thickness", "pump_curve",
                                                             "choke"}
