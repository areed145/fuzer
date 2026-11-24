"""Trend specs: one config for every chart over time (the trends design, Nov 2026). The server decides what a trend
shows and the browser only draws it, so the entity page, a case, an incident and a metric all hand the one chart
(`ui/src/trends/TrendChart.tsx`) the same shape:

    spec(conn, fuzer_id, at=None, hours_before=48, hours_after=24, focus=None) -> TrendSpec
    from_measures(conn, pack, keys, scope, window, at) -> TrendSpec   registered measures over a report window

    {"version": 1, "subject", "title", "unit", "unit_label", "provider", "step_minutes", "template",
     "time": {"start", "end", "centre"},
     "panels": [{"key", "label", "show", "axis": "auto", "series": [key, ...]}],
     "series": [{"key", "point", "tag", "label", "description", "unit", "uom", "role", "on", "link", "as", "axis",
                 "values", "min", "max", "mean", "last", "limits", "methods", "modelled", "show"}],
     "focus": [key, ...] | None,
     "events": [{"lane", "label", "show"}],
     "marks": [{"kind": "window" | "time", "start", "end" | "at", "label", "tone"}],
     "missing": [...], "errors": [...]}

A panel lists its series by key, so a series keeps its identity (and its colour) whatever else is switched on. Every
series carries its label and printed unit, worded here once: the browser does not name a trace. `focus` opens the chart
on the series it names (keys, points or measurement roles); the rest are one tap away. Built on `trend_view.view`,
which picks the template and reads the values, so the window, centre, limits and provider are the trend view's own.
"""

import re
from datetime import datetime, timedelta

from ..core import wording
from . import trend_view, values

VERSION = 1
LEAD_IN = timedelta(hours=12)
# What a trend can be compared with: the same hours a period earlier, or the same hours around the unit's earlier
# incidents, each lined up on this window's centre.
SHIFTS = {"1d": timedelta(days=1), "7d": timedelta(days=7), "28d": timedelta(days=28)}
PAST_INCIDENTS = 3
# The event lanes the timeline draws under a trend, in order, with their short names.
LANES = {"alarms": "Alarms", "log": "Log", "cases": "Cases", "maintenance": "Work orders", "downtime": "Downtime"}

_TITLE_WORD = re.compile(r"^[A-Z][a-z]+(-[A-Za-z][a-z]+)*$")


def sentence(s: str) -> str:
    """A name in sentence case: "Thrust Bearing Temperature" reads "Thrust bearing temperature". Codes and acronyms (all
    capitals, or with digits) keep their case."""
    return " ".join(w.lower() if i and _TITLE_WORD.match(w) else w for i, w in enumerate(s.split(" ")))


def tag(point: str) -> str:
    """A point as a person reads it: its tag without the historian's server prefix and value suffix
    ("OK_GGCP:20-FT-2100B.PV" reads "20-FT-2100B")."""
    return re.sub(r"\.(PV|CV|OUT|SP|MV)$", "", re.sub(r"^[^:]*:", "", point), flags=re.IGNORECASE)


def _neighbour(t: dict) -> str | None:
    on = t.get("on") or {}
    return on.get("label") if on.get("relation") not in (None, "self") else None


def trace_name(t: dict) -> str:
    """What a trace is called: its role, and the neighbour it is on when not the unit."""
    what = t.get("role_label") or (sentence(t["description"]) if t.get("description") else t["point"])
    near = _neighbour(t)
    return f"{what} · {near}" if near else what


def _distinct(own: str, every: list[str]) -> str:
    """A description less the words every twin starts with: "Train B Compressor Thrust Bearing Temperature" beside
    "Train B Compressor Journal Bearing Temperature" reads "Thrust Bearing Temperature". What is left is never a bare
    number: "Exhaust Thermocouple 4" keeps the words that say what 4 is."""
    words = [x.split(" ") for x in every]
    k = 0
    while all(len(w) > k + 1 and w[k] == words[0][k] for w in words):
        k += 1
    mine = own.split(" ")
    if not re.search(r"[A-Za-z]{2}", " ".join(mine[k:])):
        k = max(0, k - 2)
    return " ".join(mine[k:])


def names(groups: list[list[dict]]) -> dict[str, str]:
    """Every trace's name: its role, and where two traces in a group would read the same (two bearing temperatures,
    four exhaust thermocouples), its description instead."""
    out: dict[str, str] = {}
    for traces in groups:
        base = [trace_name(t) for t in traces]
        for i, t in enumerate(traces):
            twins = [x for j, x in enumerate(traces) if base[j] == base[i]]
            if len(twins) < 2:
                out[t["id"]] = base[i]
                continue
            own = sentence(_distinct(t.get("description") or t["point"], [x.get("description") or x["point"] for x in twins]))
            near = _neighbour(t)
            out[t["id"]] = f"{own} · {near}" if near else own
    return out


def _series(t: dict, label: str) -> dict:
    return {
        "key": t["id"], "point": t["point"], "tag": tag(t["point"]), "label": label,
        "description": t.get("description"), "unit": wording.unit(t.get("uom")), "uom": t.get("uom"),
        "role": t.get("role"), "on": t.get("on"), "link": t.get("link") or t["id"],
        "as": "step" if t.get("sampled") else "line", "axis": t["axis"], "values": t["values"],
        "min": t["min"], "max": t["max"], "mean": t["mean"], "last": t["last"], "limits": t.get("limits") or [],
        **({"methods": t["methods"]} if t.get("methods") else {}), **({"modelled": True} if t.get("modelled") else {}),
        "show": bool(t.get("show")),
    }


def _focus(series: list[dict], focus: list[str] | None) -> list[str] | None:
    """The series `focus` names, by key, point, tag or role, in the spec's order; None when it names none of them."""
    if not focus:
        return None
    want = {f.strip().lower() for f in focus if f and f.strip()}
    keys = [s["key"] for s in series
            if {s["key"].lower(), s["point"].lower(), s["tag"].lower(), (s["role"] or "").lower()} & want]
    return keys or None


def _marks(view: dict) -> list[dict]:
    """What is drawn on the chart itself: the 12 h leading into each incident near the window."""
    start, end = datetime.fromisoformat(view["start"]), datetime.fromisoformat(view["end"])
    out = []
    for u in view.get("upsets", []):
        t = datetime.fromisoformat(u)
        if t - LEAD_IN <= end and t >= start:
            out.append({"kind": "window", "start": (t - LEAD_IN).isoformat(timespec="minutes"), "end": u,
                        "label": "12 h before the incident", "tone": "warn"})
    return out


def from_view(view: dict, focus: list[str] | None = None, marks: list[dict] | None = None) -> dict:
    """A trend view (`trend_view.view`) as a spec."""
    if "error" in view:
        return {"version": VERSION, "error": view["error"]}
    groups = [*view["groups"], {"key": "other", "label": "Other points", "show": False, "traces": view["other"]}]
    name = names([g["traces"] for g in groups])
    series = [_series(t, name[t["id"]]) for g in groups for t in g["traces"]]
    panels = [{"key": g["key"], "label": g["label"], "show": bool(g["show"]), "axis": "auto",
               "series": [t["id"] for t in g["traces"]]} for g in groups if g["traces"]]
    template = view["template"]
    return {
        "version": VERSION, "subject": view.get("entity"), "unit": view["unit"], "unit_label": view.get("unit_label"),
        "title": " · ".join(x for x in (view.get("unit_label"), template["title"]) if x),
        "template": template, "provider": view["provider"], "step_minutes": view["step_minutes"],
        "time": {"start": view["start"], "end": view["end"], "centre": view["centre"]},
        "panels": panels, "series": series, "focus": _focus(series, focus),
        "events": [{"lane": k, "label": v, "show": True} for k, v in LANES.items()],
        "marks": [*_marks(view), *(marks or [])], "missing": view.get("missing", []), "errors": view.get("errors", []),
    }


def _overlays(conn, fuzer_id: str, view: dict, compare: str, hours_before: int, hours_after: int, src, pack) -> list[dict]:
    """What the window is compared with, each as {"label", "shift", "view"}: the trend read over the earlier hours, and
    how far forward its times move to line up with this window."""
    centre = datetime.fromisoformat(view["centre"])
    if compare in SHIFTS:
        by = SHIFTS[compare]
        anchors = [(centre - by, f"{by.days} d earlier")]
    elif compare == "incidents":
        earlier = [t for t in values.incidents(conn, view["unit"]) if t < centre - timedelta(hours=1)][-PAST_INCIDENTS:]
        anchors = [(t, f"{t:%b} {t.day} incident") for t in reversed(earlier)]
    else:
        return []
    out = []
    for t, label in anchors:
        other = trend_view.view(conn, fuzer_id, t.isoformat(timespec="minutes"), hours_before, hours_after, src=src, pack=pack)
        if "error" not in other:
            out.append({"label": label, "shift": centre - t, "view": other})
    return out


def _with_compare(s: dict, overlays: list[dict], compare: str) -> dict:
    """Each series with its values over the compared windows, moved forward onto this window's times."""
    if "error" in s:
        return s
    for o in overlays:
        theirs = {t["id"]: t for g in [*o["view"]["groups"], {"traces": o["view"]["other"]}] for t in g["traces"]}
        for x in s["series"]:
            t = theirs.get(x["key"])
            if not t or not t["values"]:
                continue
            moved = [[(datetime.fromisoformat(at) + o["shift"]).isoformat(timespec="minutes"), v] for at, v in t["values"]]
            x.setdefault("compare", []).append({"label": o["label"], "values": moved})
    s["compare"] = {"mode": compare, "labels": [o["label"] for o in overlays]}
    return s


def spec(conn, fuzer_id: str, at: str | None = None, hours_before: int = 48, hours_after: int = 24,
         focus: list[str] | None = None, marks: list[dict] | None = None, compare: str | None = None,
         src=None, pack=None) -> dict:
    """The entity's trend as a spec: its class's template, every series in the window, worded. `marks` are a page's
    own (a case's event), drawn beside the trend's. `compare` ("1d", "7d", "28d" or "incidents") adds to each series
    its values over the same hours a period earlier, or around the unit's last three incidents before this window's
    centre, lined up on it."""
    view = trend_view.view(conn, fuzer_id, at, hours_before, hours_after, src=src, pack=pack)
    s = from_view(view, focus, marks)
    if compare in (*SHIFTS, "incidents") and "error" not in view:
        s = _with_compare(s, _overlays(conn, fuzer_id, view, compare, hours_before, hours_after, src, pack), compare)
    return s


def _rate(v: float) -> str:
    return f"{'+' if v > 0 else ''}{v:.3g}"


def from_measurements(data: dict, title: str = "Tests and samples") -> dict:
    """An entity's landed readings over their whole history (`graph.measurements.for_entity`) as a spec: one panel per
    quantity and location, each reading a marker on its line, with what qualified it and how it was taken in the
    hover, and the change per year in its name."""
    rows = data.get("series", [])
    if not rows:
        return {"version": VERSION, "error": "no readings"}
    series, panels = [], []
    for r in rows:
        key = f"m:{r['of']['id']}|{r['quantity']}|{r['location'] or ''}"
        name = r["quantity"].replace("_", " ").capitalize() + (f" at {r['location']}" if r["location"] else "")
        if r["of"]["id"] != data.get("id") and r["of"].get("label"):
            name += f" · {r['of']['label']}"
        unit = wording.unit(r.get("unit"))
        rate = f" · {_rate(r['per_year'])} {unit}/year".replace("  ", " ") if r.get("per_year") is not None else ""
        vals = r["values"]
        series.append({
            "key": key, "point": name, "tag": name, "label": name + rate, "description": None, "unit": unit,
            "uom": r.get("unit"), "role": None, "on": {"id": r["of"]["id"], "label": r["of"].get("label"), "relation": "self"},
            "link": r["of"]["id"], "as": "points", "axis": (r.get("unit") or "value").lower(),
            "values": [[v["at"][:16], v["value"]] for v in vals],
            "methods": [" · ".join(x for x in (f"{v['qualifier']} below the detection limit" if v.get("qualifier") else "",
                                                v.get("method") or "") if x) for v in vals],
            "min": r["min"], "max": r["max"], "mean": sum(v["value"] for v in vals) / len(vals),
            "last": r["latest"]["value"], "limits": [], "show": True,
        })
        panels.append({"key": key, "label": name, "show": True, "axis": "auto", "series": [key]})
    start = min(r["first"] for r in rows)[:16]
    end = max(r["latest"]["at"] for r in rows)[:16]
    return {"version": VERSION, "subject": data.get("id"), "unit": data.get("id"), "unit_label": None, "title": title,
            "template": {"key": "measurements", "title": title, "source": "platform"}, "provider": "measurements",
            "step_minutes": 0, "time": {"start": start, "end": end, "centre": end}, "panels": panels, "series": series,
            "focus": None, "events": [], "marks": [], "missing": [], "errors": [],
            **({"truncated": True} if data.get("truncated") else {})}


def from_measures(conn, pack, keys: list[str], scope: str | None, window: str, at=None) -> dict:
    """Registered measures over a window as a spec (docs/reports.md): one series per measure on one panel, each worded
    by its declaration, so the one chart draws a report's trend like any other.

    Each series is the measure's history (`metrics.value(..., history=True)`) up to the window's end; where a measure
    keeps fewer than two points inside the window, its earlier points are kept too, so the line has a shape, and
    the spec starts at its first point. A measure not cut by scope says so in `errors`."""
    from ..core import metrics, windows

    w = windows.resolve(window, at)
    series, errors = [], []
    for key in keys:
        m = metrics.value(conn, pack, key, scope=scope, window=window, at=None, history=True)
        points = [(p["at"][:10], p["value"]) for p in m.get("history") or [] if p["at"][:10] <= w["end"]]
        inside = [p for p in points if p[0] >= w["start"]]
        vals = [[d, v] for d, v in (inside if len(inside) >= 2 else points)]
        if scope and m.get("scope") != scope:
            errors.append(f"{m['label']} is for the whole register")
        if not vals:
            errors.append(f"{m['label']}: no history")
            continue
        nums = [v for _, v in vals]
        unit = wording.unit(m.get("unit"))
        series.append({
            "key": f"measure:{key}", "point": key, "tag": key, "label": m["label"], "description": m.get("definition"),
            "unit": unit, "uom": m.get("unit"), "role": None, "on": None, "link": f"/metrics/{key}", "as": "line",
            "axis": (m.get("unit") or "value").lower(), "values": vals, "min": min(nums), "max": max(nums),
            "mean": sum(nums) / len(nums), "last": nums[-1], "limits": [], "show": True,
        })
    start = min([s["values"][0][0] for s in series] + [w["start"]])
    title = " · ".join(s["label"] for s in series) or "Measures"
    return {"version": VERSION, "subject": scope, "unit": scope, "unit_label": None, "title": title,
            "template": {"key": "measures", "title": title, "source": "platform"}, "provider": "metrics",
            "step_minutes": 0, "time": {"start": start, "end": w["end"], "centre": w["end"]},
            "panels": [{"key": "measures", "label": title, "show": True, "axis": "auto",
                        "series": [s["key"] for s in series]}],
            "series": series, "focus": None, "events": [], "marks": [], "missing": [], "errors": errors}
