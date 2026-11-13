"""Process model fits and suggested tuning ranges (#359): what a step test says about how a loop's process responds,
and the PI settings that response suggests, with the method, the data window and how well the model fits.

    fit_step(t, op, pv, step_at)       a first order plus dead time model fitted to one step's response
    simc(k, tau, theta)                SIMC PI settings from a model, as a range from tight to smooth
    tuning(conn, test)                 a step test on record: its high-rate response, the fit, the suggested range
                                       against the settings in force, and a before and after comparison where the
                                       settings changed after the test

The model: the value moves by `gain` x the output step, starting `dead time` after the step and closing on its new
level with `time constant`, fitted by least squares to the window read every second or so (a bounded high-rate
read), flat before the step and answering after it. Fit quality is R-squared over that window; under R2_MIN the fit isn't used for a suggestion. The settings
come from Skogestad's SIMC rules (2003) for a PI controller on a first order plus dead time process: Kc =
tau / (K (tau_c + theta)) and Ti = min(tau, 4 (tau_c + theta)), with the closed loop time constant tau_c from theta
(tight: fast, less margin) to 3 theta (smooth: slower, more robust). K is the gain in % of the value's span per %
of output, so Kc is in the DCS's own % per %.

Agents only recommend ranges. A change stays with the control-system owner under management of change, and nothing
is ever written to a controller: this module only reads.
"""

import numpy as np

from .calcs import CalcError

R2_MIN = 0.9  # a fit explaining less of the response than this isn't used for a suggestion
TAU_C = (1.0, 3.0)  # the SIMC closed loop time constant, in dead times: tight to smooth
COMPARE_DAYS = 7  # days either side of a setting change for the before and after comparison


def fopdt(t: np.ndarray, k: float, tau: float, theta: float, step: float) -> np.ndarray:
    """A first order plus dead time response to an output step `step` at t = 0 (t in seconds)."""
    return k * step * (1 - np.exp(-np.maximum(t - theta, 0) / tau))


def fit_step(t, op, pv, step_at: float) -> dict:
    """Fit gain, time constant and dead time to the response of `pv` to the output step at `step_at` (all times in
    seconds). The step's size is read from the output either side of it; the value's base from before it."""
    from scipy.optimize import curve_fit

    t, op, pv = (np.asarray(x, float) for x in (t, op, pv))
    before, after = t < step_at, t >= step_at
    if before.sum() < 5 or after.sum() < 20:
        raise CalcError("too few readings either side of the step to fit a model")
    step = float(np.median(op[after]) - np.median(op[before]))
    if abs(step) < 0.5:
        raise CalcError("the output barely moved, so there is no step to fit")
    tt, y = t - step_at, pv - float(np.mean(pv[before]))  # fitted over the whole window: flat, then the answer
    ta, ya = tt[after], y[after]
    tail = ya[-max(5, len(ya) // 10):]
    k0 = float(np.mean(tail)) / step
    if k0 == 0:
        raise CalcError("the value didn't answer the step")
    frac = ya / (k0 * step)
    theta0 = float(ta[np.argmax(frac > 0.1)]) if (frac > 0.1).any() else 0.0
    t63 = float(ta[np.argmax(frac > 0.632)]) if (frac > 0.632).any() else float(ta[-1])
    tau0 = max(t63 - theta0, float(np.median(np.diff(ta))))
    # The dead time makes the model kink, so it is searched over the samples, with gain and time constant fitted at each
    dt = float(np.median(np.diff(ta)))
    best = None
    for th in np.arange(0.0, min(float(ta[-1]) / 2, 4 * theta0 + 10 * dt) + dt / 2, dt):
        try:
            (k1, tau1), _ = curve_fit(lambda x, k, tau, th=th: fopdt(x, k, tau, th, step), tt, y,
                                      p0=[k0, max(tau0 - th + theta0, dt)], bounds=([-np.inf, 1e-3], [np.inf, np.inf]))
        except RuntimeError:
            continue
        sse = float(np.sum((y - fopdt(tt, k1, tau1, th, step)) ** 2))
        if best is None or sse < best[0]:
            best = (sse, k1, tau1, float(th))
    if best is None:
        raise CalcError("the model didn't converge on this response")
    _, k, tau, theta = best
    resid = y - fopdt(tt, k, tau, theta, step)
    r2 = 1 - float(np.sum(resid ** 2)) / float(np.sum((y - y.mean()) ** 2))
    tt = ta
    return {"gain": round(float(k), 4), "tau_s": round(float(tau), 2), "dead_s": round(float(theta), 2),
            "r2": round(r2, 3), "step": round(step, 3), "settled": bool(tt[-1] >= theta + 4 * tau),
            "residual_sd": round(float(np.std(resid)), 4), "readings": len(tt)}


def simc(k: float, tau: float, theta: float, sample_s: float = 1.0) -> dict:
    """SIMC PI settings for a first order plus dead time process (gain `k` in % of span per % of output, times in
    seconds), from tight (tau_c = theta) to smooth (tau_c = 3 theta). A dead time under half a sample is taken as half
    a sample, since a digital controller adds at least that."""
    theta_eff = max(theta, sample_s / 2)
    out = {}
    for name, m in zip(("tight", "smooth"), TAU_C):
        tc = m * theta_eff
        out[name] = {"tau_c_s": round(tc, 2), "kc": round(tau / (abs(k) * (tc + theta_eff)), 3),
                     "ti_s": round(min(tau, 4 * (tc + theta_eff)), 2)}
    kcs, tis = [out["tight"]["kc"], out["smooth"]["kc"]], [out["tight"]["ti_s"], out["smooth"]["ti_s"]]
    return {"kc": [min(kcs), max(kcs)], "ti_s": [min(tis), max(tis)], "td_s": 0.0, **out,
            "action": "reverse" if k > 0 else "direct", "dead_time_used_s": round(theta_eff, 2)}


def _seconds(times: list[str]) -> np.ndarray:
    from datetime import datetime

    t0 = datetime.fromisoformat(times[0])
    return np.array([(datetime.fromisoformat(x) - t0).total_seconds() for x in times])


def _variability(conn, ref: str, since: str, until: str) -> dict | None:
    """How tightly the loop held its setpoint in auto over a window: the spread and mean absolute size of its error."""
    from datetime import datetime

    from . import control_loops as cl

    days = max(1, round((datetime.fromisoformat(until) - datetime.fromisoformat(since)).total_seconds() / 86400))
    s = cl.series(conn, ref, days, until, 5)
    err = [p - q for p, q, m in zip(s["pv"], s["sp"], s["mode"]) if m >= 0.5]
    if len(err) < 50:
        return None
    return {"since": s["since"], "until": s["until"], "readings": len(err), "error_sd": round(float(np.std(err)), 4),
            "error_mean_abs": round(float(np.mean(np.abs(err))), 4)}


def _compare(conn, lp: dict, change: dict) -> dict:
    """Before and after a setting change: compare_periods on the loop's process value (a move in its median) and the
    setpoint error's spread, which is where a tuning change shows."""
    from datetime import datetime, timedelta

    from . import cases

    at = datetime.fromisoformat(change["effective_from"])
    a = ((at - timedelta(days=COMPARE_DAYS)).isoformat(timespec="minutes"), at.isoformat(timespec="minutes"))
    b = ((at + timedelta(hours=1)).isoformat(timespec="minutes"),
         (at + timedelta(days=COMPARE_DAYS, hours=1)).isoformat(timespec="minutes"))
    pv = lp["_series"]["pv"]
    try:
        periods = cases.compare_periods(conn, pv["id"], a, b)
        median = next((p for p in periods["points"] if p["point_id"] == pv["id"]), None)
    except CalcError as e:
        periods, median = {"error": str(e)}, None
    before, after = _variability(conn, lp["id"], *a), _variability(conn, lp["id"], *b)
    change_pct = (round((after["error_sd"] - before["error_sd"]) / before["error_sd"] * 100, 1)
                  if before and after and before["error_sd"] else None)
    return {"change": change, "windows": {"before": list(a), "after": list(b)},
            "process_value_median": median and {k: median[k] for k in ("before_median", "after_median", "change",
                                                                     "p_value", "significant")},
            "compare_periods_verdict": periods.get("verdict") if isinstance(periods, dict) else None,
            "setpoint_error": {"before": before, "after": after, "sd_change_pct": change_pct},
            "reads": ("compare_periods tests a move in the value's median; a tuning change shows in how far the value "
                      "strays from setpoint, so the setpoint error's spread is set beside it.")}


def tuning(conn, test: str, src=None) -> dict:
    """One step test on record (by its id, or the latest on a loop): its response read at the test's sample rate, the
    fitted model and its quality, the SIMC range it suggests against the settings in force at the test, and a before
    and after comparison where the settings changed after it."""
    from datetime import datetime

    from . import control_loops as cl

    tests = cl.step_tests(conn)
    st = next((x for x in tests if x["test"].upper() == test.upper() or x["id"] == test), None)
    if st is None:
        mine = [x for x in tests if x["loop"].upper() == test.upper()]
        if not mine:
            raise CalcError("no step test on record by that id or on that loop")
        st = mine[-1]
    lp = cl.loop(conn, st["loop_id"])
    step_s = max(1, int(st["sample_s"] or 1))
    hr = cl.high_rate(conn, st["loop_id"], st["start"], st["end"], step_s, src)
    if len(hr["times"]) < 30:
        raise CalcError(f"the historian holds too few high-rate values over {st['test']}'s window to fit a model")
    t = _seconds(hr["times"])
    step_at = (datetime.fromisoformat(st["step_at"]) - datetime.fromisoformat(hr["times"][0])).total_seconds()
    f = fit_step(t, hr["op"], hr["pv"], step_at)
    span = (lp["range"][1] - lp["range"][0]) if lp.get("range") else None
    k_norm = f["gain"] * 100 / span if span else None
    settings = cl.settings(conn, st["loop_id"])
    at_test = cl.settings(conn, st["loop_id"], at=st["start"])["in_force"]
    later = [h for h in settings["history"] if h["effective_from"] > st["start"][:16]]
    usable = f["r2"] >= R2_MIN and f["settled"] and k_norm is not None
    why_not = (None if usable else "the fit explains too little of the response" if f["r2"] < R2_MIN
               else "the test ended before the value settled" if not f["settled"]
               else "the loop list gives no span for the value, so the gain can't be put in the controller's % per %")
    return {
        "test": st["test"], "loop": lp["loop"], "loop_id": lp["id"], "holds": lp["holds"], "unit": lp["unit"],
        "range": lp.get("range"), "window": {"since": st["start"], "until": st["end"], "step_at": st["step_at"],
                                             "sample_s": step_s, "readings": len(hr["times"])},
        "model": f | {"gain_pct_per_pct": round(k_norm, 4) if k_norm is not None else None,
                      "gain_unit": f"{lp['unit']} per % output"},
        "fit_quality": {"r2": f["r2"], "r2_min": R2_MIN, "settled": f["settled"], "residual_sd": f["residual_sd"]},
        "suggested": (simc(k_norm, f["tau_s"], f["dead_s"], step_s) if usable else None),
        "why_no_suggestion": why_not,
        "in_force_at_test": at_test, "changed_since": later,
        "before_after": _compare(conn, lp, later[0]) if later else None,
        "synthetic": hr["synthetic"],
        "method": (f"First order plus dead time fitted by least squares to the step response; PI settings by SIMC "
                   f"(Skogestad 2003), tau_c from {TAU_C[0]:g} to {TAU_C[1]:g} dead times."),
        "assumptions": ["The process is self-regulating and roughly linear over the step's range.",
                        "The load didn't move during the test (open loop, controller in manual).",
                        "Kc is in % of output per % of the value's span, as the DCS holds it; Ti in seconds.",
                        "A PI controller: no derivative is suggested."],
        "boundary": ("A range to inform the control-system owner, not a change: any change is theirs under "
                     "management of change, and nothing is written to a controller."),
    }
