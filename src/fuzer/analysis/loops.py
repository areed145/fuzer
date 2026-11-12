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
- `candidates(conn, days, until)`: every point whose measurement role is a quantity a controller holds (a level, a
  process or suction or discharge pressure, a flow, an outlet temperature) on equipment, not on a well, that cycles
  regularly over the window. The `loop_oscillation` detector raises each one as a `loop_candidate` for the engineer (P3.1a), and clears
  it when the point stops cycling.

A cycling loop is a finding for the instrument technician and the control-system owner: the playbook never
proposes a tuning or setpoint change.
"""

from datetime import datetime

import numpy as np

from ..core import detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact
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


# ---- the screen as a detector (P3.1a) ----

CONTROLLED = ("level", "pressure", "differential_pressure", "flow", "temperature", "outlet_temperature",
              "suction_pressure", "discharge_pressure", "suction_flow", "fuel_flow")  # what a controller holds
CANDIDATE = ("A candidate, not a diagnosed loop: Fuzer can't tell which points are under control. It never proposes "
             "a tuning, setpoint or mode change.")


def _loop_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("loop_candidate") or exception_type(
        "loop_candidate", "Loop cycling", role="role:engineer", audience=("ioc",), joins=False,
        description="A measurement that cycles regularly, as a control loop does when it hunts or limit-cycles on a "
                    "sticking valve. A candidate for the engineer to look at, not a diagnosis. It clears when the "
                    "measurement stops cycling.")


LOOP_TYPE = _loop_type().name


def candidates(conn, days: int = 3, until: str | None = None, src=None) -> list[dict]:
    """The points on equipment whose role is a controlled quantity and that cycle regularly over the window, the most
    regular first: {point, point_id, asset, asset_label, role, r, period_min, amplitude, uom, provider}."""
    from ..sources import timeseries

    rows = conn.execute(
        """
        select mr.point_id, mr.asset, mr.role, coalesce(al.label, mr.asset) as asset_label
        from measurement_role mr join entity a on a.fuzer_id = mr.asset and a.kind <> 'well'
        left join lateral (select label from entity_label where fuzer_id = mr.asset order by label limit 1) al on true
        where mr.role = any(%s)
        order by mr.point_id
        """, (list(CONTROLLED),)).fetchall()
    pts = {r["point_id"]: p for r in rows if (p := timeseries._point(conn, r["point_id"]))}
    if not pts:
        return []
    src = src or timeseries.provider()
    start, end = window_of(conn, days, until)
    vals = read(pts, start, end, STEP_MINUTES, src)
    out = []
    for r in rows:
        if r["point_id"] not in pts:
            continue
        c = cycling([v for _, v in sorted(vals[r["point_id"]].items())], STEP_MINUTES)
        if c is not None and _cycles(c):
            p = pts[r["point_id"]]
            out.append({"point": p["point"], "point_id": r["point_id"], "asset": r["asset"],
                        "asset_label": r["asset_label"], "role": r["role"], "uom": p.get("uom"),
                        "provider": getattr(src, "name", None) or type(src).__name__.removesuffix("Provider").lower(),
                        "window": [start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")], **c})
    return sorted(out, key=lambda c: -c["r"])


@detectors.register("loop_oscillation", title="A measurement cycling as a hunting control loop does",
                    kind=detectors.STATISTICAL, cadence="daily", raises=(LOOP_TYPE,), roles=CONTROLLED,
                    needs={"historian points": detectors.POINTS,
                           "measurement roles of what a controller holds": lambda conn, pack: conn.execute(
                               "select 1 from measurement_role where role = any(%s) limit 1", (list(CONTROLLED),)
                           ).fetchone() is not None},
                    eval="test:tests/analysis/test_loops.py::"
                         "test_a_cycling_level_is_a_loop_candidate_until_it_stops",
                    found=lambda out: len(out["raised"]))
def watch(conn, until: str | None = None, days: int = 3, by: str = "agent:loop_screen", src=None) -> dict:
    """Each controlled measurement cycling regularly over the last `days` raised as `loop_candidate` for the engineer
    (keyed by the point), with its period and amplitude; open ones whose point no longer cycles are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (LOOP_TYPE,))]
    found_rows = candidates(conn, days, until, src)
    raised, errors = [], []
    for c in found_rows:
        period = f"{c['period_min']:g} minutes"
        synthetic = c["provider"] == "synthetic"
        found = Finding(
            "loops.oscillation", DETERMINISTIC, method="loop_oscillation", start=c["window"][0], end=c["window"][1],
            data={"r": c["r"], "period_min": c["period_min"], "amplitude": c["amplitude"], "role": c["role"]},
            items=(fact("Point", c["point"]), fact("On", c["asset_label"]), fact("Cycles every", period),
                   fact("Amplitude", f"{c['amplitude']:g} {c['uom'] or ''}".strip()),
                   fact("Regularity r", f"{c['r']:g} (over {REGULAR:g} is a regular oscillation)"),
                   at("From", c["window"][0]), at("To", c["window"][1]), fact("What this is", CANDIDATE),
                   fact("Data", "synthetic values from the fake plant, not plant data" if synthetic else None)))
        try:
            x = raise_exception(conn, LOOP_TYPE, c["point"], f"{c['point']} on {c['asset_label']} cycles every "
                                f"{period}: a loop candidate", by, source="loops", refs=[c["point_id"], c["asset"]],
                                priority=0.4, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{c['point']}: {e}")
            continue
        raised.append({"point": c["point"], "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
    cleared = []
    for key in set(held) - {c["point"] for c in found_rows}:
        clear_exception(conn, LOOP_TYPE, key, by, reason="the measurement no longer cycles")
        cleared.append(key)
    conn.commit()
    return {"at": until or datetime.now().isoformat(timespec="minutes"), "candidates": len(found_rows),
            "raised": raised, "cleared": cleared, "errors": errors}
