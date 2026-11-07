"""Capability registry: each tool is declared once, next to its code, with the domain agents that use it.

    @capability("production-surveillance", "wells-subsurface", kind="plot")
    def plot_choke(ref: str, ...) -> dict: ...

"all" gives a tool to every agent. A tool with no agents is MCP-only (the work tools agents call as
themselves through the workflow engine). The agents, personas and skills are in catalog.py.
"""

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from .catalog import (
    AGENT_BY_ID,
    AGENT_CATALOG,
    AGENTS,
    PERSONA_DISPOSITION,
    SKILL_CATALOG,
    UNAVAILABLE,
    AgentDefinition,
    SkillDefinition,
)

__all__ = ["AGENT_BY_ID", "AGENT_CATALOG", "AGENTS", "PERSONA_DISPOSITION", "SKILL_CATALOG", "UNAVAILABLE",
           "AgentDefinition", "SkillDefinition"]

KINDS = ("query", "calc", "plot", "work")
ASKER: ContextVar[str | None] = ContextVar("fuzer_asker", default=None)  # the signed-in person asking in Ask


@dataclass(frozen=True)
class Capability:
    name: str
    agents: tuple[str, ...]
    kind: str
    fn: Callable


REGISTRY: dict[str, Capability] = {}


def registrar(server, annotations: dict[str, Any]) -> Callable:
    """The `capability` decorator for one MCP server; `annotations` gives the MCP tool annotations by kind."""

    def capability(*agents: str, kind: str = "query") -> Callable:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
        if bad := set(agents) - {*AGENTS, "all"}:
            raise ValueError(f"no agent {', '.join(sorted(bad))}")
        owners = AGENTS if "all" in agents else tuple(a for a in AGENTS if a in agents)

        def register(fn: Callable) -> Callable:
            if fn.__name__ in REGISTRY:
                raise ValueError(f"tool {fn.__name__} is declared twice")
            REGISTRY[fn.__name__] = Capability(fn.__name__, owners, kind, fn)
            server.tool(annotations=annotations[kind])(fn)
            return fn

        return register

    return capability


def tools_by_agent() -> dict[str, list[str]]:
    """Each agent's tool names, in the order they are declared."""
    from .. import mcp_server  # noqa: F401  (declaring the tools fills the registry)

    return {a: [c.name for c in REGISTRY.values() if a in c.agents] for a in AGENTS}


def _summary(fn: Callable) -> str:
    doc = " ".join((fn.__doc__ or "").split())
    return (doc.split(". ")[0].rstrip(".") + ".") if doc else ""


def _params(fn: Callable) -> str:
    import inspect

    return ", ".join(p if v.default is inspect.Parameter.empty else f"{p}?"
                     for p, v in inspect.signature(fn).parameters.items())


def catalogue() -> str:
    """docs/capabilities.md: personas, skills, prerequisites, boundaries and tools, all from the registry
    (`uv run python -m fuzer.agents.capabilities > docs/capabilities.md`; a test keeps it current)."""
    from ...analysis import calcs

    by_agent = tools_by_agent()
    shared = [c for c in REGISTRY.values() if len([a for a in c.agents if a != "orchestrator"]) > 1]
    rows = lambda cs: ["| Tool | Kind | Parameters | What it does |", "|---|---|---|---|"] + [  # noqa: E731
        f"| `{c.name}` | {c.kind} | {_params(c.fn)} | {_summary(c.fn)} |" for c in cs]
    out = ["# Capabilities and agent catalog", "",
           "Generated from the capability registry (`src/fuzer/agents/capabilities/`, #133); don't edit by hand. "
           "Each tool is declared once with the agents that use it. `?` marks an optional parameter.", "",
           "## Domain agents", "",
           "Tool availability is not evidence that every source exists. Each specialist stays within its listed "
           "data prerequisites and human-decision boundary, which are part of its prompt; unsupported inputs remain "
           "explicit unknowns.", "",
           "| Agent | Routes here | Not for | Required data | Expected output | Limits | Human decision |",
           "|---|---|---|---|---|---|---|"]
    for agent in AGENT_CATALOG:
        out.append(f"| {agent.label} | {agent.description} | {agent.not_for} | {'; '.join(agent.data_requirements)} "
                   f"| {'; '.join(agent.outputs)} | {'; '.join(agent.constraints)} "
                   f"| {'; '.join(agent.human_decisions)} |")
    out += ["", "## Persona coverage", "", "Every persona has one owner and at least one skill.", "",
            "| Persona | Owner | Skills |", "|---|---|---|"]
    for agent in AGENT_CATALOG:
        out += [f"| {persona} | {agent.label} | "
                f"{', '.join(s.name for s in SKILL_CATALOG if persona in s.personas)} |" for persona in agent.personas]
    out += [f"| {persona} | {disposition} | {', '.join(s.name for s in SKILL_CATALOG if persona in s.personas) or 'none'} |"
            for persona, disposition, _ in PERSONA_DISPOSITION]
    out += ["", "## Skills", "",
            "Tools are read from each `SKILL.md`. A shared skill is loaded by every agent, each of which can call only "
            "the tools in its own list; a skill never grants a capability.", "",
            "| Skill | Owner | Also used by | Personas | Purpose | Tools | Required data | Output | Human boundary |",
            "|---|---|---|---|---|---|---|---|---|"]
    for skill in SKILL_CATALOG:
        owner, also = ("Shared", "every agent") if skill.shared else (
            AGENT_BY_ID[skill.owner].label, ", ".join(AGENT_BY_ID[a].label for a in skill.agents[1:]) or "none")
        tools = ", ".join(f"`{t}`" for t in skill.tools(REGISTRY))
        out.append(f"| `{skill.path}` | {owner} | {also} | {'; '.join(skill.personas) or 'all'} | {skill.purpose} "
                   f"| {tools} | {skill.data} | {skill.output} | {skill.boundary} |")
    out += ["", "## Not available", "",
            "No agent can do these yet, whatever the question. A specialist asked for one says it is unavailable.", "",
            "| Agent | Capability | Waiting for |", "|---|---|---|"]
    out += [f"| {AGENT_BY_ID[a].label} | {capability} | {needed} |" for a, capability, needed in UNAVAILABLE]
    out += ["", "## Tool coverage", "", "| Agent | Tools |", "|---|---|"] + [
        f"| {AGENT_BY_ID[a].label} | {len(t)} |" for a, t in by_agent.items()]
    out += ["", f"## Shared ({len(shared)})", "", "Used by every agent or by several specialists.", "", *rows(shared)]
    for a in AGENTS:
        own = [c for c in REGISTRY.values() if c not in shared and c.agents and
               ([x for x in c.agents if x != "orchestrator"] or ["orchestrator"])[0] == a]
        if own:
            out += ["", f"## {AGENT_BY_ID[a].label} ({len(own)})", "", *rows(own)]
    mcp_only = [c for c in REGISTRY.values() if not c.agents]
    out += ["", f"## MCP only ({len(mcp_only)})", "", "Called by agents as themselves through the workflow engine.", "",
            *rows(mcp_only)]
    out += ["", "## Calc checks", "", "`calc(check, ref)` runs one; each returns {value, expected, deviation, flag, "
            "basis, sources}.", "", "| Check | On | Also used by |", "|---|---|---|"]
    label = {"person": "chart", "detector": "SRE detector"}
    out += [f"| `{k}` | {v['ref']} | " + ", ".join(f"{label[w]} `{x}`" for w, x in v["used_by"].items() if x and w in label)
            + " |" for k, v in calcs.CHECKS.items()]
    return "\n".join(out) + "\n"


def public_catalog() -> dict:
    """The roster for people choosing where to ask: what each specialist covers, its skills and limits. Tools and
    prompts stay out of it."""
    return {
        "agents": [{"id": a.id, "label": a.label, "description": a.description, "personas": list(a.personas),
                    "skills": [s.name for s in SKILL_CATALOG if a.id in s.agents and not s.shared],
                    "limits": list(a.constraints), "human_decisions": list(a.human_decisions)}
                   for a in AGENT_CATALOG],
        "unavailable": [{"agent": a, "capability": capability, "needed": needed} for a, capability, needed in UNAVAILABLE],
    }
