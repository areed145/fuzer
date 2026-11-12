"""The exception contract: one way for any source to put something in front of the IOC.

An exception is anything that should draw the IOC's attention and be worked: an upset, an IOW breach, a source
that stopped loading, an overdue PM, deferment above plan, a notification from SAP, a report from the field. Fuzer
is the router: whatever raised it, an exception is matched to the entities it is about, kept once per open
condition, sent to the role or process that works it, and published on the event outbox so the board, the field
and other systems hear of it the same way.

    TYPE = exception_type("source_issue", "Source problem", role="role:data_steward")   declared by the module
    raise_exception(conn, "source_issue", key, title, by, refs=[...], ...)               open, or update the open one
    clear_exception(conn, "source_issue", key, by, reason)                               the condition went away

Each type says who works it (`role`, or a BPMN `process` that is started or correlated), who sees it
(`audience`: the IOC desk, and the field once a case is dispatched to someone there) and whether clearing the
condition closes its case. Callers pass `refs` in whatever terms they have: a Fuzer ID, `kind:key` (tag:12-FI-101,
well:A-12) or `source/key` (sap/10004711); the ones that match no entity stay on the exception, visible. What was
found is a `finding` (fuzer.core.findings): detector, origin, method and standard, time window, evidence items and
confidence, the same shape from a rule, an agent or another system.

This module is the port, in platform core so sources, analyses and operations raise exceptions without importing
the implementation (fuzer.ops.exceptions today, the IOC core module later), which `use()` can replace.
"""

from dataclasses import dataclass
from importlib import import_module
from types import ModuleType

DEFAULT = "fuzer.ops.exceptions"
AUDIENCES = ("ioc", "field")
# Where types are declared in code. `load()` imports them by name (core does not import the modules above it), so
# the routing table holds every type before any has been raised; a module that declares one adds itself here.
DECLARED_IN = ("fuzer.ops.intake", "fuzer.ops.sre_handoff", "fuzer.sources.alerts", "fuzer.analysis.records",
               "fuzer.analysis.limits", "fuzer.analysis.trips", "fuzer.analysis.rotating", "fuzer.graph.data_work",
               "fuzer.graph.criticality", "fuzer.analysis.completeness",
               "fuzer.graph.contextualized", "fuzer.sources.scorecard", "fuzer.ops.rounds",
               "fuzer.ops.safeguards", "fuzer.analysis.loops", "fuzer.analysis.well_tests",
               "fuzer.analysis.gas_lift", "fuzer.analysis.quality")
_impl: ModuleType | None = None


class ExceptionError(ValueError):
    """An exception the contract refused: an unknown type, a missing key."""


@dataclass(frozen=True)
class ExceptionType:
    name: str
    label: str
    role: str | None = None              # the queue a case goes to; None with no process: the exception waits on the board
    process: str | None = None           # a published BPMN process to start, or to send evidence to when one is open
    case_type: str | None = None         # the work item type of its case (default: the exception type's name)
    audience: tuple[str, ...] = ("ioc",)
    close_case_on_clear: bool = True     # a case without a process closes when the condition clears
    description: str = ""
    joins: bool = True                   # about an asset another open exception's case already works: join that case
    due_hours: str | None = None         # its due policy: priority -> hours ("1:24,2:72"), or a setting that holds one
    suppress: bool = False               # a detection planned work or a shutdown on its asset explains: counted, no case


TYPES: dict[str, ExceptionType] = {}


def exception_type(name: str, label: str, **kw) -> ExceptionType:
    """Declare an exception type, next to the code that raises it."""
    if name in TYPES:
        raise ValueError(f"exception type {name} is declared twice")
    t = ExceptionType(name, label, **kw)
    if bad := set(t.audience) - set(AUDIENCES):
        raise ValueError(f"audience must be among {AUDIENCES}, not {sorted(bad)}")
    TYPES[name] = t
    return t


def use(module: str | ModuleType | None) -> None:
    """Choose the implementation (a module with raise_ and clear); None restores the built-in one."""
    global _impl
    _impl = import_module(module) if isinstance(module, str) else module


def _engine() -> ModuleType:
    global _impl
    if _impl is None:
        _impl = import_module(DEFAULT)  # by name: core must not import the modules above it
    return _impl


def raise_exception(conn, type: str, key: str, title: str, by: str, **fields) -> dict:
    """Open an exception of `type` for `key`, or update the one already open (refs, priority, value_usd, finding,
    source, assignee, role as keywords). What was found goes in `finding`, the one record shape every raiser uses
    (fuzer.core.findings). Returns its row, with `raised` true when it is new."""
    return _engine().raise_(conn, type, key, title, by, **fields)


def clear_exception(conn, type: str, key: str, by: str, reason: str | None = None) -> dict | None:
    """The condition went away: clear the open exception (closing its case if the type says so). None if none open."""
    return _engine().clear(conn, type, key, by, reason=reason)


def match(conn, refs) -> tuple[list[str], list[str]]:
    """The register ids `refs` name, and the refs that matched nothing."""
    return _engine().match(conn, refs)


def reconcile(conn) -> dict:
    """Close open exceptions whose case finished, and put back the shelved ones whose expiry passed; the work tick
    runs it."""
    return _engine().reconcile(conn)


def load() -> None:
    """Import every module that declares a type in code."""
    for module in DECLARED_IN:
        import_module(module)


def define_type(conn, name: str, by: str, label: str, **fields) -> dict:
    """Define (or change) a type as data (role, process, case_type, audience, close_case_on_clear, description,
    joins, source_system, due_hours as keywords): how a source pack brings the exceptions its system raises (#465)."""
    return _engine().define(conn, name, by, label, **fields)


def types(conn) -> dict[str, ExceptionType]:
    """Every type: the ones declared in code and the ones defined as data."""
    return _engine().types(conn)


def defined_types(conn, source_system: str | None = None) -> list[dict]:
    """Types defined as data (all, or the ones a system brought), each with how many are open."""
    return _engine().defined_types(conn, source_system)


def open_keys(conn, type: str, rule: str | None = None) -> set[str]:
    """Keys of the open exceptions of a type (only the ones a given source rule raised, when named)."""
    return _engine().open_keys(conn, type, rule)
