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
    from . import mcp_server  # noqa: F401  (declaring the tools fills the registry)

    return {a: [c.name for c in REGISTRY.values() if a in c.agents] for a in AGENTS}
