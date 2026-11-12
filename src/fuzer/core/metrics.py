"""The metric registry (P7.1): each KPI declared once, with its definition, formula, unit, the standard it follows where
there is one, the grains it can be cut by and the records behind it. Pages, the daily report and agents read the
same registered metric, so a number is the same wherever it is shown, and every number drills to its cases or records.

A metric is declared next to a catalog of computations (`DECLARED_IN`); the computation itself stays in the module
that owns it, and the declaration only reads it. Its compute function takes (conn, pack, grain) and returns:

    value    the headline number (None when it can't be said)
    rows     [{key, label, value, refs}] by the grain asked for (empty without one)
    refs     what the headline is made of: entity ids or "case:<id>"
    as_of    the date or period it is for
    note     why it is None, or what it leaves out

A metric over a dated period (`dated=True`) also takes `at`, the day the period ends, so a dated document such as the
daily report reads the value for its own day and keeps it when it is rebuilt later; without `at` it reads the latest.

    metric(key, label, ...)      declare one (decorates its compute function)
    registry()                   every metric, by key
    value(conn, pack, key, grain=None, at=None)   one metric now (or for the period ending at), with its declaration
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module

GRAINS = ("asset", "area", "class", "role", "queue", "type", "facility", "system", "dataset", "period")
DECLARED_IN = ("fuzer.ops.metrics",)
_REGISTRY: dict[str, "Metric"] = {}


class MetricError(ValueError):
    """A metric that isn't registered, or a grain it can't be cut by."""


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    definition: str
    formula: str
    unit: str
    records: str
    compute: Callable = field(repr=False, compare=False)
    grains: tuple[str, ...] = ()
    standard: str | None = None
    period: str = "now"
    better: str = "higher"  # higher | lower | none
    group: str = "Other"
    dated: bool = False

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "group": self.group, "definition": self.definition,
                "formula": self.formula, "unit": self.unit, "standard": self.standard, "grains": list(self.grains),
                "period": self.period, "better": self.better, "records": self.records, "dated": self.dated}


def metric(key: str, label: str, *, definition: str, formula: str, unit: str, records: str, grains=(),
           standard: str | None = None, period: str = "now", better: str = "higher", group: str = "Other",
           dated: bool = False):
    """Declare a metric; the decorated function computes it."""
    if bad := set(grains) - set(GRAINS):
        raise ValueError(f"metric {key}: unknown grain {sorted(bad)}")

    def wrap(fn: Callable) -> Callable:
        if key in _REGISTRY:
            raise ValueError(f"metric {key} is declared twice")
        _REGISTRY[key] = Metric(key, label, definition, formula, unit, records, fn, tuple(grains), standard, period,
                                better, group, dated)
        return fn
    return wrap


def registry() -> dict[str, Metric]:
    for module in DECLARED_IN:
        import_module(module)
    return dict(sorted(_REGISTRY.items()))


def value(conn, pack, key: str, grain: str | None = None, at=None) -> dict:
    """One metric now (a dated one for the period ending `at`, a date): its declaration, its headline value and, with a
    grain, its rows."""
    m = registry().get(key)
    if m is None:
        raise MetricError(f"no metric {key}")
    if grain and grain not in m.grains:
        raise MetricError(f"{key} can't be cut by {grain}; it can be cut by {', '.join(m.grains) or 'nothing'}")
    if at is not None and not m.dated:
        raise MetricError(f"{key} is not for a dated period; it is for {m.period}")
    out = (m.compute(conn, pack, grain, at=at) if m.dated else m.compute(conn, pack, grain)) or {}
    return {**m.describe(), "value": out.get("value"), "rows": out.get("rows") or [], "refs": out.get("refs") or [],
            "as_of": out.get("as_of"), "note": out.get("note"), "grain": grain}
