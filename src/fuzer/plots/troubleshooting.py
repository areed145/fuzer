"""Troubleshooting charts: an incident on one page, which process values move together (and which
moved first), vibration against ISO 20816 zones, and readings with their instrument health findings or
the changes found in them (signals.py)."""

import math

from . import ChartError, Figure, axis, layout, rnd, stacked, thin
from .general import _shade_upsets, _summ, _window, short, synthetic_note


def incident(conn, fid: str, hours_before: int = 36, hours_after: int = 12, max_series: int = 5) -> Figure:
    """An incident (or a unit's latest): the points that moved most in the lead-in, stacked, with the
    incident's log events, trips, notifications and downtime as markers in a lane on top."""
    from .. import queries

    kind = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()
    if kind and kind["kind"] != "incident":
        latest = queries.incidents(conn, fid, limit=1)["items"]
        if not latest:
            raise ChartError("no incidents on that unit")
        fid = latest[0]["id"]
    inc = queries.entity(conn, fid, per_group=1)
    series, upsets, out = _window(conn, [fid], None, hours_before, hours_after, max_points=12)
    moved = sorted(series, key=lambda s: -abs((s.get("lead_in") or {}).get("change_pct") or 0))[:max_series]
    timeline = [t for t in queries.incident_timeline(conn, fid) if t["at"]]
    start, end = out["start"], out["end"]
    lanes = list(dict.fromkeys(t["kind"].replace("_", " ") for t in timeline))
    doms = stacked(len(moved) + 1)
    lane_dom = [doms[0][0] + (doms[0][1] - doms[0][0]) * 0.3, doms[0][1]]
    data = [{"type": "scatter", "mode": "markers", "name": "signals", "yaxis": "y",
             "x": [t["at"][:16] for t in timeline], "y": [t["kind"].replace("_", " ") for t in timeline],
             "text": [(t["detail"] or "") + ": " + (t["text"] or "")[:120] for t in timeline],
             "customdata": [t["id"] for t in timeline], "marker": {"size": 10, "symbol": "diamond"},
             "hovertemplate": "%{x}<br>%{text}<extra>%{y}</extra>"}]
    lay = layout(f"Incident: {(inc['golden'].get('description') or {}).get('value') or inc['label']}", showlegend=False)
    lay["yaxis"] = {"domain": lane_dom, "type": "category", "categoryarray": lanes}
    for i, (s, dom) in enumerate(zip(moved, doms[1:]), start=1):
        ref, key = axis(i)
        xs, ys = thin([t for t, _ in s["values"]], [v for _, v in s["values"]])
        data.append({"type": "scatter", "mode": "lines", "name": s["point"], "x": xs, "y": ys, "yaxis": ref,
                     "meta": {"entity": s["id"]}, "hovertemplate": f"%{{y:.3g}} {s['uom'] or ''}<extra>{s['point']}</extra>"})
        lay[key] = {"domain": dom, "title": {"text": short(s), "font": {"size": 10}}}
    lay["xaxis"] = {"anchor": axis(len(moved))[0], "range": [start, end]}
    lay["shapes"] = _shade_upsets(upsets) + [
        {"type": "line", "xref": "x", "yref": "paper", "x0": t["at"][:16], "x1": t["at"][:16], "y0": 0, "y1": 1,
         "line": {"width": 1, "dash": "dot", "color": "rgba(120,120,120,0.6)"}}
        for t in timeline if start <= t["at"][:16] <= end]
    lay["height"] = 220 + 130 * len(moved)
    in_window = [t for t in timeline if start <= t["at"][:16] <= end]
    return Figure("incident", lay["title"]["text"], data, lay, {
        "incident": fid, "window": [start, end], "provider": out["provider"],
        "signals": [{"at": t["at"][:16], "kind": t["kind"], "detail": t["detail"], "text": (t["text"] or "")[:100]}
                    for t in in_window[:12]],
        "signals_outside_window": len(timeline) - len(in_window),
        "moved_most": [_summ(s) for s in moved]}, [fid] + [s["id"] for s in moved])


def _pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 5:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if not va or not vb:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) ** 0.5


def correlation(conn, fid: str, at: str | None = None, hours_before: int = 72, hours_after: int = 12,
                upstream: bool = True, target: str | None = None, max_points: int = 14) -> Figure:
    """Correlation of the process values on a unit (and on what feeds it) over a window, as a heatmap.
    With `target` (a timeseries, e.g. the vibration that tripped), also the lag at which each other
    point best matches it: a point that moves before the target is a lead to the cause."""
    from .. import graph

    ids = [fid]
    if upstream:
        ids += [r["fuzer_id"] for r in graph.trace(conn, fid, "flows_to", "up", None, 1)][:2]
    series, upsets, out = _window(conn, ids + ([target] if target else []), at, hours_before, hours_after,
                                  max_points=max_points)
    series = [s for s in series if len(s["values"]) >= 5 and s["max"] != s["min"]][:max_points]
    if len(series) < 2:
        raise ChartError("fewer than two points with values that change")
    times = sorted(set.intersection(*(set(t for t, _ in s["values"]) for s in series)))
    cols = [[dict(s["values"])[t] for t in times] for s in series]
    names = [s["point"] for s in series]
    z = [[rnd(_pearson(a, b), 2) if i != j else 1.0 for j, b in enumerate(cols)] for i, a in enumerate(cols)]
    data = [{"type": "heatmap", "x": names, "y": names, "z": z, "zmin": -1, "zmax": 1, "colorscale": "RdBu",
             "reversescale": True, "colorbar": {"title": {"text": "r"}},
             "hovertemplate": "%{y} vs %{x}: r = %{z}<extra></extra>"}]
    pairs = sorted(((names[i], names[j], z[i][j]) for i in range(len(names)) for j in range(i + 1, len(names))
                    if z[i][j] is not None), key=lambda p: -abs(p[2]))
    summary = {"window": [out["start"], out["end"]], "step_minutes": out["step_minutes"], "provider": out["provider"],
               "points": [{"point": s["point"], "description": s["description"]} for s in series],
               "strongest_pairs": [{"a": a, "b": b, "r": r} for a, b, r in pairs[:8]]}
    lay = layout(f"Correlation of process values, {out['start'][:10]} to {out['end'][:10]}", showlegend=False)
    lay["height"] = 260 + 22 * len(names)
    lay["yaxis"] = {"autorange": "reversed"}
    if target:
        tgt = next((k for k, s in enumerate(series) if s["id"] == target), None)
        if tgt is None:
            raise ChartError("target must be a timeseries with values in the window")
        lags = []
        for k, col in enumerate(cols):
            if k == tgt:
                continue
            best = max(((lag, _pearson(col[:len(col) - lag] if lag else col, cols[tgt][lag:]))
                        for lag in range(0, min(24, len(times) // 3))), key=lambda p: abs(p[1] or 0))
            lags.append({"point": names[k], "r": rnd(best[1], 2), "leads_by_minutes": best[0] * out["step_minutes"]})
        lags.sort(key=lambda r: -abs(r["r"] or 0))
        summary["against_target"] = {"target": names[tgt], "best_lag": lags[:8]}
    summary |= synthetic_note(out["provider"], series)
    return Figure("correlation", lay["title"]["text"], data, lay, summary, [s["id"] for s in series])


# ISO 20816-3 zone limits (mm/s RMS, flexible supports): A/B, B/C, C/D.
ZONES = {"large": (3.5, 7.1, 11.0), "medium": (2.3, 4.5, 7.1)}


def vibration(conn, fid: str, at: str | None = None, days: int = 30) -> Figure:
    """Overall vibration on a machine against ISO 20816 zones, bearing / discharge temperature on a
    second axis, incident lead-ins shaded."""
    from .. import timeseries

    pts = timeseries._unit_points(conn, fid, 40)
    vib = [p for p in pts if (p["uom"] or "") in ("mm/s", "in/s") or "vibration" in (p["description"] or "").lower()]
    if not vib:
        raise ChartError("no vibration points on that unit")
    temp = [p for p in pts if (p["uom"] or "") == "degC"][:1]
    series, upsets, out = _window(conn, [vib[0]["id"]] + [t["id"] for t in temp], at, days * 24 * 2 // 3,
                                  days * 24 // 3, max_points=1)
    by = {s["id"]: s for s in series}
    v = by[vib[0]["id"]]
    label = conn.execute("select label from entity_label where fuzer_id = %s", (fid,)).fetchone()
    big = "-K-" in (label["label"] if label else "") or "compressor" in (v["description"] or "").lower()
    ab, bc, cd = ZONES["large" if big else "medium"]
    xs, ys = thin([t for t, _ in v["values"]], [x for _, x in v["values"]])
    data = [{"type": "scatter", "mode": "lines", "name": f"{v['point']} (mm/s)", "x": xs, "y": ys,
             "meta": {"entity": v["id"]}}]
    lay = layout(f"Vibration on {label['label'] if label else fid} against ISO 20816 zones", None, "vibration (mm/s RMS)")
    top = max(cd * 1.2, v["max"] * 1.1)
    bands = [(0, ab, "rgba(60,170,90,0.10)", "A"), (ab, bc, "rgba(160,190,60,0.10)", "B"),
             (bc, cd, "rgba(230,150,30,0.12)", "C"), (cd, top, "rgba(210,60,50,0.12)", "D")]
    lay["shapes"] = [{"type": "rect", "xref": "paper", "yref": "y", "x0": 0, "x1": 1, "y0": a, "y1": b, "fillcolor": c,
                      "line": {"width": 0}, "layer": "below"} for a, b, c, _ in bands] + _shade_upsets(upsets)
    lay["annotations"] = [{"xref": "paper", "x": 1.0, "xanchor": "left", "yref": "y", "y": (a + b) / 2, "text": z,
                           "showarrow": False} for a, b, _, z in bands]
    lay["yaxis"]["range"] = [0, top]
    if temp and temp[0]["id"] in by:
        t = by[temp[0]["id"]]
        tx, ty = thin([a for a, _ in t["values"]], [b for _, b in t["values"]])
        data.append({"type": "scatter", "mode": "lines", "name": f"{t['point']} (degC)", "x": tx, "y": ty,
                     "yaxis": "y2", "line": {"dash": "dot"}, "meta": {"entity": t["id"]}})
        lay["yaxis2"] = {"overlaying": "y", "side": "right", "title": {"text": "temperature (degC)"}}

    def zone(x):
        return "A" if x < ab else "B" if x < bc else "C" if x < cd else "D"

    return Figure("vibration", lay["title"]["text"], data, lay, {
        "machine_size": "large (>300 kW)" if big else "medium (15-300 kW)", "zone_limits_mm_s": [ab, bc, cd],
        "latest": v["last"], "latest_zone": zone(v["last"]), "peak": v["max"], "peak_zone": zone(v["max"]),
        "lead_in": v.get("lead_in"), "incidents_in_window": upsets, "provider": out["provider"],
        **synthetic_note(out["provider"], series)}, [fid, v["id"]])


FINDING_COLOURS = {"noisy": "rgba(200,50,45,0.18)", "frozen": "rgba(60,110,200,0.18)", "stuck": "rgba(60,110,200,0.18)",
                   "out_of_range": "rgba(190,120,0,0.22)", "redundant_drift": "rgba(140,70,170,0.18)",
                   "drift": "rgba(110,120,135,0.16)"}


def _signal_panels(title: str, out: dict, marks: list[dict], max_series: int) -> tuple[list[dict], dict, list[dict]]:
    """The points with something to show first, stacked; findings shaded on their panel, changes at a
    point in time drawn as a line with a marker on the value."""
    hit = {m["point_id"] for m in marks}
    series = sorted(out["series"], key=lambda s: s["id"] not in hit)[:max_series]
    if not series:
        raise ChartError("no process values in that window")
    times = [t.isoformat(timespec="minutes") for t in out["times"]]
    data, lay, shapes = [], layout(title, showlegend=False), []
    for i, (s, dom) in enumerate(zip(series, stacked(len(series)))):
        ref, key = axis(i)
        vals = [None if math.isnan(v) else round(float(v), 4) for v in s["values"]]
        xs, ys = thin(times, vals)
        data.append({"type": "scatter", "mode": "lines", "name": s["point"], "x": xs, "y": ys, "yaxis": ref,
                     "meta": {"entity": s["id"]}, "hovertemplate": f"%{{y:.3g}} {s['uom'] or ''}<extra>{s['point']}</extra>"})
        lay[key] = {"domain": dom, "title": {"text": short(s), "font": {"size": 10}}}
        mine = [m for m in marks if m["point_id"] == s["id"]]
        for m in mine:
            if m.get("end") and (m["flag"] in FINDING_COLOURS):
                shapes.append({"type": "rect", "xref": "x", "yref": f"{ref} domain", "x0": m["start"], "x1": m["end"],
                               "y0": 0, "y1": 1, "fillcolor": FINDING_COLOURS[m["flag"]], "line": {"width": 0},
                               "layer": "below"})
            else:
                shapes.append({"type": "line", "xref": "x", "yref": f"{ref} domain", "x0": m["start"], "x1": m["start"],
                               "y0": 0, "y1": 1, "line": {"dash": "dot", "width": 1}})
        if mine:
            at = {t: v for t, v in zip(times, vals)}
            data.append({"type": "scatter", "mode": "markers", "name": f"{s['point']} findings", "yaxis": ref,
                         "x": [m["start"] for m in mine], "y": [at.get(m["start"]) for m in mine],
                         "text": [f"{m['flag'].replace('_', ' ')}: {m['expected']} -> {m['value']}" for m in mine],
                         "marker": {"size": 9, "symbol": "diamond"}, "hovertemplate": "%{x}<br>%{text}<extra></extra>"})
    lay["xaxis"] = {"anchor": axis(len(series) - 1)[0], "range": [times[0], times[-1]]}
    lay["shapes"] = shapes
    lay["height"] = 160 + 130 * len(series)
    return data, lay, series


def instrument_health(conn, fid: str, since: str | None = None, until: str | None = None, days: int = 14,
                      pack=None, max_series: int = 6) -> Figure:
    """A point's or a unit's readings with the stretches instrument health flags shaded (noisy red, frozen
    or stuck blue, out of range amber, apart from its redundant twin purple)."""
    from .. import signals

    since, until = signals.window(conn, since, until, days)
    out = signals.instrument_health(conn, pack, fid, since, until, values=True)
    data, lay, series = _signal_panels(f"Instrument health {since[:10]} to {until[:10]}", out, out["findings"], max_series)
    return Figure("instrument_health", lay["title"]["text"], data, lay, {
        "window": [since, until], "points": out["points"], "suspect": out["suspect"],
        "findings": [{k: f[k] for k in ("point", "flag", "start", "end", "minutes", "value", "expected", "basis")}
                     for f in out["findings"][:20]]}, [s["id"] for s in series])


def changes(conn, fid: str, since: str | None = None, until: str | None = None, days: int = 14,
            max_series: int = 6) -> Figure:
    """A point's or a unit's readings with the steps and variance changes detect_changes finds marked,
    and drifts shaded."""
    from .. import signals

    since, until = signals.window(conn, since, until, days)
    out = signals.changes(conn, fid, since, until, values=True)
    data, lay, series = _signal_panels(f"Changes {since[:10]} to {until[:10]}", out, out["changes"], max_series)
    return Figure("changes", lay["title"]["text"], data, lay, {
        "window": [since, until], "step_minutes": out["step_minutes"], "points": out["points"], "found": out["found"],
        "changes": [{k: c.get(k) for k in ("point", "flag", "start", "end", "expected", "value", "deviation_pct", "score",
                                           "per_day")} for c in out["changes"][:20]]}, [s["id"] for s in series])
