"""General charts any agent can make: trends, landed measurements, one variable against another,
ranked bars, before / after distributions, and small charts from numbers the agent supplies."""

from datetime import datetime, timedelta

from . import ChartError, Figure, axis, layout, rnd, stacked, thin


def _window(conn, ids: list[str], at, hours_before, hours_after, max_points=6, step_minutes=None):
    """The same window for every ref: the first sets the centre (its latest incident, unless `at`)."""
    from ..analysis import values

    series, upsets, out = [], set(), None
    for fid in ids:
        out = values.around(conn, fid, at, hours_before, hours_after, step_minutes=step_minutes,
                            max_points=max_points)
        if "error" in out:
            raise ChartError(f"{fid}: {out['error']}")
        at, step_minutes = at or out["centre"], out["step_minutes"]
        upsets |= set(out["upsets"])
        series += [s for s in out["series"] if s["id"] not in {x["id"] for x in series}]
    if not series:
        raise ChartError("no process values in that window")
    return series, sorted(upsets), out


def _shade_upsets(upsets: list[str]) -> list[dict]:
    return [{"type": "rect", "xref": "x", "yref": "paper", "y0": 0, "y1": 1, "line": {"width": 0},
             "fillcolor": "rgba(230,150,30,0.15)", "layer": "below",
             "x0": (datetime.fromisoformat(u) - timedelta(hours=12)).isoformat(timespec="minutes"), "x1": u}
            for u in upsets]


def short(s: dict, n: int = 28) -> str:
    """A panel title: the measurement end of a point's description ("... Compressor Vibration" ->
    "Vibration"), with units."""
    words = (s["description"] or s["point"]).split()
    title = words[-1]
    for w in reversed(words[:-1]):
        if len(w) + 1 + len(title) > n or w[0].isdigit() or w in ("Stage", "Compressor", "Pump", "Separator"):
            break
        title = f"{w} {title}"
    return f"{title} ({s['uom'] or ''})"


def synthetic_note(provider: str, series: list[dict]) -> dict:
    """What the reader should know about synthetic values: from the fake plant's process model (they move
    together as the plant would), or drawn per point (only the timing of upsets means anything)."""
    if provider != "synthetic":
        return {}
    if series and all(s.get("modelled") for s in series):
        return {"note": "synthetic values from the fake plant's process model, not plant data"}
    return {"note": "synthetic values, not plant data; points outside the process model are drawn one by one, "
                    "so only the timing of upsets in them means anything"}


def _summ(s: dict) -> dict:
    return {"point": s["point"], "description": s["description"], "uom": s["uom"], "min": s["min"],
            "max": s["max"], "mean": s["mean"], "last": s["last"], "lead_in": s.get("lead_in"),
            "limits": s.get("limits") or []}


LIMIT_COLOURS = {"alarm": "rgba(190,120,0,0.9)", "iow": "rgba(200,50,45,0.9)", "learned": "rgba(110,120,135,0.8)"}


def _limit_lines(s: dict, yref: str) -> list[dict]:
    """A series' operating limits (#101) near its values as dashed lines on its panel (HiHi / LoLo and IOWs
    red, learned ranges grey); ones far off scale are left to the summary."""
    span = max(s["max"] - s["min"], abs(s["mean"]) * 0.05, 1e-6)
    out = []
    for lim in s.get("limits") or []:
        if s["min"] - span <= lim["value"] <= s["max"] + span:
            red = lim["basis"] == "iow" or lim["type"] in ("hihi", "lolo")
            out.append({"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": yref, "y0": lim["value"],
                        "y1": lim["value"], "layer": "below",
                        "line": {"width": 1, "dash": "solid" if lim["basis"] == "iow" else "dash",
                                 "color": LIMIT_COLOURS["iow"] if red else LIMIT_COLOURS[lim["basis"]]}})
    return out


def trend(conn, ids: list[str], at: str | None = None, hours_before: int = 48, hours_after: int = 24,
          max_series: int = 6) -> Figure:
    """Process values on stacked panels sharing time, incident lead-ins shaded, operating limits drawn."""
    series, upsets, out = _window(conn, ids, at, hours_before, hours_after, max_series)
    series = series[:max_series]
    data, lay = [], layout(f"Process values around {out['centre'].replace('T', ' ')}", showlegend=False)
    for i, (s, dom) in enumerate(zip(series, stacked(len(series)))):
        ref, key = axis(i)
        xs, ys = thin([t for t, _ in s["values"]], [v for _, v in s["values"]])
        data.append({"type": "scatter", "mode": "lines", "name": s["point"], "x": xs, "y": ys, "yaxis": ref,
                     "meta": {"entity": s["id"]}, "hovertemplate": f"%{{y:.3g}} {s['uom'] or ''}<extra>{s['point']}</extra>"})
        lay[key] = {"domain": dom, "title": {"text": short(s), "font": {"size": 10}}}
    lay["xaxis"] = {"anchor": axis(len(series) - 1)[0]}
    lay["shapes"] = _shade_upsets(upsets) + [ln for i, s in enumerate(series) for ln in _limit_lines(s, axis(i)[0])]
    lay["height"] = 160 + 130 * len(series)
    return Figure("trend", lay["title"]["text"], data, lay,
                  {"provider": out["provider"], "window": [out["start"], out["end"]], "step_minutes": out["step_minutes"],
                   "incidents_in_window": upsets, "series": [_summ(s) for s in series],
                   **synthetic_note(out["provider"], series)}, [s["id"] for s in series])


def measurements(conn, fid: str, quantity: str | None = None, since: str | None = None,
                 until: str | None = None) -> Figure:
    """Landed readings over time: one panel per quantity, one line per location (CML, sample point)."""
    from ..graph import measurements as m

    out = m.for_entity(conn, fid, quantity, since, until)
    if not out["series"]:
        raise ChartError("no measurements" + (f" of {quantity}" if quantity else ""))
    quantities = list(dict.fromkeys(s["quantity"] for s in out["series"]))[:6]
    label = out["series"][0]["of"]["label"]
    data, lay = [], layout(f"{label}: {', '.join(q.replace('_', ' ') for q in quantities)}")
    for i, (q, dom) in enumerate(zip(quantities, stacked(len(quantities)))):
        ref, key = axis(i)
        for s in (s for s in out["series"] if s["quantity"] == q):
            vals = s["values"]
            data.append({"type": "scatter", "mode": "lines+markers", "yaxis": ref,
                         "name": " ".join(x for x in (q.replace("_", " "), s["location"]) if x),
                         "x": [v["at"] for v in vals], "y": [v["value"] for v in vals],
                         "marker": {"symbol": ["circle-open" if v["qualifier"] else "circle" for v in vals], "size": 6},
                         "text": [("below detection limit " if v["qualifier"] == "<" else "") + (v["method"] or "")
                                  for v in vals],
                         "meta": {"entity": s["of"]["id"]},
                         "hovertemplate": f"%{{y:.4g}} {s['unit'] or ''}<br>%{{x}}<br>%{{text}}<extra></extra>"})
        unit = next((s["unit"] for s in out["series"] if s["quantity"] == q), "")
        lay[key] = {"domain": dom, "title": {"text": f"{q.replace('_', ' ')} ({unit})", "font": {"size": 10}}}
    lay["xaxis"] = {"anchor": axis(len(quantities) - 1)[0]}
    lay["height"] = 160 + 150 * len(quantities)
    return Figure("measurements", lay["title"]["text"], data, lay, {"series": [
        {k: s[k] for k in ("quantity", "location", "unit", "count", "first", "min", "max", "per_year")}
        | {"latest": {k: s["latest"][k] for k in ("at", "value", "qualifier")}} for s in out["series"]][:20]},
        sorted({s["of"]["id"] for s in out["series"]}))


def xy(conn, x_id: str, y_id: str, at: str | None = None, hours_before: int = 72, hours_after: int = 24,
       fit: bool = True) -> Figure:
    """One timeseries against another at the same times, coloured by time, with a straight-line fit."""
    series, _upsets, out = _window(conn, [x_id, y_id], at, hours_before, hours_after, max_points=1)
    by_id = {s["id"]: s for s in series}
    if x_id not in by_id or y_id not in by_id:
        raise ChartError("x and y must each be one timeseries (a PI point)")
    sx, sy = by_id[x_id], by_id[y_id]
    ys = dict(sy["values"])
    pts = [(t, v, ys[t]) for t, v in sx["values"] if t in ys]
    if len(pts) < 3:
        raise ChartError("too few readings at the same times")
    xs, yv = [p[1] for p in pts], [p[2] for p in pts]
    data = [{"type": "scatter", "mode": "markers", "name": "readings", "x": xs, "y": yv, "text": [p[0] for p in pts],
             "marker": {"color": list(range(len(pts))), "colorscale": "Viridis", "size": 6,
                        "colorbar": {"title": {"text": "time"}, "tickvals": [0, len(pts) - 1],
                                     "ticktext": [pts[0][0][:16], pts[-1][0][:16]]}},
             "hovertemplate": "%{x:.3g}, %{y:.3g}<br>%{text}<extra></extra>"}]
    summary = {"n": len(pts), "x": _summ(sx), "y": _summ(sy), "provider": out["provider"],
               **synthetic_note(out["provider"], [sx, sy])}
    if fit:
        mx, my = sum(xs) / len(xs), sum(yv) / len(yv)
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in yv)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, yv))
        if sxx and syy:
            b = sxy / sxx
            a = my - b * mx
            lo, hi = min(xs), max(xs)
            data.append({"type": "scatter", "mode": "lines", "name": f"fit y = {a:.3g} + {b:.3g}x",
                         "x": [lo, hi], "y": [a + b * lo, a + b * hi], "line": {"dash": "dash"}})
            summary["fit"] = {"slope": rnd(b, 4), "intercept": rnd(a, 4), "r": rnd(sxy / (sxx * syy) ** 0.5)}
    title = f"{sy['point']} against {sx['point']}"
    return Figure("xy", title, data, layout(title, f"{sx['description'] or sx['point']} ({sx['uom']})",
                                            f"{sy['description'] or sy['point']} ({sy['uom']})"), summary, [x_id, y_id])


BAR_DATASETS = ("deferment_by_reason", "deferment_by_well", "deferment_by_tag", "deferment_by_class",
                "failures_by_tag", "oil_change_by_well")


def bar(conn, dataset: str, since: str | None = None, until: str | None = None, limit: int = 15) -> Figure:
    """A ranked bar chart (Pareto with a cumulative share line where the parts add up)."""
    from ..graph import operations as ops

    if dataset.startswith("deferment_by_"):
        by = dataset.removeprefix("deferment_by_")
        out = ops.deferment_pareto(conn, by, since, until, limit)
        items = [(r.get("label") or r.get(by) or "?", r["lost_oil_sm3"], r.get("fuzer_id")) for r in out["items"]]
        unit, total, what = "Sm3 oil", out["total_lost_oil_sm3"], f"Deferred oil by {by}"
    elif dataset == "failures_by_tag":
        out = ops.bad_actors(conn, since, until, limit)
        items = [(r["tag"], r["failures"], r["id"]) for r in out["bad_actors"]]
        items.sort(key=lambda r: -r[1])
        unit, total, what = "failures", None, "Failures (corrective notifications) by unit"
    elif dataset == "oil_change_by_well":
        out = ops.well_performance(conn, None, since, limit)
        items = [(r["well"], r["oil_change_pct"], r["id"]) for r in out["biggest_declines"]]
        unit, total, what = "% oil rate change", None, "Oil rate change by well, first to latest test"
    else:
        raise ChartError(f"dataset must be one of {', '.join(BAR_DATASETS)}")
    if not items:
        raise ChartError("nothing to chart in that window")
    labels, vals, ids = [i[0] for i in items], [i[1] for i in items], [i[2] for i in items]
    data = [{"type": "bar", "name": unit, "x": labels, "y": vals, "customdata": ids,
             "hovertemplate": f"%{{x}}: %{{y:.4g}} {unit}<extra></extra>"}]
    lay = layout(f"{what} ({out['since']} to {out['until']})", None, unit)
    summary = {"since": out["since"], "until": out["until"], "items": [{"label": a, "value": b} for a, b, _ in items[:10]]}
    if total:
        cum, run = [], 0.0
        for v in vals:
            run += v
            cum.append(round(run / total * 100, 1))
        data.append({"type": "scatter", "mode": "lines+markers", "name": "cumulative % of all", "x": labels, "y": cum,
                     "yaxis": "y2"})
        lay["yaxis2"] = {"overlaying": "y", "side": "right", "range": [0, 105], "title": {"text": "cumulative %"}}
        summary["total"] = total
        summary["top_share_pct"] = cum[min(2, len(cum) - 1)]
    return Figure("bar", lay["title"]["text"], data, lay, summary, [i for i in ids if i])


def distribution(conn, fid: str, split_at: str, hours_before: int = 168, hours_after: int = 168) -> Figure:
    """One timeseries before and after a moment (a repair, a setpoint change), as two boxes."""
    series, _, out = _window(conn, [fid], split_at, hours_before, hours_after, max_points=1)
    s = series[0]
    before = [v for t, v in s["values"] if t < split_at]
    after = [v for t, v in s["values"] if t >= split_at]
    if len(before) < 3 or len(after) < 3:
        raise ChartError("too few readings on one side of split_at")

    def stats(v):
        v = sorted(v)
        q = lambda f: v[min(len(v) - 1, int(f * len(v)))]
        return {"n": len(v), "median": rnd(q(0.5)), "p10": rnd(q(0.1)), "p90": rnd(q(0.9))}

    data = [{"type": "box", "name": "before", "y": before, "boxpoints": False},
            {"type": "box", "name": "after", "y": after, "boxpoints": False}]
    title = f"{s['point']} before and after {split_at.replace('T', ' ')}"
    return Figure("distribution", title, data, layout(title, None, f"{s['description']} ({s['uom']})", showlegend=False),
                  {"point": s["point"], "before": stats(before), "after": stats(after), "provider": out["provider"]}, [fid])


def custom(title: str, traces: list[dict], x_title: str | None = None, y_title: str | None = None) -> Figure:
    """A small chart from numbers the agent supplies (at most 500 points), labelled as such."""
    data, n = [], 0
    for t in traces[:8]:
        x, y = list(t.get("x") or []), list(t.get("y") or [])
        if not y or (x and len(x) != len(y)):
            raise ChartError("each trace needs y, and x of the same length if given")
        if not all(isinstance(v, (int, float)) for v in y) or not all(isinstance(v, (int, float, str)) for v in x):
            raise ChartError("y must be numbers; x numbers or text")
        n += len(y)
        kind = t.get("kind", "line")
        data.append({"type": "bar" if kind == "bar" else "scatter", "name": str(t.get("name", ""))[:60],
                     **({} if kind == "bar" else {"mode": "markers" if kind == "markers" else "lines+markers"}),
                     "x": x or list(range(len(y))), "y": y})
    if not data or n > 500:
        raise ChartError("1-8 traces, at most 500 points in all")
    title = f"{title[:100]} (assistant-entered data)"
    return Figure("custom", title, data, layout(title, x_title, y_title), {"points": n, "note": "data entered by the assistant"},
                  by="assistant")


# ---- views for agentic operations (#105 E) -------------------------------------------------------------

def operating_window(conn, fid: str, since: str, until: str, pack=None) -> Figure:
    """A point against its limits (#101): the value with each limit, time inside and outside, and the minutes
    outside per day."""
    from ..analysis import calcs, limits
    from ..sources.timeseries import _point

    p = _point(conn, fid)
    if not p:
        raise ChartError("that isn't a PI point")
    a, b = datetime.fromisoformat(since), datetime.fromisoformat(until)
    lines = limits.lines(limits.for_points(conn, pack, [fid], b)[fid])
    if not lines:
        raise ChartError("no limits on that point")
    v = calcs.read({"v": p}, a, b, 10)["v"]
    times, vals = list(v), list(v.values())
    lo = max((x["value"] for x in lines if x["type"] in ("lo", "lolo")), default=None)
    hi = min((x["value"] for x in lines if x["type"] in ("hi", "hihi")), default=None)
    out = [(t, x) for t, x in zip(times, vals) if (hi is not None and x > hi) or (lo is not None and x < lo)]
    per_day: dict[str, int] = {}
    for t, _ in out:
        per_day[t[:10]] = per_day.get(t[:10], 0) + 10
    top, bottom = stacked(2, 0.1)
    xs, ys = thin(times, vals)
    data = [{"type": "scatter", "mode": "lines", "name": p["point"], "x": xs, "y": ys, "yaxis": "y"},
            {"type": "bar", "name": "minutes outside per day", "x": list(per_day), "y": list(per_day.values()), "yaxis": "y2",
             "xaxis": "x2"}]
    lay = layout(f"{p['point']} against its operating window", showlegend=False)
    lay["yaxis"] = {"domain": top, "title": {"text": p["uom"] or ""}}
    lay["yaxis2"] = {"domain": bottom, "anchor": "x2", "title": {"text": "min outside"}}
    lay["xaxis2"] = {"anchor": "y2", "matches": "x"}
    lay["shapes"] = [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": "y", "y0": x["value"], "y1": x["value"],
                      "line": {"dash": "dash", "width": 1, "color": "rgba(200,50,45,0.9)" if x["basis"] == "iow" else
                               "rgba(190,120,0,0.9)" if x["basis"] == "alarm" else "rgba(110,120,135,0.8)"}} for x in lines]
    share = len(out) / len(vals) if vals else 0
    return Figure("operating_window", lay["title"]["text"], data, lay, {
        "point": p["point"], "window": [since, until], "limits": lines, "share_outside": round(share, 4),
        "minutes_outside": len(out) * 10, "days_outside": len(per_day),
        "worst_day": max(per_day.items(), key=lambda kv: kv[1]) if per_day else None}, [fid])


def loss_waterfall(conn, since: str, until: str, pack=None) -> Figure:
    """Oil potential, then planned losses, unplanned losses by reason, then actual, over a period (DAY001 and
    EC downtime), priced with the pack's oil price."""
    from ..analysis import value
    from ..graph import operations

    days = operations._rows(conn, "production_day", "f->>'report_date' >= %(s)s and f->>'report_date' < %(u)s",
                            {"s": since, "u": until})
    if not days:
        raise ChartError("no DAY001 reports in that period")
    pot = sum(operations._num(d["f"].get("oil_potential_sm3")) for d in days)
    act = sum(operations._num(d["f"].get("oil_sm3")) for d in days)
    planned, unplanned = 0.0, {}
    for r in operations._rows(conn, "downtime", "f->>'start' >= %(s)s and f->>'start' < %(u)s", {"s": since, "u": until}):
        f = r["f"]
        lost = operations._num(f.get("lost_oil_sm3"))
        if (f.get("reason_code") or "").startswith("PLAN") or f.get("reason_category") == "planned":
            planned += lost
        else:
            unplanned[f.get("reason") or "unknown"] = unplanned.get(f.get("reason") or "unknown", 0) + lost
    other = max(pot - act - planned - sum(unplanned.values()), 0.0)  # what downtime records don't explain
    steps = [("potential", pot, 0.0)]
    level = pot
    for name, v in [("planned", planned), *sorted(unplanned.items(), key=lambda kv: -kv[1]), ("unexplained", other)]:
        if v > 0:
            level -= v
            steps.append((name, -v, level))
    steps.append(("actual", act, 0.0))
    price = value.config(pack)["prices"]["oil_sm3"] if pack else value.DEFAULTS["prices"]["oil_sm3"]
    data = [{"type": "bar", "name": "oil", "x": [s[0] for s in steps], "y": [abs(s[1]) for s in steps],
             "base": [s[2] for s in steps], "text": [f"{abs(s[1]):,.0f} Sm3, ${abs(s[1]) * price:,.0f}" for s in steps],
             "marker": {"color": ["rgba(90,110,140,0.8)" if s[1] >= 0 else "rgba(210,60,50,0.75)" for s in steps]},
             "hovertemplate": "%{x}: %{text}<extra></extra>"}]
    lay = layout(f"Production losses {since} to {until}", None, "oil (Sm3)", showlegend=False)
    return Figure("loss_waterfall", lay["title"]["text"], data, lay, {
        "potential_sm3": round(pot), "actual_sm3": round(act), "planned_sm3": round(planned),
        "unplanned_sm3": {k: round(v) for k, v in sorted(unplanned.items(), key=lambda kv: -kv[1])},
        "unexplained_sm3": round(other), "lost_usd": round((pot - act) * price), "oil_price_usd_sm3": price,
        "efficiency_pct": round(act / pot * 100, 1) if pot else None}, [d["id"] for d in days][:5])


def score_breakdown(conn, ref: str, pack=None) -> Figure:
    """What makes up one item's #100 score: production, risk and compliance at stake, the urgency factor and
    the effort, so people can see why one card ranks above another."""
    from ..analysis import value
    from ..core.pack import resolve

    pack = pack or resolve(conn)
    found = value.ranked(conn, pack, ref_id=ref, limit=5)["items"]
    if not found:
        raise ChartError("nothing on the value score for that ref")
    s = found[0]
    parts = [(k, p["value_usd"]) for k, p in s["components"].items()]
    names = [k for k, _ in parts] + ["x urgency", "value", "effort"]
    vals = [v or 0 for _, v in parts] + [s["value_usd"] - s["at_stake_usd"], s["value_usd"], s["effort"]["value_usd"] or 0]
    data = [{"type": "bar", "x": names, "y": vals, "text": [("unknown" if (k in s["unknown"]) else f"${v:,.0f}") for k, v in
                                                          zip(names, vals)], "hovertemplate": "%{x}: %{text}<extra></extra>"}]
    lay = layout(f"Score breakdown: {s['label']}", None, "USD over the horizon", showlegend=False)
    return Figure("score_breakdown", lay["title"]["text"], data, lay, {
        "item": s["label"], "kind": s["kind"], "value_usd": s["value_usd"], "at_stake_usd": s["at_stake_usd"],
        "urgency_factor": s["urgency"]["factor"], "effort_usd": s["effort"]["value_usd"], "unknown": s["unknown"],
        "reason": s["reason"]}, [s["id"]])


CO2_T_PER_KSM3_FLARED = 2.0  # tonnes CO2 per kSm3 gas flared (combustion of a typical associated gas); an estimate


def flare(conn, since: str, until: str) -> Figure:
    """Daily flare from DAY001, with fuel gas and vent beside it when the report carries them (#369), and the flare's
    CO2 per barrel of oil (an estimate)."""
    from ..graph import operations

    days = sorted(operations._rows(conn, "production_day", "f->>'report_date' >= %(s)s and f->>'report_date' < %(u)s",
                                   {"s": since, "u": until}), key=lambda d: d["f"]["report_date"])
    if not days:
        raise ChartError("no DAY001 reports in that period")
    x = [d["f"]["report_date"] for d in days]
    fl = [operations._num(d["f"].get("flare_ksm3")) for d in days]
    oil = [operations._num(d["f"].get("oil_sm3")) for d in days]
    intensity = [round(f * CO2_T_PER_KSM3_FLARED * 1000 / (o * 6.2898), 2) if o > 0 else None for f, o in zip(fl, oil)]
    top, bottom = stacked(2, 0.1)
    from ..analysis.gas_balance import _f
    fuel = [_f(d["f"].get("fuel_gas_ksm3")) for d in days]
    vent = [_f(d["f"].get("vent_ksm3")) for d in days]
    has_fuel, has_vent = any(v is not None for v in fuel), any(v is not None for v in vent)
    data = [{"type": "bar", "name": "flare (kSm3/d)", "x": x, "y": fl, "yaxis": "y"},
            *([{"type": "bar", "name": "fuel (kSm3/d)", "x": x, "y": fuel, "yaxis": "y"}] if has_fuel else []),
            *([{"type": "bar", "name": "vent (kSm3/d)", "x": x, "y": vent, "yaxis": "y"}] if has_vent else []),
            {"type": "scatter", "mode": "lines", "name": "flare CO2 (kg per bbl oil)", "x": x, "y": intensity, "yaxis": "y2",
             "xaxis": "x2"}]
    lay = layout(f"Flare{', fuel and vent' if has_fuel or has_vent else ''} and the flare's CO2 intensity {since} to {until}")
    lay["barmode"] = "group"
    lay["yaxis"] = {"domain": top, "title": {"text": "kSm3/d"}}
    lay["yaxis2"] = {"domain": bottom, "anchor": "x2", "title": {"text": "kg CO2 / bbl"}}
    lay["xaxis2"] = {"anchor": "y2", "matches": "x"}
    vals = [i for i in intensity if i is not None]
    return Figure("flare", lay["title"]["text"], data, lay, {
        "days": len(days), "flare_ksm3": round(sum(fl)), "co2_t": round(sum(fl) * CO2_T_PER_KSM3_FLARED),
        "co2_kg_per_bbl_median": round(sorted(vals)[len(vals) // 2], 2) if vals else None,
        "fuel_ksm3": round(sum(v for v in fuel if v)) if has_fuel else None,
        "vent_ksm3": round(sum(v for v in vent if v), 1) if has_vent else None,
        "note": f"CO2 estimated at {CO2_T_PER_KSM3_FLARED} t per kSm3 flared, for the flare only. "
                + ("Fuel gas and vent are the report's own volumes; " if has_fuel or has_vent else "")
                + ("" if has_fuel and has_vent else "the report carries no "
                   + " or ".join(n for n, h in (("fuel gas", has_fuel), ("vent", has_vent)) if not h) + "; ")
                + "a closing gas balance is plot gas_balance. Evidence only, not an emissions inventory."},
        [d["id"] for d in days][:5])


def scorecard(conn, until: str | None = None, days: int = 30) -> Figure:
    """The KPI scorecard (P7.3a, #353): each KPI's change against the period before, as a share of where it was,
    coloured better or worse; the values, units and sources are in the summary. An unknown KPI is a gap, not zero."""
    from ..analysis import kpis

    try:
        s = kpis.scorecard(conn, until, days)
    except ValueError as e:
        raise ChartError(str(e)) from e
    colour = {"better": "rgba(40,150,90,0.85)", "worse": "rgba(210,60,50,0.85)", "same": "rgba(120,120,120,0.7)"}
    rows = list(reversed(s["kpis"]))
    pct = [rnd(100 * k["change"] / k["before"]["value"], 1) if k["change"] is not None and k["before"]["value"]
           else (0 if k["change"] == 0 else None) for k in rows]
    text = [f"{k['now']['value']} {k['unit']} (was {k['before']['value']})" if k["verdict"] != "unknown"
            else "unknown" for k in rows]
    data = [{"type": "bar", "orientation": "h", "name": "change", "x": pct, "y": [k["label"] for k in rows],
             "text": text, "textposition": "auto",
             "marker": {"color": [colour.get(k["verdict"], "rgba(0,0,0,0)") for k in rows]},
             "hovertemplate": "%{y}: %{text}<extra></extra>"}]
    lay = layout(f"KPIs {s['now']['since']} to {s['now']['through']} against the {days} days before",
                 "change against the period before (%)", None)
    return Figure("scorecard", lay["title"]["text"], data, lay, {
        "now": s["now"], "before": s["before"], "unknown": s["unknown"], "note": s["note"],
        "kpis": [{k2: k[k2] for k2 in ("label", "unit", "source", "verdict", "change")} |
                 {"now": k["now"]["value"], "before": k["before"]["value"]} for k in s["kpis"]]},
        [r for k in s["kpis"] for r in k["now"]["refs"][:3]])


def reconciliation(conn, since: str | None = None, until: str | None = None, days: int = 365) -> Figure:
    """Each month's daily allocated oil against what production accounting closed (P7.5c, #372), as the difference
    in % of the accounted volume, with the tolerance marked. A month that can't be compared isn't drawn as zero."""
    from ..analysis import accounting

    out = accounting.reconcile(conn, since, until, days)
    oil = sorted((m for m in out["months"] if m["product"] == "oil" and m["difference_pct"] is not None
                  and m["verdict"] in ("agrees", "differs", "preliminary")), key=lambda m: m["period"])
    if not oil:
        raise ChartError("no month in that window has both daily reports and an accounted oil volume")
    colour = {"agrees": "rgba(140,140,140,0.7)", "differs": "rgba(210,60,50,0.85)", "preliminary": "rgba(200,150,40,0.7)"}
    tol = out["tolerance_pct"]
    data = [{"type": "bar", "name": "daily reports against accounting", "x": [m["period"] for m in oil],
             "y": [m["difference_pct"] for m in oil], "marker": {"color": [colour[m["verdict"]] for m in oil]},
             "meta": {"entity": [m["accounting"]["record"] for m in oil]},
             "text": [m["why"] for m in oil], "hovertemplate": "%{x}: %{y} %<br>%{text}<extra></extra>"},
            {"type": "scatter", "mode": "lines", "name": f"±{tol:g} % tolerance", "x": [oil[0]["period"], oil[-1]["period"]],
             "y": [tol, tol], "line": {"dash": "dot", "color": "rgba(120,120,120,0.8)"}},
            {"type": "scatter", "mode": "lines", "showlegend": False, "x": [oil[0]["period"], oil[-1]["period"]],
             "y": [-tol, -tol], "line": {"dash": "dot", "color": "rgba(120,120,120,0.8)"}}]
    lay = layout(f"Daily allocated oil against production accounting, {out['since']} to {out['until']}", None,
                 "difference (% of accounted)")
    return Figure("reconciliation", lay["title"]["text"], data, lay, {
        "summary": out["summary"], "differs": [{k: m[k] for k in ("period", "difference_pct", "why")} | {
            "comment": m["accounting"]["comment"]} for m in oil if m["verdict"] == "differs"],
        "not_compared": [{k: m[k] for k in ("period", "product", "verdict", "why")} for m in out["months"]
                         if m["verdict"] in ("incomplete", "no_accounting", "no_operational")],
        "since": out["since"], "until": out["until"], "sources": out["sources"], "note": out["note"]},
        [m["accounting"]["record"] for m in oil][:10])


def loop_health(conn, days: int = 7, until: str | None = None) -> Figure:
    """Control loop health ranking (#356): each loop's score, worst on top, split into what makes it up (time in
    manual, output saturated, setpoint error, a regular oscillation); the controls engineer's worklist."""
    from ..analysis import control_loops as cl

    out = cl.health(conn, days, until)
    if not out["loops"]:
        raise ChartError("no control loop on the loop list with enough readings in that window")
    ls = list(reversed(out["loops"]))  # a horizontal bar chart draws its first row at the bottom
    y = [m["loop"] for m in ls]
    parts = [("time in manual (%)", lambda m: m["manual_pct"], "rgba(150,150,150,0.85)"),
             ("output saturated (% of auto)", lambda m: m["saturated_pct"] or 0, "rgba(230,150,40,0.85)"),
             ("setpoint error (10 x % of setpoint)", lambda m: min(100.0, 10 * (m["error_pct"] or 0)),
              "rgba(40,110,190,0.85)"),
             ("regular oscillation", lambda m: cl.OSCILLATION_SCORE if m["oscillating"] else 0, "rgba(210,60,50,0.85)")]
    data = [{"type": "bar", "orientation": "h", "name": n, "y": y, "x": [f(m) for m in ls], "marker": {"color": c}}
            for n, f, c in parts]
    lay = layout(f"Control loop health, {out['since'][:10]} to {out['until'][:10]}", "score", None)
    lay["barmode"] = "stack"
    return Figure("loop_health", lay["title"]["text"], data, lay, {
        "verdict": out["verdict"], "summary": out["summary"], "thresholds": out["thresholds"],
        "synthetic": out["synthetic"], "loops": [{k: m[k] for k in ("loop", "score", "flags", "manual_pct",
                                                                    "saturated_pct", "error_pct", "period_min")}
                                                 for m in out["loops"]], "note": out["note"]},
        [m["id"] for m in out["loops"] if m["flags"]][:10])


def valve_stiction(conn, ref: str, days: int = 1, until: str | None = None) -> Figure:
    """Valve stiction (#357): a loop's process value against its controller output over the window, with the
    cross-correlation verdict. A sticking valve draws a parallelogram, the output ramping while the value holds and
    then jumps; a tuning cycle draws an ellipse."""
    from ..analysis import calcs, control_loops, loops, stiction

    try:
        s = control_loops.series(conn, ref, days, until, loops.STEP_MINUTES)
        verdict = stiction.stiction(conn, ref, max(days, 1), until)["loops"][0]
    except calcs.CalcError as e:
        raise ChartError(str(e)) from e
    auto = [i for i in range(len(s["times"])) if s["mode"][i] >= 0.5]
    if not auto:
        raise ChartError(f"{s['loop']} was in manual all through that window")
    data = [{"type": "scatter", "mode": "lines+markers", "name": s["loop"], "x": [rnd(s["op"][i]) for i in auto],
             "y": [rnd(s["pv"][i]) for i in auto], "text": [s["times"][i] for i in auto],
             "marker": {"size": 4, "color": list(range(len(auto))), "colorscale": "Viridis"},
             "line": {"width": 1, "color": "rgba(120,120,120,0.4)"}}]
    lay = layout(f"{s['loop']} value against output, {s['since'][:10]} to {s['until'][:10]}",
                 "controller output (%)", f"process value ({s['unit']})" if s["unit"] else "process value")
    return Figure("valve_stiction", lay["title"]["text"], data, lay, {
        "verdict": verdict["verdict"], "why": verdict.get("why"), "r0_share": verdict.get("r0_share"),
        "period_min": verdict.get("period_min"), "caveat": verdict.get("caveat"), "valve": verdict.get("valve"),
        "synthetic": s["synthetic"], "method": "Horch (1999) cross-correlation of controller output and process value",
        "note": "A suspicion for the instrument technician to confirm in the field, not a diagnosis."}, [s["id"]])


def step_fit(conn, test: str) -> Figure:
    """A step test (#359): the value read every second against the fitted first order plus dead time model, and the
    output step on a second axis."""
    from ..analysis import calcs, control_loops, loop_tuning

    try:
        got = loop_tuning.tuning(conn, test)
        hr = control_loops.high_rate(conn, got["loop_id"], got["window"]["since"], got["window"]["until"],
                                     got["window"]["sample_s"])
    except calcs.CalcError as e:
        raise ChartError(str(e)) from e
    t = loop_tuning._seconds(hr["times"])
    step_at = float(t[hr["times"].index(got["window"]["step_at"])]) if got["window"]["step_at"] in hr["times"] else 0.0
    m = got["model"]
    before = [v for x, v in zip(t, hr["pv"]) if x < step_at]
    base = sum(before) / len(before)
    model = base + loop_tuning.fopdt(t - step_at, m["gain"], m["tau_s"], m["dead_s"], m["step"])
    data = [{"type": "scatter", "mode": "lines", "name": "value (every second)", "x": hr["times"], "y": hr["pv"],
             "line": {"width": 1, "color": "rgba(40,110,190,0.6)"}},
            {"type": "scatter", "mode": "lines", "name": "fitted model", "x": hr["times"],
             "y": [rnd(v) for v in model], "line": {"width": 2, "color": "rgba(210,60,50,0.9)"}},
            {"type": "scatter", "mode": "lines", "name": "controller output (%)", "x": hr["times"], "y": hr["op"],
             "yaxis": "y2", "line": {"width": 1, "shape": "hv", "color": "rgba(120,120,120,0.8)"}}]
    lay = layout(f"{got['loop']} step test {got['test']}: response and fitted model", None,
                 f"process value ({got['unit']})" if got["unit"] else "process value")
    lay["yaxis2"] = {"title": {"text": "controller output (%)"}, "overlaying": "y", "side": "right"}
    return Figure("step_fit", lay["title"]["text"], data, lay, {
        "model": {k: m[k] for k in ("gain", "gain_unit", "tau_s", "dead_s", "gain_pct_per_pct")},
        "fit_quality": got["fit_quality"], "suggested": got["suggested"], "why_no_suggestion": got["why_no_suggestion"],
        "window": got["window"], "method": got["method"], "synthetic": got["synthetic"], "boundary": got["boundary"]},
        [got["loop_id"]])

