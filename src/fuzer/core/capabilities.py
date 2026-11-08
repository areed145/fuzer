"""The capability registry: each tool declared once, next to its code, with the agents that use it and its kind.

    @capability("production-surveillance", "wells-subsurface", kind="plot")
    def plot_choke(ref: str, ...) -> dict: ...

It is platform core, so any part of Fuzer can offer a tool without importing the assistant. Declaring a tool only
records it. The surfaces that serve tools bind to the registry: the MCP server checks the agent names, expands
"all" and serves each tool, and the assistant reads which agents may call it. A binder sees every tool declared
before it bound and every one declared after, so import order never drops a tool.

"all" gives a tool to every agent. A tool with no agents is MCP-only (the work tools agents call as themselves
through the workflow engine).
"""

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass

KINDS = ("query", "calc", "plot", "work")
ASKER: ContextVar[str | None] = ContextVar("fuzer_asker", default=None)  # the signed-in person asking in Ask


@dataclass(frozen=True)
class Capability:
    name: str
    agents: tuple[str, ...]
    kind: str
    fn: Callable


REGISTRY: dict[str, Capability] = {}
Binder = Callable[[Capability], Capability | None]
_binders: list[Binder] = []


def _bound(cap: Capability) -> Capability:
    for binder in _binders:
        cap = binder(cap) or cap
    return cap


def capability(*agents: str, kind: str = "query") -> Callable:
    """Declare the decorated function as a tool for these agents."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")

    def register(fn: Callable) -> Callable:
        if fn.__name__ in REGISTRY:
            raise ValueError(f"tool {fn.__name__} is declared twice")
        REGISTRY[fn.__name__] = _bound(Capability(fn.__name__, tuple(agents), kind, fn))
        return fn

    return register


def bind(binder: Binder) -> None:
    """Run `binder` on every tool declared so far and on each one declared later. It may return a replacement
    (the same tool with its agents checked and expanded, say), which the registry keeps."""
    _binders.append(binder)
    for name, cap in list(REGISTRY.items()):
        REGISTRY[name] = binder(cap) or cap
