"""The capability registry (#133): every tool declared once with its agents and kind; each agent's tool
list comes from it; and a skill only names tools its agent has."""

import inspect
import re

import pytest

from fuzer import agents
from fuzer.agents import capabilities, mcp_server
from fuzer.analysis import calcs


def test_every_tool_is_declared_once_with_a_kind_and_a_docstring():
    reg = capabilities.REGISTRY
    assert len(reg) > 90 and all(c.kind in capabilities.KINDS and c.fn.__doc__ for c in reg.values())
    assert all(getattr(mcp_server, name) is c.fn for name, c in reg.items())
    assert all(c.kind == "plot" for c in reg.values() if c.name.startswith("plot_"))
    with pytest.raises(ValueError, match="no agent"):
        mcp_server.capability("nobody")


def test_capabilities_are_grouped_by_domain():
    modules = {c.fn.__module__.rsplit(".", 1)[-1] for c in capabilities.REGISTRY.values()}
    assert {"graph", "operations", "sources", "studies", "plots"} <= modules
    assert "shared" not in modules


def test_agent_tool_lists_come_from_the_registry():
    tools = agents.TOOLS
    assert set(tools) == set(capabilities.AGENTS)
    assert set(capabilities.AGENTS) == {
        "orchestrator", "production-surveillance", "wells-subsurface", "field-operations-facilities",
        "maintenance-integrity", "work-management-supply-chain", "hse-incident-management",
        "asset-performance-reporting",
    }
    shared = [c.name for c in capabilities.REGISTRY.values() if c.agents == capabilities.AGENTS]
    assert "search" in shared and all(set(shared) <= set(t) for t in tools.values())
    assert not any(c.agents for c in capabilities.REGISTRY.values() if c.kind == "work")  # MCP-only
    assert all(set(c.agents) <= set(capabilities.AGENTS) for c in capabilities.REGISTRY.values())
    assert {"production_constraints", "backpressure_sensitivity"} <= set(tools["field-operations-facilities"])
    assert {"get_events", "get_incidents", "incident_detail"} <= set(tools["hse-incident-management"])
    assert "production_summary" not in tools["hse-incident-management"]
    assert {"work_order_backlog", "maintenance_compliance", "turnaround_scope"} <= \
        set(tools["work-management-supply-chain"])
    assert "plot_wall_thickness" not in tools["work-management-supply-chain"]
    assert {"production_summary", "work_order_backlog", "maintenance_compliance"} <= \
        set(tools["asset-performance-reporting"])


def test_personas_have_one_documented_domain_or_cross_cutting_disposition():
    personas = [persona for agent in capabilities.AGENT_CATALOG for persona in agent.personas]
    personas += [persona for persona, _, _ in capabilities.PERSONA_DISPOSITION]
    assert len(personas) == 25 and len(set(personas)) == 25
    assert {persona for persona, _, _ in capabilities.PERSONA_DISPOSITION} == {
        "Data Scientist / Operations Research Analyst", "Data Steward / Platform Administrator",
    }
    assert all(agent.skill_paths and agent.data_requirements and agent.outputs and agent.constraints
               and agent.human_decisions for agent in capabilities.AGENT_CATALOG)


def test_skills_only_name_tools_their_agent_has():
    """Every domain skill names only available tools; shared/charts is loaded as a cross-domain catalogue."""
    tools, known = agents.TOOLS, set(capabilities.REGISTRY)
    bad = []
    for skill in capabilities.SKILL_CATALOG:
        named = set(skill.tools(known))
        if skill.shared:  # a catalogue of methods: each agent calls what is in its own list
            missing = named - set().union(*map(set, tools.values()))
            bad += [f"{skill.path}: {sorted(missing)}"] if missing else []
            continue
        for agent in skill.agents:  # every agent that loads it, not only the owner
            if missing := named - set(tools[agent]):
                bad.append(f"{skill.path} for {agent}: {sorted(missing)}")
    assert not bad, bad


def test_skill_calls_match_the_tools_they_name():
    """A skill's `tool(a, b)` examples pass arguments the tool has; a wrong one fails when the agent follows it."""
    known, bad = capabilities.REGISTRY, []
    for skill in capabilities.SKILL_CATALOG:
        for name, args in re.findall(r"`([a-z_]+)\(([^`]*)\)`", skill.markdown):
            if name not in known:
                continue
            params = inspect.signature(known[name].fn).parameters
            given = [a.strip() for a in re.split(r",(?![^\[]*\])", args) if a.strip() and a.strip() != "..."]
            positional = [a for a in given if "=" not in a]
            required = [p for p in params.values() if p.default is inspect.Parameter.empty]
            if (len(positional) > len(params) or len(given) < len(required) and "..." not in args
                    or any(a.split("=")[0].strip() not in params for a in given if "=" in a)):
                bad.append(f"{skill.path}: {name}({args})")
    assert not bad, bad


def test_every_skill_is_catalogued_with_its_coverage():
    on_disk = {str(p.parent.relative_to(agents.SKILLS)) for p in agents.SKILLS.glob("*/*/SKILL.md")}
    assert on_disk == {s.path for s in capabilities.SKILL_CATALOG}
    for skill in capabilities.SKILL_CATALOG:
        assert skill.purpose and skill.data and skill.output and skill.boundary, skill.path
        assert skill.agents and set(skill.agents) <= set(capabilities.AGENTS), skill.path
        if not skill.shared:  # owned by the domain whose folder it is in, and only for that domain's personas
            assert skill.path.split("/")[0] == skill.owner, skill.path
            served = {p for a in skill.agents for p in capabilities.AGENT_BY_ID[a].personas}
            assert set(skill.personas) <= served, skill.path
    skilled = {p for s in capabilities.SKILL_CATALOG for p in s.personas}
    personas = {p for a in capabilities.AGENT_CATALOG for p in a.personas}
    assert personas <= skilled, personas - skilled  # nobody is left with an agent but no method
    assert {a for a, _, _ in capabilities.UNAVAILABLE} <= set(capabilities.AGENTS)


def test_domains_keep_the_skills_they_took_over():
    """Nothing a retired agent could do was lost, and shared tools didn't make two domains the same agent."""
    names = {a.id: set(a.skill_names) for a in capabilities.AGENT_CATALOG}
    assert {"reliability", "work-orders", "turnarounds"} <= names["maintenance-integrity"]
    assert {"production-surveillance", "production-efficiency", "constraint-management", "back-pressure",
            "production-engineering", "gas-lift"} <= names["production-surveillance"]
    assert "troubleshooting" in names["field-operations-facilities"]
    tools = agents.TOOLS
    specialists = [a for a in capabilities.AGENTS if a != "orchestrator"]
    assert len({frozenset(tools[a]) for a in specialists}) == len(specialists)
    assert "plot_debottleneck" not in tools["wells-subsurface"] and "maintenance_history" in tools["wells-subsurface"]
    assert {"plot_incident", "lessons_learned", "maintenance_history"} <= set(tools["hse-incident-management"])
    assert {"plot_backlog", "plot_compliance"} <= set(tools["work-management-supply-chain"])
    assert {"plot_loss_waterfall", "opportunities"} <= set(tools["asset-performance-reporting"])


def test_boundaries_reach_the_model():
    """The catalog's limits are the agent's prompt, not only documentation, and it names only skills it has."""
    skills = {s.name for s in capabilities.SKILL_CATALOG}
    for agent in capabilities.AGENT_CATALOG:
        for text in (*agent.data_requirements, *agent.constraints, *agent.human_decisions):
            assert text in agent.prompt, agent.id
        assert set(re.findall(r"`([a-z-]+)`", agent.prompt)) & skills == set(agent.skill_names), agent.id
    assert all(prompt.endswith(capabilities.AGENT_BY_ID[a].boundaries) for a, (_, prompt) in agents.SPECIALISTS.items())


def test_no_agent_is_told_it_lacks_data_its_tools_use():
    """A limit is now in the prompt, so a wrong one makes the agent refuse work its tools can do."""
    maintenance = capabilities.AGENT_BY_ID["maintenance-integrity"]
    assert {"plot_pump_curve", "plot_choke", "equipment_curves"} <= set(agents.TOOLS[maintenance.id])
    assert "`equipment_curves`" in maintenance.prompt and "not currently available" not in maintenance.prompt
    assert not any("pump curve" in capability.lower() for _, capability, _ in capabilities.UNAVAILABLE)


def test_every_specialist_can_hold_a_workflow_step():
    from fuzer.workflow import bpmn, roles

    assert set(agents.SPECIALISTS) <= bpmn.AGENT_ROLES and set(agents.SPECIALISTS) <= set(roles.BUILTIN)
    assert {r for r, (kind, *_) in roles.BUILTIN.items() if kind == "agent"} == bpmn.AGENT_ROLES


def test_the_public_catalog_shows_coverage_without_prompts_or_tools():
    out = capabilities.public_catalog()
    assert [a["id"] for a in out["agents"]] == list(capabilities.AGENTS)
    hse = next(a for a in out["agents"] if a["id"] == "hse-incident-management")
    assert hse["label"] == "HSE & Incident Management" and "incident-evidence" in hse["skills"]
    assert any("regulatory" in u["capability"] for u in out["unavailable"])
    assert not any("instructions" in a or "tools" in a for a in out["agents"])


def test_catalogue_is_current():
    """docs/capabilities.md is generated from the registry: regenerate with
    `uv run python -m fuzer.agents.capabilities > docs/capabilities.md`."""
    from pathlib import Path

    doc = Path(__file__).parents[2] / "docs" / "capabilities.md"
    assert doc.read_text() == capabilities.catalogue()
    assert sum(1 for line in doc.read_text().splitlines() if line.startswith("| `") and "|---" not in line) == \
        len(capabilities.REGISTRY) + len(calcs.CHECKS) + len(capabilities.SKILL_CATALOG)
