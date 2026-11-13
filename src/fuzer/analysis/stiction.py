"""Valve stiction (#357): whether a cycling control loop's valve is sticking, told from its controller output (OP)
and process value (PV) by the cross-correlation test of Horch (1999). A sticking valve can't be fixed by retuning, so
telling it from an aggressive tuning or a disturbance decides who looks at the loop: the instrument technician at the
valve, or the control-system owner at the controller.

    indicator(op, pv, period)          the test on two aligned series cycling with `period` readings
    stiction(conn, ref, days, until)   each loop (or one) over the window: whether it cycles, and if it does, the test

The test: a loop cycling on a sticking valve has an OP that ramps (a triangle) while the PV jumps (a square) a quarter
cycle later, so the cross-correlation of OP and PV is odd: near zero at lag 0, with peaks of opposite sign either
side. Cycling from an aggressive tuning or an outside disturbance moves the two nearly in or against phase, so the
cross-correlation is even, strongest at lag 0. The share |r(0)| / max |r| within half a period on each side reads
which: under ODD_MAX (and the peaks either side of opposite sign) a suspected sticking valve, over EVEN_MIN tuning
or a disturbance, between the two inconclusive.

Horch's test is stated for loops whose value follows the valve directly (flows, pressures). A level integrates
the flow its valve passes, so on a level loop it is a weaker indication, and says so. Either way it is a suspicion for
the instrument technician to confirm in the field (a valve travel or bump test), never a diagnosis, and never a
tuning or maintenance order. Read-only.
"""

import numpy as np

from . import loops as osc
from .calcs import CalcError
from .control_loops import loop, loops, series

ODD_MAX = 0.3  # |r(0)| under this share of the strongest correlation, with peaks of opposite sign: odd, sticking
EVEN_MIN = 0.7  # over this share: even, tuning or a disturbance
MIN_AUTO_PCT = 90.0  # the test reads a loop in auto: under this share of the window in auto it isn't run


def _ccf(op: np.ndarray, pv: np.ndarray, lag: int) -> np.ndarray:
    """r(k) = corr(op[t], pv[t + k]) for k from -lag to lag."""
    op, pv = (op - op.mean()) / op.std(), (pv - pv.mean()) / pv.std()
    n = len(op)
    return np.array([float(np.mean(op[max(0, -k): n - max(0, k)] * pv[max(0, k): n - max(0, -k)]))
                     for k in range(-lag, lag + 1)])


def _detrend(x) -> np.ndarray:
    x = np.asarray(x, float)
    t = np.arange(len(x))
    return x - np.polyval(np.polyfit(t, x, 1), t)


def indicator(op, pv, period: float) -> dict:
    """Horch's cross-correlation test on aligned OP and PV series cycling every `period` readings."""
    op, pv = _detrend(op), _detrend(pv)
    if len(op) != len(pv) or len(op) < 4 * period or op.std() == 0 or pv.std() == 0:
        return {"verdict": "inconclusive", "why": "too few cycles, or the output or value doesn't move"}
    lag = max(2, round(period / 2))
    r = _ccf(op, pv, lag)
    zero, left, right = r[lag], r[:lag], r[lag + 1:]
    peak = float(np.abs(r).max())
    lo, hi = float(left[np.abs(left).argmax()]), float(right[np.abs(right).argmax()])
    share = abs(float(zero)) / peak if peak else 1.0
    odd = share < ODD_MAX and lo * hi < 0
    verdict = "stiction_suspected" if odd else "tuning_or_disturbance" if share >= EVEN_MIN else "inconclusive"
    return {"verdict": verdict, "r0_share": round(share, 2), "r0": round(float(zero), 2),
            "peak_before": round(lo, 2), "peak_after": round(hi, 2),
            "ccf": [round(float(v), 3) for v in r], "lags": list(range(-lag, lag + 1))}


def _integrating(tag: str) -> bool:
    """A level controller (LIC, LC): its value integrates the flow the valve passes."""
    parts = tag.split("-")
    return len(parts) > 1 and parts[1][:1].upper() == "L"


def _one(conn, lp: dict, days: int, until: str | None, src) -> dict:
    s = series(conn, lp["id"], days, until, osc.STEP_MINUTES, src)
    n = len(s["times"])
    base = {"loop": s["loop"], "id": s["id"], "description": s["description"], "holds": s["holds"],
            "valve": s["final_element"], "unit": s["unit"], "points": s["points"], "readings": n}
    if n < osc.MIN_READINGS:
        return base | {"verdict": "not_tested", "why": f"{n} readings, fewer than {osc.MIN_READINGS}"}
    auto = [i for i in range(n) if s["mode"][i] >= 0.5]
    auto_pct = round(100 * len(auto) / n, 1)
    if auto_pct < MIN_AUTO_PCT:
        return base | {"verdict": "not_tested", "auto_pct": auto_pct,
                       "why": f"in auto {auto_pct:g}% of the window: the test reads a loop the controller is moving"}
    pv, op = [s["pv"][i] for i in auto], [s["op"][i] for i in auto]
    c = osc.cycling(pv, osc.STEP_MINUTES)
    if not (c and osc._cycles(c)):
        return base | {"verdict": "not_cycling", "auto_pct": auto_pct, "regularity": c["r"] if c else None,
                       "why": "the value doesn't cycle regularly, so there is no cycle to explain"}
    got = indicator(op, pv, c["period_min"] / osc.STEP_MINUTES)
    caveat = ("A level integrates the flow its valve passes, so on a level loop the cross-correlation test is a weaker "
              "indication.") if _integrating(s["loop"]) else None
    return base | {"auto_pct": auto_pct, "period_min": c["period_min"], "amplitude": c["amplitude"],
                   "regularity": c["r"], "op_span": round(max(op) - min(op), 2),
                   "pv_span": round(max(pv) - min(pv), 3), **got, "caveat": caveat}


def stiction(conn, ref: str | None = None, days: int = 3, until: str | None = None, src=None) -> dict:
    """Each loop on the loop list (or the one named) over the `days` up to `until`: whether its value cycles, and when
    it does, whether its output and value read as a sticking valve. Suspected valves first."""
    from .calcs import window_of

    targets = [loop(conn, ref)] if ref else loops(conn)
    out = []
    for lp in targets:
        try:
            out.append(_one(conn, lp, days, until, src))
        except CalcError as e:
            out.append({"loop": lp["loop"], "id": lp["id"], "verdict": "not_tested", "why": str(e)})
    rank = {"stiction_suspected": 0, "inconclusive": 1, "tuning_or_disturbance": 2, "not_cycling": 3, "not_tested": 4}
    out.sort(key=lambda m: (rank[m["verdict"]], m["loop"]))
    suspected = [m for m in out if m["verdict"] == "stiction_suspected"]
    start, end = window_of(conn, days, until)
    from .control_loops import _synthetic

    return {"since": start.isoformat(timespec="minutes"), "until": end.isoformat(timespec="minutes"), "loops": out,
            "verdict": "suspected" if suspected else "none_suspected" if out else "no_loops",
            "summary": {"loops": len(out), "cycling": sum(m["verdict"] not in ("not_cycling", "not_tested") for m in out),
                        "suspected": [m["loop"] for m in suspected],
                        "valves": [m["valve"] for m in suspected if m.get("valve")]},
            "synthetic": _synthetic() if src is None else getattr(src, "name", None) == "synthetic",
            "thresholds": {"odd_max": ODD_MAX, "even_min": EVEN_MIN, "min_auto_pct": MIN_AUTO_PCT,
                           "regular": osc.REGULAR},
            "method": "Horch (1999) cross-correlation of controller output and process value",
            "source": "DCS loop list and each loop's PV, OP and mode points",
            "note": (f"Run on loops in auto at least {MIN_AUTO_PCT:g}% of the window whose value cycles regularly. A "
                     f"cross-correlation near zero at lag 0 (under {ODD_MAX:g} of its peak) with peaks of opposite sign "
                     f"either side reads as a sticking valve; over {EVEN_MIN:g} as tuning or a disturbance. A suspicion "
                     "for the instrument technician to confirm in the field, not a diagnosis, a tuning change or a "
                     "maintenance order.")}
