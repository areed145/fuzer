"""Capability registry (#133): each tool is declared once, where it is defined, with the agents that use it
and its kind. The MCP server registers it from the same decorator, and each agent's tool list is built
from the registry, so there is no hand-kept list to fall out of step.

    @capability("troubleshooter", "production", kind="plot")
    def plot_choke(ref: str, ...) -> dict: ...

"all" gives a tool to every agent. A tool with no agents is MCP-only (the work tools agents call as
themselves through the workflow engine).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

AGENTS = ("orchestrator", "troubleshooter", "production", "maintenance")
KINDS = ("query", "calc", "plot", "work")


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
    """docs/capabilities.md: every tool by the agent that owns it, and the calc checks, from the registry
    (`uv run python -m fuzer.agents.capabilities > docs/capabilities.md`; a test keeps it current)."""
    from ...analysis import calcs

    by_agent = tools_by_agent()
    shared = [c for c in REGISTRY.values() if len([a for a in c.agents if a != "orchestrator"]) > 1]
    rows = lambda cs: ["| Tool | Kind | Parameters | What it does |", "|---|---|---|---|"] + [  # noqa: E731
        f"| `{c.name}` | {c.kind} | {_params(c.fn)} | {_summary(c.fn)} |" for c in cs]
    out = ["# Capabilities", "",
           "Generated from the capability registry (`src/fuzer/agents/capabilities/`, #133); don't edit by hand. "
           "Each tool is declared once with the agents that use it. `?` marks an optional parameter.", "",
           "| Agent | Tools |", "|---|---|"] + [f"| {a} | {len(t)} |" for a, t in by_agent.items()]
    out += ["", f"## Shared ({len(shared)})", "", "Used by every agent or by several specialists.", "", *rows(shared)]
    for a in AGENTS:
        own = [c for c in REGISTRY.values() if c not in shared and c.agents and
               ([x for x in c.agents if x != "orchestrator"] or ["orchestrator"])[0] == a]
        if own:
            out += ["", f"## {a.capitalize()} ({len(own)})", "", *rows(own)]
    mcp_only = [c for c in REGISTRY.values() if not c.agents]
    out += ["", f"## MCP only ({len(mcp_only)})", "", "Called by agents as themselves through the workflow engine.", "",
            *rows(mcp_only)]
    out += ["", "## Calc checks", "", "`calc(check, ref)` runs one; each returns {value, expected, deviation, flag, "
            "basis, sources}.", "", "| Check | On | Also used by |", "|---|---|---|"]
    label = {"person": "chart", "detector": "SRE detector"}
    out += [f"| `{k}` | {v['ref']} | " + ", ".join(f"{label[w]} `{x}`" for w, x in v["used_by"].items() if x and w in label)
            + " |" for k, v in calcs.CHECKS.items()]
    return "\n".join(out) + "\n"
