"""Shared pieces of the condition checks (protection channels, seals, lube oil, drivers): a machine's points by what
they measure, their alarm and trip settings, hours it was running, daily medians and a trend with the date it
reaches a limit, with a range (ISO 13381-1: a prognosis states its uncertainty)."""

import re
from datetime import datetime, timedelta

import numpy as np

from .calcs import CalcError, read
from .forecast import LEVEL, sufficiency

T90 = 1.645  # the 90 % two-sided band on a fitted slope
TREND_MIN_DAYS = 7  # daily values a trend needs before it projects to a limit
HORIZON_DAYS = 36525  # a limit further off than a century at today's rate is not dated
TREND_MAX_GAP_DAYS = 7  # a longer gap between daily values (a stop, a data outage) makes the trend thin


def points_like(conn, tag: str, pattern: str, n: int = 60) -> list[dict]:
    """The PI points on a unit (and its instruments) whose description matches `pattern` (a regex, any case)."""
    from ..sources.timeseries import _unit_points

    rx = re.compile(pattern, re.IGNORECASE)
    return [p for p in _unit_points(conn, tag, n) if rx.search(p["description"] or "")]


def settings(conn, ids: list[str], at: datetime | None = None) -> dict[str, dict[str, float]]:
    """Each point's effective alarm and trip settings: {point id: {hi, hihi, lo, lolo}} (DCS, AF, ...)."""
    from . import limits

    eff = limits.for_points(conn, None, ids, at)
    return {p: {t: float(x["value"]) for t, x in e.get("alarm", {}).items()} for p, e in eff.items()}


def speed_point(conn, tag: str) -> dict | None:
    """The speed (rpm) point on the machine or, failing that, on its train's driver."""
    own = [p for p in points_like(conn, tag, r"speed") if (p["uom"] or "").lower() == "rpm"]
    if own:
        return own[0]
    try:
        from .trips import train

        tr = train(conn, tag)
    except CalcError:
        return None
    for u in tr["members"]:
        found = [p for p in points_like(conn, u, r"speed") if (p["uom"] or "").lower() == "rpm"]
        if found:
            return found[0]
    return None


def running(conn, tag: str, start: datetime, end: datetime, step: int, src=None) -> set[str] | None:
    """The times the machine ran (its or its driver's speed above half of normal), or None when nothing says."""
    sp = speed_point(conn, tag)
    if not sp:
        return None
    v = read({"n": sp}, start, end, step, src)["n"]
    if not v:
        return None
    top = float(np.percentile(list(v.values()), 90))
    return {t for t, x in v.items() if x > 0.5 * top}


def daily(series: dict[str, float], keep: set[str] | None = None) -> tuple[list[str], np.ndarray]:
    """Daily medians of a series (only the times in `keep`, when given)."""
    by: dict[str, list[float]] = {}
    for t, x in series.items():
        if keep is None or t in keep:
            by.setdefault(t[:10], []).append(x)
    days = sorted(by)
    return days, np.array([float(np.median(by[d])) for d in days])


def eta(days: list[str], ys: np.ndarray, limit: float | None, side: str = "hi", recent: int = 30) -> dict:
    """The trend of daily values over the last `recent` days (per day, r2) and, when it heads for `limit` (an upper
    one for side 'hi', a lower one for 'lo'), the day it gets there, with the earliest and latest days a 90 % band on
    the slope allows (`range`). Already past it: `reaches` is 'now'. `sufficiency` is the shared check (P4.6) on the
    days the trend rests on: too few, and there is no trend and no date."""
    seg_d, seg_y = days[-recent:], ys[-recent:]
    enough = sufficiency(seg_d, TREND_MIN_DAYS, max_gap_days=TREND_MAX_GAP_DAYS)
    out = {"per_day": 0.0, "r2": 0.0, "reaches": None, "early": None, "late": None, "range": None, "level": LEVEL,
           "window": enough["window"], "points": enough["points"], "sufficiency": enough}
    if not enough["quotable"]:
        return out
    xs = np.array([(datetime.fromisoformat(d) - datetime.fromisoformat(seg_d[0])).days for d in seg_d], float)
    slope, icpt = (float(x) for x in np.polyfit(xs, seg_y, 1))
    fit = slope * xs + icpt
    ss = float(np.sum((seg_y - seg_y.mean()) ** 2))
    r2 = 1 - float(np.sum((seg_y - fit) ** 2)) / ss if ss else 0.0
    sxx = float(np.sum((xs - xs.mean()) ** 2))
    se = float(np.sqrt(np.sum((seg_y - fit) ** 2) / max(len(xs) - 2, 1)) / np.sqrt(sxx)) if sxx else 0.0
    out.update(per_day=slope, r2=round(r2, 2))
    if limit is None:
        return out
    now = float(np.median(ys[-3:]))
    up = side == "hi"
    if (now >= limit) if up else (now <= limit):
        out["reaches"] = "now"
        return out
    if (slope <= 0 if up else slope >= 0) or r2 < 0.5:
        return out
    last, gap = datetime.fromisoformat(days[-1]), limit - now

    def day(s: float) -> str | None:  # beyond HORIZON_DAYS is no date: a trend that slow says nothing about when
        return (last + timedelta(days=gap / s)).date().isoformat() if s * gap > 0 and gap / s <= HORIZON_DAYS else None
    out["reaches"] = day(slope)
    out["early"] = day(slope + (T90 * se if up else -T90 * se))
    out["late"] = day(slope - (T90 * se if up else -T90 * se))
    out["range"] = [out["early"], out["late"]] if out["early"] else None
    return out


def projection(what: str, point: str | None, e: dict) -> dict:
    """One forecast entry, as every prognosis lists it: what reaches its limit, when, the range, and the shared
    sufficiency verdict on the days behind it (P4.6)."""
    return {"what": what, "point": point, "reaches": e["reaches"], "range": e.get("range"),
            "level": e.get("level", LEVEL), "window": e.get("window"), "points": e.get("points"),
            "sufficiency": e.get("sufficiency")}


def crossed(now: float, lim: dict[str, float], side: str = "hi") -> str | None:
    """Which setting a value is past: 'trip' (hihi / lolo) or 'alarm' (hi / lo), else None."""
    if side == "hi":
        return "trip" if "hihi" in lim and now >= lim["hihi"] else "alarm" if "hi" in lim and now >= lim["hi"] else None
    return "trip" if "lolo" in lim and now <= lim["lolo"] else "alarm" if "lo" in lim and now <= lim["lo"] else None
