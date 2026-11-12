"""Control loop oscillation (P4.2b-2, #354): whether a measurement cycles regularly, as a control loop does when it
hunts or limit-cycles on a sticking valve, with the cycle's period and amplitude.

- `regularity(intervals)`: the regularity of an oscillation from the intervals between successive zero crossings of
  its autocorrelation, after Thornhill, Huang and Zhang (2003): each interval is half a period, and r = mean period
  / (3 x the periods' standard deviation). A regular oscillation has r over 1; noise, however large, does not.
  The autocorrelation must also come back to `ACF_PEAK` one period on, so slowly wandering noise whose few
  crossings happen to be even is not called a cycle.
- `cycling(values, step)`: one series' oscillation: r, period and amplitude, or None when it has too few readings.
- `oscillation(conn, fid, days, until)`: each point on a unit (or one point) over the window, the most regular one
  first; flagged `oscillating` when it cycles regularly for at least `MIN_CYCLES` cycles.

A cycling loop is a finding for the instrument technician and the control-system owner: the playbook never
proposes a tuning or setpoint change.
"""

import numpy as np

from .calcs import CalcError, _label, read, result, window_of

REGULAR = 1.0  # r over this is a regular oscillation (Thornhill, Huang and Zhang 2003)
MIN_CYCLES = 4  # the autocorrelation must cross zero at least twice this many times
ACF_PEAK = 0.4  # and come back at least this high one period on: slow noise wanders back to about 0.3 at most
R_CAP = 99.0  # a perfectly even cycle has r without bound; reported as this
STEP_MINUTES = 5
MIN_READINGS = 288  # a day of 5-minute readings


def regularity(intervals) -> float:
    """r from the intervals between successive autocorrelation zero crossings (each half a period)."""
    periods = 2 * np.asarray(intervals, float)
    if len(periods) < 2:
        return 0.0
    sd = float(np.std(periods))
    return float("inf") if sd == 0 else float(np.mean(periods) / (3 * sd))


def cycling(values, step: float) -> dict | None:
    """r, period (minutes) and amplitude of one series on a regular grid of `step` minutes; None when too short."""
    x = np.asarray([v for v in values if v is not None and np.isfinite(v)], float)
    if len(x) < MIN_READINGS:
        return None
    t = np.arange(len(x))
    x = x - np.polyval(np.polyfit(t, x, 1), t)  # around its own trend
    if float(np.std(x)) == 0:
        return {"r": 0.0, "period_min": None, "amplitude": 0.0, "acf_peak": None, "crossings": 0}
    n = len(x)
    f = np.fft.rfft(x, 2 * n)
    acf = np.fft.irfft(f * np.conj(f))[: n // 2]
    acf = acf / acf[0]
    crossings = np.where(np.diff(np.signbit(acf)))[0][: 2 * MIN_CYCLES + 3]
    amplitude = round(float(np.percentile(x, 95) - np.percentile(x, 5)) / 2, 3)
    if len(crossings) < 2 * MIN_CYCLES:
        return {"r": 0.0, "period_min": None, "amplitude": amplitude, "acf_peak": None, "crossings": len(crossings)}
    gaps = np.diff(crossings)
    peak = float(acf[crossings[1]: crossings[2] + 1].max())  # one period on
    return {"r": round(min(regularity(gaps), R_CAP), 2), "period_min": round(float(2 * np.mean(gaps) * step), 1),
            "amplitude": amplitude, "acf_peak": round(peak, 2), "crossings": len(crossings)}


def _cycles(c: dict) -> bool:
    return c["r"] > REGULAR and (c["acf_peak"] or 0) >= ACF_PEAK


def oscillation(conn, fid: str, days: int = 3, until: str | None = None, src=None) -> dict:
    """Whether the measurements on a unit (or one point) cycle regularly over the window; the most regular first."""
    from .signals import _points

    pts = [p for p in _points(conn, fid, 20)]
    if not pts:
        raise CalcError("no historian points on that item")
    start, end = window_of(conn, days, until)
    vals = read({p["id"]: p for p in pts}, start, end, STEP_MINUTES, src)
    found = []
    for p in pts:
        c = cycling([v for _, v in sorted(vals[p["id"]].items())], STEP_MINUTES)
        if c is not None:
            found.append({"point": p["point"], "point_id": p["id"], "description": p.get("description"),
                          "uom": p.get("uom"), **c})
    if not found:
        raise CalcError(f"fewer than {MIN_READINGS} readings on any point in that window")
    found.sort(key=lambda c: (not _cycles(c), -c["r"]))
    top = found[0]
    flag = "oscillating" if _cycles(top) else None
    return result(top["r"], REGULAR, flag,
                  f"regularity r of each measurement's autocorrelation zero crossings over {days} days of "
                  f"{STEP_MINUTES}-minute readings around its own trend (Thornhill, Huang and Zhang 2003): r over "
                  f"{REGULAR:g} with at least {MIN_CYCLES} cycles, and the autocorrelation back to {ACF_PEAK:g} one period "
                  "on, is a regular oscillation, as a hunting loop or a sticking valve makes; noise is not regular. The most regular measurement is reported, with its "
                  "period and amplitude",
                  [p["id"] for p in pts], unit="r", item=_label(conn, fid), point=top["point"],
                  period_min=top["period_min"], amplitude=top["amplitude"], amplitude_uom=top["uom"],
                  oscillating=[c["point"] for c in found if _cycles(c)], points=found[:10])
