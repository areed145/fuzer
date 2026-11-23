"""Trend specs: one config for every chart over time (the trends design, Nov 2026). The server decides what a trend
shows and the browser only draws it, so the entity page, a case, an incident and a metric all hand the one chart
(`ui/src/trends/TrendChart.tsx`) the same shape:

    spec(conn, fuzer_id, at=None, hours_before=48, hours_after=24, focus=None) -> TrendSpec

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
from . import trend_view

VERSION = 1
LEAD_IN = timedelta(hours=12)
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


def from_view(view: dict, focus: list[str] | None = None) -> dict:
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
        "marks": _marks(view), "missing": view.get("missing", []), "errors": view.get("errors", []),
    }


def spec(conn, fuzer_id: str, at: str | None = None, hours_before: int = 48, hours_after: int = 24,
         focus: list[str] | None = None, src=None, pack=None) -> dict:
    """The entity's trend as a spec: its class's template, every series in the window, worded."""
    return from_view(trend_view.view(conn, fuzer_id, at, hours_before, hours_after, src=src, pack=pack), focus)
