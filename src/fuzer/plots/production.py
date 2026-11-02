"""Production charts from well tests (the measurement table): decline with an Arps fit, the water
diagnostic (WOR and its derivative), a gas lifted well's performance curve, and lift gas allocation
across a header."""

import math
from datetime import datetime, timedelta

from .. import calcs
from ..calcs import YEAR, linreg, rate
from . import ChartError, Figure, layout, rnd, stacked, thin


def _label(conn, fid: str) -> str:
    r = conn.execute("select label from entity_label where fuzer_id = %s", (fid,)).fetchone()
    return r["label"] if r else fid


def decline(conn, well: str, model: str = "auto", forecast_months: int = 12) -> Figure:
    """Oil rate from well tests (log scale) with an Arps fit after the last step change, and a forecast."""
    d = calcs.decline_fit(conn, well, model)
    tests, cuts, start, seg, t0, fit = d["tests"], d["cuts"], d["start"], d["seg"], d["t0"], d["fit"]
    qs = [t["oil_rate"] for t in tests]
    last = seg[-1]["at"]
    horizon = [last + timedelta(days=30.4 * m) for m in range(0, forecast_months + 1)]
    fit_x = [t0 + timedelta(days=d) for d in range(0, (horizon[-1] - t0).days + 1, 15)]
    q_fit = [rate(fit, (x - t0).days / YEAR) for x in fit_x]
    cum = sum(rate(fit, (last - t0).days / YEAR + d / YEAR) for d in range(int(forecast_months * 30.4)))
    name = _label(conn, well)
    data = [
        {"type": "scatter", "mode": "markers", "name": "tests used in the fit", "x": [t["at"].isoformat() for t in seg],
         "y": [t["oil_rate"] for t in seg], "customdata": [t["record"] for t in seg], "marker": {"size": 7}},
        {"type": "scatter", "mode": "lines", "name": f"Arps {fit['model']}", "line": {"dash": "dash"},
         "x": [x.isoformat()[:10] for x in fit_x if x <= last], "y": [q for x, q in zip(fit_x, q_fit) if x <= last]},
        {"type": "scatter", "mode": "lines", "name": f"forecast {forecast_months} months", "line": {"dash": "dot"},
         "x": [x.isoformat()[:10] for x in fit_x if x >= last], "y": [q for x, q in zip(fit_x, q_fit) if x >= last]},
    ]
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
        "forecast": {"months": forecast_months, "rate_at_end_sm3d": rnd(rate(fit, (horizon[-1] - t0).days / YEAR), 1),
                     "oil_sm3": round(cum)}}, [well])


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
    lay["xaxis2"] = {"anchor": "y2", "title": {"text": "cumulative oil since first test (Sm3)"}}
    lay["height"] = 620
    jumps = [(x[i], round(wor[i] - wor[i - 1], 3)) for i in range(1, len(wor))]
    third = max(2, len(wor) // 3)
    early = linreg(yrs[:third], [math.log(w) for w in wor[:third]])[1] if min(wor[:third]) > 0 else None
    late = linreg(yrs[-third:], [math.log(w) for w in wor[-third:]])[1] if min(wor[-third:]) > 0 else None
    return Figure("water_diagnostic", lay["title"]["text"], data, lay, {
        "well": name, "tests": len(tests), "cumulative_oil_sm3": round(run),
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
    from .. import operations as ops

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
    from .. import hydraulics

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
    from .. import hydraulics

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
    from .. import calcs, hydraulics

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
    from .. import hydraulics

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
    from .. import hydraulics

    d = hydraulics.dp_drift(conn, well, days, until)
    pts, times, whp, back = _pressure_series(conn, well, days, until)
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


def debottleneck(conn, scope: str = "facility", at: str | None = None, pack=None) -> Figure:
    """Debottleneck options ranked by oil gained, coloured by kind."""
    from .. import hydraulics
    from ..pack import load_pack

    out = hydraulics.debottleneck_options(conn, pack or load_pack(), scope, at, limit=15)
    opts = out["options"]
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
