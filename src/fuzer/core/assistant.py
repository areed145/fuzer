"""The assistant port: how the platform and the shared core ask the operations assistant a question without
importing it (the SRE hand-off's diagnosis today).

    ask(question, **kw) -> {"answer": str, "usage": {...}, ...}

The assistant is a cross-cutting module above the code that asks it, so the implementation is found by name: the
built-in team (`fuzer.agents.ask_sync`) unless `use()` names another callable. Callers that can take their own
`ask` (tests, evals) still pass it directly.
"""

from collections.abc import Callable
from importlib import import_module

DEFAULT = "fuzer.agents:ask_sync"
_impl: Callable[..., dict] | None = None


def use(fn: Callable[..., dict] | str | None) -> None:
    """Choose the implementation: a callable, or "module:function"; None restores the built-in assistant."""
    global _impl
    if isinstance(fn, str):
        module, name = fn.split(":")
        fn = getattr(import_module(module), name)
    _impl = fn


def ask(question: str, **kw) -> dict:
    """Ask the operations assistant from synchronous code; returns its answer and usage."""
    if _impl is not None:
        return _impl(question, **kw)
    module, name = DEFAULT.split(":")  # by name, each call: the assistant sits above the code that asks it
    return getattr(import_module(module), name)(question, **kw)
