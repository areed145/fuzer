"""Maintenance and inspection charts: wall thickness per CML (or the fastest wall loss across the
asset), reliability (Weibull or Crow-AMSAA) from failure notifications, and bad actors."""

import math
from datetime import date, datetime, timedelta

from . import ChartError, Figure, layout, rnd


def wall_thickness(conn, fid: str | None = None, kind: str | None = None, min_required_mm: float | None = None,
                   limit: int = 20) -> Figure:
    """One item's CMLs over time with fitted loss lines (and retirement dates when the minimum required
    thickness is given); without an item, the items losing wall fastest across the asset."""
    from .. import measurements as m

    if fid is None:
        return _wall_ranking(conn, kind, limit)
    series = m.for_entity(conn, fid, "wall_thickness")["series"]
    if not series:
        raise ChartError("no wall thickness readings on that item")
    worst = min(series, key=lambda s: s["per_year"] if s["per_year"] is not None else 0)
    label = series[0]["of"]["label"]
    data, cmls = [], []
    for s in series:
        vals = s["values"]
        ts = [datetime.fromisoformat(v["at"]) for v in vals]
        hot = s is worst
        data.append({"type": "scatter", "mode": "lines+markers", "name": s["location"] or "?",
                     "x": [v["at"][:10] for v in vals], "y": [v["value"] for v in vals],
                     "text": [v["reported"] and f"reported {v['reported']}" or (v["method"] or "") for v in vals],
                     "line": {"width": 3 if hot else 1.5}, "meta": {"entity": s["of"]["id"]},
                     "hovertemplate": "%{y:.2f} mm<br>%{x}<br>%{text}<extra>%{fullData.name}</extra>"})
        retire = None
        if min_required_mm and s["per_year"] and s["per_year"] < 0:
            years = (s["latest"]["value"] - min_required_mm) / -s["per_year"]
            retire = ts[-1].year + years
            end = ts[-1].replace(year=min(2200, int(retire)))
            data.append({"type": "scatter", "mode": "lines", "name": f"{s['location']} projected", "showlegend": False,
                         "x": [ts[-1].isoformat()[:10], end.isoformat()[:10]], "y": [s["latest"]["value"], min_required_mm],
                         "line": {"dash": "dot", "width": 1}})
        cmls.append({"cml": s["location"], "readings": s["count"], "latest_mm": rnd(s["latest"]["value"], 2),
                     "latest_at": s["latest"]["at"][:10], "loss_mm_per_year": rnd(-(s["per_year"] or 0), 3),
                     **({"retirement_year": rnd(retire, 1)} if retire else {})})
    lay = layout(f"Wall thickness at the CMLs on {label}", None, "wall thickness (mm)")
    if min_required_mm:
        lay["shapes"] = [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": "y", "y0": min_required_mm,
                          "y1": min_required_mm, "line": {"color": "rgba(210,60,50,0.8)", "dash": "dash"}}]
    cmls.sort(key=lambda c: -c["loss_mm_per_year"])
    return Figure("wall_thickness", lay["title"]["text"], data, lay, {
        "item": label, "cmls": cmls, "fastest": cmls[0]["cml"],
        "min_required_mm": min_required_mm,
        "note": None if min_required_mm else "no minimum required thickness given, so no retirement dates"}, [fid])


def _wall_ranking(conn, kind: str | None, limit: int) -> Figure:
    rows = conn.execute(
        """
        with cml as (
            select fuzer_id, location, count(*) as n,
                   regr_slope(value, extract(epoch from at) / 31557600) as per_year
            from measurement where quantity = 'wall_thickness' group by fuzer_id, location having count(*) >= 2
        )
        select c.fuzer_id, e.kind, l.label, g.value #>> '{}' as description,
               min(c.per_year) as worst, avg(c.per_year) as mean, count(*) as cmls
        from cml c join entity e using (fuzer_id) left join entity_label l using (fuzer_id)
        left join golden g on g.fuzer_id = c.fuzer_id and g.field = 'description'
        where %(kind)s::text is null or e.kind = %(kind)s
        group by c.fuzer_id, e.kind, l.label, g.value order by worst limit %(limit)s
        """, {"kind": kind, "limit": limit}).fetchall()
    if not rows:
        raise ChartError("no wall thickness readings")
    data = [{"type": "bar", "orientation": "h", "name": "fastest CML", "y": [r["label"] for r in rows],
             "x": [round(-r["worst"], 3) for r in rows], "customdata": [r["fuzer_id"] for r in rows],
             "text": [r["description"] or "" for r in rows], "hovertemplate": "%{y}: %{x:.3f} mm/year<br>%{text}<extra></extra>"},
            {"type": "bar", "orientation": "h", "name": "mean of its CMLs", "y": [r["label"] for r in rows],
             "x": [round(-r["mean"], 3) for r in rows], "customdata": [r["fuzer_id"] for r in rows]}]
    lay = layout("Fastest wall loss" + (f" ({kind}s)" if kind else ""), "wall loss (mm/year)", None, barmode="group")
    lay["yaxis"] = {"autorange": "reversed"}
    lay["height"] = 200 + 26 * len(rows)
    rates = sorted(-r["worst"] for r in rows)
    return Figure("wall_ranking", lay["title"]["text"], data, lay, {
        "items": [{"label": r["label"], "kind": r["kind"], "description": r["description"], "cmls": r["cmls"],
                   "fastest_loss_mm_per_year": rnd(-r["worst"], 3), "mean_loss_mm_per_year": rnd(-r["mean"], 3)}
                  for r in rows[:10]],
        "median_of_shown_mm_per_year": rnd(rates[len(rates) // 2], 3)}, [r["fuzer_id"] for r in rows])


def _failures(conn, fid: str) -> tuple[str, dict[str, list[date]]]:
    """Failure dates per unit for a tag / equipment, or for every unit in an equipment class."""
    e = conn.execute("select e.kind, l.label from entity e left join entity_label l using (fuzer_id) "
                     "where fuzer_id = %s", (fid,)).fetchone()
    if e is None:
        raise ChartError("unknown entity")
    units = [fid]
    if e["kind"] == "equipment_class":
        units = [r["src"] for r in conn.execute(
            "select distinct src from edge where rel = 'classified_as' and dst = %s", (fid,))]
    rows = conn.execute(
        """
        select c.dst as unit, g.value #>> '{}' as created from edge c
        join entity n on n.fuzer_id = c.src and n.kind = 'notification'
        join golden g on g.fuzer_id = c.src and g.field = 'created'
        where c.rel = 'concerns' and c.dst = any(%s)
        """, (units,)).fetchall()
    out: dict[str, list[date]] = {}
    for r in rows:
        out.setdefault(r["unit"], []).append(date.fromisoformat(r["created"][:10]))
    return e["label"] or fid, {u: sorted(d) for u, d in out.items()}


def reliability(conn, fid: str, method: str = "weibull") -> Figure:
    """Weibull probability plot of times between failures (median ranks; beta < 1 early failures,
    about 1 random, > 1 wear-out), or Crow-AMSAA cumulative failures on log-log (beta > 1: failures
    coming faster)."""
    label, per = _failures(conn, fid)
    if method == "crow_amsaa":
        dates = sorted(d for ds in per.values() for d in ds)
        if len(dates) < 4:
            raise ChartError("fewer than four failures")
        start = dates[0]
        ts = [max(1, (d - start).days) for d in dates]
        n = list(range(1, len(ts) + 1))
        pts = [(math.log(t), math.log(k)) for t, k in zip(ts, n) if t > 1]
        mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        beta = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x, _ in pts)
        lam = math.exp(my - beta * mx)
        data = [{"type": "scatter", "mode": "markers", "name": "failures", "x": ts, "y": n,
                 "text": [d.isoformat() for d in dates], "hovertemplate": "%{y} failures by day %{x}<br>%{text}<extra></extra>"},
                {"type": "scatter", "mode": "lines", "name": f"fit N = {lam:.3g} t^{beta:.2f}", "line": {"dash": "dash"},
                 "x": [ts[0], ts[-1]], "y": [lam * ts[0] ** beta, lam * ts[-1] ** beta]}]
        lay = layout(f"Crow-AMSAA: cumulative failures on {label}", f"days since {start}", "cumulative failures")
        lay["xaxis"]["type"] = lay["yaxis"]["type"] = "log"
        return Figure("crow_amsaa", lay["title"]["text"], data, lay, {
            "scope": label, "units": len(per), "failures": len(dates), "first": dates[0].isoformat(),
            "last": dates[-1].isoformat(), "beta": rnd(beta, 2), "lambda": rnd(lam, 4),
            "reading": "failures coming faster" if beta > 1.15 else "failures slowing" if beta < 0.85 else "steady rate"},
            [fid])
    tbf = sorted((b - a).days for ds in per.values() for a, b in zip(ds, ds[1:]) if (b - a).days > 0)
    if len(tbf) < 3:
        raise ChartError("fewer than three times between failures (need repeat failures on the same unit)")
    n = len(tbf)
    F = [(i + 1 - 0.3) / (n + 0.4) for i in range(n)]
    xs = [math.log(t) for t in tbf]
    ys = [math.log(-math.log(1 - f)) for f in F]
    mx, my = sum(xs) / n, sum(ys) / n
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    eta = math.exp(mx - my / beta)
    lo, hi = min(tbf) * 0.7, max(tbf) * 1.4
    ticks = [0.01, 0.05, 0.1, 0.2, 0.5, 0.632, 0.9, 0.99]
    data = [{"type": "scatter", "mode": "markers", "name": "times between failures", "x": tbf, "y": ys,
             "hovertemplate": "%{x} days<extra></extra>"},
            {"type": "scatter", "mode": "lines", "name": f"Weibull beta {beta:.2f}, eta {eta:.0f} d", "line": {"dash": "dash"},
             "x": [lo, hi], "y": [beta * (math.log(lo) - math.log(eta)), beta * (math.log(hi) - math.log(eta))]}]
    lay = layout(f"Weibull plot: times between failures on {label}", "days between failures", "unreliability F(t)")
    lay["xaxis"]["type"] = "log"
    lay["yaxis"] |= {"tickvals": [math.log(-math.log(1 - f)) for f in ticks], "ticktext": [f"{f * 100:g}%" for f in ticks]}
    return Figure("weibull", lay["title"]["text"], data, lay, {
        "scope": label, "units_with_repeats": sum(1 for d in per.values() if len(d) > 1), "times_between_failures": n,
        "beta": rnd(beta, 2), "eta_days": rnd(eta, 0), "median_days": rnd(eta * math.log(2) ** (1 / beta), 0),
        "reading": "early-life failures (installation, quality)" if beta < 0.85 else
        "random failures (time-based PM won't help)" if beta < 1.2 else "wear-out (a PM interval below eta helps)"},
        [fid])


def bad_actors(conn, since: str | None = None, until: str | None = None, limit: int = 25) -> Figure:
    """Bad actors as bubbles: failures against oil lost to the downtime they caused, bubble size =
    downtime events, colour = ISO 14224 class."""
    from .. import operations as ops

    out = ops.bad_actors(conn, since, until, limit)
    items = out["bad_actors"]
    if not items:
        raise ChartError("no failures in that window")
    data = []
    for cls in dict.fromkeys(i["class"] or "unclassified" for i in items):
        xs = [i for i in items if (i["class"] or "unclassified") == cls]
        data.append({"type": "scatter", "mode": "markers+text", "name": cls, "x": [i["failures"] for i in xs],
                     "y": [i["lost_oil_sm3"] for i in xs], "text": [i["tag"] for i in xs], "textposition": "top center",
                     "customdata": [i["id"] for i in xs],
                     "marker": {"size": [10 + 4 * min(10, i["downtime_events"]) for i in xs], "opacity": 0.7},
                     "hovertemplate": "%{text}: %{x} failures, %{y:.0f} Sm3 lost<extra>%{fullData.name}</extra>"})
    lay = layout(f"Bad actors {out['since']} to {out['until']}", "failures (corrective notifications)", "oil lost (Sm3)")
    return Figure("bad_actors", lay["title"]["text"], data, lay, {
        "since": out["since"], "until": out["until"],
        "top": [{k: i[k] for k in ("tag", "class", "failures", "mtbf_days", "lost_oil_sm3", "downtime_events",
                                    "repeat_failure_modes")} for i in items[:6]]}, [i["id"] for i in items])


def _window_of(conn, days: int) -> tuple[datetime, datetime]:
    from .. import operations

    end = datetime.fromisoformat(operations.as_of(conn)) + timedelta(days=1)
    return end - timedelta(days=days), end


def _read(points: dict[str, dict], start: datetime, end: datetime, step: int) -> dict[str, dict[str, float]]:
    """Values of several points on one time grid: {name: {time: value}}."""
    from .. import timeseries

    src = timeseries.provider()
    return {k: dict(src.series({**p, "upsets": []}, start, end, step)) for k, p in points.items()}


def _curve_or_why(conn, model: str | None, what: str) -> dict:
    from .. import curves

    if model is None:
        raise ChartError(f"no equipment model known for that {what}")
    c = curves.for_model(conn, model)
    if c:
        return c
    label = conn.execute("select label from entity_label where fuzer_id = %s", (model,)).fetchone()
    wait = curves.waiting(conn, model)
    why = "; ".join(f"datasheet {w['label']} " + ("waiting for review" if w["status"] == "open" else
                                                  "not read yet" if w["status"] is None else w["status"]) for w in wait)
    raise ChartError(f"no OEM curve for {label['label'] if label else model}" + (f" ({why})" if why else
                                                                                   " and no datasheet for it"))


def pump_curve(conn, fid: str, days: int = 365, rho: float = 850.0) -> Figure:
    """A pump's OEM head and efficiency curves, with its operating points from PI (head from discharge
    minus suction pressure) coloured by time, the BEP and the preferred / allowable operating regions.
    Points falling below the curve at the same flow mean lost head (wear rings, impeller)."""
    from .. import curves, timeseries

    tag = fid
    if conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()["kind"] == "equipment":
        r = conn.execute("select dst from edge where rel = 'installed_at' and src = %s and valid_to is null", (fid,)).fetchone()
        tag = r["dst"] if r else fid
    c = _curve_or_why(conn, curves.model_of(conn, tag), "pump")
    if c["curve"] != "pump" or "head" not in c["series"]:
        raise ChartError("that model's curve isn't a pump curve")
    pts = timeseries._unit_points(conn, tag, 20)
    need = {k: next((p for p in pts if (p["description"] or "").endswith(end)), None)
            for k, end in (("flow", " Flow"), ("suction", "Suction Pressure"), ("discharge", "Discharge Pressure"))}
    if None in need.values():
        raise ChartError("the pump needs flow, suction and discharge pressure points in PI")
    start, end = _window_of(conn, days)
    v = _read(need, start, end, 360)
    times = sorted(set(v["flow"]) & set(v["suction"]) & set(v["discharge"]))
    bep = c["ratings"].get("bep", [None, None])
    mcsf = (c["ratings"].get("mcsf") or [0])[0] or 0.3 * (bep[0] or 100)
    ops = [(t, v["flow"][t], (v["discharge"][t] - v["suction"][t]) * 1e5 / (rho * 9.81)) for t in times
           if v["flow"][t] > 0.5 * mcsf]
    if len(ops) < 10:
        raise ChartError("the pump hardly ran in that window")
    head = c["series"]["head"]
    t0 = datetime.fromisoformat(ops[0][0])
    data = [{"type": "scatter", "mode": "lines", "name": "OEM head curve", "x": [p[0] for p in head], "y": [p[1] for p in head],
             "line": {"width": 3}},
            {"type": "scatter", "mode": "markers", "name": "operating points (PI)", "x": [o[1] for o in ops],
             "y": [round(o[2], 1) for o in ops], "text": [o[0][:16] for o in ops], "meta": {"entity": tag},
             "marker": {"size": 5, "opacity": 0.6, "color": [(datetime.fromisoformat(o[0]) - t0).days for o in ops],
                        "colorscale": "Viridis", "colorbar": {"title": {"text": f"days since {ops[0][0][:10]}"}}},
             "hovertemplate": "%{x:.0f} m3/h, %{y:.0f} m<br>%{text}<extra></extra>"}]
    if bep[0]:
        data.append({"type": "scatter", "mode": "markers", "name": "BEP", "x": [bep[0]], "y": [bep[1]],
                     "marker": {"size": 14, "symbol": "star"}})
    if "efficiency" in c["series"]:
        e = c["series"]["efficiency"]
        data.append({"type": "scatter", "mode": "lines", "name": "OEM efficiency (%)", "x": [p[0] for p in e],
                     "y": [p[1] for p in e], "yaxis": "y2", "line": {"dash": "dash"}})
    label = conn.execute("select label from entity_label where fuzer_id = %s", (tag,)).fetchone()["label"]
    lay = layout(f"{label} against its pump curve", "flow (m3/h)", "head (m)")
    lay["yaxis2"] = {"overlaying": "y", "side": "right", "range": [0, 100], "title": {"text": "efficiency (%)"}}
    lay["shapes"] = []
    if por := c["ratings"].get("por"):
        lay["shapes"].append({"type": "rect", "xref": "x", "yref": "paper", "x0": por[0], "x1": por[1], "y0": 0, "y1": 1,
                              "fillcolor": "rgba(60,170,90,0.10)", "line": {"width": 0}, "layer": "below"})
    if aor := c["ratings"].get("aor"):
        lay["shapes"] += [{"type": "line", "xref": "x", "yref": "paper", "x0": x, "x1": x, "y0": 0, "y1": 1,
                           "line": {"dash": "dot", "width": 1}} for x in aor if x]
    # head against the curve at the same flow, month by month
    months: dict[str, list[float]] = {}
    for t, q, h in ops:
        hc = curves.at(head, q)
        if hc:
            months.setdefault(t[:7], []).append((h - hc) / hc * 100)
    deficit = [{"month": m, "head_vs_curve_pct": round(sorted(x)[len(x) // 2], 1)} for m, x in sorted(months.items())]
    in_por = sum(1 for _, q, _ in ops if por and por[0] <= q <= por[1]) / len(ops) if por else None
    worst = min(deficit, key=lambda d: d["head_vs_curve_pct"]) if deficit else None
    qs = sorted(o[1] for o in ops)
    return Figure("pump_curve", lay["title"]["text"], data, lay, {
        "pump": label, "curve_source": c["source"], "window": [start.date().isoformat(), end.date().isoformat()],
        "running_readings": len(ops), "bep": {"flow_m3h": bep[0], "head_m": bep[1]},
        "median_flow_m3h": round(qs[len(qs) // 2], 1), "median_flow_vs_bep_pct": round(qs[len(qs) // 2] / bep[0] * 100)
        if bep[0] else None, "share_in_preferred_region": round(in_por, 3) if in_por is not None else None,
        "head_vs_curve_by_month": deficit, "worst_month": worst,
        "latest": {"at": ops[-1][0][:16], "flow_m3h": round(ops[-1][1], 1), "head_m": round(ops[-1][2], 1)},
        "note": "head from discharge minus suction pressure at SG 0.85; points below the curve at the same flow "
                "mean lost head"}, [tag, c["model_id"]])
