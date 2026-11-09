"""The plan contract: one way for any module or system to put planned work on the integrated plan.

    propose(conn, kind, title, by, start, end, refs=[...], source="sap", source_key="4000123", ...)
    update(conn, item_id, by, start=..., end=..., status=..., crew=...)

The integrated plan is the shared IOC timeline: maintenance and inspections, well interventions, turnaround
tasks, logistics and the exceptions the IOC chose to plan rather than work now. Every item is matched to the
assets it touches, so clashes (two jobs on one asset, a person booked twice) and bundling chances (work that
could be done during a shutdown of its area) show across sources. Domains propose; the IOC schedules.

This module is the port, in platform core; the implementation is fuzer.ops.plan unless `use()` names another.
"""

from importlib import import_module
from types import ModuleType

DEFAULT = "fuzer.ops.plan"
KINDS = ("maintenance", "inspection", "well_intervention", "turnaround", "logistics", "shutdown", "exception", "other")
STATUSES = ("proposed", "scheduled", "in_progress", "done", "cancelled")
_impl: ModuleType | None = None


class PlanError(ValueError):
    """A plan change the contract refused: an unknown kind or status, an end before its start."""


def use(module: str | ModuleType | None) -> None:
    global _impl
    _impl = import_module(module) if isinstance(module, str) else module


def _engine() -> ModuleType:
    global _impl
    if _impl is None:
        _impl = import_module(DEFAULT)
    return _impl


def propose(conn, kind: str, title: str, by: str, start, end, **fields) -> dict:
    """Put an item on the plan (refs, source, source_key, crew, crew_hours, needs_shutdown, deferment_usd,
    exception_id, notes as keywords). With a source_key, proposing again updates the same item."""
    return _engine().propose(conn, kind, title, by, start, end, **fields)


def update(conn, item_id: int, by: str, **fields) -> dict:
    """Reschedule or move an item on (start, end, status, crew, crew_hours, needs_shutdown, notes)."""
    return _engine().update(conn, item_id, by, **fields)
