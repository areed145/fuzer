"""The capability registry (#133): every tool declared once with its agents and kind; each agent's tool
list comes from it; and a skill only names tools its agent has."""

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
    shared = [c.name for c in capabilities.REGISTRY.values() if c.agents == capabilities.AGENTS]
    assert "search" in shared and all(set(shared) <= set(t) for t in tools.values())
    assert not any(c.agents for c in capabilities.REGISTRY.values() if c.kind == "work")  # MCP-only


def test_skills_only_name_tools_their_agent_has():
    """A skill under skills/<agent>/ that names a tool in backticks, `tool(...)` or `tool`, names one the
    agent can call. skills/shared/ (loaded by every agent) is a catalogue across domains: any agent's tool."""
    tools, known = agents.TOOLS, set(capabilities.REGISTRY)
    bad = []
    for md in sorted(agents.SKILLS.rglob("*.md")):
        owner = md.relative_to(agents.SKILLS).parts[0]
        allowed = set().union(*map(set, tools.values())) if owner == "shared" else set(tools[owner])
        for name in set(re.findall(r"`([a-z_]+)(?:\(|`)", md.read_text())) & known:
            if name not in allowed:
                bad.append(f"{md.relative_to(agents.SKILLS)}: {name}")
    assert not bad, bad


def test_catalogue_is_current():
    """docs/capabilities.md is generated from the registry: regenerate with
    `uv run python -m fuzer.agents.capabilities > docs/capabilities.md`."""
    from pathlib import Path

    doc = Path(__file__).parents[2] / "docs" / "capabilities.md"
    assert doc.read_text() == capabilities.catalogue()
    assert sum(1 for line in doc.read_text().splitlines() if line.startswith("| `") and "|---" not in line) == \
        len(capabilities.REGISTRY) + len(calcs.CHECKS)
