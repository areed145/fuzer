"""Row sets (the reports design, Nov 2026): registered lists with fixed columns, each row a case, an entity or a source
row that opens it, read for any scope and window like a measure. A report's table names a row set by key and never a
table, a source or a query, so the rows it lists are the ones the rest of Fuzer lists.

Two places declare one, and both land here:

    in YAML    a list of the register's records (`core/row_sets.yaml`, and a pack's `row_sets:`), in the measures'
               `from:` shape, with its columns, read by `ops/measures.py`
    in code    `@row_set(...)` next to the module that owns the rows (cases, plan lines: ops/metrics.py)

    open_cases:                     # (YAML form)
      label: Downtime events
      from: {kind: downtime, at: start, links: [affects, caused_by]}   # as a measure's from (core/measures.py)
      stock: false                  # true: the records open at the window's end, not those dated inside it
      cutoff: production_day        # what `at` is when nobody says
      window: 30d                   # its own period when a report doesn't name one
      order: lost_oil_bbl desc      # a field (or @at) and asc or desc
      columns:
        - {key: start, label: Start, field: start}
        - {key: on, label: On, field: "@subjects"}        # @label: the record's name; @subjects: what it links to
        - {key: lost, label: Oil lost, field: lost_oil_bbl, unit: bbl, align: end}

    registry(pack)                           {key: RowSet}
    read(conn, pack, key, scope=None, window=None, at=None, limit=50)
        -> {key, label, columns: [{key, label, unit?, align?}], rows: [{key, cells, href}], total, note, scoped,
            scope, window, span}

An entity row opens its entity page (`/e/<id>`), a case row its case (`/cases/<id>`), as hrefs for the shell's router.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from importlib import import_module
from pathlib import Path

import yaml

from . import windows

BUILTIN = Path(__file__).with_name("row_sets.yaml")
DECLARED_IN = ("fuzer.ops.metrics", "fuzer.ops.measures")
FIELDS = {"label", "definition", "from", "stock", "cutoff", "window", "order", "columns"}
COLUMN = {"key", "label", "field", "unit", "align"}
LIMIT = 500
_REGISTRY: dict[str, "RowSet"] = {}


class RowSetError(ValueError):
    """A row set that isn't registered, or one declared with something the model doesn't have."""


@dataclass(frozen=True)
class RowSet:
    key: str
    label: str
    definition: str
    columns: tuple
    compute: Callable = field(repr=False, compare=False)  # (conn, pack, q) -> {"rows": [...], "note"}
    scoped: bool = True
    windowed: bool = True
    cutoff: str = "day"
    default_window: str | None = "30d"
    source: str = "platform"

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "definition": self.definition, "columns": list(self.columns),
                "scoped": self.scoped, "windowed": self.windowed, "cutoff": self.cutoff,
                "default_window": self.default_window, "source": self.source}


def row_set(key: str, label: str, *, definition: str, columns: list[dict], scoped: bool = True,
            windowed: bool = True, cutoff: str = "day", default_window: str | None = "30d"):
    """Declare a row set in code; the decorated function takes (conn, pack, q) and returns {"rows", "note"}, each row
    {key, cells, href, sort?}, already in its order."""
    def wrap(fn):
        if key in _REGISTRY:
            raise ValueError(f"row set {key} is declared twice")
        _REGISTRY[key] = RowSet(key, label, definition, tuple(columns), fn, scoped, windowed, cutoff, default_window)
        return fn
    return wrap


def _check(key: str, r: dict) -> dict:
    from . import measures

    def bad(why: str):
        raise RowSetError(f"row set {key}: {why}")

    if not isinstance(r, dict):
        bad("is not a mapping")
    if extra := set(r) - FIELDS:
        bad(f"unknown field {sorted(extra)}")
    if not r.get("label") or not r.get("columns"):
        bad("needs a label and columns")
    try:  # the same `from` as a measure, checked the same way
        measures._check(f"row set {key}", {"label": "x", "unit": "x", "definition": "x", "formula": "x",
                                          "records": "x", "from": r.get("from"), "stock": r.get("stock", False)})
    except measures.MeasureError as e:
        bad(str(e).split(": ", 1)[1])
    for c in r["columns"]:
        if not isinstance(c, dict) or set(c) - COLUMN or not all(isinstance(c.get(k), str) and c[k]
                                                                  for k in ("key", "label", "field")):
            bad(f"a column is {{key, label, field, unit?, align?}} of text (quote On, Yes, No), not {c!r}")
        if c.get("align", "start") not in ("start", "end"):
            bad("a column's align is start or end")
    if r.get("cutoff", "day") not in windows.CUTOFFS:
        bad(f"cutoff isn't one of {', '.join(windows.CUTOFFS)}")
    if not windows.valid(r.get("window", "30d")):
        bad(f"window {r.get('window')!r} isn't a named window")
    return r


def checked(raw: dict | None) -> dict:
    """A pack's row sets, checked: {key: row set}."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise RowSetError("row_sets holds a mapping of row set key to row set")
    return {k: _check(k, v) for k, v in raw.items()}


@cache
def builtin() -> dict:
    raw = yaml.safe_load(BUILTIN.read_text())
    return {k: _check(k, v) for k, v in raw["row_sets"].items()}


_ENGINE: list[Callable] = []


def engine(fn: Callable) -> Callable:
    """Register what reads a YAML row set's rows: fn(spec, conn, pack, q) -> {"rows", "note"} (ops/measures.py)."""
    _ENGINE[:] = [fn]
    return fn


def _yaml(key: str, spec: dict, source: str) -> RowSet:
    return RowSet(key, spec["label"], spec.get("definition") or "",
                  tuple({k: c[k] for k in ("key", "label", "unit", "align") if k in c} for c in spec["columns"]),
                  lambda conn, pack, q, _s=spec: _ENGINE[0](_s, conn, pack, q), True, True, spec.get("cutoff", "day"),
                  spec.get("window", "30d"), source)


def registry(pack=None) -> dict[str, RowSet]:
    for module in DECLARED_IN:
        import_module(module)
    out = dict(_REGISTRY)
    for source, specs in (("platform", builtin()), ("bundle", getattr(pack, "row_sets", None) or {})):
        for k, spec in specs.items():
            if k in _REGISTRY:
                raise RowSetError(f"row set {k} is declared in code; a YAML row set can't replace it")
            out[k] = _yaml(k, spec, source)
    return dict(sorted(out.items()))


def read(conn, pack, key: str, scope: str | None = None, window: str | None = None, at=None,
         limit: int | None = 50) -> dict:
    """A row set's rows for a scope and window, at most `limit` of them (`total` counts them all)."""
    from . import metrics

    rs = registry(pack).get(key)
    if rs is None:
        raise RowSetError(f"no row set {key}")
    q = metrics.Query(None, None)
    q.scope = metrics._scope(conn, scope, q.cache) if rs.scoped else None
    span = metrics._span(conn, rs, window, at) if rs.windowed else None
    q.span = span
    out = rs.compute(conn, pack, q) or {}
    rows = out.get("rows") or []
    limit = min(limit or LIMIT, LIMIT)
    note = out.get("note")
    if scope and not q.scope:
        note = " ".join(x for x in (note, "For the whole register: it isn't cut by entity.") if x)
    return {"key": key, "label": rs.label, "columns": list(rs.columns),
            "rows": [{k: r.get(k) for k in ("key", "cells", "href")} for r in rows[:limit]], "total": len(rows),
            "note": note, "scoped": bool(q.scope) or not scope, "scope": q.scope.id if q.scope else None,
            "window": (window or rs.default_window) if span else None,
            "span": {"start": span.start.isoformat(timespec="minutes"), "end": span.end.isoformat(timespec="minutes"),
                     "at": span.at.isoformat(), "days": span.days} if span else None}
