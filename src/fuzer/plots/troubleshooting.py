"""Troubleshooting charts: an incident on one page, which process values move together (and which
moved first), vibration against ISO 20816 zones, and readings with their instrument health findings or
the changes found in them (signals.py)."""

import math

import numpy as np

from . import ChartError, Figure, axis, layout, rnd, stacked, thin
from .general import _shade_upsets, _summ, _window, short, synthetic_note


def incident(conn, fid: str, hours_before: int = 36, hours_after: int = 12, max_series: int = 5) -> Figure:
    """An incident (or a unit's latest): the points that moved most in the lead-in, stacked, with the
    incident's log events, trips, notifications and downtime as markers in a lane on top."""
    from ..graph import queries

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


def correlation(conn, fid: str, at: str | None = None, hours_before: int = 72, hours_after: int = 12,
                upstream: bool = True, target: str | None = None, max_points: int = 14) -> Figure:
    """Correlation of the process values on a unit (and on what feeds it) over a window, as a heatmap.
    With `target` (a timeseries, e.g. the vibration that tripped), also the lag at which each other
    point best matches it: a point that moves before the target is a lead to the cause."""
    from ..analysis import studies
    from ..graph import edges

    ids = [fid]
    if upstream:
        ids += [r["fuzer_id"] for r in edges.trace(conn, fid, "flows_to", "up", None, 1)][:2]
    series, _upsets, out = _window(conn, ids + ([target] if target else []), at, hours_before, hours_after,
                                  max_points=max_points)
    series = [s for s in series if len(s["values"]) >= 5 and s["max"] != s["min"]][:max_points]
    if len(series) < 2:
        raise ChartError("fewer than two points with values that change")
    times = sorted(set.intersection(*({t for t, _ in s["values"]} for s in series)))
    cols = [[dict(s["values"])[t] for t in times] for s in series]
    names = [s["point"] for s in series]
    z = [[rnd(studies.pearson(a, b), 2) if i != j else 1.0 for j, b in enumerate(cols)] for i, a in enumerate(cols)]
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
        others = [k for k in range(len(cols)) if k != tgt]
        _, lag, r = studies.lagged(np.array([cols[k] for k in others], float).T, np.array(cols[tgt], float),
                                   range(min(24, len(times) // 3)))  # leads only, as before
        lags = [{"point": names[k], "r": rnd(float(r[i]), 2), "leads_by_minutes": int(lag[i]) * out["step_minutes"]}
                for i, k in enumerate(others)]
        lags.sort(key=lambda r: -abs(r["r"] or 0))
        summary["against_target"] = {"target": names[tgt], "best_lag": lags[:8]}
    summary |= synthetic_note(out["provider"], series)
    return Figure("correlation", lay["title"]["text"], data, lay, summary, [s["id"] for s in series])


# ISO 20816-3 zone limits (mm/s RMS, flexible supports): A/B, B/C, C/D.
ZONES = {"large": (3.5, 7.1, 11.0), "medium": (2.3, 4.5, 7.1)}


def vibration(conn, fid: str, at: str | None = None, days: int = 30) -> Figure:
    """Overall vibration on a machine against ISO 20816 zones, bearing / discharge temperature on a
    second axis, incident lead-ins shaded."""
    from ..sources import timeseries

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
    from ..analysis import signals

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
    from ..analysis import signals

    since, until = signals.window(conn, since, until, days)
    out = signals.changes(conn, fid, since, until, values=True)
    data, lay, series = _signal_panels(f"Changes {since[:10]} to {until[:10]}", out, out["changes"], max_series)
    return Figure("changes", lay["title"]["text"], data, lay, {
        "window": [since, until], "step_minutes": out["step_minutes"], "points": out["points"], "found": out["found"],
        "changes": [{k: c.get(k) for k in ("point", "flag", "start", "end", "expected", "value", "deviation_pct", "score",
                                           "per_day")} for c in out["changes"][:20]]}, [s["id"] for s in series])


# ---- alarms, trips and states (#105 D) ---------------------------------------------------------------

STATE_COLOURS = {"running": "rgba(60,170,90,0.85)", "startup": "rgba(160,190,60,0.85)", "stopped": "rgba(210,60,50,0.85)",
                 "transition": "rgba(230,150,30,0.85)", "unknown": "rgba(150,150,150,0.5)"}


def state_timeline(conn, scope: str | None, since: str, until: str, pack=None, max_units: int = 25) -> Figure:
    """Swimlanes: each unit's state (running, startup, stopped, transition) over the window; units that
    changed state first."""
    from datetime import datetime as dt

    from ..analysis import alarms

    s = alarms.states(conn, pack, scope, since, until)
    units = sorted(s["units"].items(), key=lambda kv: (len(kv[1]["spans"]) == 1, kv[0]))[:max_units]
    data = []
    for state, colour in STATE_COLOURS.items():
        ys, base, x = [], [], []
        for name, u in units:
            for sp in u["spans"]:
                if sp["state"] == state:
                    ys.append(name)
                    base.append(sp["start"])
                    x.append((dt.fromisoformat(sp["end"]) - dt.fromisoformat(sp["start"])).total_seconds() * 1000)
        if ys:
            data.append({"type": "bar", "orientation": "h", "name": state, "y": ys, "x": x, "base": base,
                         "marker": {"color": colour}, "hovertemplate": "%{y}: " + state + " from %{base}<extra></extra>"})
    lay = layout(f"Unit states {since[:16].replace('T', ' ')} to {until[:16].replace('T', ' ')}", None, None, barmode="overlay")
    lay["xaxis"] = {"type": "date"}
    lay["yaxis"] = {"autorange": "reversed", "automargin": True}
    lay["height"] = 200 + 24 * len(units)
    changed = [n for n, u in units if len(u["spans"]) > 1]
    return Figure("state_timeline", lay["title"]["text"], data, lay, {
        "window": s["window"], "units": len(s["units"]), "changed_state": changed[:20],
        "first_to_stop": min(((sp["start"], n) for n, u in units for sp in u["spans"] if sp["state"] == "stopped"), default=None)},
        [u["id"] for _, u in units])


def alarms_chart(conn, scope: str | None, since: str, until: str, pack=None) -> Figure:
    """Alarms per 10 minutes over the window against the flood line, with the top bad-actor points."""
    from datetime import datetime as dt
    from datetime import timedelta as td

    from ..analysis import alarms, limits

    out = alarms.alarm_analysis(conn, pack, scope, since, until)
    a, b = dt.fromisoformat(since), dt.fromisoformat(until)
    ex = [x for x in limits.excursions(conn, pack, alarms._scope_points(conn, scope), a, b, 10) if x["basis"] == "alarm"]
    bins: dict[str, int] = {}
    for x in ex:
        k = x["start"].replace(minute=x["start"].minute // 10 * 10, second=0).isoformat(timespec="minutes")
        bins[k] = bins.get(k, 0) + 1
    t, xs, ys = a, [], []
    while t <= b:
        k = t.isoformat(timespec="minutes")
        xs.append(k)
        ys.append(bins.get(k, 0))
        t += td(minutes=10)
    data = [{"type": "bar", "name": "alarms per 10 min", "x": xs, "y": ys}]
    lay = layout(f"Alarms per 10 minutes ({out['alarms']} in all)", None, "alarms")
    lay["shapes"] = [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": "y", "y0": out["flood_per_10min"], "y1": out["flood_per_10min"],
                      "line": {"dash": "dash", "color": "rgba(210,60,50,0.8)"}}]
    return Figure("alarms", lay["title"]["text"], data, lay, {
        k: out[k] for k in ("value", "flag", "alarms", "points_alarming", "peak_10_min", "floods", "chattering", "standing",
                            "bad_actors", "basis")}, out["sources"][:10])


def propagation(conn, upset: int) -> Figure:
    """An upset on the plant model: its units as nodes coloured by when each was first detected, links where
    the model connects them, the likely first cause marked."""
    from datetime import datetime as dt

    from ..graph import topology
    from ..ops import sre

    d = sre.upset_detail(conn, upset, limit=2000)
    if d is None:
        raise ChartError(f"no upset {upset}")
    first: dict[str, str] = {}
    ids: dict[str, str] = {}
    for x in d["timeline"]:  # multivariate findings are area-wide symptoms, not a unit's own first sign
        if x["unit"] and x["unit"] not in first and x["detector"] != "multivariate":
            first[x["unit"]] = x["start"]
    rows = conn.execute("select fuzer_id, label from entity_label where label = any(%s)", (list(first),)).fetchall()
    ids = {r["label"]: r["fuzer_id"] for r in rows}
    if not first:
        raise ChartError("no unit detections in that upset")
    order = sorted(first, key=lambda u: first[u])
    t0 = dt.fromisoformat(first[order[0]])
    mins = {u: (dt.fromisoformat(first[u]) - t0).total_seconds() / 60 for u in order}
    # layered layout: x = minutes after the first detection, y spreads the units at about the same time
    pos, lanes = {}, {}
    for u in order:
        b = int(mins[u] // 10)
        lanes[b] = lanes.get(b, 0) + 1
        pos[u] = (mins[u], lanes[b])
    adj = topology.topology(conn)
    ex, ey = [], []
    for i, u in enumerate(order):
        for v in order[i + 1:]:
            if ids.get(v) in adj.get(ids.get(u), ()):
                ex += [pos[u][0], pos[v][0], None]
                ey += [pos[u][1], pos[v][1], None]
    data = [{"type": "scatter", "mode": "lines", "name": "plant model link", "x": ex, "y": ey,
             "line": {"width": 1, "color": "rgba(140,140,140,0.5)"}, "hoverinfo": "skip"},
            {"type": "scatter", "mode": "markers+text", "name": "units", "x": [pos[u][0] for u in order],
             "y": [pos[u][1] for u in order], "text": order, "textposition": "top center",
             "customdata": [ids.get(u) for u in order],
             "marker": {"size": 12, "color": [mins[u] for u in order], "colorscale": "Viridis",
                        "colorbar": {"title": {"text": "min after first"}}},
             "hovertemplate": "%{text}: first detected %{x:.0f} min in<extra></extra>"},
            {"type": "scatter", "mode": "markers", "name": "likely first cause", "x": [pos[order[0]][0]], "y": [pos[order[0]][1]],
             "marker": {"size": 22, "symbol": "star-open", "line": {"width": 2}}}]
    lay = layout(f"Upset {upset}: how it spread", "minutes after the first detection", None)
    lay["yaxis"] = {"visible": False}
    return Figure("propagation", lay["title"]["text"], data, lay, {
        "upset": upset, "first_cause": d["first_unit"], "first_detection": f"{d['first_key']} on {d['first_point']}",
        "units": len(order), "order": [{"unit": u, "minutes_after_first": round(mins[u])} for u in order[:15]],
        "links_drawn": len(ex) // 3}, [i for i in ids.values()][:20])



def modes_chart(conn, scope, since: str, until: str, k: int | None = None, pack=None) -> Figure:
    """A unit's operating modes over time as a coloured strip, under the regime-carrying point's trend."""
    from ..analysis import clusters

    r = clusters.modes(conn, scope, since, until, k, pack=pack)
    names = [m["name"] for m in r["modes"]]
    reg = r["regime_points"][0] if r["regime_points"] else r["points"][0]
    j = r["points"].index(reg)
    prof_rows = [m["profile"][reg] for m in r["modes"]]
    top, bottom = stacked(2, 0.08)
    data = [{"type": "heatmap", "x": r["times"], "y": ["mode"], "z": [r["labels"]], "yaxis": "y2", "xaxis": "x",
             "colorscale": "Viridis", "showscale": False, "hovertemplate": "%{x}: mode %{z}<extra></extra>"},
            {"type": "scatter", "mode": "markers", "name": reg, "x": r["times"], "y": [m["profile"][reg] for m in r["modes"]] and
             [prof_rows[x] for x in r["labels"]], "marker": {"size": 4, "color": r["labels"], "colorscale": "Viridis"},
             "yaxis": "y", "hovertemplate": "%{x}: mode profile %{y:.1f}<extra></extra>"}]
    lay = layout(f"Operating modes {since[:10]} to {until[:10]}", showlegend=False)
    lay["yaxis"] = {"domain": top, "title": {"text": f"{reg} (mode mean)"}}
    lay["yaxis2"] = {"domain": [bottom[1] - 0.12, bottom[1]], "showticklabels": False}
    _ = j
    return Figure("modes", lay["title"]["text"], data, lay, {
        "modes": [{k: m[k] for k in ("name", "share", "most_different")} for m in r["modes"]], "names": names,
        "regime_points": r["regime_points"], "mode_before_stops": r["mode_before_stops"], "basis": r["basis"]},
        r["sources"])


def spe_chart(conn, scope, train_since: str, train_until: str, since: str, until: str) -> Figure:
    """A PCA model's squared prediction error over a window against its limit, the anomalies shaded."""
    from ..analysis import clusters

    r = clusters.spe(conn, scope, (train_since, train_until), (since, until))
    data = [{"type": "scatter", "mode": "lines", "name": "limit", "x": [since, until], "y": [r["limit"], r["limit"]],
             "line": {"dash": "dash", "color": "rgba(210,60,50,0.8)"}}]
    lay = layout(f"Multivariate anomaly {since[:10]} to {until[:10]} ({r['components']} components)", None, "SPE")
    lay["shapes"] = [{"type": "rect", "xref": "x", "x0": a["start"], "x1": a["end"], "yref": "paper", "y0": 0, "y1": 1,
                      "fillcolor": "rgba(210,60,50,0.15)", "line": {"width": 0}, "layer": "below"} for a in r["anomalies"]]
    return Figure("spe", lay["title"]["text"], data, lay, {
        "anomalies": r["anomalies"][:10], "limit": r["limit"], "components": r["components"], "basis": r["basis"]}, r["sources"])
