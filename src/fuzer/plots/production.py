"""Production charts from well tests (the measurement table): decline with an Arps fit, the water
diagnostic (WOR and its derivative), a gas lifted well's performance curve, and lift gas allocation
across a header."""

import math
from datetime import datetime, timedelta

from ..analysis import calcs, forecast
from ..analysis.calcs import YEAR, linreg, rate
from ..core.labels import label as _label
from . import ChartError, Figure, layout, rnd, stacked, thin

DECLINE_MIN_TESTS = 4  # well tests a decline forecast rests on, as calcs.well_tests asks


def decline(conn, well: str, model: str = "auto", forecast_months: int = 12) -> Figure:
    """Oil rate from well tests (log scale) with an Arps fit after the last step change, and a forecast."""
    try:
        d = calcs.decline_fit(conn, well, model, product="oil")
    except calcs.CalcError as e:
        raise ChartError(f"{e}; the chart is of oil rate, and calc('decline') fits a gas well's gas rate") from e
    tests, cuts, start, seg, t0, fit = d["tests"], d["cuts"], d["start"], d["seg"], d["t0"], d["fit"]
    qs = [t["oil_rate"] for t in tests]
    last = seg[-1]["at"]
    horizon = [last + timedelta(days=30.4 * m) for m in range(forecast_months + 1)]
    fit_x = [t0 + timedelta(days=d) for d in range(0, (horizon[-1] - t0).days + 1, 15)]
    q_fit = [rate(fit, (x - t0).days / YEAR) for x in fit_x]
    ts, qs_fit = [(t["at"] - t0).days / YEAR for t in seg], [t["oil_rate"] for t in seg]
    enough = forecast.sufficiency([t["at"] for t in seg], DECLINE_MIN_TESTS)
    if not enough["quotable"]:
        raise ChartError("too little data to forecast: " + "; ".join(enough["reasons"]))
    t_last = (last - t0).days / YEAR
    days = range(int(forecast_months * 30.4))
    band = [forecast.arps_interval(fit, ts, qs_fit, t_last + d / YEAR) for d in days]
    cum = sum(b["value"] for b in band)
    cum_range = [round(sum(b["range"][i] for b in band)) for i in (0, 1)] if band and band[0]["range"] else None
    end_band = forecast.arps_interval(fit, ts, qs_fit, (horizon[-1] - t0).days / YEAR)
    edge = [x for x in fit_x if x >= last]
    edge_band = [forecast.arps_interval(fit, ts, qs_fit, (x - t0).days / YEAR) for x in edge]
    name = _label(conn, well)
    data = [
        {"type": "scatter", "mode": "markers", "name": "tests used in the fit", "x": [t["at"].isoformat() for t in seg],
         "y": [t["oil_rate"] for t in seg], "customdata": [t["record"] for t in seg], "marker": {"size": 7}},
        {"type": "scatter", "mode": "lines", "name": f"Arps {fit['model']}", "line": {"dash": "dash"},
         "x": [x.isoformat()[:10] for x in fit_x if x <= last], "y": [q for x, q in zip(fit_x, q_fit) if x <= last]},
        {"type": "scatter", "mode": "lines", "name": f"forecast {forecast_months} months", "line": {"dash": "dot"},
         "x": [x.isoformat()[:10] for x in fit_x if x >= last], "y": [q for x, q in zip(fit_x, q_fit) if x >= last]},
    ]
    if edge_band and edge_band[0]["range"]:
        xs = [x.isoformat()[:10] for x in edge]
        data += [{"type": "scatter", "mode": "lines", "name": "90 % range", "x": xs, "y": [b["range"][1] for b in edge_band],
                  "line": {"width": 0}, "showlegend": False, "hoverinfo": "skip"},
                 {"type": "scatter", "mode": "lines", "name": "90 % range", "x": xs, "y": [b["range"][0] for b in edge_band],
                  "line": {"width": 0}, "fill": "tonexty", "fillcolor": "rgba(120,120,120,0.15)"}]
    if start:
        data.insert(0, {"type": "scatter", "mode": "markers", "name": "before the last step change",
                        "x": [t["at"].isoformat() for t in tests[:start]], "y": [t["oil_rate"] for t in tests[:start]],
                        "marker": {"size": 6, "color": "rgba(140,140,140,0.6)"}})
    lay = layout(f"{name} oil rate decline", None, "oil rate (Sm3/d)")
    lay["yaxis"]["type"] = "log"
    lay["shapes"] = [{"type": "line", "xref": "x", "yref": "paper", "x0": tests[i]["at"].isoformat(),
                      "x1": tests[i]["at"].isoformat(), "y0": 0, "y1": 1, "line": {"dash": "dot", "width": 1}} for i in cuts]
    effective = 1 - rate(fit, 1) / rate(fit, 0)
    return Figure("decline", lay["title"]["text"], data, lay, {
        "well": name, "tests": len(tests), "fit_from": t0.isoformat()[:10], "tests_in_fit": len(seg),
        "step_changes": [{"at": tests[i]["at"].isoformat()[:10], "from": rnd(qs[i - 1], 1), "to": rnd(qs[i], 1)} for i in cuts],
        "model": fit["model"], "qi_sm3d": rnd(fit["qi"], 1), "b": rnd(fit["b"], 2),
        "nominal_decline_per_year": rnd(fit["D"], 3), "effective_decline_pct_per_year": rnd(effective * 100, 1),
        "r2_log": rnd(fit["r2"], 3), "latest_test": {"at": last.isoformat()[:10], "oil_sm3d": rnd(seg[-1]["oil_rate"], 1)},
        "fit_rate_now_sm3d": rnd(rate(fit, (last - t0).days / YEAR), 1),
        "forecast": {"months": forecast_months, "rate_at_end_sm3d": rnd(end_band["value"], 1),
                     "rate_at_end_range_sm3d": [rnd(x, 1) for x in end_band["range"]] if end_band["range"] else None,
                     "oil_sm3": round(cum), "oil_range_sm3": cum_range, "level": end_band["level"],
                     "window": enough["window"], "points": enough["points"], "sufficiency": enough,
                     "is": "an estimate from the fit, not a plan or a commitment"}}, [well])


def water_diagnostic(conn, well: str) -> Figure:
    """Water cut against cumulative oil, and the water-oil ratio and its time derivative on log scales
    (Chan): a steady rise with a rising derivative points to channelling, a derivative that falls then
    rises to coning, a sudden step to a breakthrough."""
    tests = [t for t in calcs.well_tests(conn, well) if "water_cut" in t and "oil_rate" in t]
    cum, run = [], 0.0
    for i, t in enumerate(tests):
        if i:
            run += (tests[i - 1]["oil_rate"] + t["oil_rate"]) / 2 * (t["at"] - tests[i - 1]["at"]).days
        cum.append(run)
    wor = [min(t["water_cut"], 99.0) / (100 - min(t["water_cut"], 99.0)) for t in tests]
    yrs = [(t["at"] - tests[0]["at"]).days / YEAR for t in tests]
    deriv = []
    for i in range(len(wor)):
        a, b = max(0, i - 1), min(len(wor) - 1, i + 1)
        deriv.append((wor[b] - wor[a]) / (yrs[b] - yrs[a]) if yrs[b] > yrs[a] else 0.0)
    smooth = [sum(deriv[max(0, i - 1):i + 2]) / len(deriv[max(0, i - 1):i + 2]) for i in range(len(deriv))]
    name = _label(conn, well)
    top, bottom = stacked(2, 0.12)
    x = [t["at"].isoformat()[:10] for t in tests]
    data = [
        {"type": "scatter", "mode": "lines+markers", "name": "water cut (%)", "x": cum, "y": [t["water_cut"] for t in tests],
         "xaxis": "x2", "yaxis": "y2", "text": x, "hovertemplate": "%{y:.1f}% at %{x:.0f} Sm3<br>%{text}<extra></extra>"},
        {"type": "scatter", "mode": "lines+markers", "name": "WOR", "x": x, "y": wor},
        {"type": "scatter", "mode": "lines", "name": "WOR' (per year)", "x": x, "y": [abs(d) or None for d in smooth],
         "line": {"dash": "dash"}},
    ]
    lay = layout(f"{name} water diagnostic", showlegend=True)
    lay["yaxis"] = {"domain": bottom, "type": "log", "title": {"text": "WOR, |WOR'| (log)"}}
    lay["xaxis"] = {"anchor": "y", "title": {"text": "test date"}}
    lay["yaxis2"] = {"domain": top, "anchor": "x2", "title": {"text": "water cut (%)"}}
    lay["xaxis2"] = {"anchor": "y2", "title": {"text": "cumulative oil since first test (Sm3, estimated from well tests)"}}
    lay["height"] = 620
    jumps = [(x[i], round(wor[i] - wor[i - 1], 3)) for i in range(1, len(wor))]
    third = max(2, len(wor) // 3)
    early = linreg(yrs[:third], [math.log(w) for w in wor[:third]])[1] if min(wor[:third]) > 0 else None
    late = linreg(yrs[-third:], [math.log(w) for w in wor[-third:]])[1] if min(wor[-third:]) > 0 else None
    return Figure("water_diagnostic", lay["title"]["text"], data, lay, {
        "well": name, "tests": len(tests), "cumulative_oil_sm3": round(run),
        "cumulative_basis": "estimated from well tests (rate held between tests); no per-well allocation is loaded",
        "water_cut": {"first": rnd(tests[0]["water_cut"], 1), "latest": rnd(tests[-1]["water_cut"], 1)},
        "wor": {"first": rnd(wor[0]), "latest": rnd(wor[-1])},
        "log_wor_slope_per_year": {"early": rnd(early), "late": rnd(late)},
        "derivative_per_year": {"early_mean": rnd(sum(smooth[:third]) / third), "late_mean": rnd(sum(smooth[-third:]) / third)},
        "largest_wor_step": dict(zip(("at", "change"), max(jumps, key=lambda j: j[1]))) if jumps else None}, [well])


def gas_lift_curve(conn, well: str, breakeven: float = 0.5) -> Figure:
    """A gas lifted well's performance curve: oil against lift gas from its tests (colour = date), with
    the decline taken out, the current operating point, the marginal response there, and the optimum
    where one more kSm3/d of gas gains `breakeven` Sm3/d of oil. Tests far below the curve (a valve
    failed open: more gas, less oil) are left out of the fit and shown apart."""
    g = calcs.gas_lift_fit(conn, well, breakeven)
    tests, pts, keep, fit, t_now = g["tests"], g["pts"], g["keep"], g["fit"], g["t_now"]
    a, c, k = g["a"], g["c"], g["k"]
    gmax = max(p[0] for p in pts) * 1.4
    gs = [gmax * i / 60 for i in range(61)]
    curve = [a + c * (1 - math.exp(-x / k)) for x in gs]
    g_now, q_now, marginal, g_opt, gain = g["g_now"], g["q_now"], g["marginal"], g["g_opt"], g["gain"]
    name = _label(conn, well)
    out_pts = [i for i in range(len(pts)) if i not in keep]
    dates = [t["at"].isoformat()[:10] for t in tests]
    # Each test moved to today along the fitted decline, so the points sit on the curve they're read against.
    now = [q + fit["d"] * (t_now - t) for _, q, t in pts]
    data = [
        {"type": "scatter", "mode": "markers", "name": "tests, adjusted to today for decline",
         "x": [pts[i][0] for i in keep], "y": [now[i] for i in keep],
         "text": [f"{dates[i]}, tested {pts[i][1]:.1f} Sm3/d" for i in keep], "customdata": [tests[i]["record"] for i in keep],
         "marker": {"size": 8, "color": [pts[i][2] for i in keep], "colorscale": "Viridis",
                    "colorbar": {"title": {"text": "years"}}},
         "hovertemplate": "%{y:.1f} Sm3/d today at %{x:.1f} kSm3/d<br>%{text}<extra></extra>"},
        {"type": "scatter", "mode": "lines", "name": f"performance curve now ({dates[-1]})", "x": gs, "y": curve},
        {"type": "scatter", "mode": "markers", "name": "latest test", "x": [g_now], "y": [q_now],
         "marker": {"size": 14, "symbol": "star"}},
    ]
    if out_pts:
        data.append({"type": "scatter", "mode": "markers", "name": "far below the curve (left out)",
                     "x": [pts[i][0] for i in out_pts], "y": [now[i] for i in out_pts], "text": [dates[i] for i in out_pts],
                     "marker": {"size": 8, "symbol": "x"}, "hovertemplate": "%{y:.1f} at %{x:.1f}<br>%{text}<extra></extra>"})
    if g_opt:
        data.append({"type": "scatter", "mode": "markers", "name": f"optimum at {breakeven} Sm3 oil per kSm3 gas",
                     "x": [g_opt], "y": [a + c * (1 - math.exp(-g_opt / k))], "marker": {"size": 12, "symbol": "diamond"}})
    lay = layout(f"{name} gas lift performance", "lift gas (kSm3/d)", "oil rate (Sm3/d)")
    return Figure("gas_lift_curve", lay["title"]["text"], data, lay, {
        "well": name, "tests": len(tests), "tests_left_out": [{"at": dates[i], "lift_gas": rnd(pts[i][0], 1),
                                                                "oil": rnd(pts[i][1], 1)} for i in out_pts],
        "curve": {"oil_at_zero_gas": rnd(a, 1), "max_gain_from_gas": rnd(c, 1), "saturation_ksm3d": rnd(k, 1),
                  "decline_sm3d_per_year": rnd(fit["d"], 1)},
        "now": {"lift_gas_ksm3d": rnd(g_now, 1), "oil_sm3d": rnd(q_now, 1),
                "marginal_oil_per_extra_gas": rnd(marginal, 2)},
        "optimum": {"breakeven_oil_per_gas": breakeven, "lift_gas_ksm3d": rnd(g_opt, 1),
                    "oil_change_sm3d": rnd(gain, 1), "gas_change_ksm3d": rnd(g_opt - g_now, 1)}}, [well])


def gas_lift_allocation(conn, ref: str | None = None) -> Figure:
    """Every gas lifted well's marginal response (oil per extra lift gas, from its tests with decline
    taken out) by header: moving gas from the bottom of a header's list to the top gains oil."""
    from ..graph import operations as ops

    out = ops.gas_lift_network(conn, ref, limit=500)
    wells = sorted(out["most_response"], key=lambda w: -w["response_oil_per_extra_gas"])
    if not wells:
        raise ChartError("no gas lifted wells with enough tests")
    data, moves = [], []
    for h in dict.fromkeys(w["header"] or "no header" for w in wells):
        ws = [w for w in wells if (w["header"] or "no header") == h]
        data.append({"type": "bar", "name": h, "x": [w["well"] for w in ws], "y": [w["response_oil_per_extra_gas"] for w in ws],
                     "customdata": [w["id"] for w in ws], "text": [f"{w['lift_gas_ksm3d']} kSm3/d gas, {w['oil_sm3d']} Sm3/d oil"
                                                                 for w in ws],
                     "hovertemplate": "%{x}: %{y:.2f} Sm3/d per kSm3/d<br>%{text}<extra>%{fullData.name}</extra>"})
        if len(ws) >= 2 and ws[-1]["lift_gas_ksm3d"]:
            hi, lo = ws[0], ws[-1]
            move = round(min(2.0, lo["lift_gas_ksm3d"] * 0.3), 1)
            moves.append({"header": h, "from": lo["well"], "to": hi["well"], "move_ksm3d": move,
                          "oil_gain_sm3d": rnd((hi["response_oil_per_extra_gas"] - lo["response_oil_per_extra_gas"]) * move, 1)})
    lay = layout("Marginal gas lift response by well", None, "oil gained per extra lift gas (Sm3/d per kSm3/d)",
                 barmode="group")
    lay["xaxis"] = {"tickangle": -60}
    return Figure("gas_lift_allocation", lay["title"]["text"], data, lay, {
        "since": out["since"], "until": out["until"], "wells": len(wells), "lift_gas_ksm3d": out["lift_gas_ksm3d"],
        "headers": [{k: h[k] for k in ("header", "gas_lifted_wells", "lift_gas_ksm3d", "oil_sm3d")} for h in out["headers"]],
        "most_response": [{k: w[k] for k in ("well", "header", "response_oil_per_extra_gas", "lift_gas_ksm3d")} for w in wells[:5]],
        "least_response": [{k: w[k] for k in ("well", "header", "response_oil_per_extra_gas", "lift_gas_ksm3d")} for w in wells[-5:]],
        "suggested_moves": moves,
        "note": "response is a slope from tests over the window; check against a step-rate test before moving gas"},
        [w["id"] for w in wells])



def choke(conn, fid: str, days: int = 730, sg: float = calcs.SG) -> Figure:
    """A well's production choke against its characteristic: effective Cv at each well test (oil wells:
    liquid rate from the test, pressure drop from wellhead to the jacket header in PI) or from PI rates
    (gas wells), against the opening. Above the curve = more flow than the trim should pass (erosion);
    below = restriction (scale, hydrate, debris). A replacement starts a new series."""
    k = calcs.choke_operation(conn, fid, days, sg)
    well, tag, wl, tl, c, start, end = k["well"], k["tag"], k["wl"], k["tl"], k["curve"], k["start"], k["end"]
    installs, rows, ratio, note = k["installs"], k["rows"], k["ratio"], k["note"]
    cv_curve = c["series"]["cv"]
    cut = installs[-1] if installs else None
    data = [{"type": "scatter", "mode": "lines", "name": f"characteristic ({c['source']})", "x": [p[0] for p in cv_curve],
             "y": [p[1] for p in cv_curve], "line": {"width": 3}}]
    t0 = datetime.fromisoformat(rows[0][0])
    for name, part in (("before the choke was replaced", [r for r in ratio if cut and r[0] < cut]),
                       (f"since new choke {cut}" if cut else "effective Cv", [r for r in ratio if not cut or r[0] >= cut])):
        if part:
            data.append({"type": "scatter", "mode": "markers", "name": name, "x": [round(r[1], 1) for r in part],
                         "y": [round(r[2], 2) for r in part], "text": [f"{r[0][:10]}: {r[3]:.2f}x the curve" for r in part],
                         "meta": {"entity": well},
                         "marker": {"size": 7, "symbol": "circle" if name.startswith("since") or not cut else "x",
                                    "color": [(datetime.fromisoformat(r[0]) - t0).days for r in part],
                                    "colorscale": "Viridis"},
                         "hovertemplate": "%{x:.0f}% open, Cv %{y:.2f}<br>%{text}<extra></extra>"})
    lay = layout(f"{wl} production choke {tl} against its characteristic", "choke opening (%)", "Cv (US)")
    quarters: dict[str, list[float]] = {}
    for t, _, _, r in ratio:
        quarters.setdefault(f"{t[:4]}-Q{(int(t[5:7]) - 1) // 3 + 1}", []).append(r)
    by_q = [{"quarter": q, "cv_vs_curve": round(sorted(x)[len(x) // 2], 2)} for q, x in sorted(quarters.items())]
    peak = max(ratio, key=lambda r: abs(r[3] - 1))
    drift, reading = k["drift"], k["reading"]
    return Figure("choke", lay["title"]["text"], data, lay, {
        "well": wl, "choke": tl, "curve_source": c["source"], "points": len(ratio),
        "window": [start.date().isoformat(), end.date().isoformat()], "replaced": installs,
        "cv_vs_curve_by_quarter": by_q, "before_replacement_latest": round(drift, 2), "reading": reading,
        "furthest": {"at": peak[0][:10], "opening_pct": round(peak[1], 1), "cv_vs_curve": round(peak[3], 2)},
        **({"note": note} if note else {})}, [well, tag, c["model_id"]])


# ---- back-pressure (#107) ---------------------------------------------------------------------------

def pressure_profile(conn, well: str, at: str | None = None, ref_at: str | None = None) -> Figure:
    """Pressure at each node down a well's flow path, today and (with ref_at) on a reference date."""
    from ..analysis import hydraulics

    now = hydraulics.pressure_profile(conn, well, at)
    data = [{"type": "scatter", "mode": "lines+markers", "name": f"at {at or 'today'}", "x": [n["node"] for n in now["nodes"]],
             "y": [n["pressure_barg"] for n in now["nodes"]], "text": [n["point"] for n in now["nodes"]],
             "hovertemplate": "%{x}: %{y:.2f} barg<br>%{text}<extra></extra>"}]
    ref = hydraulics.pressure_profile(conn, well, ref_at) if ref_at else None
    if ref:
        data.append({"type": "scatter", "mode": "lines+markers", "name": f"at {ref_at}", "line": {"dash": "dot"},
                     "x": [n["node"] for n in ref["nodes"]], "y": [n["pressure_barg"] for n in ref["nodes"]]})
    lay = layout(f"{_label(conn, well)} pressure down its flow path", None, "pressure (barg)")
    return Figure("pressure_profile", lay["title"]["text"], data, lay, {
        "well": _label(conn, well), "nodes": now["nodes"], "segments": now["segments"], "largest_drop": now["largest_drop"],
        **({"reference": {"at": ref_at, "nodes": ref["nodes"]}} if ref else {})}, now["sources"][:8])


def backpressure_sensitivity(conn, scope: str = "facility", at: str | None = None) -> Figure:
    """Oil per bar of back-pressure for each back-pressure-limited well: the model's and the tests'."""
    from ..analysis import hydraulics

    s = hydraulics.backpressure_sensitivity(conn, scope, at)
    ws = s["wells"]
    if not ws:
        raise ChartError("no back-pressure-limited wells with tests in that scope")
    data = [{"type": "bar", "name": "model (inflow and choke)", "x": [w["well"] for w in ws],
             "y": [w["model_sm3d_per_bar"] for w in ws], "customdata": [w["id"] for w in ws]},
            {"type": "bar", "name": "data (tests against header pressure)", "x": [w["well"] for w in ws],
             "y": [w["data_sm3d_per_bar"] for w in ws], "customdata": [w["id"] for w in ws]}]
    lay = layout("Oil per bar of back-pressure", None, "Sm3/d oil per bar (negative: more back-pressure, less oil)",
                 barmode="group")
    lay["xaxis"] = {"tickangle": -60}
    return Figure("backpressure_sensitivity", lay["title"]["text"], data, lay, {
        "total_model_sm3d_per_bar": s["value"], "wells": len(ws), "by_header": s["by_header"][:10],
        "disagree": s["disagree"][:10], "basis": s["basis"]}, [w["id"] for w in ws])


def _pressure_series(conn, well: str, days: int, until: str | None, step: int = 60):
    from ..analysis import calcs, hydraulics

    pts = hydraulics.well_points(conn, well)
    if not pts["back"]:
        raise ChartError("no production header pressure for the well's jacket")
    start, end = calcs.window_of(conn, days, until)
    v = calcs.read({"whp": pts["whp"], "back": pts["back"]}, start, end, step)
    times = sorted(set(v["whp"]) & set(v["back"]))
    return pts, times, [v["whp"][t] for t in times], [v["back"][t] for t in times]


def choke_criticality(conn, well: str, days: int = 90, until: str | None = None) -> Figure:
    """Back over wellhead pressure (absolute) through a well's choke over time, against the critical
    ratio: under it, back-pressure doesn't reach the well."""
    from ..analysis import hydraulics

    pts, times, whp, back = _pressure_series(conn, well, days, until)
    ratio = [round((b + hydraulics.ATM) / (w + hydraulics.ATM), 3) for w, b in zip(whp, back)]
    xs, ys = thin(times, ratio)
    data = [{"type": "scatter", "mode": "lines", "name": "back / wellhead pressure", "x": xs, "y": ys}]
    lay = layout(f"{_label(conn, well)} choke: pressure ratio", None, "back / wellhead pressure (absolute)")
    lay["shapes"] = [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": "y", "y0": hydraulics.CRITICAL,
                      "y1": hydraulics.CRITICAL, "line": {"dash": "dash", "color": "rgba(210,60,50,0.8)"}}]
    below = sum(r < hydraulics.CRITICAL for r in ratio)
    return Figure("choke_criticality", lay["title"]["text"], data, lay, {
        "well": _label(conn, well), "critical_ratio": hydraulics.CRITICAL, "latest_ratio": ratio[-1] if ratio else None,
        "share_critical": round(below / len(ratio), 3) if ratio else None,
        "reading": "choke-limited (critical flow)" if ratio and ratio[-1] < hydraulics.CRITICAL else "back-pressure reaches the well"},
        [well, pts["whp"]["id"], pts["back"]["id"]])


def dp_drift(conn, well: str, days: int = 60, until: str | None = None) -> Figure:
    """The wellhead-to-header pressure drop over time against its baseline band, with any rise marked."""
    from ..analysis import hydraulics

    d = hydraulics.dp_drift(conn, well, days, until)
    _pts, times, whp, back = _pressure_series(conn, well, days, until)
    dp = [w - b for w, b in zip(whp, back)]
    base = sorted(dp[: max(24, len(dp) // 10)])
    lo, hi = base[len(base) // 10], base[-max(1, len(base) // 10)]
    xs, ys = thin(times, [round(x, 3) for x in dp])
    data = [{"type": "scatter", "mode": "lines", "name": "wellhead - header pressure", "x": xs, "y": ys}]
    lay = layout(f"{_label(conn, well)} pressure drop to the header", None, "bar")
    lay["shapes"] = [{"type": "rect", "xref": "paper", "x0": 0, "x1": 1, "yref": "y", "y0": lo, "y1": hi, "layer": "below",
                      "fillcolor": "rgba(110,120,135,0.15)", "line": {"width": 0}}]
    if d["rising_since"]:
        lay["shapes"].append({"type": "line", "xref": "x", "x0": d["rising_since"], "x1": d["rising_since"], "yref": "paper",
                              "y0": 0, "y1": 1, "line": {"dash": "dot", "color": "rgba(210,60,50,0.8)"}})
    return Figure("dp_drift", lay["title"]["text"], data, lay, {
        "well": _label(conn, well), "dp_now_bar": d["value"], "dp_before_bar": d["expected"], "flag": d["flag"],
        "rising_since": d["rising_since"], "baseline_band": [round(lo, 2), round(hi, 2)], "changes": d["changes"]},
        d["sources"])


def debottleneck(conn, scope: str = "facility", at: str | None = None, pack=None, options: dict | None = None) -> Figure:
    """Debottleneck options ranked by oil gained, coloured by kind (`options`: a scan of that scope already made)."""
    from ..analysis import hydraulics
    from ..core.pack import resolve

    out = options or hydraulics.debottleneck_options(conn, pack or resolve(conn), scope, at, limit=15)
    opts = out["options"][:15]
    if not opts:
        raise ChartError("no debottleneck options in that scope")
    data = []
    for kind in dict.fromkeys(o["kind"] for o in opts):
        os_ = [o for o in opts if o["kind"] == kind]
        data.append({"type": "bar", "orientation": "h", "name": kind.replace("_", " "), "y": [o["action"] for o in os_],
                     "x": [o["gain_sm3d"] for o in os_], "text": [f"confidence {o['confidence']}" for o in os_],
                     "hovertemplate": "%{y}: %{x:.0f} Sm3/d<br>%{text}<extra></extra>"})
    lay = layout("Debottleneck options by oil gained", "oil gained (Sm3/d)", None)
    lay["yaxis"] = {"autorange": "reversed", "automargin": True}
    lay["height"] = 220 + 28 * len(opts)
    return Figure("debottleneck", lay["title"]["text"], data, lay, {
        "at": out["at"], "considered": out["considered"],
        "options": [{k: o[k] for k in ("rank", "action", "gain_sm3d", "confidence", "next_constraint", "confirm_by")} for o in opts[:8]],
        "note": "proposals for people, not operating instructions"}, [o["target"] for o in opts if o.get("target")][:10])


def forecast_vs_actual(conn, well: str | None = None, since: str | None = None, until: str | None = None,
                       days: int = 365) -> Figure:
    """A well's decline forecast with its 90 % range against its tests in the window, its downtime marked; without a
    well, the wells ranked by their latest gap to forecast (P7.3b, #336)."""
    from ..analysis import forecast_actual as fa

    out = fa.forecast_vs_actual(conn, well, since, until, days)
    if well:
        w = out["wells"][0] if out["wells"] else None
        if not w:
            nf = out["not_fitted"][0] if out["not_fitted"] else None
            raise ChartError(f"too little data to forecast: {nf['reason']}" if nf else "no well tests on that well")
        pts = w.get("points") or []
        if not pts:
            raise ChartError("no well test in the window to compare")
        xs = [p["at"] for p in pts]
        data = [
            {"type": "scatter", "mode": "lines", "name": "90 % range", "x": xs, "y": [p["high_sm3d"] for p in pts],
             "line": {"width": 0}, "showlegend": False, "hoverinfo": "skip"},
            {"type": "scatter", "mode": "lines", "name": "90 % range", "x": xs, "y": [p["low_sm3d"] for p in pts],
             "line": {"width": 0}, "fill": "tonexty", "fillcolor": "rgba(120,120,120,0.18)"},
            {"type": "scatter", "mode": "lines", "name": "forecast (its own decline)", "x": xs,
             "y": [p["forecast_sm3d"] for p in pts], "line": {"dash": "dash"}},
            {"type": "scatter", "mode": "markers", "name": "well tests", "x": xs, "y": [p["actual_sm3d"] for p in pts],
             "customdata": [p["record"] for p in pts], "marker": {"size": 8, "color": [
                 "rgba(210,60,50,0.9)" if p["outside"] == "below" else "rgba(40,110,180,0.9)" for p in pts]}},
        ]
        lay = layout(f"{w['well']} forecast against tests {out['since']} to {out['until']}", None, "oil rate (Sm3/d)")
        lay["shapes"] = [{"type": "rect", "xref": "x", "yref": "paper", "x0": d["start"], "x1": d["end"] or d["start"],
                          "y0": 0, "y1": 1, "fillcolor": "rgba(230,150,40,0.25)", "line": {"width": 0}}
                         for d in w["downtime"] if d["start"]]
        return Figure("forecast_vs_actual", lay["title"]["text"], data, lay, {
            "well": w["well"], "verdict": w["verdict"], "gap_sm3d": w.get("gap_sm3d"), "below_since": w.get("below_since"),
            "fit": w.get("fit"), "downtime": w["downtime"], "since": out["since"], "until": out["until"],
            "source": out["source"], "note": out["note"]}, [w["id"]] + [p["record"] for p in pts][-3:])
    ranked = [w for w in out["wells"] if w.get("gap_sm3d") is not None]
    ranked = sorted(ranked, key=lambda w: w["gap_sm3d"])[:15]
    if not ranked:
        raise ChartError("no well has a forecast and a test in the window")
    data = [{"type": "bar", "orientation": "h", "name": "latest gap to forecast", "x": [w["gap_sm3d"] for w in ranked],
             "y": [w["well"] for w in ranked], "meta": {"entity": [w["id"] for w in ranked]},
             "marker": {"color": ["rgba(210,60,50,0.85)" if w["verdict"] == "below_forecast" else "rgba(140,140,140,0.6)"
                                  for w in ranked]},
             "hovertemplate": "%{y}: %{x} Sm3/d against its forecast<extra></extra>"}]
    lay = layout(f"Wells furthest under their forecast, {out['since']} to {out['until']}",
                 "latest test less forecast (Sm3/d)", None)
    lay["yaxis"] = {"autorange": "reversed"}
    return Figure("forecast_vs_actual", lay["title"]["text"], data, lay, {
        "field": out["field"], "not_fitted": len(out["not_fitted"]), "since": out["since"], "until": out["until"],
        "source": out["source"], "note": out["note"]}, [w["id"] for w in ranked])


def intervention_gains(conn, since: str | None = None, until: str | None = None, days: int = 730) -> Figure:
    """Each well intervention's oil gain net of the decline before it (P7.5a, #340), with its 90 % range; the ones that
    can't be said are listed in the summary as unknown, not drawn as zero."""
    from ..analysis import interventions as iv

    out = iv.gains(conn, since, until, None, days)
    said = [i for i in out["interventions"] if i["verdict"] != "unknown"]
    if not said:
        raise ChartError("no intervention in that window has tests before and after it")
    said.sort(key=lambda i: i["start"])
    names = [f"{i['well']} {i['start'][:10]}" for i in said]
    colour = {"gained": "rgba(40,150,90,0.85)", "lost": "rgba(210,60,50,0.85)", "no_clear_change": "rgba(140,140,140,0.7)"}
    err = [(i["oil_range_sm3d"][1] - i["oil_expected_sm3d"]) if i["oil_range_sm3d"] else 0 for i in said]
    data = [{"type": "bar", "name": "oil gain net of decline", "x": names, "y": [i["gain_sm3d"] for i in said],
             "meta": {"entity": [i["well_id"] for i in said]},
             "error_y": {"type": "data", "array": [rnd(e, 1) for e in err], "visible": True},
             "marker": {"color": [colour[i["verdict"]] for i in said]},
             "text": [i["reason"] or "" for i in said],
             "hovertemplate": "%{x}: %{y} Sm3/d against its decline<br>%{text}<extra></extra>"}]
    lay = layout(f"Oil gain by well intervention, net of decline, {out['since']} to {out['until']}", None,
                 "oil gain (Sm3/d)")
    return Figure("intervention_gains", lay["title"]["text"], data, lay, {
        "summary": out["summary"], "unknown": [{k: i[k] for k in ("well", "start", "why")} for i in out["interventions"]
                                               if i["verdict"] == "unknown"],
        "since": out["since"], "until": out["until"], "source": out["source"], "note": out["note"]},
        [i["id"] for i in said][:10])



def gor_trend(conn, well: str) -> Figure:
    """One well's GOR over its tests (#339), with the level of its first and latest tests and the water cut step if
    one was found. A climb is a pattern for the engineer, not a mechanism."""
    from ..analysis import well_diagnostics as wd

    d = wd.diagnose(conn, well)
    if d["verdict"] == "too_little_data":
        raise ChartError(d["reason"])
    pts = [p for p in d["points"] if p["gor"] is not None]
    if not pts:
        raise ChartError("no GOR on that well's tests")
    x = [p["at"] for p in pts]
    data = [{"type": "scatter", "mode": "lines+markers", "name": "GOR (Sm3/Sm3)", "x": x, "y": [p["gor"] for p in pts]}]
    if d["gor_climb"]:
        c, n = d["gor_climb"], d["thresholds"]["tests_for_a_gor_level"]
        data += [{"type": "scatter", "mode": "lines", "name": f"first {n} tests' median", "x": [x[0], x[n - 1]],
                  "y": [c["first_gor"]] * 2, "line": {"dash": "dot"}},
                 {"type": "scatter", "mode": "lines", "name": f"latest {n} tests' median", "x": [x[-n], x[-1]],
                  "y": [c["latest_gor"]] * 2, "line": {"dash": "dot", "color": "rgba(210,60,50,0.9)"}}]
    lay = layout(f"{d['well']}: GOR over its well tests", None, "GOR (Sm3/Sm3)")
    return Figure("gor_trend", lay["title"]["text"], data, lay, {
        "verdict": d["verdict"], "gor_climb": d["gor_climb"], "water_cut_step": d["water_cut_step"],
        "tests": d["tests"], "thresholds": d["thresholds"], "note": d["note"]}, [p["record"] for p in pts][-10:])


def field_allocation_factor(conn, since: str | None = None, until: str | None = None, days: int = 365) -> Figure:
    """Allocated oil over the wells' tested offer each month (#338), with and without each well's downtime taken out,
    the 0.90 to 1.10 band, and the median age of the tests used."""
    from ..analysis import well_test_factor

    out = well_test_factor.trend(conn, since, until, days)
    months = [m for m in out["months"] if m["factor"]]
    if not months:
        raise ChartError("no month with both a daily report and a live well test in that window")
    x = [m["month"] for m in months]
    lo, hi = out["summary"]["band"]
    data = [{"type": "scatter", "mode": "lines+markers", "name": "allocated / tested (shut-in wells out)", "x": x,
             "y": [m["factor"] for m in months], "line": {"color": "rgba(90,120,200,0.95)"}},
            {"type": "scatter", "mode": "lines", "name": "ignoring downtime", "x": x,
             "y": [m["factor_ignoring_downtime"] for m in months], "line": {"dash": "dot", "color": "rgba(90,120,200,0.5)"}},
            {"type": "scatter", "mode": "lines", "name": f"band {lo} to {hi}", "x": x + x[::-1],
             "y": [lo] * len(x) + [hi] * len(x), "fill": "toself", "fillcolor": "rgba(40,150,90,0.08)",
             "line": {"width": 0}, "hoverinfo": "skip"},
            {"type": "bar", "name": "median test age (days)", "x": x, "y": [m["median_test_age_days"] for m in months],
             "yaxis": "y2", "marker": {"color": "rgba(150,150,150,0.35)"}}]
    lay = layout(f"Allocated oil against well tests, {out['since']} to {out['until']}", None, "factor")
    lay["yaxis2"] = {"title": {"text": "test age (days)"}, "overlaying": "y", "side": "right", "showgrid": False}
    return Figure("field_allocation_factor", lay["title"]["text"], data, lay, {
        "summary": out["summary"], "verdict": out["verdict"], "flags": out["flags"], "since": out["since"],
        "until": out["until"], "source": out["source"], "note": out["note"]}, [])


def gas_lift_heading(conn, well: str, days: int = 3, until: str | None = None) -> Figure:
    """One gas lifted well's casing pressure and wellhead pressure (left) and lift gas (right) over the window (#341):
    a heading well's three cycle together."""
    from ..analysis import gas_lift_stability as gls

    try:
        d = gls.well(conn, well, days, until)
    except calcs.CalcError as e:
        raise ChartError(str(e)) from e
    names = {"casing_pressure": "casing pressure", "wellhead_pressure": "wellhead pressure", "gas_lift_rate": "lift gas"}
    data = []
    for role, s in d["series"].items():
        uom = d["points"][role]["uom"] or ""
        data.append({"type": "scatter", "mode": "lines", "name": f"{names[role]} ({uom})", "x": [t for t, _ in s],
                     "y": [rnd(v) for _, v in s], **({"yaxis": "y2"} if role == "gas_lift_rate" else {})})
    lay = layout(f"{d['well']}: casing, wellhead and lift gas, last {days} days ({d['verdict']})", None, "pressure")
    lay["yaxis2"] = {"title": {"text": "lift gas"}, "overlaying": "y", "side": "right", "showgrid": False}
    return Figure("gas_lift_heading", lay["title"]["text"], data, lay, {
        k: d[k] for k in ("verdict", "period_min", "cycling_together", "casing_swing_bar", "casing_swing_pct", "r",
                          "window")} | {"signals": d["signals"]}, [p["id"] for p in d["points"].values()])


def gas_lift_screen(conn, days: int = 3, until: str | None = None, limit: int = 15) -> Figure:
    """The gas lifted wells ranked by their casing pressure swing over the window (#341), heading wells marked."""
    from ..analysis import gas_lift_stability as gls

    out = gls.screen(conn, days, until)
    wells = sorted((w for w in out["wells"] if w.get("casing_swing_pct") is not None),
                   key=lambda w: (w["verdict"] != "heading", w["verdict"] != "unstable", -w["casing_swing_pct"]))[:limit]
    if not wells:
        raise ChartError("no gas lifted well with casing pressure readings in that window")
    tone = {"heading": "rgba(210,60,50,0.85)", "unstable": "rgba(230,150,40,0.85)"}
    data = [{"type": "bar", "orientation": "h", "name": "casing swing (% of level)",
             "y": [w["well"] for w in wells][::-1], "x": [w["casing_swing_pct"] for w in wells][::-1],
             "marker": {"color": [tone.get(w["verdict"], "rgba(150,150,150,0.6)") for w in wells][::-1]},
             "text": [w["verdict"] for w in wells][::-1]}]
    lay = layout(f"Gas lifted wells by casing pressure swing, last {days} days", "swing (% of level)", None)
    return Figure("gas_lift_screen", lay["title"]["text"], data, lay, {
        "summary": out["summary"], "thresholds": out["thresholds"], "window": out["window"], "note": out["note"],
        "heading": [w["well"] for w in out["wells"] if w["verdict"] == "heading"]}, [w["well_id"] for w in wells][:10])


def voidage_replacement(conn, since: str | None = None, until: str | None = None, days: int = 730) -> Figure:
    """Water injected against the reservoir volume produced each month (#365), as bars in reservoir m3, with the
    month's voidage replacement ratio and its running total on the right. A window with no injection has no chart."""
    from ..analysis import water_injection as wi

    out = wi.voidage(conn, since, until, days)
    if out["verdict"] in ("no_injection", "no_daily_report", "unknown"):
        raise ChartError(out["note"])
    months = out["months"]
    x = [m["month"] for m in months]
    data = [{"type": "bar", "name": "produced (reservoir m3)", "x": x, "y": [m["voidage_rm3"] for m in months],
             "marker": {"color": "rgba(150,150,150,0.45)"}},
            {"type": "bar", "name": "injected (reservoir m3)", "x": x, "y": [m["injected_rm3"] for m in months],
             "marker": {"color": "rgba(60,130,200,0.8)"}},
            {"type": "scatter", "mode": "lines+markers", "name": "ratio", "x": x, "y": [m["vrr"] for m in months],
             "yaxis": "y2", "line": {"color": "rgba(210,90,40,0.95)"}},
            {"type": "scatter", "mode": "lines", "name": "running ratio", "x": x,
             "y": [m["cumulative_vrr"] for m in months], "yaxis": "y2", "line": {"dash": "dot", "color": "rgba(210,90,40,0.6)"}},
            {"type": "scatter", "mode": "lines", "name": "ratio of 1", "x": [x[0], x[-1]], "y": [1, 1], "yaxis": "y2",
             "line": {"width": 1, "color": "rgba(40,150,90,0.6)"}, "hoverinfo": "skip"}]
    lay = layout(f"Voidage replacement, {out['since']} to {out['until']} ({out['verdict'].replace('_', ' ')})", None,
                 "reservoir m3")
    lay["barmode"] = "group"
    lay["yaxis2"] = {"title": {"text": "ratio"}, "overlaying": "y", "side": "right", "showgrid": False, "rangemode": "tozero"}
    return Figure("voidage_replacement", lay["title"]["text"], data, lay, {
        k: out[k] for k in ("summary", "verdict", "since", "until", "pvt", "thresholds", "source", "note")}, [])


def hall_plot(conn, well: str | None = None, since: str | None = None, until: str | None = None,
              days: int = 730) -> Figure:
    """Each injector's Hall plot (#365), or one injector's: cumulative wellhead pressure x days on against cumulative
    water. A line that bends up is harder to inject into; one that bends down, easier."""
    from ..analysis import water_injection as wi

    out = wi.hall(conn, since, until, days, well)
    if not out["injectors"]:
        raise ChartError(f"no water injection recorded for {well}" if well else out["note"])
    tone = {"rising": "rgba(210,60,50,0.9)", "falling": "rgba(230,150,40,0.9)"}
    data = [{"type": "scatter", "mode": "lines+markers", "name": f"{i['well']} ({i['verdict'].replace('_', ' ')})",
             "x": [p["cumulative_water_sm3"] for p in i["points"]], "y": [p["cumulative_bar_days"] for p in i["points"]],
             "text": [p["month"] for p in i["points"]], **({"line": {"color": tone[i["verdict"]]}} if i["verdict"] in tone else {})}
            for i in out["injectors"]]
    lay = layout(f"Hall plot{' ' + out['injectors'][0]['well'] if well else ''}, {out['since']} to {out['until']}",
                 "cumulative water injected (Sm3)", "cumulative wellhead pressure x days (bar d)")
    return Figure("hall_plot", lay["title"]["text"], data, lay, {
        "verdict": out["verdict"], "since": out["since"], "until": out["until"], "thresholds": out["thresholds"],
        "source": out["source"], "note": out["note"],
        "injectors": [{k: i[k] for k in ("well", "months", "early_slope", "late_slope", "slope_ratio", "verdict", "reads")}
                      for i in out["injectors"]]}, [])


def gas_balance(conn, since: str | None = None, until: str | None = None, days: int = 365) -> Figure:
    """The daily gas balance (#369): where the gas handled went (export, lift gas, flare, fuel, vent) stacked against
    the gas handled, and the closure gap below it with the open band, so the gap is never hidden."""
    from ..analysis import gas_balance as gb

    out = gb.daily(conn, since, until, days)
    rows = [r for r in out["days"] if r["gap_pct"] is not None]
    if not rows:
        raise ChartError(out["note"] if out["verdict"] == "no_daily_report" else "no day with gas handled in that window")
    x = [r["date"] for r in rows]
    top, bottom = stacked(2, 0.1)
    data = [{"type": "bar", "name": f"{name} (kSm3/d)", "x": x, "y": [r[name] for r in rows], "yaxis": "y"}
            for _, name in gb.OUT if name not in out["missing_terms"]]
    data += [{"type": "scatter", "mode": "lines", "name": "gas handled (kSm3/d)", "x": x,
              "y": [r["gas_in_ksm3"] for r in rows], "yaxis": "y", "line": {"color": "rgba(30,30,30,0.85)"}},
             {"type": "bar", "name": "gap (% of gas handled)", "x": x, "y": [r["gap_pct"] for r in rows], "yaxis": "y2",
              "xaxis": "x2", "marker": {"color": ["rgba(210,60,50,0.85)" if r["open"] else "rgba(150,150,150,0.6)"
                                                  for r in rows]}},
             {"type": "scatter", "mode": "lines", "name": f"open past ±{out['threshold_pct']:g} %",
              "x": x + x[::-1], "y": [out["threshold_pct"]] * len(x) + [-out["threshold_pct"]] * len(x),
              "fill": "toself", "fillcolor": "rgba(40,150,90,0.08)", "line": {"width": 0}, "yaxis": "y2", "xaxis": "x2",
              "hoverinfo": "skip"}]
    lay = layout(f"Gas balance, {out['since']} to {out['until']} ({out['verdict'].replace('_', ' ')})")
    lay["barmode"] = "stack"
    lay["yaxis"] = {"domain": top, "title": {"text": "kSm3/d"}}
    lay["yaxis2"] = {"domain": bottom, "anchor": "x2", "title": {"text": "gap %"}}
    lay["xaxis2"] = {"anchor": "y2", "matches": "x"}
    return Figure("gas_balance", lay["title"]["text"], data, lay, {
        "summary": out["summary"], "verdict": out["verdict"], "episodes": out["episodes"],
        "missing_terms": out["missing_terms"], "since": out["since"], "until": out["until"], "source": out["source"],
        "note": out["note"]}, [])


def productivity_index(conn, well: str | None = None) -> Figure:
    """Each surveyed well's productivity index over its flowing surveys (#364), or one well's; fallen wells marked."""
    from ..analysis import reservoir_pressure as rp

    out = rp.productivity(conn, well)
    wells = [w for w in out["wells"] if any(p["pi"] for p in w["surveys"])]
    if not wells:
        raise ChartError(f"no flowing survey paired with a static one for {well}" if well else out["note"])
    data = [{"type": "scatter", "mode": "lines+markers", "name": f"{w['well']} ({w['verdict'].replace('_', ' ')})",
             "x": [p["date"] for p in w["surveys"] if p["pi"]], "y": [p["pi"] for p in w["surveys"] if p["pi"]],
             **({"line": {"color": "rgba(210,60,50,0.9)", "width": 3}} if w["verdict"] == "fallen" else {})}
            for w in wells]
    lay = layout(f"Productivity index{' ' + wells[0]['well'] if well else ''}", None, out["unit"])
    return Figure("productivity_index", lay["title"]["text"], data, lay, {
        "verdict": out["verdict"], "threshold": out["threshold"], "assumptions": out["assumptions"],
        "source": out["source"], "note": out["note"],
        "wells": [{k: w[k] for k in ("well", "first_pi", "latest_pi", "ratio", "verdict")} for w in wells]}, [])


def material_balance(conn, since: str | None = None, until: str | None = None, days: int = 730) -> Figure:
    """Static survey pressures against the reservoir volume taken out net of injection (#364), each well's surveys
    joined, with the fitted slope drawn through the field's average."""
    from collections import defaultdict

    from ..analysis import reservoir_pressure as rp

    out = rp.material_balance(conn, since, until, days)
    if out["verdict"] != "fitted":
        raise ChartError(out["note"])
    by = defaultdict(list)
    for s in out["surveys"]:
        by[s["well"]].append(s)
    data = [{"type": "scatter", "mode": "lines+markers", "name": w, "x": [s["cumulative_net_voidage_rm3"] / 1e6 for s in ss],
             "y": [s["static_barg"] for s in ss], "text": [s["date"] for s in ss], "line": {"width": 1},
             "marker": {"size": 6}} for w, ss in sorted(by.items())]
    xs = [s["cumulative_net_voidage_rm3"] / 1e6 for s in out["surveys"]]
    mx, my = sum(xs) / len(xs), sum(s["static_barg"] for s in out["surveys"]) / len(xs)
    k = out["bar_per_million_rm3"]
    data.append({"type": "scatter", "mode": "lines", "name": f"fit: {k} bar per million rm3 (r² {out['r2']})",
                 "x": [min(xs), max(xs)], "y": [my - k * (min(xs) - mx), my - k * (max(xs) - mx)],
                 "line": {"color": "rgba(30,30,30,0.9)", "width": 3, "dash": "dash"}})
    lay = layout(f"Material balance, {out['since']} to {out['until']}", "reservoir volume taken out net of injection "
                 "(million rm3)", "static pressure (barg)")
    return Figure("material_balance", lay["title"]["text"], data, lay, {
        k2: out[k2] for k2 in ("verdict", "bar_per_million_rm3", "compressible_volume_rm3_per_bar", "r2", "wells",
                               "decline_bar_per_year", "assumptions", "pvt", "since", "until", "source", "note")}, [])
