"""Instrument health and change detection on any series (#105 C).

Both are plain functions over values on a regular time grid, shared by the `instrument_health` and
`detect_changes` MCP tools, their charts and the #96 detectors. Findings come back in the calcs.result
shape, with the time span they cover.

Instrument health asks whether the reading can be trusted, which is a different question from whether
the process is upset: a transmitter that froze, sticks, hunts, sits past its range or walks away from its
redundant twin. Frozen and sticking readings only count while the unit runs, since a stopped machine's
values are meant to sit still. The baseline is the point's own behaviour in the days before the window.

Change detection finds where a series steps (a trip, a choke move), drifts (fouling, wear) or changes
its variance (a transmitter starting to hunt, a controller retuned), with the time each starts.
"""

import itertools
import re
from datetime import datetime, timedelta

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .calcs import result

FLAT_HOURS = 2  # no change at all for this long: frozen (comms, a stale tag)
STUCK_HOURS = 6  # high-frequency spread under STUCK x its usual for this long, not frozen: sticking
NOISY_HOURS = 1  # over NOISY x its usual for this long: hunting, a loose connection
STUCK, NOISY = 0.1, 4.0
RANGE_MINUTES = 30  # at or past the end of its range for this long
DRIFT_HOURS = 2  # redundant transmitters this far apart for this long
RANGES = {"%": (0.0, 100.0)}  # instrument range by unit, where the unit fixes it
BASELINE_DAYS = 7
MERGE_MINUTES = 240  # the same finding again within this long is one finding


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) of each run of True."""
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    edges = np.flatnonzero(m[1:] != m[:-1])
    return list(zip(edges[::2], edges[1::2]))


def spread(x: np.ndarray) -> float:
    """Robust standard deviation (1.4826 x the median absolute deviation)."""
    x = x[~np.isnan(x)]
    return float(1.4826 * np.median(np.abs(x - np.median(x)))) if len(x) else float("nan")


def rolling_spread(v: np.ndarray, w: int) -> np.ndarray:
    """Spread of the step-to-step changes over a centred window of w steps, at each value (nan near the
    ends): how much the reading jitters, whatever its level or a slow trend."""
    out = np.full(len(v), np.nan)
    d = np.diff(v)
    if len(d) < w:
        return out
    win = sliding_window_view(d, w)
    med = np.median(win, axis=1)
    s = 1.4826 * np.median(np.abs(win - med[:, None]), axis=1)
    out[w // 2 + 1: w // 2 + 1 + len(s)] = s
    return out


def _span(times: list[datetime], i: int, j: int, step: int) -> dict:
    return {"start": times[i].isoformat(timespec="minutes"), "end": times[min(j, len(times) - 1)].isoformat(timespec="minutes"),
            "minutes": int(j - i) * step}


def health(times: list[datetime], v: np.ndarray, step: int, baseline: np.ndarray | None = None,
           running: np.ndarray | None = None, lo: float | None = None, hi: float | None = None) -> list[dict]:
    """Frozen, stuck, noisy and out-of-range stretches of one reading: [{check, start, end, minutes,
    value, expected}]. `baseline` is the same point before the window (else the window itself sets
    what's usual); `running` masks the steps its unit runs; lo / hi its instrument range."""
    v = np.asarray(v, float)
    run = np.ones(len(v), bool) if running is None else np.asarray(running, bool)
    out = []
    w = max(6, 60 // step)
    s = rolling_spread(v, w)
    base = np.asarray(baseline, float) if baseline is not None and len(baseline) > 3 * w else v
    usual = float(np.nanmedian(rolling_spread(base, w)))
    if not np.isfinite(usual) or usual <= 0:
        usual = max(spread(np.diff(base)), 1e-9)

    if lo is not None or hi is not None:
        past = ((v <= lo) if lo is not None else False) | ((v >= hi) if hi is not None else False)
        for i, j in _runs(past & ~np.isnan(v)):
            if (j - i) * step >= RANGE_MINUTES:
                x = v[i:j]
                out.append({"check": "out_of_range", **_span(times, i, j, step), "value": float(x[np.argmax(np.abs(x - x.mean()))]
                            if len(x) else x[0]), "expected": hi if hi is not None and x[0] >= hi else lo})
    at_end = np.zeros(len(v), bool) if lo is None and hi is None else \
        (((v <= lo) if lo is not None else False) | ((v >= hi) if hi is not None else False))

    same = np.concatenate([[False], np.diff(v) == 0]) & (v != 0) & run & ~at_end
    for i, j in _runs(same):
        i = max(0, i - 1)  # the first of the repeated values
        if (j - i) * step >= FLAT_HOURS * 60:
            out.append({"check": "frozen", **_span(times, i, j, step), "value": float(v[i]), "expected": None})
    frozen = np.zeros(len(v), bool)
    for f in out:
        if f["check"] == "frozen":
            frozen[(np.array(times) >= datetime.fromisoformat(f["start"])) & (np.array(times) <= datetime.fromisoformat(f["end"]))] = True

    with np.errstate(invalid="ignore"):
        for check, mask, hours in (("stuck", (s < STUCK * usual) & run & ~frozen & ~at_end, STUCK_HOURS),
                                   ("noisy", s > NOISY * usual, NOISY_HOURS)):
            for i, j in _runs(mask):
                if (j - i) * step >= hours * 60:
                    out.append({"check": check, **_span(times, i, j, step), "value": round(float(np.nanmedian(s[i:j])), 4),
                                "expected": round(usual, 4)})
    return _merge(out, step)


def _merge(found: list[dict], step: int) -> list[dict]:
    """One finding per check for stretches that stop and start again within MERGE_MINUTES."""
    out: list[dict] = []
    for f in sorted(found, key=lambda f: (f["check"], f["start"])):
        last = out[-1] if out and out[-1]["check"] == f["check"] else None
        if last and datetime.fromisoformat(f["start"]) - datetime.fromisoformat(last["end"]) <= timedelta(minutes=MERGE_MINUTES):
            gap = (datetime.fromisoformat(f["start"]) - datetime.fromisoformat(last["end"])).total_seconds() / 60
            worse = max(last, f, key=lambda x: abs((x["value"] or 0) - (x["expected"] or 0)))
            out[-1] = {**last, "end": max(last["end"], f["end"]), "minutes": last["minutes"] + f["minutes"] + gap,
                       "value": worse["value"]}
        else:
            out.append(dict(f))
    return sorted(out, key=lambda f: f["start"])


def redundant_drift(times: list[datetime], a: np.ndarray, b: np.ndarray, step: int, base_a: np.ndarray | None = None,
                    base_b: np.ndarray | None = None) -> list[dict]:
    """Stretches where two transmitters on the same measurement (A / B) move apart from the offset they
    usually keep: [{check, start, end, minutes, value (gap), expected (usual gap)}]."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    ba, bb = (np.asarray(base_a, float), np.asarray(base_b, float)) if base_a is not None and base_b is not None else (a, b)
    offset = float(np.nanmedian(ba - bb))
    tol = max(4 * spread(ba - bb), 0.01 * abs(float(np.nanmedian(ba))), 1e-9)
    w = max(3, 60 // step)
    gap = a - b
    if len(gap) >= w:
        gap = np.concatenate([np.full(w // 2, np.nan), np.median(sliding_window_view(gap, w), axis=1),
                              np.full(w - 1 - w // 2, np.nan)])
    out = []
    with np.errstate(invalid="ignore"):
        for i, j in _runs(np.abs(gap - offset) > tol):
            if (j - i) * step >= DRIFT_HOURS * 60:
                x = gap[i:j]
                out.append({"check": "redundant_drift", **_span(times, i, j, step),
                            "value": round(float(x[np.nanargmax(np.abs(x - offset))]), 4), "expected": round(offset, 4)})
    return _merge(out, step)


# ---- change detection -----------------------------------------------------------------------------

STEP_Z = 6.0  # a step: the median moves this many times the spread inside the windows either side
VAR_RATIO = 4.0  # a variance change: the jitter in the two windows either side differs by this factor
DRIFT_Z = 4.0  # a drift: the fitted trend moves this many times the spread of what's left


def _suppress(cands: list[tuple[float, int]], w: int) -> list[int]:
    """The strongest candidates, at least w apart."""
    kept: list[int] = []
    for _, i in sorted(cands, reverse=True):
        if all(abs(i - k) >= w for k in kept):
            kept.append(i)
    return sorted(kept)


def detect_changes(times: list[datetime], v: np.ndarray, step: int, window_minutes: int = 180,
                   min_change_pct: float = 2.0) -> list[dict]:
    """Steps, drifts and variance changes in one series: [{kind, start, end, before, after, score}].
    A step compares the medians in the windows either side of each time; a variance change compares
    their jitter; a drift is a straight-line trend between steps that stands out from what's left
    around it. `min_change_pct` of the series' typical level is the smallest step or drift reported."""
    v = np.asarray(v, float)
    ok = ~np.isnan(v)
    if ok.sum() < 18:
        return []
    if not ok.all():  # short gaps: carry the last value
        idx = np.where(ok, np.arange(len(v)), 0)
        v = v[np.maximum.accumulate(idx)]
    n = len(v)
    w = max(6, window_minutes // step)
    if n < 3 * w:
        w = max(3, n // 3)
    level = float(np.median(np.abs(v)))
    floor = max(1e-3 * level, 1e-9)
    small = min_change_pct / 100 * max(level, float(np.percentile(v, 95) - np.percentile(v, 5)))
    win = sliding_window_view(v, w)  # window k covers [k, k + w)
    med = np.median(win, axis=1)
    mad = 1.4826 * np.median(np.abs(win - med[:, None]), axis=1)
    jit = rolling_spread(v, 2 * w)
    out = []

    # A step is a move between the windows either side that stands out from how far those windows
    # usually move in this series: a daily swing moves them every day, a trip once.
    move = med[w:] - med[:-w]  # move[i - w]: the window starting at i against the one before
    usual = max(spread(move), floor)
    typical = float(np.median(move))
    cands = []
    for i in range(w, n - w + 1):
        diff = move[i - w]
        z = abs(diff - typical) / max(usual, mad[i - w], mad[i], floor)
        if z >= STEP_Z and abs(diff) >= small:
            cands.append((z, i))
    steps = _suppress(cands, w)
    for i in steps:
        before, after = float(med[i - w]), float(med[i])
        z = abs(after - before - typical) / max(usual, mad[i - w], mad[i], floor)
        out.append({"kind": "step", "start": times[i].isoformat(timespec="minutes"), "end": None,
                    "before": round(before, 4), "after": round(after, 4), "score": round(float(z), 1)})

    # The same for the jitter: a change of VAR_RATIO or more that stands out from its usual ratio.
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.full(n, np.nan)
        for i in range(2 * w, n - 2 * w):
            a, b = jit[i - w - 1], jit[i + w]  # the window just before i, the one just after
            if np.isfinite(a) and np.isfinite(b) and max(a, b) > floor:
                ratio[i] = np.log(max(b, floor) / max(a, floor))
        wobble = max(spread(ratio), 1e-6)
        cands = [(abs(ratio[i]), i) for i in range(n) if np.isfinite(ratio[i]) and abs(ratio[i]) >= np.log(VAR_RATIO)
                 and abs(ratio[i]) >= 4 * wobble and not any(abs(i - k) < 2 * w for k in steps)]
    shifts = _suppress(cands, 4 * w)  # a step changes the jitter around it too; the step is the change
    for i in shifts:
        before, after = float(jit[i - w - 1]), float(jit[i + w])
        out.append({"kind": "variance", "start": times[i].isoformat(timespec="minutes"), "end": None,
                    "before": round(before, 4), "after": round(after, 4),
                    "score": round(max(before, after) / max(min(before, after), floor), 1)})

    edges = sorted({0, *steps, *shifts, n})  # a drift is a straight line within one regime
    centre = np.arange(len(med)) + w // 2  # med[k] is the median of the window centred here
    for a, b in itertools.pairwise(edges):
        a, b = a + (w if a else 0), b - (w if b < n else 0)  # keep clear of the steps
        k = np.flatnonzero((centre >= a) & (centre < b))
        if b - a < 3 * w or len(k) < 3:
            continue
        # fitted on the window medians, so a daily swing riding on a slow climb (fouling) doesn't hide it
        t, y = centre[k].astype(float), med[k]
        slope, icept = np.polyfit(t, y, 1)
        resid = y - (slope * t + icept)
        total = slope * (t[-1] - t[0])
        noise = max(spread(resid), floor)
        r2 = 1 - float(np.var(resid)) / max(float(np.var(y)), 1e-12)
        if abs(total) >= DRIFT_Z * noise and abs(total) >= small and r2 >= 0.7:
            out.append({"kind": "drift", "start": times[a].isoformat(timespec="minutes"),
                        "end": times[b - 1].isoformat(timespec="minutes"), "before": round(float(icept + slope * t[0]), 4),
                        "after": round(float(icept + slope * t[-1]), 4), "score": round(abs(total) / noise, 1),
                        "per_day": round(float(slope) * 1440 / step, 4)})
    return sorted(out, key=lambda c: c["start"])


# ---- on Fuzer points ------------------------------------------------------------------------------

def _points(conn, fid: str, n: int = 12) -> list[dict]:
    from ..sources import timeseries

    kind = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()
    if kind and kind["kind"] == "timeseries":
        return [p for p in [timeseries._point(conn, fid)] if p]
    return list(timeseries._unit_points(conn, fid, n))


def window(conn, since: str | None, until: str | None, days: int = 14) -> tuple[str, str]:
    """[since, until] as given, else `days` back from until or on from since, else the `days` up to the
    end of the data's today."""
    from .calcs import window_of

    if since and until:
        return since, until
    start, end = window_of(conn, days)
    if until:
        end = datetime.fromisoformat(until)
        start = end - timedelta(days=days)
    elif since:
        start = datetime.fromisoformat(since)
        end = start + timedelta(days=days)
    return start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")


def _grid(start: datetime, end: datetime, step: int) -> list[datetime]:
    return [start + timedelta(minutes=step * i) for i in range(int((end - start).total_seconds() // 60 // step) + 1)]


def _read(src, p: dict, start: datetime, end: datetime, step: int) -> np.ndarray:
    from .limits import _values

    return _values(src, p, start, end, step, len(_grid(start, end, step)))


def _twins(points: list[dict]) -> list[tuple[dict, dict]]:
    """Redundant transmitters on one unit: the same measurement told apart only by a trailing number or
    A / B / C ("Pressure", "Pressure 2"; 20-PT-2001A / 20-PT-2001B)."""
    key = lambda p: (re.sub(r"\s+(\d+|[A-C])$", "", (p["description"] or "").strip()),  # noqa: E731
                     re.sub(r"[A-C]?(\.PV)?$", "", p["point"]), p["uom"])
    groups: dict[tuple, list[dict]] = {}
    for p in points:
        if p["description"]:
            groups.setdefault(key(p)[::2], []).append(p)
    return [(g[0], q) for g in groups.values() for q in g[1:]]


BASIS = {
    "frozen": "no change at all for {FLAT_HOURS} h or more while the unit runs",
    "stuck": "step-to-step spread under {STUCK:g} x its usual for {STUCK_HOURS} h or more while the unit runs",
    "noisy": "step-to-step spread over {NOISY:g} x its usual (the {BASELINE_DAYS} days before) for {NOISY_HOURS} h or more",
    "out_of_range": "at or past the end of its {lo:g}-{hi:g} range for {RANGE_MINUTES} min or more",
    "redundant_drift": "apart from {peer} by more than its usual offset allows for {DRIFT_HOURS} h or more",
}


def instrument_health(conn, pack, fid: str, since: str, until: str, step: int = 10, src=None,
                      baseline_days: int = BASELINE_DAYS, values: bool = False) -> dict:
    """Instrument health of a PI point, or every point on a unit, over [since, until]: frozen, stuck,
    noisy, out of range, and drift between redundant transmitters. Each finding is evidence
    ({value, expected, flag, basis, sources}) with its span. Reads only up to `until`, so the replay
    can run it. `values` adds what was read ({series: [{point, values}], times}), for a chart."""
    from ..sources import timeseries
    from . import limits

    src = src or timeseries.provider()
    a, b = datetime.fromisoformat(since), datetime.fromisoformat(until)
    times = _grid(a, b, step)
    base_a = a - timedelta(days=baseline_days)
    pts = _points(conn, fid)
    unit = limits.units(conn, [p["id"] for p in pts])
    mode = limits.modes(conn, pack, set(unit.values()), a, b, step, src) if unit else {}
    vals, base, out, errors = {}, {}, [], []
    for p in pts:
        try:
            vals[p["id"]] = _read(src, p, a, b, step)
            base[p["id"]] = _read(src, p, base_a, a - timedelta(minutes=step), step)
        except Exception as e:  # one unreadable point shouldn't hide the rest
            errors.append(f"{p['point']}: {e}")
            continue
        m = mode.get(unit.get(p["id"]))
        running = None if m is None else (m == "running") | (m == "unknown")
        lo, hi = RANGES.get(p["uom"] or "", (None, None))
        for f in health(times, vals[p["id"]], step, base[p["id"]], running, lo, hi):
            out.append(_finding(p, f, BASIS[f["check"]].format(**globals(), lo=lo or 0, hi=hi or 0)))
    for x, y in _twins([p for p in pts if p["id"] in vals]):
        for f in redundant_drift(times, vals[x["id"]], vals[y["id"]], step, base[x["id"]], base[y["id"]]):
            out.append(_finding(x, f, BASIS["redundant_drift"].format(**globals(), peer=y["point"]), [y["id"]]))
    return {"of": fid, "since": since, "until": until, "step_minutes": step, "points": len(pts),
            "suspect": sorted({f["point"] for f in out}), "findings": sorted(out, key=lambda f: f["start"]),
            **({"errors": errors[:5]} if errors else {}), **(_values_out(pts, vals, times) if values else {})}


def _values_out(pts: list[dict], vals: dict, times: list[datetime]) -> dict:
    return {"times": times, "series": [{**{k: p[k] for k in ("id", "point", "description", "uom")}, "values": vals[p["id"]]}
                                       for p in pts if p["id"] in vals]}


def _finding(p: dict, f: dict, basis: str, more: list[str] = ()) -> dict:
    return {"point": p["point"], "point_id": p["id"], "start": f["start"], "end": f["end"], "minutes": f["minutes"],
            **result(f["value"], f["expected"], f["check"], basis, [p["id"], *more], unit=p["uom"])}


def changes(conn, fid: str, since: str, until: str, step: int | None = None, src=None,
            window_minutes: int = 180, limit: int = 40, values: bool = False) -> dict:
    """Steps, drifts and variance changes on a PI point, or every point on a unit, over [since, until]
    (detect_changes on each), in time order; the `limit` strongest when there are more. The step
    defaults to about 2000 values over the window."""
    from ..sources import timeseries

    src = src or timeseries.provider()
    a, b = datetime.fromisoformat(since), datetime.fromisoformat(until)
    step = step or timeseries.auto_step((b - a).total_seconds() / 3600, 2000)
    window_minutes = max(window_minutes, 6 * step)
    times = _grid(a, b, step)
    out, errors, vals = [], [], {}
    pts = _points(conn, fid)
    for p in pts:
        try:
            v = vals[p["id"]] = _read(src, p, a, b, step)
        except Exception as e:
            errors.append(f"{p['point']}: {e}")
            continue
        for c in detect_changes(times, v, step, window_minutes):
            basis = {"step": f"median of the {window_minutes} min after against the {window_minutes} min before",
                     "variance": f"step-to-step spread over the {window_minutes} min after against before",
                     "drift": "straight-line trend between steps, against the spread around it"}[c["kind"]]
            out.append({"point": p["point"], "point_id": p["id"], "start": c["start"], "end": c["end"],
                        "score": c["score"], **({"per_day": c["per_day"]} if "per_day" in c else {}),
                        **result(c["after"], c["before"], c["kind"], basis, [p["id"]], unit=p["uom"])})
    kept = sorted(out, key=lambda c: -c["score"])[:limit]
    return {"of": fid, "since": since, "until": until, "step_minutes": step, "points": len(pts), "found": len(out),
            "changes": sorted(kept, key=lambda c: (c["start"], c["point"])), **({"errors": errors[:5]} if errors else {}),
            **(_values_out(pts, vals, times) if values else {})}
