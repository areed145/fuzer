"""The Work port: the one way code outside the work engine opens, feeds and closes cases.

Sources, analyses and operations raise work (a source problem, an IOW breach, an upset, the day's report, an
opportunity) without importing the engine, so the engine can sit above them as a shared-core module and a
deployment could hand the same requests to another system (an SAP PM or ServiceNow adapter) instead.

    open_case(conn, type, title, by, ...)                 a work item with no process behind it
    start_or_correlate(conn, process_key, data, by, ...)  start a process, or add evidence to its open one
    close_case(conn, item_id, state, by, reason)          finish or cancel a case
    agent_steps(conn, role, limit)                        open agent steps for a role (the board reads these)
    dispatch(conn, item_id, by, to, reason)               give a case (or its open person step) to someone
    comment(conn, item_id, by, text)                      add a note to a case's history

Every function takes the caller's connection and writes in its transaction, as the engine always has. The
implementation is the built-in engine unless `use()` names another module with the same functions.
"""

from importlib import import_module
from types import ModuleType

DEFAULT = "fuzer.workflow.engine"
_impl: ModuleType | None = None


class WorkError(Exception):
    """A request the work implementation refused: an unknown process, a missing correlation key, a closed case.
    The engine's own errors are this type, so callers catch it without importing the engine."""


def use(module: str | ModuleType | None) -> None:
    """Choose the implementation: a module (or its dotted name) with this port's functions; None restores the
    built-in engine."""
    global _impl
    _impl = import_module(module) if isinstance(module, str) else module


def _engine() -> ModuleType:
    global _impl
    if _impl is None:
        _impl = import_module(DEFAULT)  # by name: core must not import the engine, which sits above it
    return _impl


def open_case(conn, type: str, title: str, by: str, **fields) -> dict:
    """A new work item (subjects, priority, assignee, role, due, source, payload as keywords); returns its row."""
    return _engine().create_item(conn, type, title, by, **fields)


def start_or_correlate(conn, process_key: str, data: dict, by: str, **fields) -> dict:
    """Start the published `process_key` for this data, or send the data as evidence to the one already open
    for the same correlation key. Returns the engine's result, with `item_id` and `started`."""
    return _engine().start_or_correlate(conn, process_key, data, by=by, **fields)


def close_case(conn, item_id: int, state: str, by: str, reason: str | None = None) -> dict:
    """Move a case to `done` or `cancelled` (or another state the engine allows), with the reason on its history."""
    return _engine().transition(conn, item_id, state, by, reason=reason)


def agent_steps(conn, role: str, limit: int = 100) -> list[dict]:
    """Open steps waiting for an agent role, most urgent first."""
    return _engine().agent_work(conn, role, limit=limit)


def dispatch(conn, item_id: int, by: str, to: str, reason: str | None = None) -> dict:
    """Give a case to a person, `role:<role>` or `agent:<role>`: a case without a process directly, a process's
    open person step otherwise (the one due first)."""
    return _engine().dispatch_item(conn, item_id, by, to, reason)


def roles_of(conn, person: str) -> list[str]:
    """The routing roles a person holds (operator, maintenance, ...), so work sent to `role:<role>` reaches them."""
    return _engine().roles_of(conn, person)


def comment(conn, item_id: int, by: str, text: str) -> dict:
    """A note on a case's history (what a field operator reported, say)."""
    return _engine().comment(conn, item_id, by, text)
