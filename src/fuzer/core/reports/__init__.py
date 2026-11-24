"""Report definitions (docs/reports.md): a scope, a window and rows of panels, read from `core/reports/*.yaml` and a
pack's `reports:`, and checked when read, so a definition that names an unknown panel type, measure, grain, kind or
row set is refused with the reason instead of drawing a broken page. Reading a report for a scope and window is
`ops/report_pages.py`.

    load(pack)          every definition, the platform's with the pack's on top (`source`: platform or bundle)
    get(pack, key)      one definition; ReportError when there is none
    check(definition)   the definition with its defaults filled; ReportError with the reason
    panels(definition)  every panel, flattened out of columns, each with its row's id
"""

from functools import cache
from pathlib import Path

import yaml

from .. import windows

HERE = Path(__file__).parent
SCHEMA = 1
TYPES = ("stat", "trend", "figure", "table", "cases", "note", "summary", "narrative", "view")
# The plant's own analyses, each answered by its own read for the whole register over its own period, drawn on a report
# by a `view` panel: they moved here from the retired Scorecard page and keep their own cards.
VIEWS = {
    "across-assets": "Across assets",
    "wells-forecast": "Wells against their own forecast",
    "well-screen": "Water, gas and test age",
    "allocation-factor": "Allocated oil against well tests",
    "intervention-gains": "Did the well work pay off?",
    "gas-balance": "Gas balance",
    "gas-lift-heading": "Gas lift heading",
    "water-injection": "Water injection",
    "reservoir-pressure": "Reservoir pressure",
    "quality": "Separator and export quality",
    "cost-of-unreliability": "Cost of unreliability by unit",
    "leak-surveys": "Leak surveys",
    "alarm-performance": "Alarm system performance",
    "backlog-burn": "Is the backlog under control?",
    "work-execution": "Does work finish when planned, and do repairs hold?",
    "accounts": "Against the accounts",
}
# The day a window ends on when the link names none: today, or the last production day (the daily report's day).
WINDOW_AT = ("day", "production_day")
CHARTS = ("bar", "pareto", "share")
SIZE = {"stat": 1, "table": 4, "cases": 4}
CASE_FILTERS = ("state", "kind", "stage", "holder", "queue", "source", "overdue", "q", "sort")
REPEAT_LIMIT = 24
# The daily and regulatory reports' own routes sit under /reports.
RESERVED = ("daily", "regulatory", "obligations")


class ReportError(ValueError):
    """A definition that can't be drawn, or a report that isn't there."""


def _measures() -> dict:
    from .. import metrics

    return metrics.registry()


def _rowsets() -> set[str] | None:
    """The registered row sets, or None where the platform has no row-set registry yet."""
    from importlib import import_module

    try:  # the measures add the registry (core/rowsets.py)
        rowsets = import_module("fuzer.core.rowsets")
    except ImportError:
        return None
    return set(rowsets.registry())


def _kinds() -> set[str]:
    from .. import ontology

    return set(ontology.builtin()["kinds"])


def _measure(where: str, key, grain=None) -> None:
    m = _measures().get(key)
    if m is None:
        raise ReportError(f"{where}: no measure {key!r}")
    if grain is not None and grain not in m.grains:
        raise ReportError(f"{where}: {key} can't be cut by {grain!r}; it can be cut by {', '.join(m.grains) or 'nothing'}")


def _panel(where: str, p: dict, ids: set) -> dict:
    if not isinstance(p, dict):
        raise ReportError(f"{where}: a panel is a mapping")
    t = p.get("type")
    if t not in TYPES:
        raise ReportError(f"{where}: panel type {t!r} isn't one of {', '.join(TYPES)}")
    pid = p.get("id")
    if not isinstance(pid, str) or not pid:
        raise ReportError(f"{where}: every panel needs an id")
    if pid in ids:
        raise ReportError(f"{where}: panel id {pid!r} is used twice")
    ids.add(pid)
    where = f"{where}.{pid}"
    out = {**p, "size": p.get("size", SIZE.get(t, 2))}
    if out["size"] not in (1, 2, 3, 4):
        raise ReportError(f"{where}: size is 1 to 4 quarters of the width, not {out['size']!r}")
    if "phone" in out and not isinstance(out["phone"], bool):
        raise ReportError(f"{where}: phone is true or false")
    if t == "stat":
        _measure(where, p.get("measure"))
    elif t == "trend":
        keys = p.get("measures")
        if not isinstance(keys, list) or not keys:
            raise ReportError(f"{where}: a trend names its measures")
        for k in keys:
            _measure(where, k)
    elif t == "figure":
        _measure(where, p.get("measure"), p.get("grain") or "")
        out["chart"] = p.get("chart", "bar")
        if out["chart"] not in CHARTS:
            raise ReportError(f"{where}: chart {out['chart']!r} isn't one of {', '.join(CHARTS)}")
    elif t == "table":
        if p.get("rows"):
            known = _rowsets()
            if known is None or p["rows"] not in known:
                raise ReportError(f"{where}: no row set {p['rows']!r}")
        elif p.get("measures") and p.get("grain"):  # one row per grain value, one column per measure
            if not isinstance(p["measures"], list):
                raise ReportError(f"{where}: measures is a list")
            for k in p["measures"]:
                _measure(where, k, p["grain"])
        elif p.get("measure") and p.get("grain"):
            _measure(where, p["measure"], p["grain"])
        else:
            raise ReportError(f"{where}: a table names a row set, or a measure (or measures) and a grain")
    elif t == "cases":
        f = p.get("filter") or {}
        if not isinstance(f, dict) or (bad := set(f) - set(CASE_FILTERS)):
            raise ReportError(f"{where}: the cases filter takes {', '.join(CASE_FILTERS)}; not {sorted(bad) if isinstance(f, dict) else f}")
        out["filter"] = f
    elif t == "view" and p.get("view") not in VIEWS:
        raise ReportError(f"{where}: no view {p.get('view')!r}; it is one of {', '.join(VIEWS)}")
    elif t == "note" and "text" in p and not isinstance(p["text"], str):
        raise ReportError(f"{where}: a note's text is markdown")
    if "limit" in p and (not isinstance(p["limit"], int) or p["limit"] < 1):
        raise ReportError(f"{where}: limit is a positive whole number")
    return out


def check(d: dict, source: str = "platform") -> dict:
    """A definition with its defaults filled (row ids, sizes, window options), or ReportError with the reason."""
    if not isinstance(d, dict):
        raise ReportError("a report definition is a mapping")
    key = d.get("key")
    where = f"report {key}"
    if not key or not isinstance(key, str) or not key.replace("-", "").replace("_", "").isalnum():
        raise ReportError("a report needs a key of letters, digits, - and _")
    if key in RESERVED:
        raise ReportError(f"report key {key} is taken by the {key} reports' own pages")
    if d.get("schema", SCHEMA) != SCHEMA:
        raise ReportError(f"{where}: schema {d.get('schema')} isn't {SCHEMA}")
    if not d.get("title"):
        raise ReportError(f"{where}: needs a title")
    scope = d.get("scope") or {}
    kinds = scope.get("kinds") or []
    if not kinds or (bad := set(kinds) - _kinds()):
        raise ReportError(f"{where}: scope kinds {sorted(bad) if kinds else '(none)'} aren't kinds in the ontology")
    win = d.get("window") or {}
    options = win.get("options") or list(windows.WINDOWS)
    default = win.get("default", options[0])
    if bad := [w for w in [*options, default] if w not in windows.WINDOWS]:
        raise ReportError(f"{where}: no window {bad[0]!r}; it is one of {', '.join(windows.WINDOWS)}")
    if default not in options:
        raise ReportError(f"{where}: the default window {default} isn't one of its options")
    ends = win.get("at", "day")
    if ends not in WINDOW_AT:
        raise ReportError(f"{where}: a window ends on {' or '.join(WINDOW_AT)} when the link names no day, not {ends!r}")
    rows, ids = [], set()
    for i, r in enumerate(d.get("rows") or []):
        rid = r.get("id") or f"r{i}"
        rw = f"{where}.{rid}"
        panels = []
        for p in r.get("panels") or []:
            if isinstance(p, dict) and p.get("type") == "column":
                held = p.get("panels") or []
                if not held or any(isinstance(q, dict) and q.get("type") == "column" for q in held):
                    raise ReportError(f"{rw}: a column holds panels, one level deep")
                inner = [_panel(rw, q, ids) for q in held]
                size = p.get("size", 2)
                if size not in (1, 2, 3, 4):
                    raise ReportError(f"{rw}: size is 1 to 4 quarters of the width, not {size!r}")
                panels.append({"type": "column", "size": size, "panels": inner})
            else:
                panels.append(_panel(rw, p, ids))
        if not panels:
            raise ReportError(f"{rw}: a row holds at least one panel")
        row = {"id": rid, "panels": panels, **({"title": r["title"]} if r.get("title") else {})}
        if rep := r.get("repeat"):
            if rep.get("kind") not in _kinds():
                raise ReportError(f"{rw}: repeat kind {rep.get('kind')!r} isn't a kind in the ontology")
            row["repeat"] = {"kind": rep["kind"], "limit": min(int(rep.get("limit", 8)), REPEAT_LIMIT)}
        rows.append(row)
    if not rows:
        raise ReportError(f"{where}: a report holds at least one row")
    if len({r["id"] for r in rows}) != len(rows):
        raise ReportError(f"{where}: two rows share an id")
    return {"schema": SCHEMA, "key": key, "title": d["title"], "version": int(d.get("version", 1)),
            "for": list(d.get("for") or []), "scope": {"kinds": list(kinds)},
            "window": {"default": default, "options": options, "at": ends}, "rows": rows, "source": source}


def panels(d: dict) -> list[dict]:
    """Every panel of a checked definition, out of its columns, in order, each with `row` (its row's id)."""
    out = []
    for r in d["rows"]:
        for p in r["panels"]:
            for q in p["panels"] if p["type"] == "column" else [p]:
                out.append({**q, "row": r["id"]})
    return out


@cache
def builtin() -> dict[str, dict]:
    """The platform's definitions, one YAML file each, checked."""
    out = {}
    for path in sorted(HERE.glob("*.yaml")):
        d = check(yaml.safe_load(path.read_text()))
        out[d["key"]] = d
    return out


def checked(raw) -> dict:
    """A pack's `reports:` (a mapping of key to definition), checked; what the pack stores."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ReportError("reports holds a mapping of report key to definition")
    return {k: check({**d, "key": k}, "bundle") for k, d in raw.items()}


def load(pack=None) -> dict[str, dict]:
    """Every report: the platform's, with the pack's added or put in their place."""
    return builtin() | (getattr(pack, "reports", None) or {})


def get(pack, key: str) -> dict:
    d = load(pack).get(key)
    if d is None:
        raise ReportError(f"no report {key}")
    return d
