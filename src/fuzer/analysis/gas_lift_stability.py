"""Gas lift casing heading and instability (#341), as a screen across the wells and one well's signals: which gas
lifted wells cycle, and how hard. The `gas_lift_instability` detector (gas_lift.watch, P3.1c) raises the heading wells
for the production engineer; this is what an engineer or an agent reads to see the field and one well. A heading well's annulus
fills and dumps through the gas lift valve over and over, so its casing pressure, its lift gas and its wellhead
pressure cycle together, and it makes less oil than steady lift would.

    screen(conn, days, until)        every gas lifted well's casing pressure, lift gas and wellhead pressure through the
                                     regularity test of `loops.cycling`; heading wells first, most regular first
    well(conn, well, days, until)    one well's three signals on the same grid, with each one's cycle

A well is `heading` when its casing pressure cycles regularly (the loops test: r over 1, at least 4 cycles, the
autocorrelation back up one period on) and its half swing is at least the detector's MIN_SWING of its median level; `unstable` when lift gas or
wellhead pressure cycles that way but casing pressure doesn't. The cause (a valve failed open, multipointing, too
little gas, a choke too wide) stays the engineer's question: this names candidates and never proposes a gas or choke
change.
"""

import numpy as np

from .calcs import CalcError, _label, read, window_of
from .gas_lift import MIN_SWING
from .loops import MIN_READINGS, STEP_MINUTES, _cycles, cycling

SIGNALS = ("casing_pressure", "gas_lift_rate", "wellhead_pressure")
SWING_PCT = 100 * MIN_SWING  # the detector's: half the 5-95 % swing as a share of the level
SAME_PERIOD = 0.25  # signals whose periods are within this share of the casing's cycle together


def _points(conn, wells: list[str] | None = None) -> dict[str, dict[str, dict]]:
    from ..sources import timeseries

    rows = conn.execute(
        "select mr.asset, mr.role, mr.point_id from measurement_role mr join entity a on a.fuzer_id = mr.asset "
        "and a.kind = 'well' and a.retired_at is null and a.merged_into is null where mr.role = any(%s) "
        "and (%s::text[] is null or mr.asset = any(%s)) order by mr.point_id",
        (list(SIGNALS), wells, wells)).fetchall()
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        if (p := timeseries._point(conn, r["point_id"])) and r["role"] not in out.get(r["asset"], {}):
            out.setdefault(r["asset"], {})[r["role"]] = p
    return {w: s for w, s in out.items() if "casing_pressure" in s}  # gas lifted: it has an annulus to watch


def _assess(series: dict[str, list]) -> dict:
    sig = {}
    for role, v in series.items():
        c = cycling(v, STEP_MINUTES)
        if c is None:
            continue
        x = np.asarray([a for a in v if a is not None], float)
        level = float(np.median(x)) if len(x) else 0.0
        swing = round(100 * c["amplitude"] / abs(level), 1) if level else None
        sig[role] = c | {"level": round(level, 2), "swing_pct": swing,
                         "cycles": bool(_cycles(c) and swing is not None and swing >= SWING_PCT)}
    casing = sig.get("casing_pressure")
    if casing is None:
        return {"verdict": "too_little_data", "signals": sig}
    verdict = "heading" if casing["cycles"] else "unstable" if any(s["cycles"] for s in sig.values()) else "steady"
    period = casing["period_min"] if casing["cycles"] else next(
        (s["period_min"] for s in sig.values() if s["cycles"]), None)
    together = sorted(r for r, s in sig.items() if s["cycles"] and period and s["period_min"]
                      and abs(s["period_min"] - period) <= SAME_PERIOD * period)
    return {"verdict": verdict, "period_min": period, "cycling_together": together,
            "casing_swing_bar": casing["amplitude"], "casing_swing_pct": casing["swing_pct"], "r": casing["r"],
            "signals": sig}


def screen(conn, days: int = 3, until: str | None = None, src=None) -> dict:
    """Every gas lifted well over the last `days`: heading, unstable or steady, heading wells first."""
    from ..sources import timeseries

    pts = _points(conn)
    start, end = window_of(conn, days, until)
    flat = {p["id"]: p for s in pts.values() for p in s.values()}
    src = src or timeseries.provider()
    vals = read(flat, start, end, STEP_MINUTES, src) if flat else {}
    wells = []
    for w, s in pts.items():
        a = _assess({role: [v for _, v in sorted(vals.get(p["id"], {}).items())] for role, p in s.items()})
        wells.append({"well_id": w, "well": _label(conn, w)} | {k: v for k, v in a.items() if k != "signals"} | {
            "signals": {r: {k: x[k] for k in ("r", "period_min", "swing_pct", "cycles")} for r, x in a["signals"].items()}})
    order = {"heading": 0, "unstable": 1, "steady": 2, "too_little_data": 3}
    wells.sort(key=lambda w: (order[w["verdict"]], -(w.get("r") or 0), w["well"]))
    count = {v: sum(1 for w in wells if w["verdict"] == v) for v in order}
    synthetic = getattr(src, "name", None) == "synthetic"
    return {"synthetic": synthetic, "window": [start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")], "days": days,
            "wells": wells, "summary": count | {"wells": len(wells)},
            "thresholds": {"swing_pct": SWING_PCT, "same_period": SAME_PERIOD, "step_minutes": STEP_MINUTES,
                           "min_readings": MIN_READINGS},
            "source": "historian: casing pressure, lift gas injection rate and wellhead pressure per gas lifted well",
            "note": ("Values are synthetic, from the fake plant, not plant data. " if synthetic else "") + "Heading is the casing pressure cycling regularly (autocorrelation regularity after Thornhill, Huang "
                    f"and Zhang 2003) with a swing of at least {SWING_PCT:g} % of its level; unstable is lift gas or "
                    "wellhead pressure cycling that way without it. Candidates for the engineer: the cause (a valve "
                    "failed open, multipointing, too little gas, a wide choke) is theirs to find, and no gas or choke "
                    "change is proposed."}


def well(conn, fid: str, days: int = 3, until: str | None = None, src=None) -> dict:
    """One gas lifted well's casing pressure, lift gas and wellhead pressure over the window, with each one's cycle."""
    from ..sources import timeseries

    pts = _points(conn, [fid]).get(fid)
    if not pts:
        raise CalcError("no casing pressure point on that well: it isn't gas lifted, or its points aren't mapped")
    start, end = window_of(conn, days, until)
    src = src or timeseries.provider()
    vals = read({p["id"]: p for p in pts.values()}, start, end, STEP_MINUTES, src)
    series = {role: sorted(vals.get(p["id"], {}).items()) for role, p in pts.items()}
    a = _assess({role: [v for _, v in s] for role, s in series.items()})
    if a["verdict"] == "too_little_data":
        raise CalcError(f"fewer than {MIN_READINGS} casing pressure readings in that window")
    return {"well_id": fid, "well": _label(conn, fid), "days": days, "synthetic": getattr(src, "name", None) == "synthetic",
            "window": [start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")], **a,
            "points": {role: {"id": p["id"], "point": p["point"], "uom": p.get("uom")} for role, p in pts.items()},
            "series": {role: [[t, v] for t, v in s] for role, s in series.items()}}
