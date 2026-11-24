"""Reading a report (docs/reports.md): a definition from `core/reports`, for a scope (an entity in the register) and a
named window, answered one panel at a time from the application's own model, the metric registry, trend specs and
the Work list, so a figure on a report is the figure everywhere else.

    view(conn, pack, key, scope, window, at)                 the definition, the scope, the window, the scopes to pick
                                                              from and the entities each repeat row draws for
    panel_data(conn, pack, key, panel_id, scope, window, at)  one panel's result: the envelope and its type's fields
    panel_type(name)                                          register fn(conn, pack, report, panel, scope, window)
    default_scope(conn, pack, report)                         the scope a report opens on when its link names none

A panel type's function gets the definition, the panel, the scope as {id, label, kind} and the resolved window
(`core/windows.resolve`) and returns its own fields; the envelope (title, as_of, note, scoped, refs, href) is filled
here. The modules in `DECLARED_IN` register types: this one registers stat, trend, figure, table and cases; the
commentary registers note (ops/report_notes) and summary (agents/report_summary).
"""

from collections.abc import Callable
from datetime import date
from importlib import import_module
from urllib.parse import urlencode

from ..core import metrics, reports, windows
from ..core.reports import ReportError

DECLARED_IN = ("fuzer.ops.report_pages", "fuzer.ops.report_notes", "fuzer.agents.report_summary")
_TYPES: dict[str, Callable] = {}
SCOPES = 300
FIGURE_ROWS = 12
# A measure's grain and the Work list filter that shows the same rows.
LIST_FILTER = {"queue": "queue", "type": "kind"}


def panel_type(name: str):
    """Register the function that answers a panel type."""
    def wrap(fn: Callable) -> Callable:
        _TYPES[name] = fn
        return fn
    return wrap


def types() -> dict[str, Callable]:
    for module in DECLARED_IN:
        import_module(module)
    return dict(_TYPES)


def _ref(conn, fid: str) -> dict | None:
    row = conn.execute(
        "select n.fuzer_id as id, n.kind, coalesce(l.label, n.fuzer_id) as label from entity n "
        "left join entity_label l on l.fuzer_id = n.fuzer_id where n.fuzer_id = %s and n.merged_into is null", (fid,)
    ).fetchone()
    return dict(row) if row else None


def scopes(conn, kinds: list[str]) -> list[dict]:
    """The entities a report can be scoped to, by kind in the definition's order, then by name."""
    rows = conn.execute(
        "select n.fuzer_id as id, n.kind, coalesce(l.label, n.fuzer_id) as label from entity n "
        "left join entity_label l on l.fuzer_id = n.fuzer_id "
        "where n.kind = any(%s) and n.merged_into is null and n.retired_at is null "
        "order by array_position(%s::text[], n.kind::text), 3 limit %s", (kinds, kinds, SCOPES)).fetchall()
    return [dict(r) for r in rows]


def _scope(conn, d: dict, scope: str | None, also: str | None = None, pack=None) -> dict:
    if scope:
        ref = _ref(conn, scope)
        if ref is None:
            raise ReportError(f"no entity {scope}")
        if ref["kind"] not in d["scope"]["kinds"] and ref["kind"] != also:
            raise ReportError(f"{d['title']} is for a {' or '.join(d['scope']['kinds'])}, not a {ref['kind']}")
        return ref
    # the project's own facility first, where the report takes facilities, else the first by name
    first = scopes(conn, d["scope"]["kinds"][:1])
    own = next((s for s in first if s["kind"] == "facility" and s["label"] == getattr(pack, "facility", None)), None)
    return own or (first[0] if first else {"id": None, "label": "Everything", "kind": None})


def default_scope(conn, pack, d: dict) -> dict:
    """The scope a report opens on when its link names none: the project's own facility, else the first by name."""
    return _scope(conn, d, None, pack=pack)


def _window(d: dict, window: str | None, at) -> dict:
    window = window or d["window"]["default"]
    if window not in d["window"]["options"]:
        raise ReportError(f"{d['title']} reads {', '.join(d['window']['options'])}, not {window}")
    # `asked`: the day was named in the link; without it each measure reads its own latest day (a production day)
    return {**windows.resolve(window, at), "asked": at is not None}


def _at(w: dict) -> date | None:
    """The day to read measures for: the one asked for, else None so each reads its own latest."""
    return date.fromisoformat(w["at"]) if w.get("asked") else None


def view(conn, pack, key: str, scope: str | None = None, window: str | None = None, at=None) -> dict:
    """The report for a scope and window: its definition, what they resolve to, the scopes it can take and, for each
    repeat row, the entities beneath the scope it draws for (by name, capped)."""
    from ..graph import hierarchy

    d = reports.get(pack, key)
    ref = _scope(conn, d, scope, pack=pack)
    w = _window(d, window, at)
    repeats = {}
    for r in d["rows"]:
        if rep := r.get("repeat"):
            tree = hierarchy.subtree(conn, ref["id"]) if ref["id"] else {}
            kids = sorted(({"id": i, "label": n["label"], "kind": n["kind"]} for i, n in tree.items()
                           if n["kind"] == rep["kind"]), key=lambda x: x["label"])
            repeats[r["id"]] = kids[:rep["limit"]]
    choices = scopes(conn, d["scope"]["kinds"])
    choices.sort(key=lambda c: c["id"] != ref["id"])  # the one shown first
    return {"definition": d, "scope": ref, "window": w, "repeats": repeats, "scopes": choices}


def panel_data(conn, pack, key: str, panel_id: str, scope: str | None = None, window: str | None = None,
               at=None) -> dict:
    """One panel's result: what every panel carries, then its type's own fields; `error` when it couldn't be read."""
    d = reports.get(pack, key)
    panel = next((p for p in reports.panels(d) if p["id"] == panel_id), None)
    if panel is None:
        raise ReportError(f"{d['title']} has no panel {panel_id}")
    # a repeat row's panels are read for each entity it draws for, of the repeat's kind
    row = next(r for r in d["rows"] if r["id"] == panel["row"])
    ref = _scope(conn, d, scope, (row.get("repeat") or {}).get("kind"), pack)
    w = _window(d, window, at)
    out = {"id": panel_id, "type": panel["type"], "title": panel.get("title") or "", "scope": ref, "window": w,
           "as_of": None, "note": None, "scoped": ref["id"] is None, "refs": [], "href": None}
    fn = types().get(panel["type"])
    if fn is None:
        return {**out, "error": f"{panel['type']} panels aren't available yet"}
    try:
        fields = fn(conn, pack, d, panel, ref, w) or {}
    except (metrics.MetricError, ValueError) as e:
        return {**out, "error": str(e)}
    return {**out, **fields, "title": panel.get("title") or fields.get("title") or out["title"]}


# --- the engine's panels ---------------------------------------------------------------------------------------------

def _value(conn, pack, key: str, scope: dict, w: dict, grain: str | None = None, history: bool = False) -> dict:
    m = metrics.registry()[key]
    at = _at(w) if m.dated else None
    return metrics.value(conn, pack, key, scope=scope["id"], window=w["key"], at=at, grain=grain, history=history)


def _scoped(scope: dict, m: dict) -> bool:
    return scope["id"] is None or m.get("scope") == scope["id"]


def _note(scope: dict, m: dict) -> str | None:
    """What a figure leaves out: why it is empty, and the scope or period it is for when not the report's."""
    whole = not _scoped(scope, m)
    period = m["period"] if m.get("window") is None and m.get("period") not in (None, "now") else None
    said = ("Whole register" if whole else "") + (f", {period}" if whole and period else period.capitalize() if period else "")
    return "; ".join(p for p in (m.get("note"), said) if p) or None


def _list_href(f: dict) -> str:
    return "/list?" + urlencode({k: ("true" if v is True else v) for k, v in f.items() if v not in (None, False, "")})


def _row_href(key: str, grain: str, value: str, scope: dict) -> str:
    if grain in LIST_FILTER and key.startswith("work."):
        return _list_href({LIST_FILTER[grain]: value, "within": scope["id"],
                           **({"state": "all"} if key in ("work.arrivals", "work.closed") else {})})
    return f"/metrics/{key}?" + urlencode({"grain": grain})


@panel_type("stat")
def stat(conn, pack, d, panel, scope, w) -> dict:
    m = _value(conn, pack, panel["measure"], scope, w, history=True)
    past = m.get("history") or []
    previous = m.get("previous")
    if previous is None and len(past) >= 2 and str(past[-1]["at"])[:10] == str(m.get("as_of") or "")[:10]:
        previous = past[-2]["value"]
    return {"title": m["label"], "measure": m["key"], "value": m["value"], "unit": m["unit"], "target": m["target"],
            "better": m["better"], "previous": previous, "history": past, "as_of": m.get("as_of"),
            "note": _note(scope, m), "scoped": _scoped(scope, m), "refs": m["refs"], "href": f"/metrics/{m['key']}"}


@panel_type("trend")
def trend(conn, pack, d, panel, scope, w) -> dict:
    from ..analysis import trend_spec

    spec = trend_spec.from_measures(conn, pack, panel["measures"], scope["id"], w["key"], _at(w))
    whole = [e for e in spec.get("errors") or [] if "whole register" in e]
    return {"title": spec.get("title"), "spec": spec, "scoped": scope["id"] is None or not whole,
            "note": "; ".join((["Whole register"] if whole else []) + [e for e in spec.get("errors") or [] if e not in whole])
            or None, "as_of": spec["time"]["end"],
            "href": f"/metrics/{panel['measures'][0]}"}


def _grain_rows(conn, pack, panel, scope, w) -> tuple[dict, list[dict]]:
    m = _value(conn, pack, panel["measure"], scope, w, grain=panel["grain"])
    rows = sorted(m["rows"], key=lambda r: -(r["value"] or 0))[: panel.get("limit", FIGURE_ROWS)]
    return m, [{"key": r["key"], "label": r["label"], "value": r["value"], "refs": r.get("refs") or [],
                "href": _row_href(m["key"], panel["grain"], r["key"], scope)} for r in rows]


def _figure(chart: str, rows: list[dict], unit: str) -> dict:
    """A server-built chart of rows by a grain: bars, a pareto with its running share, or one bar of shares."""
    labels, values = [r["label"] for r in rows], [r["value"] for r in rows]
    hover = f"%{{y}}: %{{x}} {unit}<extra></extra>"
    if chart == "share":
        data = [{"type": "bar", "orientation": "h", "name": r["label"], "y": [""], "x": [r["value"]],
                 "hovertemplate": f"{r['label']}: %{{x}} {unit}<extra></extra>"} for r in rows]
        return {"data": data, "layout": {"barmode": "stack", "showlegend": True, "yaxis": {"visible": False}}}
    data = [{"type": "bar", "orientation": "h", "y": labels[::-1], "x": values[::-1], "name": unit,
             "hovertemplate": hover}]
    if chart == "pareto" and sum(values):
        total, run, cum = sum(values), 0, []
        for v in values:
            run += v
            cum.append(round(100 * run / total, 1))
        data.append({"type": "scatter", "mode": "lines+markers", "y": labels[::-1], "x": cum[::-1], "xaxis": "x2",
                     "name": "cumulative %", "hovertemplate": "%{y}: %{x}% of the total<extra></extra>"})
    layout = {"showlegend": chart == "pareto", "height": 122 + 34 * max(len(rows), 2), "bargap": 0.35,
              "margin": {"t": 40, "r": 16, "b": 36, "l": 8},  # room for the full-screen button
              "yaxis": {"automargin": True}, "xaxis": {"title": {"text": unit}},
              **({"xaxis2": {"overlaying": "x", "side": "top", "range": [0, 100], "ticksuffix": "%"}}
                 if chart == "pareto" else {})}
    return {"data": data, "layout": layout}


@panel_type("figure")
def figure(conn, pack, d, panel, scope, w) -> dict:
    m, rows = _grain_rows(conn, pack, panel, scope, w)
    return {"title": f"{m['label']} by {panel['grain']}", "figure": _figure(panel.get("chart", "bar"), rows, m["unit"]),
            "rows": rows, "unit": m["unit"], "as_of": m.get("as_of"), "note": _note(scope, m),
            "scoped": _scoped(scope, m), "refs": m["refs"], "href": f"/metrics/{m['key']}?grain={panel['grain']}"}


@panel_type("table")
def table(conn, pack, d, panel, scope, w) -> dict:
    if panel.get("rows"):
        out = import_module("fuzer.core.rowsets").read(conn, pack, panel["rows"], scope["id"], w["key"], _at(w),
                                                    panel.get("limit", 20))
        return {"columns": out["columns"], "rows": out["rows"], "total": out["total"], "note": out.get("note"),
                "scoped": out.get("scoped", False)}
    m, rows = _grain_rows(conn, pack, panel, scope, w)
    return {"title": f"{m['label']} by {panel['grain']}",
            "columns": [{"key": "label", "label": panel["grain"].capitalize()},
                        {"key": "value", "label": m["label"], "unit": m["unit"], "align": "end"}],
            "rows": [{"key": r["key"], "cells": {"label": r["label"], "value": r["value"]}, "href": r["href"]}
                     for r in rows],
            "total": len(m["rows"]), "as_of": m.get("as_of"), "note": _note(scope, m), "scoped": _scoped(scope, m),
            "refs": m["refs"], "href": f"/metrics/{m['key']}?grain={panel['grain']}"}


@panel_type("cases")
def cases(conn, pack, d, panel, scope, w) -> dict:
    from . import worklist

    f = {**panel.get("filter", {}), "within": scope["id"]}
    out = worklist.rows(conn, **{k: v for k, v in f.items() if v is not None}, limit=panel.get("limit", 10))
    rows = out["rows"]
    named = {x for r in rows for x in (r.get("facility"), r.get("area"), r.get("asset"), *(r.get("subjects") or []))}
    labels = {k: v for k, v in out["labels"].items() if k in named}
    return {"cases": rows, "total": out["total"], "overdue": out["facets"].get("overdue", 0), "labels": labels,
            "scoped": True, "as_of": w["at"], "href": _list_href(f),
            "refs": [f"case:{r['item_id']}" for r in rows if r.get("item_id")]}
