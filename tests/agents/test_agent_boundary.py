"""P4.4: an agent reads and proposes; the server refuses the rest. This covers the tool surface agents are given:
every tool a domain agent holds is read-only, the work tools are MCP-only and act through the engine as the agent
(whose refusals tests/workflow/test_workflow.py proves), and no agent tool writes the register, sources or workflow
state itself. A tool that only drafts (draft_work_item) creates nothing until a person confirms it."""

import re
from pathlib import Path

import pytest

from fuzer.agents import capabilities, mcp_server  # noqa: F401  (declaring the tools fills the registry)

pytestmark = pytest.mark.unit
TOOLS = Path(capabilities.__file__).parent
WRITES = re.compile(r"\b(insert\s+into|update\s+\w+\s+set|delete\s+from|truncate)\b", re.IGNORECASE)
# The engine and source operations that change state; only the work tools reach the engine, as the agent.
STATE_CALLS = re.compile(r"\b(ingest\.|engine\.(complete|start|hand_off|publish|claim|send_message)|"
                         r"work\.(complete|start|hand_off|publish|send_message|create_item)\(|rebuild\(|"
                         r"set_override|golden\.write|commit_review)")


def test_every_tool_a_domain_agent_holds_is_read_only():
    held = {c.name: c.kind for c in capabilities.REGISTRY.values() if c.agents}
    assert held and {k for k in held.values()} <= {"query", "calc", "plot"}, {
        n: k for n, k in held.items() if k not in ("query", "calc", "plot")}
    work = {c.name for c in capabilities.REGISTRY.values() if c.kind == "work"}
    assert work == {"claim_work", "complete_work", "hand_off", "send_evidence"}
    assert not any(capabilities.REGISTRY[n].agents for n in work), "work tools are MCP-only: agents act as themselves"


@pytest.mark.parametrize("path", sorted(p for p in TOOLS.glob("*.py") if p.name != "work.py"), ids=lambda p: p.name)
def test_no_agent_tool_writes_the_register_sources_or_workflow_itself(path):
    text = path.read_text()
    assert not WRITES.search(text), f"{path.name} writes SQL: an agent tool reads and proposes"
    assert not STATE_CALLS.search(text), f"{path.name} changes workflow or source state: go through a proposal"
