"""Calcs that return numbers, not just figures (#105 A).

Each check is a plain function used three ways: by a `plot_*` chart for people, by the `calc` MCP tool
for agents, and (from #96) by the detectors. It returns its numbers in one shape, so a work item (#97)
or a report (#98) can keep it as evidence and cite it:

    {value, expected, deviation, deviation_pct, flag, basis, sources, ...detail}

Each takes an optional `until` (ISO time) to work as of then, from what was known before it, so the
#96 detectors and the replay (#103) can run them on history.

`value` is what the data says now, `expected` what it should be (a fit, a curve, a rating), `flag` a
short code when they disagree enough to act on (None otherwise), `basis` how expected was worked out,
and `sources` the record keys and entity ids behind it.

The fits (Arps decline, the gas lift curve, Weibull, Crow-AMSAA, wall loss, the pump and choke against
their OEM curves) live here, and the charts draw from them.
"""

import itertools
import math
from datetime import date, datetime, timedelta

YEAR = 365.25


class CalcError(ValueError):
    """The data can't support the calc (too few tests, no curve, ...); the message says why."""


def result(value, expected, flag: str | None, basis: str, sources: list[str], **detail) -> dict:
    dev = value - expected if value is not None and expected is not None else None
    return {"value": value, "expected": expected, "deviation": _r(dev),
            "deviation_pct": round(dev / expected * 100, 1) if dev is not None and expected else None,
            "flag": flag, "basis": basis, "sources": sources, **detail}


def _r(x: float | None, n: int = 3) -> float | None:
    return None if x is None else round(x, n)


def _label(conn, fid: str) -> str:
    r = conn.execute("select label from entity_label where fuzer_id = %s", (fid,)).fetchone()
    return r["label"] if r else fid


# ---- wells: decline -------------------------------------------------------------------------------

def well_tests(conn, well: str, until: str | None = None) -> list[dict]:
    """A well's tests in time order: {at, record, oil_rate, water_cut, lift_gas_rate, ...}."""
    rows = conn.execute(
        "select record, at, quantity, value from measurement where fuzer_id = %s and method = 'well test' "
        "and (%s::timestamp is null or at < %s::timestamp) order by at", (well, until, until)).fetchall()
    by: dict[str, dict] = {}
    for r in rows:
        by.setdefault(r["record"], {"at": r["at"], "record": r["record"]})[r["quantity"]] = r["value"]
    tests = sorted(by.values(), key=lambda t: t["at"])
    if len(tests) < 4:
        raise CalcError("fewer than four well tests on that well")
    return tests


def linreg(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
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
    a, b, sse = linreg(ts, lq)
    fits = {"exponential": {"model": "exponential", "qi": math.exp(a), "D": -b, "b": 0.0, "sse": sse}}
    best = None
    for bb in ([1.0] if model == "harmonic" else [x / 20 for x in range(1, 21)]):
        A, B, _ = linreg(ts, [q ** -bb for q in qs])
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
        raise CalcError(f"no {model} fit: the rate isn't declining that way")
    var = sum((y - sum(lq) / len(lq)) ** 2 for y in lq)
    return fit | {"r2": 1 - fit["sse"] / var if var else None}


def rate(fit: dict, t: float) -> float:
    if fit["b"] == 0:
        return fit["qi"] * math.exp(-fit["D"] * t)
    return fit["qi"] / (1 + fit["b"] * fit["D"] * t) ** (1 / fit["b"])


def decline_fit(conn, well: str, model: str = "auto", until: str | None = None) -> dict:
    """An Arps fit to a well's oil rate after its last step change: {tests, cuts, seg, t0, fit}."""
    tests = [t for t in well_tests(conn, well, until) if t.get("oil_rate", 0) > 0]
    qs = [t["oil_rate"] for t in tests]
    cuts = breaks(qs)
    start = cuts[-1] if cuts and len(qs) - cuts[-1] >= 4 else 0
    seg = tests[start:]
    t0 = seg[0]["at"]
    fit = arps([(t["at"] - t0).days / YEAR for t in seg], [t["oil_rate"] for t in seg], model)
    return {"tests": tests, "cuts": cuts, "start": start, "seg": seg, "t0": t0, "fit": fit}


def decline(conn, well: str, model: str = "auto", tolerance: float = 0.15, until: str | None = None) -> dict:
    """The latest well test against the decline fitted through the tests before it. Flags `step_down` /
    `step_up` when the latest test is a step change, else `below_decline` / `above_decline` beyond
    `tolerance`."""
    d = decline_fit(conn, well, model, until)
    fit, seg, t0 = d["fit"], d["seg"], d["t0"]
    last = seg[-1]
    expected = rate(fit, (last["at"] - t0).days / YEAR)
    n = len(d["tests"])
    step = d["cuts"] and d["cuts"][-1] == n - 1
    if step:
        expected = d["tests"][-2]["oil_rate"]
        flag = "step_down" if last["oil_rate"] < expected else "step_up"
    else:
        flag = ("below_decline" if last["oil_rate"] < (1 - tolerance) * expected else
                "above_decline" if last["oil_rate"] > (1 + tolerance) * expected else None)
    basis = (f"step from the test before ({d['tests'][-2]['at']:%Y-%m-%d})" if step else
             f"Arps {fit['model']} fit through {len(seg)} well tests since {t0:%Y-%m-%d}")
    return result(_r(last["oil_rate"], 1), _r(expected, 1), flag, basis, [well, last["record"]],
                  unit="Sm3/d", at=last["at"].isoformat()[:10], model=fit["model"], qi=_r(fit["qi"], 1), b=_r(fit["b"], 2),
                  decline_per_year=_r(fit["D"], 3), effective_decline_pct_per_year=_r((1 - rate(fit, 1) / rate(fit, 0)) * 100, 1),
                  r2_log=_r(fit["r2"]), step_changes=[d["tests"][i]["at"].isoformat()[:10] for i in d["cuts"]])


# ---- wells: gas lift ------------------------------------------------------------------------------

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


def gas_lift_fit(conn, well: str, breakeven: float = 0.5, until: str | None = None) -> dict:
    """A gas lifted well's performance curve from its tests, decline taken out, with tests far below the
    curve (a valve failed open) left out: {tests, pts (gas, oil, years), keep, fit, a, c, k (the curve
    today), g_now, q_now, marginal, g_opt, gain}."""
    tests = [t for t in well_tests(conn, well, until) if t.get("lift_gas_rate") and t.get("oil_rate")]
    if len(tests) < 6:
        raise CalcError("fewer than six gas lifted tests on that well")
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
        raise CalcError("no gas lift response in the tests (oil doesn't rise with lift gas)")
    t_now = pts[-1][2]
    a, c, k = fit["a"] + fit["d"] * t_now, fit["c"], fit["k"]
    g_now, q_now = pts[-1][0], pts[-1][1]
    g_opt = k * math.log(c / (k * breakeven)) if c / (k * breakeven) > 1 else 0.0
    gain = (a + c * (1 - math.exp(-g_opt / k))) - (a + c * (1 - math.exp(-g_now / k)))
    return {"tests": tests, "pts": pts, "keep": keep, "fit": fit, "t_now": t_now, "a": a, "c": c, "k": k,
            "g_now": g_now, "q_now": q_now, "marginal": c / k * math.exp(-g_now / k), "g_opt": g_opt, "gain": gain}


def gas_lift(conn, well: str, breakeven: float = 0.5, min_gain: float = 1.0, until: str | None = None) -> dict:
    """The well's lift gas against the optimum on its curve (where one more kSm3/d of gas gains
    `breakeven` Sm3/d of oil). Flags `below_curve` when the latest test was left out of the fit (more gas,
    less oil: a valve failed open), else `more_gas` / `less_gas` when moving to the optimum is worth at
    least `min_gain` Sm3/d of oil or saves gas for less than that."""
    g = gas_lift_fit(conn, well, breakeven, until)
    last = len(g["pts"]) - 1
    move = g["g_opt"] - g["g_now"]
    flag = ("below_curve" if last not in g["keep"] else
            "more_gas" if move > 0 and g["gain"] >= min_gain else
            "less_gas" if move < 0 and abs(move) >= 0.2 * g["g_now"] and -g["gain"] < min_gain else None)
    return result(_r(g["g_now"], 1), _r(g["g_opt"], 1), flag,
                  f"curve q = a + c(1 - exp(-g/k)) fitted to {len(g['keep'])} of {len(g['pts'])} tests with decline "
                  f"taken out; optimum at {breakeven} Sm3 oil per kSm3 gas", [well, g["tests"][-1]["record"]],
                  unit="kSm3/d lift gas", at=g["tests"][-1]["at"].isoformat()[:10], oil_sm3d=_r(g["q_now"], 1),
                  marginal_oil_per_extra_gas=_r(g["marginal"], 2), oil_change_at_optimum_sm3d=_r(g["gain"], 1),
                  tests_left_out=len(g["pts"]) - len(g["keep"]))


# ---- equipment against its OEM curve --------------------------------------------------------------

def window_of(conn, days: int, until: str | None = None) -> tuple[datetime, datetime]:
    """The `days` up to `until`, else to the end of the data's today (operations.as_of)."""
    from . import operations

    end = datetime.fromisoformat(until) if until else datetime.fromisoformat(operations.as_of(conn)) + timedelta(days=1)
    return end - timedelta(days=days), end


def read(points: dict[str, dict], start: datetime, end: datetime, step: int, src=None) -> dict[str, dict[str, float]]:
    """Values of several points on one time grid: {name: {time: value}}."""
    from . import timeseries

    src = src or timeseries.provider()
    return {k: dict(src.series({**p, "upsets": []}, start, end, step)) for k, p in points.items()}


def curve_for(conn, model: str | None, what: str) -> dict:
    """An equipment model's OEM curve, or a CalcError naming the datasheet waiting to be read."""
    from . import curves

    if model is None:
        raise CalcError(f"no equipment model known for that {what}")
    c = curves.for_model(conn, model)
    if c:
        return c
    label = conn.execute("select label from entity_label where fuzer_id = %s", (model,)).fetchone()
    wait = curves.waiting(conn, model)
    why = "; ".join(f"datasheet {w['label']} " + ("waiting for review" if w["status"] == "open" else
                                                  "not read yet" if w["status"] is None else w["status"]) for w in wait)
    raise CalcError(f"no OEM curve for {label['label'] if label else model}" + (f" ({why})" if why else
                                                                                  " and no datasheet for it"))


def pump_operation(conn, fid: str, days: int = 365, rho: float = 850.0, src=None, until: str | None = None) -> dict:
    """A pump's operating points from PI (head from discharge minus suction pressure) against its OEM
    head curve: {tag, label, curve, start, end, ops [(time, flow, head)], by_month [{month,
    head_vs_curve_pct}]}."""
    from . import curves, timeseries

    tag = fid
    if conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()["kind"] == "equipment":
        r = conn.execute("select dst from edge where rel = 'installed_at' and src = %s and valid_to is null", (fid,)).fetchone()
        tag = r["dst"] if r else fid
    c = curve_for(conn, curves.model_of(conn, tag), "pump")
    if c["curve"] != "pump" or "head" not in c["series"]:
        raise CalcError("that model's curve isn't a pump curve")
    pts = timeseries._unit_points(conn, tag, 20)
    need = {k: next((p for p in pts if (p["description"] or "").endswith(end)), None)
            for k, end in (("flow", " Flow"), ("suction", "Suction Pressure"), ("discharge", "Discharge Pressure"))}
    if None in need.values():
        raise CalcError("the pump needs flow, suction and discharge pressure points in PI")
    start, end = window_of(conn, days, until)
    v = read(need, start, end, 360, src)
    times = sorted(set(v["flow"]) & set(v["suction"]) & set(v["discharge"]))
    bep = c["ratings"].get("bep", [None, None])
    mcsf = (c["ratings"].get("mcsf") or [0])[0] or 0.3 * (bep[0] or 100)
    ops = [(t, v["flow"][t], (v["discharge"][t] - v["suction"][t]) * 1e5 / (rho * 9.81)) for t in times
           if v["flow"][t] > 0.5 * mcsf]
    if len(ops) < 10:
        raise CalcError("the pump hardly ran in that window")
    months: dict[str, list[float]] = {}
    for t, q, h in ops:
        hc = curves.at(c["series"]["head"], q)
        if hc:
            months.setdefault(t[:7], []).append((h - hc) / hc * 100)
    by_month = [{"month": m, "head_vs_curve_pct": round(sorted(x)[len(x) // 2], 1)} for m, x in sorted(months.items())]
    return {"tag": tag, "label": _label(conn, tag), "curve": c, "start": start, "end": end, "ops": ops, "bep": bep,
            "by_month": by_month, "need": [p["id"] for p in need.values()]}


def pump_curve(conn, fid: str, days: int = 365, tolerance_pct: float = 5.0, until: str | None = None,
               recent_days: int = 30, min_readings: int = 40) -> dict:
    """A pump's head against its OEM curve at the same flow, as the median over its last `recent_days` of
    running: at least `min_readings` (about ten days at 6-hour readings), so a standby pump's starts and
    stops don't count. Flags `head_loss` (wear rings, impeller) at `tolerance_pct` or more below the curve."""
    from . import curves

    p = pump_operation(conn, fid, days, until=until)
    head = p["curve"]["series"]["head"]
    cut = (datetime.fromisoformat(p["ops"][-1][0]) - timedelta(days=recent_days)).isoformat()
    recent = sorted((h - hc) / hc * 100 for t, q, h in p["ops"] if t >= cut and (hc := curves.at(head, q)))
    if len(recent) < min_readings:
        raise CalcError(f"fewer than {min_readings} running readings in the pump's last {recent_days} days")
    value = round(recent[len(recent) // 2], 1)
    return result(value, 0.0, "head_loss" if value <= -tolerance_pct else None,
                  f"head from discharge minus suction pressure at SG 0.85 against the {p['curve']['source']} head curve "
                  f"at the same flow, median of {len(recent)} readings in the last {recent_days} days running",
                  [p["tag"], p["curve"]["model_id"], *p["need"]], unit="% of curve head", at=p["ops"][-1][0][:10],
                  by_month=p["by_month"][-12:], worst_month=min(p["by_month"], key=lambda d: d["head_vs_curve_pct"]))


SG = 0.9  # produced liquid through a choke


def choke_operation(conn, fid: str, days: int = 730, sg: float = SG, src=None, until: str | None = None) -> dict:
    """A well's production choke against its characteristic: effective Cv at each well test (oil wells:
    liquid rate from the test, pressure drop from wellhead to the jacket header in PI) or from PI rates
    (gas wells), against the opening. {well, tag, wl, tl, curve, start, end, installs, rows [(time,
    opening, Cv)], ratio [(time, opening, Cv, Cv / curve)], note, drift (latest ratio before any
    replacement), reading}."""
    from . import curves

    kind = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()["kind"]
    if kind == "well":
        well = fid
        tag = conn.execute("select e.src from edge e join entity_label l on l.fuzer_id = e.src "
                           "where e.rel = 'part_of' and e.dst = %s and l.label like '%%-HCV-%%' limit 1", (fid,)).fetchone()
        if tag is None:
            raise CalcError("no production choke recorded on that well")
        tag = tag["src"]
    else:
        tag = fid
        w = conn.execute("select e.dst from edge e join entity x on x.fuzer_id = e.dst and x.kind = 'well' "
                         "where e.rel = 'part_of' and e.src = %s", (fid,)).fetchone()
        if w is None:
            raise CalcError("that isn't a well's production choke")
        well = w["dst"]
    c = curve_for(conn, curves.model_of(conn, tag), "choke")
    if "cv" not in c["series"]:
        raise CalcError("that model's curve isn't a choke characteristic")
    wl, tl = _label(conn, well), _label(conn, tag)
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
        raise CalcError("the well needs wellhead pressure and choke position points in PI")
    start, end = window_of(conn, days, until)
    installs = [r["valid_from"].isoformat() for r in conn.execute(
        "select valid_from from edge where rel = 'installed_at' and dst = %s and valid_from is not null "
        "order by valid_from", (tag,)) if r["valid_from"] > start.date()]
    cv_curve = c["series"]["cv"]
    rows = []  # (time, opening, effective Cv)
    note = None
    if by_end["qgas"]:  # gas: rate against upstream pressure (critical flow), scaled on the early points
        v = read({k: by_end[k] for k in ("whp", "opening", "qgas")}, start, end, 360, src)
        typical = sorted(v["qgas"].values())[len(v["qgas"]) // 2]
        raw = [(t, v["opening"][t], v["qgas"][t] / (v["whp"][t] + 1.013)) for t in sorted(v["qgas"])
               if v["qgas"][t] > 0.3 * typical and v["opening"][t] > 2 and t in v["whp"]]  # flowing, not ramping
        if len(raw) < 10:
            raise CalcError("the gas well hardly flowed in that window")
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
        v = read(need, start, end, 60, src)
        for t in sorted(tests.values(), key=lambda t: t["at"]):
            when = (t["at"].replace(hour=12, minute=0)).isoformat(timespec="minutes")
            if "oil_rate" not in t or when not in v["whp"]:
                continue
            liquid = t["oil_rate"] / (1 - min(t.get("water_cut", 0), 98) / 100)
            dp = v["whp"][when] - (v["back"][when] if back else 11.0)
            if dp > 0.2 and v["opening"][when] > 2:
                rows.append((when, v["opening"][when], liquid / 24 * 4.403 / math.sqrt(dp * 14.5038 / sg)))
        if len(rows) < 4:
            raise CalcError("fewer than four well tests with pressures in that window")
        if not back:
            note = "no production header transmitter: downstream pressure taken as 11 barg"
    ratio = [(t, o, cv, cv / ref) for t, o, cv in rows if (ref := curves.at(cv_curve, o))]
    cut = installs[-1] if installs else None
    before = [r[3] for r in ratio if not cut or r[0] < cut]
    tail = before[-max(3, len(before) // 6):]
    drift = sorted(tail)[len(tail) // 2] if before else 1.0
    reading = ("eroded: passing more than the characteristic" if drift > 1.15 else
               "restricted: passing less than the characteristic" if drift < 0.85 else "on its characteristic")
    return {"well": well, "tag": tag, "wl": wl, "tl": tl, "curve": c, "start": start, "end": end, "installs": installs,
            "rows": rows, "ratio": ratio, "note": note, "drift": drift, "reading": reading}


def choke(conn, fid: str, days: int = 730, until: str | None = None) -> dict:
    """A production choke's effective Cv against its characteristic at the same opening: the median of
    the readings since it was replaced (three or more), else of the latest readings before. Flags
    `eroded` above 1.15, `restricted` below 0.85."""
    c = choke_operation(conn, fid, days, until=until)
    new = c["installs"][-1] if c["installs"] else None
    since_new = sorted(r[3] for r in c["ratio"] if new and r[0] >= new)
    value = since_new[len(since_new) // 2] if len(since_new) >= 3 else c["drift"]
    basis = (f"median of the {len(since_new)} readings since the choke was replaced on {new}" if len(since_new) >= 3 else
             "median of the latest readings" + (f" before the replacement on {new}" if new else ""))
    return result(round(value, 2), 1.0, "eroded" if value > 1.15 else "restricted" if value < 0.85 else None,
                  f"effective Cv against the {c['curve']['source']} characteristic at the same opening, {basis}",
                  [c["well"], c["tag"], c["curve"]["model_id"]], unit="Cv / characteristic", replaced=c["installs"],
                  points=len(c["ratio"]), before_replacement=round(c["drift"], 2) if new else None,
                  **({"note": c["note"]} if c["note"] else {}))


# ---- reliability ----------------------------------------------------------------------------------

def failures(conn, fid: str, until: str | None = None) -> tuple[str, dict[str, list[date]]]:
    """Failure dates per unit for a tag / equipment, or for every unit in an equipment class."""
    e = conn.execute("select e.kind, l.label from entity e left join entity_label l using (fuzer_id) "
                     "where fuzer_id = %s", (fid,)).fetchone()
    if e is None:
        raise CalcError("unknown entity")
    units = [fid]
    if e["kind"] == "equipment_class":
        units = [r["src"] for r in conn.execute(
            "select distinct src from edge where rel = 'classified_as' and dst = %s", (fid,))]
    rows = conn.execute(
        """
        select c.dst as unit, g.value #>> '{}' as created from edge c
        join entity n on n.fuzer_id = c.src and n.kind = 'notification'
        join golden g on g.fuzer_id = c.src and g.field = 'created'
        where c.rel = 'concerns' and c.dst = any(%s) and (%s::text is null or g.value #>> '{}' < %s)
        """, (units, until, until)).fetchall()
    out: dict[str, list[date]] = {}
    for r in rows:
        out.setdefault(r["unit"], []).append(date.fromisoformat(r["created"][:10]))
    return e["label"] or fid, {u: sorted(d) for u, d in out.items()}


def weibull(tbf: list[float]) -> dict:
    """Weibull fit to times between failures by median ranks: {n, ys (linearised F), beta, eta, median,
    reading}; beta < 1 early failures, about 1 random, > 1 wear-out."""
    tbf = sorted(tbf)
    if len(tbf) < 3:
        raise CalcError("fewer than three times between failures (need repeat failures on the same unit)")
    n = len(tbf)
    F = [(i + 1 - 0.3) / (n + 0.4) for i in range(n)]
    xs = [math.log(t) for t in tbf]
    ys = [math.log(-math.log(1 - f)) for f in F]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if not sxx:
        raise CalcError("every time between failures is the same: nothing to fit")
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    eta = math.exp(mx - my / beta)
    return {"tbf": tbf, "n": n, "ys": ys, "beta": beta, "eta": eta, "median": eta * math.log(2) ** (1 / beta),
            "reading": "early-life failures (installation, quality)" if beta < 0.85 else
            "random failures (time-based PM won't help)" if beta < 1.2 else "wear-out (a PM interval below eta helps)"}


def crow_amsaa(dates: list[date]) -> dict:
    """Crow-AMSAA (power law) fit of cumulative failures N = lambda t^beta on log-log: {dates, ts, n,
    beta, lam, reading}; beta > 1 failures coming faster."""
    dates = sorted(dates)
    if len(dates) < 4:
        raise CalcError("fewer than four failures")
    start = dates[0]
    ts = [max(1, (d - start).days) for d in dates]
    n = list(range(1, len(ts) + 1))
    pts = [(math.log(t), math.log(k)) for t, k in zip(ts, n) if t > 1]
    if len({x for x, _ in pts}) < 2:
        raise CalcError("the failures fall on too few days to fit a trend")
    mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    beta = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x, _ in pts)
    return {"dates": dates, "ts": ts, "n": n, "beta": beta, "lam": math.exp(my - beta * mx),
            "reading": "failures coming faster" if beta > 1.15 else "failures slowing" if beta < 0.85 else "steady rate"}


def times_between(per: dict[str, list[date]]) -> list[int]:
    return sorted((b - a).days for ds in per.values() for a, b in itertools.pairwise(ds) if (b - a).days > 0)


def reliability(conn, fid: str, method: str = "weibull", until: str | None = None) -> dict:
    """Weibull beta of times between failures (expected 1: random) or the Crow-AMSAA growth beta (expected 1:
    a steady rate) from failure notifications on a unit or an equipment class. Flags `wear_out` /
    `early_life` (Weibull) or `failures_accelerating` (Crow-AMSAA)."""
    label, per = failures(conn, fid, until)
    if method == "crow_amsaa":
        f = crow_amsaa([d for ds in per.values() for d in ds])
        return result(_r(f["beta"], 2), 1.0, "failures_accelerating" if f["beta"] > 1.15 else None,
                      f"Crow-AMSAA N = lambda t^beta fitted to {len(f['dates'])} failure notifications on {label}",
                      [fid], unit="beta", reading=f["reading"], failures=len(f["dates"]), lam=_r(f["lam"], 4),
                      first=f["dates"][0].isoformat(), last=f["dates"][-1].isoformat())
    f = weibull(times_between(per))
    return result(_r(f["beta"], 2), 1.0, "wear_out" if f["beta"] >= 1.2 else "early_life" if f["beta"] < 0.85 else None,
                  f"Weibull fitted by median ranks to {f['n']} times between failure notifications on {label}",
                  [fid], unit="beta", reading=f["reading"], eta_days=_r(f["eta"], 0), median_days=_r(f["median"], 0))


# ---- inspection -----------------------------------------------------------------------------------

def remaining_life(latest_mm: float, per_year: float | None, min_required_mm: float | None) -> float | None:
    """Years until a CML losing `per_year` (negative) reaches the minimum required thickness."""
    if not min_required_mm or not per_year or per_year >= 0:
        return None
    return (latest_mm - min_required_mm) / -per_year


def wall_loss(conn, fid: str, min_required_mm: float | None = None, warn_years: float = 5.0,
              until: str | None = None) -> dict:
    """An item's fastest wall loss across its CMLs (mm/year, least squares per CML) against the median
    of its CMLs, with remaining life per CML when the minimum required thickness is given. Flags
    `retire_soon` when a CML reaches it within `warn_years`, else `localised` when the fastest CML loses
    more than twice the median."""
    from . import measurements as m

    series = m.for_entity(conn, fid, "wall_thickness", until=until)["series"]
    if not series:
        raise CalcError("no wall thickness readings on that item")
    cmls = []
    for s in series:
        life = remaining_life(s["latest"]["value"], s["per_year"], min_required_mm)
        cmls.append({"cml": s["location"], "latest_mm": _r(s["latest"]["value"], 2), "latest_at": s["latest"]["at"][:10],
                     "loss_mm_per_year": _r(-(s["per_year"] or 0), 3), "record": s["latest"]["record"],
                     **({"remaining_years": _r(life, 1)} if life is not None else {})})
    cmls.sort(key=lambda c: -c["loss_mm_per_year"])
    losses = sorted(c["loss_mm_per_year"] for c in cmls)
    median = losses[len(losses) // 2]
    shortest = min((c["remaining_years"] for c in cmls if "remaining_years" in c), default=None)
    flag = ("retire_soon" if shortest is not None and shortest < warn_years else
            "localised" if len(cmls) >= 3 and cmls[0]["loss_mm_per_year"] > 2 * max(median, 0.01) else None)
    return result(cmls[0]["loss_mm_per_year"], median, flag,
                  f"least-squares wall loss per CML over {len(cmls)} CMLs; expected is their median"
                  + (f"; remaining life to {min_required_mm} mm" if min_required_mm else ""),
                  [fid, *[c["record"] for c in cmls[:3]]], unit="mm/year", fastest_cml=cmls[0]["cml"],
                  shortest_remaining_years=shortest, cmls=cmls[:10])


# Every check, the function behind it, and who uses it: a chart for people, the `calc` MCP tool for
# agents, a #96 detector. Unused checks can be dropped.
CHECKS = {
    "decline": {"fn": decline, "ref": "well", "used_by": {"person": "plot_decline", "agent": "calc"}},
    "gas_lift": {"fn": gas_lift, "ref": "well", "used_by": {"person": "plot_gas_lift_curve", "agent": "calc"}},
    "pump_curve": {"fn": pump_curve, "ref": "pump tag or equipment",
                   "used_by": {"person": "plot_pump_curve", "agent": "calc"}},
    "choke": {"fn": choke, "ref": "well or its choke tag", "used_by": {"person": "plot_choke", "agent": "calc"}},
    "reliability": {"fn": reliability, "ref": "tag, equipment or equipment class",
                    "used_by": {"person": "plot_reliability", "agent": "calc"}},
    "wall_loss": {"fn": wall_loss, "ref": "vessel, piping or pipeline",
                  "used_by": {"person": "plot_wall_thickness", "agent": "calc"}},
}


def run(conn, check: str, fid: str, **options) -> dict:
    """One check by name, with only the options it takes."""
    import inspect

    if check not in CHECKS:
        raise CalcError(f"unknown check {check!r}; one of {', '.join(CHECKS)}")
    fn = CHECKS[check]["fn"]
    takes = inspect.signature(fn).parameters
    return {"check": check, "of": {"id": fid, "label": _label(conn, fid)},
            **fn(conn, fid, **{k: v for k, v in options.items() if v is not None and k in takes})}
