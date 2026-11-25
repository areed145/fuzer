"""The metric registry (P7.1): each KPI declared once, with its definition, formula, unit, the standard it follows where
there is one, the grains it can be cut by and the records behind it. Pages, the daily report, agents and reports read
the same registered metric, so a number is the same wherever it is shown, and every number drills to its cases or
records. A report's measure is a registered metric: there is no reporting model or second definition (docs/reports.md).

A metric is declared in one of two places, and both land here:

    in code       `@metric(...)` next to a catalog of computations (`DECLARED_IN`); the computation stays in the module
                  that owns it, and the declaration only reads it
    in YAML       a measure over the register's records (`core/measures.yaml`, and a pack's `measures:`), checked by
                  `core/measures.py` and counted or summed by `ops/measures.py` (a `provider`), so a pack adds or
                  rebinds one without code

Scope and window (the reports design, Nov 2026). A metric declared `scoped` answers for any entity of the register and
rolls up everything beneath it (graph.hierarchy's containment, as the entity pages roll work up). One declared
`windowed` answers for a named window (core/windows) ending on `at`; `cutoff` says what `at` is when nobody gives one
(today, the last production day). A `stock` metric is a count at the window's end (open cases, permits in date); the
rest add up what happened inside it. Their compute function takes (conn, pack, q) with `q` a Query: the scope, the
span and the grain. A metric not yet cut by scope or window answers for the whole register and its own period, and
`value` says so with `scope: None` or `window: None`.

The older form of compute function takes (conn, pack, grain) (and `at` for a `dated` one) and returns the same dict:

    value    the headline number (None when it can't be said)
    rows     [{key, label, value, refs}] by the grain asked for (empty without one)
    refs     what the headline is made of: entity ids or "case:<id>"
    as_of    the date or period it is for
    note     why it is None, or what it leaves out
    unknown  True when it can't be said because what it is read from isn't there (no bypass register loaded): the
             note says why, and nobody reads it as 0 or none
    history  optional [{at, value}], the value over earlier periods, oldest first, where the computation keeps them

A metric may declare a `target`, the value it should reach (at least it when higher is better, at most when lower), so
a page can say at a glance whether it is on target (#1078). Targets are the platform's defaults for a demonstration.

    metric(key, label, ...)      declare one (decorates its compute function)
    registry(pack=None)          every metric, by key, with the pack's YAML measures on top
    value(conn, pack, key, scope=None, window=None, at=None, grain=None, history=False)
                                 one metric for a scope and window (docs/reports.md), with its declaration
    series(conn, pack, key, scope, window, at)   its value for each day of the window, for a trend
    scope_of(conn, fid)          an entity and everything beneath it, as a scope
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from importlib import import_module

from . import windows

GRAINS = ("asset", "area", "class", "role", "queue", "type", "facility", "system", "dataset", "period", "well", "cause",
          "severity", "tag", "program")
DECLARED_IN = ("fuzer.ops.metrics", "fuzer.ops.measures")
_REGISTRY: dict[str, "Metric"] = {}
_PROVIDERS: list[Callable] = []  # fn(pack) -> {key: Metric}: the YAML measures (ops/measures.py)
_ROLLUP: list[Callable] = []  # fn(conn, fid) -> ids beneath it: the register's containment (graph.hierarchy)
HISTORY_WEEKS = 8  # points in a metric's history: its last eight periods
TREND_MIN_DAYS = 14  # a trend of a shorter window draws this many days, so the line has a shape


WHOLE = "For the whole register: it isn't cut by entity."  # said on a value asked for a scope it can't be cut by

class MetricError(ValueError):
    """A metric that isn't registered, a grain it can't be cut by, or a scope that isn't in the register."""


@dataclass(frozen=True)
class Scope:
    """An entity and everything beneath it in the register."""
    id: str
    kind: str
    label: str
    ids: frozenset

    def holds(self, ids) -> bool:
        return any(i in self.ids for i in ids or ())


@dataclass
class Query:
    """What a scoped or windowed compute function is asked for. `cache` is shared by every call one `value` makes
    (the window, the one before, the history), so records read once serve all of them."""
    scope: Scope | None
    span: windows.Span | None
    grain: str | None = None
    cache: dict = field(default_factory=dict)

    def within(self, ids) -> bool:
        """Whether something about these entity ids falls in the scope (always, with no scope)."""
        return self.scope is None or self.scope.holds(ids)

    def at(self, span: windows.Span, grain: str | None = None) -> "Query":
        return Query(self.scope, span, grain, self.cache)


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
    target: float | None = None
    scoped: bool = False
    windowed: bool = False
    stock: bool = False
    cutoff: str = "day"
    default_window: str | None = None
    source: str = "platform"  # platform | bundle

    @property
    def queried(self) -> bool:
        """Its compute function takes a Query."""
        return self.scoped or self.windowed

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "group": self.group, "definition": self.definition,
                "formula": self.formula, "unit": self.unit, "standard": self.standard, "grains": list(self.grains),
                "period": self.period, "better": self.better, "records": self.records, "dated": self.dated,
                "target": self.target, "scoped": self.scoped, "windowed": self.windowed, "stock": self.stock,
                "cutoff": self.cutoff if self.windowed else None, "default_window": self.default_window,
                "source": self.source}


def _check(key, grains, cutoff, default_window, windowed):
    if bad := set(grains) - set(GRAINS):
        raise ValueError(f"metric {key}: unknown grain {sorted(bad)}")
    if cutoff not in windows.CUTOFFS:
        raise ValueError(f"metric {key}: cutoff {cutoff!r} isn't one of {', '.join(windows.CUTOFFS)}")
    if windowed and not windows.valid(default_window):
        raise ValueError(f"metric {key}: a windowed metric needs a default window, not {default_window!r}")


def declare(m: Metric, into: dict | None = None) -> Metric:
    """Register a metric built elsewhere (core/measures.py builds the YAML ones)."""
    _check(m.key, m.grains, m.cutoff, m.default_window, m.windowed)
    into = _REGISTRY if into is None else into
    if m.key in into and into is _REGISTRY:
        raise ValueError(f"metric {m.key} is declared twice")
    into[m.key] = m
    return m


def metric(key: str, label: str, *, definition: str, formula: str, unit: str, records: str, grains=(),
           standard: str | None = None, period: str = "now", better: str = "higher", group: str = "Other",
           dated: bool = False, target: float | None = None, scoped: bool = False, windowed: bool = False,
           stock: bool = False, cutoff: str = "day", default_window: str | None = None):
    """Declare a metric; the decorated function computes it."""
    _check(key, grains, cutoff, default_window, windowed)

    def wrap(fn: Callable) -> Callable:
        declare(Metric(key, label, definition, formula, unit, records, fn, tuple(grains), standard, period, better,
                       group, dated, target, scoped, windowed, stock, cutoff, default_window))
        return fn
    return wrap


def provider(fn: Callable) -> Callable:
    """Register a source of metrics that depend on the pack: fn(pack) -> {key: Metric} (the YAML measures)."""
    if fn not in _PROVIDERS:
        _PROVIDERS.append(fn)
    return fn


def rollup(fn: Callable) -> Callable:
    """Register what a scope rolls up: fn(conn, fid) -> the ids beneath it (the register's containment)."""
    _ROLLUP[:] = [fn]
    return fn


def _load() -> None:
    for module in DECLARED_IN:
        import_module(module)


def registry(pack=None) -> dict[str, Metric]:
    """Every metric: the code's, the platform's YAML measures, and the pack's YAML measures on top (a pack may rebind a
    YAML measure to its own records, never replace one declared in code)."""
    _load()
    out = dict(_REGISTRY)
    for fn in _PROVIDERS:
        for m in fn(pack).values():
            if m.key in _REGISTRY:
                raise ValueError(f"measure {m.key} is declared in code; a YAML measure can't replace it")
            out[m.key] = m
    return dict(sorted(out.items()))


# ---- scope ------------------------------------------------------------------------------------------------------------

def scope_of(conn, fid: str) -> Scope:
    """`fid` and everything beneath it (graph.hierarchy.subtree, the entity pages' roll-up, registered as `rollup`)."""
    _load()
    row = conn.execute("select e.fuzer_id, e.kind, coalesce(l.label, e.fuzer_id) as label from entity e "
                       "left join entity_label l on l.fuzer_id = e.fuzer_id "
                       "where e.fuzer_id = %s and e.merged_into is null", (fid,)).fetchone()
    if row is None:
        raise MetricError(f"no entity {fid} in the register")
    return Scope(row["fuzer_id"], row["kind"], row["label"], frozenset({fid, *_ROLLUP[0](conn, fid)}))


def _scope(conn, scope, cache: dict) -> Scope | None:
    if scope is None or isinstance(scope, Scope):
        return scope
    key = ("scope", scope)
    if key not in cache:
        cache[key] = scope_of(conn, scope)
    return cache[key]


# ---- window -----------------------------------------------------------------------------------------------------------

def cutoff_day(conn, cutoff: str) -> date:
    """The day a window ends on when nobody says: today, or the last production day."""
    from . import clock

    if cutoff == "production_day":
        row = conn.execute("select max(g.value #>> '{}') as d from golden g join entity e on e.fuzer_id = g.fuzer_id "
                           "and e.kind = 'production_day' and e.retired_at is null and e.merged_into is null "
                           "where g.field = 'report_date'").fetchone()
        if row and row["d"]:
            return date.fromisoformat(row["d"][:10])
    return clock.today()


def _day(at) -> date | None:
    if at is None or isinstance(at, date) and not isinstance(at, datetime):
        return at
    return date.fromisoformat(str(at)[:10])


def _span(conn, m, window: str | None, at) -> windows.Span:
    """The window a metric or a row set is read for: the one named, else its own; ending `at`, else on its cut-off."""
    from . import clock

    try:
        return windows.span(window or m.default_window, _day(at) or cutoff_day(conn, m.cutoff), clock.now())
    except windows.WindowError as e:
        raise MetricError(str(e)) from e


# ---- reading ----------------------------------------------------------------------------------------------------------

def _get(pack, key: str, grain: str | None) -> Metric:
    m = registry(pack).get(key)
    if m is None:
        raise MetricError(f"no metric {key}")
    if grain and grain not in m.grains:
        raise MetricError(f"{key} can't be cut by {grain}; it can be cut by {', '.join(m.grains) or 'nothing'}")
    return m


def _legacy(conn, pack, m: Metric, window, at, grain, history) -> dict:
    """A metric in the older form: its own period, the whole register."""
    if at is not None and not m.dated and window is None:
        raise MetricError(f"{m.key} is not for a dated period; it is for {m.period}")
    at = _day(at) if m.dated else None
    out = (m.compute(conn, pack, grain, at=at) if m.dated else m.compute(conn, pack, grain)) or {}
    past = (out.get("history") or []) if history else []
    if history and m.dated and not past and out.get("as_of"):
        from datetime import timedelta

        end = date.fromisoformat(str(out["as_of"])[:10])
        for weeks in range(HISTORY_WEEKS - 1, -1, -1):
            day = end - timedelta(weeks=weeks)
            v = (m.compute(conn, pack, None, at=day) or {}).get("value") if weeks else out.get("value")
            if v is not None:
                past.append({"at": day.isoformat(), "value": v})
    return {**out, "history": past, "scope": None, "window": None, "previous": None}


def _queried(conn, pack, m: Metric, scope, window, at, grain, history) -> dict:
    q = Query(None, None, grain)
    q.scope = _scope(conn, scope, q.cache) if m.scoped else None
    span = _span(conn, m, window, at) if m.windowed else None
    q.span = span
    out = m.compute(conn, pack, q) or {}
    previous = past = None
    if span is not None:
        previous = (m.compute(conn, pack, q.at(windows.before(span))) or {}).get("value")
        if history:
            past = []
            for s in windows.steps(span, HISTORY_WEEKS):
                v = out.get("value") if s == span else (m.compute(conn, pack, q.at(s)) or {}).get("value")
                if v is not None:
                    past.append({"at": s.at.isoformat(), "value": v})
    elif history:
        past = out.get("history") or []
    return {**out, "history": past or [], "previous": previous,
            "scope": q.scope.id if q.scope else None, "window": window or m.default_window if span else None,
            "as_of": out.get("as_of") or (span.at.isoformat() if span else None),
            "span": {"start": span.start.isoformat(timespec="minutes"), "end": span.end.isoformat(timespec="minutes"),
                     "at": span.at.isoformat(), "days": span.days} if span else None}


def value(conn, pack, key: str, scope: str | None = None, window: str | None = None, at=None, grain: str | None = None,
          history: bool = False) -> dict:
    """One metric for a scope and a window: its declaration, its headline value and, with a grain, its rows. With
    `history`, its values over its last HISTORY_WEEKS periods (windows of the same length, for a windowed one),
    oldest first; `previous` is its value over the window before.

    `scope` (an entity id, None for the whole register) and `window` (a name in core/windows, None for the metric's own
    period) are the report contract (docs/reports.md). The result says what it was computed for in `scope` and
    `window`; a metric not cut by scope or window answers for the whole register and its own period, and says so
    with None."""
    m = _get(pack, key, grain)
    out = _queried(conn, pack, m, scope, window, at, grain, history) if m.queried else \
        _legacy(conn, pack, m, window, at, grain, history)
    if scope and not out["scope"]:
        out["note"] = " ".join(x for x in (out.get("note"), WHOLE) if x)
    return {**m.describe(), "value": out.get("value"), "unknown": bool(out.get("unknown")),
            "rows": out.get("rows") or [], "refs": out.get("refs") or [],
            "as_of": out.get("as_of"), "note": out.get("note"), "grain": grain, "history": out["history"],
            "scope": out["scope"], "window": out["window"], "previous": out["previous"], "span": out.get("span")}


def series(conn, pack, key: str, scope: str | None = None, window: str | None = None, at=None) -> dict:
    """A metric's value for each day of the window (at least TREND_MIN_DAYS, so a one-day window still has a line),
    oldest first: {"points": [{at, value}], "scope", "window", "span"}. A metric that isn't windowed gives its
    history instead (its own periods), and says so with `window: None`."""
    m = _get(pack, key, None)
    if not m.windowed:
        v = value(conn, pack, key, scope, None, None, history=True)
        return {"points": v["history"], "scope": v["scope"], "window": None, "span": None, "metric": v}
    q = Query(None, None)
    q.scope = _scope(conn, scope, q.cache) if m.scoped else None
    span = _span(conn, m, window, at)
    drawn = span if span.days >= TREND_MIN_DAYS else windows.span(f"{TREND_MIN_DAYS}d", span.at, span.end)
    points = []
    for d in windows.daily(drawn):
        v = (m.compute(conn, pack, q.at(d)) or {}).get("value")
        if v is not None:
            points.append({"at": d.at.isoformat(), "value": v})
    return {"points": points, "scope": q.scope.id if q.scope else None, "window": window or m.default_window,
            "span": {"start": drawn.start.isoformat(timespec="minutes"), "end": drawn.end.isoformat(timespec="minutes"),
                     "at": drawn.at.isoformat(), "days": drawn.days}, "metric": m.describe()}
