"""Maintenance and inspection charts: wall thickness per CML (or the fastest wall loss across the
asset), reliability (Weibull or Crow-AMSAA) from failure notifications, bad actors, the work order
backlog and plan compliance."""

import math
from datetime import datetime

from ..analysis import calcs
from . import ChartError, Figure, layout, rnd


def wall_thickness(conn, fid: str | None = None, kind: str | None = None, min_required_mm: float | None = None,
                   limit: int = 20) -> Figure:
    """One item's CMLs over time with fitted loss lines (and retirement dates when the minimum required
    thickness is given); without an item, the items losing wall fastest across the asset."""
    from ..graph import measurements as m

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
        if (years := calcs.remaining_life(s["latest"]["value"], s["per_year"], min_required_mm)) is not None:
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


def reliability(conn, fid: str, method: str = "weibull") -> Figure:
    """Weibull probability plot of times between failures (median ranks; beta < 1 early failures,
    about 1 random, > 1 wear-out), or Crow-AMSAA cumulative failures on log-log (beta > 1: failures
    coming faster)."""
    label, per = calcs.failures(conn, fid)
    if method == "crow_amsaa":
        f = calcs.crow_amsaa([d for ds in per.values() for d in ds])
        dates, ts, n, beta, lam = f["dates"], f["ts"], f["n"], f["beta"], f["lam"]
        start = dates[0]
        data = [{"type": "scatter", "mode": "markers", "name": "failures", "x": ts, "y": n,
                 "text": [d.isoformat() for d in dates], "hovertemplate": "%{y} failures by day %{x}<br>%{text}<extra></extra>"},
                {"type": "scatter", "mode": "lines", "name": f"fit N = {lam:.3g} t^{beta:.2f}", "line": {"dash": "dash"},
                 "x": [ts[0], ts[-1]], "y": [lam * ts[0] ** beta, lam * ts[-1] ** beta]}]
        lay = layout(f"Crow-AMSAA: cumulative failures on {label}", f"days since {start}", "cumulative failures")
        lay["xaxis"]["type"] = lay["yaxis"]["type"] = "log"
        return Figure("crow_amsaa", lay["title"]["text"], data, lay, {
            "scope": label, "units": len(per), "failures": len(dates), "first": dates[0].isoformat(),
            "last": dates[-1].isoformat(), "beta": rnd(beta, 2), "lambda": rnd(lam, 4), "reading": f["reading"]},
            [fid])
    f = calcs.weibull(calcs.times_between(per))
    tbf, n, ys, beta, eta = f["tbf"], f["n"], f["ys"], f["beta"], f["eta"]
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
        "beta": rnd(beta, 2), "eta_days": rnd(eta, 0), "median_days": rnd(f["median"], 0), "reading": f["reading"]},
        [fid])


def bad_actors(conn, since: str | None = None, until: str | None = None, limit: int = 25) -> Figure:
    """Bad actors as bubbles: failures against oil lost to the downtime they caused, bubble size =
    downtime events, colour = ISO 14224 class."""
    from ..graph import operations as ops

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


def backlog(conn, as_of: str | None = None) -> Figure:
    """Open SAP work orders: how many by priority and by age, and how many are past their basic finish."""
    from ..graph import operations as ops

    out = ops.work_order_backlog(conn, as_of, limit=100_000)
    if not out["open_orders"]:
        raise ChartError(f"no open work orders as of {out['as_of']}")
    items = out["overdue_items"]
    undated = out["by_age"]["future"]
    ages = list(out["by_age"])
    data = [{"type": "bar", "name": "no priority" if p == "None" else f"priority {p}", "x": ages,
             "y": [out["by_age_priority"][a].get(p, 0) for a in ages],
             "hovertemplate": "%{x}: %{y} open orders<extra>%{fullData.name}</extra>"}
            for p in out["by_priority"]]
    lay = layout(f"Open work orders as of {out['as_of']}", "age since start date", "open work orders (count)",
                 barmode="stack")
    return Figure("backlog", lay["title"]["text"], data, lay, {
        "as_of": out["as_of"], "source": out["source"], "open_orders": out["open_orders"], "overdue": out["overdue"],
        "by_priority": out["by_priority"], "by_age": out["by_age"], "in_a_turnaround": out["in_a_turnaround"],
        "notifications_without_order": len(out["notifications_without_order"]),
        "note": f"{undated} orders have no start date, or start after {out['as_of']}, and are counted under 'future'."
                if undated else None}, [i["id"] for i in items])


def compliance(conn, as_of: str | None = None, horizon_days: int = 90) -> Figure:
    """PM and inspection plans by kind: on time, due within the horizon and overdue. A date position in SAP,
    not the state of the equipment."""
    from ..graph import operations as ops

    out = ops.maintenance_compliance(conn, as_of, horizon_days, limit=100_000)  # counted in full, not a top-N
    if not out["summary"]:
        raise ChartError("no maintenance or inspection plans")
    kinds = list(out["summary"])
    due = {k: sum(1 for i in out["due_soon"] if i["kind"] == k) for k in kinds}
    late = [out["summary"][k]["overdue"] for k in kinds]
    data = [{"type": "bar", "name": name, "x": kinds, "y": ys, "hovertemplate": "%{x}: %{y} plans<extra></extra>"}
            for name, ys in (("overdue", late), (f"due by {out['horizon']}", [due[k] for k in kinds]),
                             ("later", [out["summary"][k]["plans"] - out["summary"][k]["overdue"] - due[k] for k in kinds]))]
    lay = layout(f"Plan compliance as of {out['as_of']}", "plan kind (PM preventive, INSP inspection)",
                 "maintenance plans (count)", barmode="stack")
    return Figure("compliance", lay["title"]["text"], data, lay, {
        "as_of": out["as_of"], "horizon": out["horizon"], "source": out["source"], "summary": out["summary"],
        "worst_overdue": [{k: i[k] for k in ("plan", "kind", "tag", "next_due", "overdue_days", "needs_shutdown")}
                          for i in out["overdue"][:5]],
        "note": "Overdue means past next due in SAP; it does not state the equipment's condition."},
        [i["id"] for i in out["overdue"] + out["due_soon"]])


def pump_curve(conn, fid: str, days: int = 365, rho: float = 850.0) -> Figure:
    """A pump's OEM head and efficiency curves, with its operating points from PI (head from discharge
    minus suction pressure) coloured by time, the BEP and the preferred / allowable operating regions.
    Points falling below the curve at the same flow mean lost head (wear rings, impeller)."""
    p = calcs.pump_operation(conn, fid, days, rho)
    tag, c, start, end, ops, bep, deficit = p["tag"], p["curve"], p["start"], p["end"], p["ops"], p["bep"], p["by_month"]
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


# ---- equipment performance (#105 B) ----------------------------------------------------------------

def compressor_performance(conn, fid: str, days: int = 90, until: str | None = None) -> Figure:
    """A compressor stage's daily polytropic efficiency, against the window's first week."""
    from ..analysis import equipment

    out = equipment.compressor_performance(conn, fid, days=days, until=until)
    d = out["daily"]
    data = [{"type": "scatter", "mode": "lines+markers", "name": "polytropic efficiency (daily median)",
             "x": [x["day"] for x in d], "y": [x["efficiency"] for x in d]}]
    lay = layout(f"{calcs._label(conn, fid)} polytropic efficiency", None, "efficiency")
    lay["shapes"] = [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": "y", "y0": out["expected"], "y1": out["expected"],
                      "line": {"dash": "dash", "width": 1}}]
    return Figure("compressor_performance", lay["title"]["text"], data, lay, {
        k: out[k] for k in ("value", "expected", "deviation", "flag", "basis", "head_kj_kg", "note")}, out["sources"])


def cooler_fouling(conn, fid: str, days: int = 240, until: str | None = None) -> Figure:
    """A cooler's daily outlet temperature against its clean baseline and its high limit."""
    from ..analysis import equipment

    out = equipment.cooler_fouling(conn, fid, days=days, until=until)
    d = out["daily"]
    data = [{"type": "scatter", "mode": "lines", "name": "outlet temperature (daily median)", "x": [x["day"] for x in d],
             "y": [x["temp"] for x in d]}]
    lay = layout(f"{calcs._label(conn, fid)} fouling: outlet temperature", None, "degC")
    lay["shapes"] = [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": "y", "y0": out["expected"], "y1": out["expected"],
                      "line": {"dash": "dash", "width": 1}}]
    if out["high_limit"] is not None:
        lay["shapes"].append({"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": "y", "y0": out["high_limit"],
                              "y1": out["high_limit"], "line": {"color": "rgba(210,60,50,0.8)", "dash": "dot"}})
    return Figure("cooler_fouling", lay["title"]["text"], data, lay, {
        k: out[k] for k in ("value", "expected", "deviation", "flag", "rise_per_day", "since_clean", "high_limit",
                            "reaches_limit", "basis")}, out["sources"])


def peers(conn, fid: str) -> Figure:
    """The units of one class side by side: failures per year, outliers marked."""
    from ..analysis import equipment

    out = equipment.peers(conn, fid)
    us = out["units"]
    data = [{"type": "bar", "name": "failures per year", "x": [u["tag"] for u in us], "y": [u["failures_per_year"] for u in us],
             "customdata": [u["id"] for u in us],
             "marker": {"color": ["rgba(210,60,50,0.8)" if u in out["outliers"] else "rgba(90,110,140,0.7)" for u in us]}}]
    lay = layout(f"Peers: {out['class_name']}", None, "failures per year")
    lay["xaxis"] = {"tickangle": -60}
    return Figure("peers", lay["title"]["text"], data, lay, {
        k: out[k] for k in ("value", "flag", "class_name", "class_median_failures_per_year", "outliers", "basis")}, out["sources"])


def spend(conn, since: str | None = None, until: str | None = None, by: str = "unit", days: int = 365) -> Figure:
    """Work order cost ranked by unit, class or order type (P7.3c, #335): one trace per currency, an unknown currency
    labelled unknown, never summed with another."""
    from ..analysis import costs

    try:
        out = costs.spend(conn, since, until, by, days, limit=0)
    except ValueError as e:
        raise ChartError(str(e)) from e
    if not out["orders"]:
        raise ChartError("no work orders started in that window")
    data = []
    for cur in out["currencies"]:
        rows = sorted((r for r in out["rows"] if r["currency"] == cur and r["cost"]), key=lambda r: -r["cost"])[:15]
        name = "cost, currency unknown" if cur == costs.UNKNOWN else f"cost ({cur})"
        data.append({"type": "bar", "orientation": "h", "name": name, "x": [r["cost"] for r in rows],
                     "y": [r["label"] for r in rows], "meta": {"entity": [r["id"] for r in rows]},
                     "hovertemplate": f"%{{y}}: %{{x:,.0f}} {cur}<extra></extra>"})
    lay = layout(f"Work order cost by {by}, {out['since']} to {out['until']}",
                 "cost (" + ", ".join(out["currencies"]) + ")", None)
    lay["yaxis"] = {"autorange": "reversed"}
    return Figure("spend", lay["title"]["text"], data, lay, {k: out[k] for k in (
        "since", "until", "by", "orders", "orders_without_cost", "without_cost_pct", "currencies", "totals",
        "mixed_currencies", "source", "note")}, [r["id"] for r in out["rows"][:10] if r["id"]])


def unreliability(conn, pack, since: str | None = None, until: str | None = None, days: int = 365) -> Figure:
    """The cost of unreliability by unit (P7.3c, #352): lost oil at the pack's price beside corrective order cost,
    each labelled with its currency; the parts are shown side by side, not stacked, unless they share one."""
    from ..analysis import costs

    out = costs.unreliability(conn, pack, since, until, days, limit=15)
    rows = out["units"]
    if not rows:
        raise ChartError("no downtime caused by a unit and no corrective orders in that window")
    p = out["price"]
    data = [{"type": "bar", "orientation": "h", "name": f"lost oil ({p['currency']} at {p['oil_sm3']:g} per Sm3)",
             "x": [r["lost_oil_value"] for r in rows], "y": [r["unit"] for r in rows],
             "meta": {"entity": [r["id"] for r in rows]},
             "hovertemplate": f"%{{y}}: %{{x:,.0f}} {p['currency']} of lost oil<extra></extra>"}]
    for cur in out["order_currencies"]:
        data.append({"type": "bar", "orientation": "h",
                     "name": "corrective orders, currency unknown" if cur == costs.UNKNOWN else f"corrective orders ({cur})",
                     "x": [r["order_cost"].get(cur, 0) for r in rows], "y": [r["unit"] for r in rows],
                     "hovertemplate": f"%{{y}}: %{{x:,.0f}} {cur} of corrective orders<extra></extra>"})
    lay = layout(f"Cost of unreliability by unit, {out['since']} to {out['until']}", "cost", None, barmode="group")
    lay["yaxis"] = {"autorange": "reversed"}
    return Figure("unreliability", lay["title"]["text"], data, lay, {
        "since": out["since"], "until": out["until"], "price": p, "order_currencies": out["order_currencies"],
        "units": [{k: r[k] for k in ("unit", "lost_oil_sm3", "lost_oil_value", "order_cost", "total",
                                     "total_currency", "why_no_total")} for r in rows[:10]],
        "source": out["source"], "note": out["note"]}, [r["id"] for r in rows[:10]])


def backlog_burn(conn, since: str | None = None, until: str | None = None, days: int = 365) -> Figure:
    """Work orders created against closed each month, and the open backlog at each month's end (#348)."""
    from ..analysis import backlog

    out = backlog.burn(conn, since, until, days)
    months = out["months"]
    if not months:
        raise ChartError("no whole month in that window")
    x = [m["month"] for m in months]
    data = [{"type": "bar", "name": "created", "x": x, "y": [m["created"] for m in months],
             "marker": {"color": "rgba(90,120,200,0.75)"}},
            {"type": "bar", "name": "closed", "x": x, "y": [m["closed"] for m in months],
             "marker": {"color": "rgba(40,150,90,0.75)"}},
            {"type": "scatter", "mode": "lines+markers", "name": "open at month end", "x": x,
             "y": [m["open_at_end"] for m in months], "line": {"color": "rgba(210,60,50,0.9)"}}]
    lay = layout(f"Work orders created, closed and open, {out['since']} to {out['until']}", None, "work orders")
    lay["barmode"] = "group"
    return Figure("backlog_burn", lay["title"]["text"], data, lay, {
        "summary": out["summary"], "lag": {k: v for k, v in out["lag"].items()
                                           if k not in ("longest", "before", "without_refs")},
        "since": out["since"], "until": out["until"], "source": out["source"], "note": out["note"]},
        [r for m in months for r in m["refs"]["created"]][:10])


def work_execution(conn, since: str | None = None, until: str | None = None, days: int = 730, by: str = "quarter") -> Figure:
    """Schedule compliance and repeat repair rate per quarter (#373); a period below the sample floor has no point."""
    from ..analysis import work_execution as we

    out = we.trend(conn, since, until, days, by)
    ps = out["periods"]
    if not ps:
        raise ChartError("no work order finished or started in that window")
    x = [p["period"] for p in ps]
    data = [{"type": "scatter", "mode": "lines+markers", "name": "schedule compliance (%)", "x": x,
             "y": [p["schedule_compliance_pct"] for p in ps], "line": {"color": "rgba(40,150,90,0.9)"}},
            {"type": "scatter", "mode": "lines+markers", "name": f"repeat repairs within {we.REPEAT_DAYS} days (%)",
             "x": x, "y": [p["repeat_rate_pct"] for p in ps], "yaxis": "y2", "line": {"color": "rgba(210,60,50,0.9)"}}]
    lay = layout(f"Schedule compliance and repeat repairs, {out['since']} to {out['until']}", None, "compliance (%)")
    lay["yaxis2"] = {"title": {"text": "repeats (%)"}, "overlaying": "y", "side": "right", "showgrid": False}
    return Figure("work_execution", lay["title"]["text"], data, lay, {
        "summary": out["summary"], "thresholds": out["thresholds"], "by": by, "since": out["since"],
        "until": out["until"], "note": out["note"]},
        [r["id"] for p in ps for r in p["late"] + p["repeats"]][:10])


def ili_anomalies(conn, line: str | None = None) -> Figure:
    """ILI metal loss along a line (#366): depth against station for each run, the anomalies crossing the screen
    marked, and the growth and strength of each in the summary."""
    from ..analysis import ili

    out = ili.anomalies(conn, line)
    if not out["lines"]:
        raise ChartError(out["note"])
    ln = out["lines"][0]
    feats = conn.execute(
        "select g.field, g.value #>> '{}' as v, g.fuzer_id from golden g join edge e on e.src = g.fuzer_id "
        "where e.dst = %s and e.rel = 'found_on' and e.valid_to is null and g.field = any(%s)",
        (ln["id"], ["run_id", "station_m", "depth_pct", "feature_type"])).fetchall()
    by: dict[str, dict] = {}
    for r in feats:
        by.setdefault(r["fuzer_id"], {})[r["field"]] = r["v"]
    colors = ["rgba(150,150,150,0.8)", "rgba(40,110,190,0.9)", "rgba(40,150,90,0.9)"]
    data = []
    for i, run in enumerate(ln["runs"]):
        pts = sorted((float(f["station_m"]) / 1000, float(f["depth_pct"])) for f in by.values()
                     if f.get("run_id") == run["run"] and f.get("feature_type") == ili.METAL_LOSS and f.get("depth_pct"))
        data.append({"type": "scatter", "mode": "markers", "name": f"{run['run']} ({run['date']})",
                     "x": [p[0] for p in pts], "y": [p[1] for p in pts],
                     "marker": {"color": colors[min(i - len(ln["runs"]) + 3, 2)], "size": 8}})
    flagged = [a for a in ln["anomalies"] if a["screen"].startswith("exceeds")]
    if flagged:
        data.append({"type": "scatter", "mode": "markers", "name": "crosses the screen",
                     "x": [a["station_m"] / 1000 for a in flagged], "y": [a["depth_pct"] for a in flagged],
                     "text": [a["feature"] for a in flagged],
                     "marker": {"symbol": "circle-open", "size": 16, "color": "rgba(210,60,50,0.95)", "line": {"width": 2}}})
    lay = layout(f"ILI metal loss along {ln['line']}", "station (km)", "depth (% of wall)")
    lay["shapes"] = [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": ili.DEPTH_LIMIT, "y1": ili.DEPTH_LIMIT,
                      "line": {"dash": "dot", "color": "rgba(210,60,50,0.6)"}}]
    return Figure("ili_anomalies", lay["title"]["text"], data, lay, {
        "line": ln["line"], "verdict": out["verdict"], "summary": out["summary"], "runs": ln["runs"],
        "anomalies": [{k: a.get(k) for k in ("feature", "station_m", "depth_pct", "rate_pct_per_year", "erf",
                                               "projected_erf", "screen")} for a in ln["anomalies"]],
        "method": out["method"], "source": out["source"], "note": out["note"]}, [a["id"] for a in flagged][:10])


def co2_corrosion(conn, until: str | None = None, days: int = 365) -> Figure:
    """CO2 corrosion screening (#368): each line's uninhibited predicted rate against its measured CML rate, on a log
    scale, the lines out of step with their peers marked."""
    from ..analysis import co2_corrosion as cc

    out = cc.screen(conn, until, days)
    rows = [x for x in out["lines"] if x["predicted_mm_per_year"] is not None and x["measured_mm_per_year"] is not None]
    if not rows:
        raise ChartError(out["note"] if not out["lines"] else "no line has both a prediction and a measured CML rate")
    x = [r["line"] for r in rows]
    flagged = [r for r in rows if r["flag"] in ("out_of_step", "above_prediction")]
    data = [{"type": "bar", "name": "predicted, uninhibited (mm/y)", "x": x,
             "y": [r["predicted_mm_per_year"] for r in rows], "marker": {"color": "rgba(150,150,150,0.6)"}},
            {"type": "bar", "name": "measured CML rate (mm/y)", "x": x, "y": [r["measured_mm_per_year"] for r in rows],
             "marker": {"color": ["rgba(210,60,50,0.9)" if r in flagged else "rgba(40,110,190,0.9)" for r in rows]}}]
    lay = layout(f"CO2 corrosion: predicted against measured, {out['since']} to {out['until']}", None, "mm/y")
    lay["barmode"] = "group"
    lay["yaxis"]["type"] = "log"
    return Figure("co2_corrosion", lay["title"]["text"], data, lay, {
        "verdict": out["verdict"], "summary": out["summary"], "typical_ratio": out["typical_ratio"],
        "lines": [{k: r.get(k) for k in ("line", "predicted_mm_per_year", "measured_mm_per_year",
                                         "measured_over_predicted", "flag")} for r in rows],
        "method": out["method"], "source": out["source"], "note": out["note"]},
        [rec for r in flagged for rec in r["records"]][:10])


def vibration_spectrum(conn, tag: str, point: str | None = None) -> Figure:
    """A measurement point's latest condition-monitoring spectrum (#367) with its fault frequencies marked: 1x, 2x and
    the bearing defect frequencies and their harmonics, the indications in the summary."""
    from ..analysis import vibration_spectra as vs

    out = vs.spectra(conn, tag)
    pts = [p for p in out["points"] if not point or p["point"].lower() == point.lower()]
    if not pts:
        raise ChartError(out["note"] if not out["points"] else f"no spectrum at {point} on {tag}")
    p = pts[0]
    f = conn.execute("select value #>> '{}' as v from golden where fuzer_id = %s and field = 'peaks'",
                     (p["spectrum"],)).fetchone()
    peaks = vs.parse_peaks(f["v"] if f else None)
    xs, ys = [], []
    for hz, a in peaks:  # stems
        xs += [hz, hz, None]
        ys += [0, a, None]
    data = [{"type": "scatter", "mode": "lines", "name": f"spectrum {p['latest']} ({p['unit'] or 'mm/s'})",
             "x": xs, "y": ys, "line": {"color": "rgba(40,110,190,0.9)", "width": 2}}]
    top = max((a for _, a in peaks), default=1.0)
    colours = {"1x": "rgba(90,90,90,0.7)", "2x": "rgba(90,90,90,0.5)", "bpfo": "rgba(210,60,50,0.8)",
               "bpfi": "rgba(230,150,40,0.8)", "bsf": "rgba(140,80,170,0.8)", "ftf": "rgba(40,150,90,0.8)"}
    markers, shapes = [], []
    for name, hz in p["frequencies"].items():
        for h in ((1,) if name in ("1x", "2x") else (1, 2, 3)):
            shapes.append({"type": "line", "x0": h * hz, "x1": h * hz, "y0": 0, "y1": top * 1.1,
                           "line": {"dash": "dot" if h > 1 else "dash", "color": colours[name], "width": 1}})
            markers.append({"name": name, "harmonic": h, "hz": round(h * hz, 1)})
    lay = layout(f"Spectrum, {p['tag']} {p['point']}, {p['latest']}", "frequency (Hz)", f"amplitude ({p['unit'] or 'mm/s'})")
    lay["shapes"] = shapes
    lay["xaxis"]["range"] = [0, max(max((hz for hz, _ in peaks), default=0), 3.3 * p["frequencies"].get("bpfi", 0)) * 1.05]
    return Figure("vibration_spectrum", lay["title"]["text"], data, lay, {
        "tag": p["tag"], "point": p["point"], "date": p["latest"], "rpm": p["rpm"], "overall": p["overall"],
        "bearing": p["bearing"], "frequencies": p["frequencies"], "markers": markers,
        "indications": [{k: i.get(k) for k in ("fault", "reads", "hz", "amplitude", "harmonics", "since")}
                        for i in p["indications"]], "note": out["note"]}, [p["spectrum"]])
