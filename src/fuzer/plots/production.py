"""Production charts from well tests (the measurement table): decline with an Arps fit, the water
diagnostic (WOR and its derivative), a gas lifted well's performance curve, and lift gas allocation
across a header."""

import math
from datetime import datetime, timedelta

from . import ChartError, Figure, layout, rnd, stacked

YEAR = 365.25


def _tests(conn, well: str) -> list[dict]:
    """A well's tests in time order: {at, oil_rate, water_cut, lift_gas_rate, ...}."""
    rows = conn.execute(
        "select record, at, quantity, value from measurement where fuzer_id = %s and method = 'well test' order by at",
        (well,)).fetchall()
    by: dict[str, dict] = {}
    for r in rows:
        by.setdefault(r["record"], {"at": r["at"], "record": r["record"]})[r["quantity"]] = r["value"]
    tests = sorted(by.values(), key=lambda t: t["at"])
    if len(tests) < 4:
        raise ChartError("fewer than four well tests on that well")
    return tests


def _label(conn, fid: str) -> str:
    r = conn.execute("select label from entity_label where fuzer_id = %s", (fid,)).fetchone()
    return r["label"] if r else fid


def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Intercept, slope and residual sum of squares."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    a = my - b * mx
    return a, b, sum((y - a - b * x) ** 2 for x, y in zip(xs, ys))


def breaks(qs: list[float], threshold: float = 0.25) -> list[int]:
    """Indexes where the rate steps (a valve failure, a breakthrough, a repair): a test-to-test change in
    log rate well beyond the usual scatter."""
    jumps = [math.log(qs[i] / qs[i - 1]) for i in range(1, len(qs)) if qs[i] > 0 and qs[i - 1] > 0]
    if len(jumps) < 3:
        return []
    mad = sorted(abs(j) for j in jumps)[len(jumps) // 2]
    limit = max(threshold, 5 * mad)
    return [i for i in range(1, len(qs)) if qs[i] > 0 and qs[i - 1] > 0 and abs(math.log(qs[i] / qs[i - 1])) > limit]


def arps(ts: list[float], qs: list[float], model: str = "auto") -> dict:
    """Arps decline fitted on log rate: exponential, harmonic (b = 1) or hyperbolic (best b in 0.05-1).
    `auto` keeps exponential unless a hyperbolic fit is clearly better. t in years."""
    lq = [math.log(q) for q in qs]
    a, b, sse = _linreg(ts, lq)
    fits = {"exponential": {"model": "exponential", "qi": math.exp(a), "D": -b, "b": 0.0, "sse": sse}}
    best = None
    for bb in ([1.0] if model == "harmonic" else [x / 20 for x in range(1, 21)]):
        A, B, _ = _linreg(ts, [q ** -bb for q in qs])
        if A <= 0 or B <= 0:
            continue
        pred = [(A + B * t) ** (-1 / bb) for t in ts]
        s = sum((math.log(p) - y) ** 2 for p, y in zip(pred, lq))
        if best is None or s < best["sse"]:
            best = {"model": "harmonic" if bb == 1.0 else "hyperbolic", "qi": A ** (-1 / bb), "D": B / (A * bb), "b": bb,
                    "sse": s}
    if best:
        fits[best["model"]] = best
    if model in fits:
        fit = fits[model]
    elif model == "auto":
        fit = best if best and best["sse"] < 0.85 * fits["exponential"]["sse"] else fits["exponential"]
    else:
        raise ChartError(f"no {model} fit: the rate isn't declining that way")
    var = sum((y - sum(lq) / len(lq)) ** 2 for y in lq)
    return fit | {"r2": 1 - fit["sse"] / var if var else None}


def rate(fit: dict, t: float) -> float:
    if fit["b"] == 0:
        return fit["qi"] * math.exp(-fit["D"] * t)
    return fit["qi"] / (1 + fit["b"] * fit["D"] * t) ** (1 / fit["b"])


def decline(conn, well: str, model: str = "auto", forecast_months: int = 12) -> Figure:
    """Oil rate from well tests (log scale) with an Arps fit after the last step change, and a forecast."""
    tests = [t for t in _tests(conn, well) if t.get("oil_rate", 0) > 0]
    qs = [t["oil_rate"] for t in tests]
    cuts = breaks(qs)
    start = cuts[-1] if cuts and len(qs) - cuts[-1] >= 4 else 0
    seg = tests[start:]
    t0 = seg[0]["at"]
    ts = [(t["at"] - t0).days / YEAR for t in seg]
    fit = arps(ts, [t["oil_rate"] for t in seg], model)
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
    tests = [t for t in _tests(conn, well) if "water_cut" in t and "oil_rate" in t]
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
    early = _linreg(yrs[:third], [math.log(w) for w in wor[:third]])[1] if min(wor[:third]) > 0 else None
    late = _linreg(yrs[-third:], [math.log(w) for w in wor[-third:]])[1] if min(wor[-third:]) > 0 else None
    return Figure("water_diagnostic", lay["title"]["text"], data, lay, {
        "well": name, "tests": len(tests), "cumulative_oil_sm3": round(run),
        "water_cut": {"first": rnd(tests[0]["water_cut"], 1), "latest": rnd(tests[-1]["water_cut"], 1)},
        "wor": {"first": rnd(wor[0]), "latest": rnd(wor[-1])},
        "log_wor_slope_per_year": {"early": rnd(early), "late": rnd(late)},
        "derivative_per_year": {"early_mean": rnd(sum(smooth[:third]) / third), "late_mean": rnd(sum(smooth[-third:]) / third)},
        "largest_wor_step": dict(zip(("at", "change"), max(jumps, key=lambda j: j[1]))) if jumps else None}, [well])


def _solve3(m: list[list[float]], v: list[float]) -> list[float] | None:
    """3x3 linear solve (Gaussian elimination)."""
    a = [row[:] + [x] for row, x in zip(m, v)]
    for c in range(3):
        p = max(range(c, 3), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-12:
            return None
        a[c], a[p] = a[p], a[c]
        for r in range(3):
            if r != c:
                f = a[r][c] / a[c][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [a[i][3] / a[i][i] for i in range(3)]


def _glfit(pts: list[tuple[float, float, float]]) -> dict | None:
    """q = a + d t + c (1 - exp(-g / k)): the gas lift response with the decline taken out."""
    best = None
    for k in [x / 2 for x in range(2, 61)]:
        rows = [(1.0, t, 1 - math.exp(-g / k)) for g, _, t in pts]
        ys = [q for _, q, _ in pts]
        m = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]
        v = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(3)]
        sol = _solve3(m, v)
        if not sol or sol[2] <= 0:
            continue
        sse = sum((y - sum(s * x for s, x in zip(sol, r))) ** 2 for r, y in zip(rows, ys))
        if best is None or sse < best["sse"]:
            best = {"a": sol[0], "d": sol[1], "c": sol[2], "k": k, "sse": sse}
    return best


def gas_lift_curve(conn, well: str, breakeven: float = 0.5) -> Figure:
    """A gas lifted well's performance curve: oil against lift gas from its tests (colour = date), with
    the decline taken out, the current operating point, the marginal response there, and the optimum
    where one more kSm3/d of gas gains `breakeven` Sm3/d of oil. Tests far below the curve (a valve
    failed open: more gas, less oil) are left out of the fit and shown apart."""
    tests = [t for t in _tests(conn, well) if t.get("lift_gas_rate") and t.get("oil_rate")]
    if len(tests) < 6:
        raise ChartError("fewer than six gas lifted tests on that well")
    t0 = tests[0]["at"]
    pts = [(t["lift_gas_rate"], t["oil_rate"], (t["at"] - t0).days / YEAR) for t in tests]
    keep = list(range(len(pts)))
    fit = _glfit(pts)
    for _ in range(2):  # drop tests far below the curve, refit
        if not fit:
            break
        res = [pts[i][1] - (fit["a"] + fit["d"] * pts[i][2] + fit["c"] * (1 - math.exp(-pts[i][0] / fit["k"]))) for i in keep]
        sd = (sum(r * r for r in res) / max(1, len(res) - 3)) ** 0.5
        drop = {i for i, r in zip(keep, res) if r < -2.5 * sd}
        if not drop:
            break
        keep = [i for i in keep if i not in drop]
        fit = _glfit([pts[i] for i in keep])
    if not fit:
        raise ChartError("no gas lift response in the tests (oil doesn't rise with lift gas)")
    t_now = pts[-1][2]
    a, c, k = fit["a"] + fit["d"] * t_now, fit["c"], fit["k"]
    gmax = max(p[0] for p in pts) * 1.4
    gs = [gmax * i / 60 for i in range(61)]
    curve = [a + c * (1 - math.exp(-g / k)) for g in gs]
    g_now, q_now = pts[-1][0], pts[-1][1]
    marginal = c / k * math.exp(-g_now / k)
    g_opt = k * math.log(c / (k * breakeven)) if c / (k * breakeven) > 1 else 0.0
    gain = (a + c * (1 - math.exp(-g_opt / k))) - (a + c * (1 - math.exp(-g_now / k)))
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



SG = 0.9  # produced liquid through a choke


def choke(conn, fid: str, days: int = 730, sg: float = SG) -> Figure:
    """A well's production choke against its characteristic: effective Cv at each well test (oil wells:
    liquid rate from the test, pressure drop from wellhead to the jacket header in PI) or from PI rates
    (gas wells), against the opening. Above the curve = more flow than the trim should pass (erosion);
    below = restriction (scale, hydrate, debris). A replacement starts a new series."""
    from .. import curves
    from .maintenance import _curve_or_why, _read, _window_of

    kind = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()["kind"]
    if kind == "well":
        well = fid
        tag = conn.execute("select e.src from edge e join entity_label l on l.fuzer_id = e.src "
                           "where e.rel = 'part_of' and e.dst = %s and l.label like '%%-HCV-%%' limit 1", (fid,)).fetchone()
        if tag is None:
            raise ChartError("no production choke recorded on that well")
        tag = tag["src"]
    else:
        tag = fid
        w = conn.execute("select e.dst from edge e join entity x on x.fuzer_id = e.dst and x.kind = 'well' "
                         "where e.rel = 'part_of' and e.src = %s", (fid,)).fetchone()
        if w is None:
            raise ChartError("that isn't a well's production choke")
        well = w["dst"]
    c = _curve_or_why(conn, curves.model_of(conn, tag), "choke")
    if "cv" not in c["series"]:
        raise ChartError("that model's curve isn't a choke characteristic")
    label = lambda f: conn.execute("select label from entity_label where fuzer_id = %s", (f,)).fetchone()["label"]  # noqa: E731
    wl, tl = label(well), label(tag)
    pts = list(conn.execute(
        """
        select t.dst as id, l.label as point, gd.value #>> '{}' as description, gu.value #>> '{}' as uom
        from edge t join entity_label l on l.fuzer_id = t.dst
        left join golden gd on gd.fuzer_id = t.dst and gd.field = 'description'
        left join golden gu on gu.fuzer_id = t.dst and gu.field = 'uom'
        where t.rel = 'has_timeseries' and t.src = %s
        """, (well,)))
    by_end = {k: next((p for p in pts if (p["description"] or "").endswith(end)), None)
              for k, end in (("whp", "Wellhead Pressure"), ("opening", "Choke Position"), ("qgas", "Gas Rate"))}
    if not by_end["whp"] or not by_end["opening"]:
        raise ChartError("the well needs wellhead pressure and choke position points in PI")
    start, end = _window_of(conn, days)
    installs = [r["valid_from"].isoformat() for r in conn.execute(
        "select valid_from from edge where rel = 'installed_at' and dst = %s and valid_from is not null "
        "order by valid_from", (tag,)) if r["valid_from"] > start.date()]
    cv_curve = c["series"]["cv"]
    rows = []  # (time, opening, effective Cv)
    note = None
    if by_end["qgas"]:  # gas: rate against upstream pressure (critical flow), scaled on the early points
        v = _read({k: by_end[k] for k in ("whp", "opening", "qgas")}, start, end, 360)
        typical = sorted(v["qgas"].values())[len(v["qgas"]) // 2]
        raw = [(t, v["opening"][t], v["qgas"][t] / (v["whp"][t] + 1.013)) for t in sorted(v["qgas"])
               if v["qgas"][t] > 0.3 * typical and v["opening"][t] > 2 and t in v["whp"]]  # flowing, not ramping
        if len(raw) < 10:
            raise ChartError("the gas well hardly flowed in that window")
        early = sorted(x / cv for t, o, x in raw[: max(5, len(raw) // 4)] if (cv := curves.at(cv_curve, o)))
        k = early[len(early) // 2]
        rows = [(t, o, x / k) for t, o, x in raw]
        note = ("gas: flow against upstream pressure assuming critical flow; scaled so the first quarter of the window "
                "sits on the characteristic, so the chart shows change, not absolute Cv")
    else:  # oil: liquid from each well test, pressure drop to the jacket production header
        tests: dict = {}
        for r in conn.execute("select record, at, quantity, value from measurement where fuzer_id = %s and "
                              "quantity in ('oil_rate', 'water_cut') and at >= %s and at < %s", (well, start, end)):
            tests.setdefault(r["record"], {"at": r["at"]})[r["quantity"]] = r["value"]
        area = tl.split("-")[0]
        back = conn.execute("select t.dst as id, l.label as point, null as description, 'barg' as uom from entity_label h "
                            "join edge t on t.src = h.fuzer_id and t.rel = 'has_timeseries' "
                            "join entity_label l on l.fuzer_id = t.dst where h.label = %s", (f"{area}-PT-3002",)).fetchone()
        need = {"whp": by_end["whp"], "opening": by_end["opening"], **({"back": back} if back else {})}
        v = _read(need, start, end, 60)
        for t in sorted(tests.values(), key=lambda t: t["at"]):
            when = (t["at"].replace(hour=12, minute=0)).isoformat(timespec="minutes")
            if "oil_rate" not in t or when not in v["whp"]:
                continue
            liquid = t["oil_rate"] / (1 - min(t.get("water_cut", 0), 98) / 100)
            dp = v["whp"][when] - (v["back"][when] if back else 11.0)
            if dp > 0.2 and v["opening"][when] > 2:
                rows.append((when, v["opening"][when], liquid / 24 * 4.403 / math.sqrt(dp * 14.5038 / sg)))
        if len(rows) < 4:
            raise ChartError("fewer than four well tests with pressures in that window")
        if not back:
            note = "no production header transmitter: downstream pressure taken as 11 barg"
    ratio = [(t, o, cv, cv / ref) for t, o, cv in rows if (ref := curves.at(cv_curve, o))]
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
    before = [r[3] for r in ratio if not cut or r[0] < cut]
    peak = max(ratio, key=lambda r: abs(r[3] - 1))
    drift = sorted(before[-max(3, len(before) // 6):])[len(before[-max(3, len(before) // 6):]) // 2] if before else 1.0
    reading = ("eroded: passing more than the characteristic" if drift > 1.15 else
               "restricted: passing less than the characteristic" if drift < 0.85 else "on its characteristic")
    return Figure("choke", lay["title"]["text"], data, lay, {
        "well": wl, "choke": tl, "curve_source": c["source"], "points": len(ratio),
        "window": [start.date().isoformat(), end.date().isoformat()], "replaced": installs,
        "cv_vs_curve_by_quarter": by_q, "before_replacement_latest": round(drift, 2), "reading": reading,
        "furthest": {"at": peak[0][:10], "opening_pct": round(peak[1], 1), "cv_vs_curve": round(peak[3], 2)},
        **({"note": note} if note else {})}, [well, tag, c["model_id"]])
