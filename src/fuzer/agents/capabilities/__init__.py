"""The assistant's side of the capability registry (fuzer.core.capabilities, #133): which domain agents exist,
how the MCP server binds the tools declared there, and the generated catalogue.

    @capability("production-surveillance", "wells-subsurface", kind="plot")
    def plot_choke(ref: str, ...) -> dict: ...

"all" gives a tool to every agent. A tool with no agents is MCP-only (the work tools agents call as
themselves through the workflow engine). The agents, personas and skills are in catalog.py.
"""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ...core import capabilities as registry
from ...core.capabilities import ASKER, KINDS, REGISTRY, Capability
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

__all__ = [
    "AGENTS",
    "AGENT_BY_ID",
    "AGENT_CATALOG",
    "ASKER",
    "KINDS",
    "PERSONA_DISPOSITION",
    "REGISTRY",
    "SKILL_CATALOG",
    "UNAVAILABLE",
    "AgentDefinition",
    "Capability",
    "SkillDefinition",
]


def registrar(server, annotations: dict[str, Any]) -> Callable:
    """Bind the registry to one MCP server and return the `capability` decorator: every tool, declared before or
    after, has its agents checked against the catalog ("all" expanded) and is served with the MCP tool
    annotations for its kind."""

    def check(agents) -> None:
        if bad := set(agents) - {*AGENTS, "all"}:
            raise ValueError(f"no agent {', '.join(sorted(bad))}")

    def serve(c: Capability) -> Capability:
        check(c.agents)
        owners = AGENTS if "all" in c.agents else tuple(a for a in AGENTS if a in c.agents)
        server.tool(annotations=annotations[c.kind])(c.fn)
        return replace(c, agents=owners)

    def capability(*agents: str, kind: str = "query") -> Callable:
        check(agents)  # a misspelt agent fails where it is written, before the tool is recorded
        return registry.capability(*agents, kind=kind)

    registry.bind(serve)
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
    from ...core import methods
    from .. import packs

    by_agent = tools_by_agent()
    shared = [c for c in REGISTRY.values() if len([a for a in c.agents if a != "orchestrator"]) > 1]
    rows = lambda cs: ["| Tool | Kind | Parameters | What it does |", "|---|---|---|---|"] + [
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
    out += ["", "## Specialist packs", "",
            "A pack is a skill folder whose `pack.yaml` names what its playbook relies on (P4.1, `agents/packs.py`); "
            "each pack passes the pack conformance test. Each shipped bundle holds its answer keys in "
            "`evals/<pack>.yaml`.", ""]
    for path in packs.shipped():
        p = packs.load(path)
        cites = ", ".join(f"`{m}` ({methods.get(m).cites})" if methods.known(m) else f"`{m}`" for m in p["methods"])
        out += [f"### {p['title']}", "",
                f"- **Skill:** `{path}`, loaded by {AGENT_BY_ID[p['owner']].label}",
                f"- **Equipment classes (ISO 14224):** {', '.join(p['classes'])}",
                f"- **Methods:** {cites}",
                f"- **Detectors:** {', '.join(f'`{d}`' for d in p['detectors'])}",
                f"- **Briefs:** {', '.join(f'`{t}`' for t in p.get('briefs') or ()) or 'none'}",
                f"- **Measurement roles:** {', '.join(f'`{r}`' for r in p['roles'])}",
                f"- **Charts:** {', '.join(f'`{c}`' for c in p['charts'])}",
                f"- **Eval:** {sum(len(q) for q in p['questions'].values())} questions with answer keys across "
                f"{len(p['questions'])} bundles", ""]
    out.pop()
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
            "basis, sources, method}. [Methods](methods.md) has each one's standard, inputs and test vectors.", "",
            "| Check | On | Method | Also used by |", "|---|---|---|---|"]
    label = {"person": "chart", "detector": "SRE detector"}
    out += [f"| `{k}` | {v['ref']} | {methods.get(k).cites} | " + ", ".join(f"{label[w]} `{x}`" for w, x in v["used_by"].items() if x and w in label)
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
