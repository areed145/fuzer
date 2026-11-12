"""Gas treating and dehydration checks (P4.2, pack 3): the columns that sweeten and dry the gas, the reboilers that
regenerate their solvent, and the chilled separator that sets the hydrocarbon dew point. Every check reads
measurement roles on the unit it is asked about and the operating windows the site recorded for those points (its
IOW register, else its alarm settings, else the range learned from the point's own history), so nothing here knows
which solvent, which facility or which spec a plant runs to.

- `contactor_foaming(ref)`: a contactor's differential pressure against its own clean level. Foaming shows as a
  sudden rise (the gas lifts the solvent: carryover, off-spec gas, solvent losses); a slow climb over weeks is fouling
  or damaged packing. When the column has a treated gas analyser, the peak and the time over its limit are quoted
  alongside. Flags `foaming` (peak at least 1.5 times the clean level, reached within 24 hours) and `fouling` (the
  last week's median at least 1.25 times it).
- `treated_gas(ref)`: the treated gas analyser on a contactor (acid gas after an amine column, water after a glycol
  one) against its operating window's upper limit, with its trend and the day it gets there at that rate. Flags
  `off_spec` (the latest day's median past the limit) and `approaching_spec` (on course for it within 30 days).
- `regenerator(ref)`: a reboiler's temperature against its window. Too cold strips the solvent poorly (lean amine or
  glycol that can't do the job); too hot degrades it. Flags `reboiler_low`, `reboiler_high`, and `reboiler_falling`
  or `reboiler_rising` (on course for a limit within 30 days).
- `chiller(ref)`: a chilled (low temperature) separator's temperature against its window's upper limit: warmer than
  that, the hydrocarbon dew point of the gas leaving it is off spec. Flags `chiller_warm` and `chiller_warming`.

Daily medians are taken while the unit has flow through it (its differential pressure or temperature away from its
idle level), so a trip doesn't read as a recovery.
"""

from datetime import datetime, timedelta

import numpy as np

from .calcs import CalcError, read, result, window_of
from .condition import daily, eta

FOAM_TIMES = 1.5  # peak differential pressure over the clean level that reads as foaming
FOAM_HOURS = 24  # reached within this long: sudden (foaming), not gradual (fouling)
FOUL_TIMES = 1.25  # the last week's median over the clean level: fouling or damaged packing
IDLE_SHARE = 0.3  # below this share of the clean level the column has no gas through it
RECENT_HOURS = 48  # the window a foaming episode is looked for in
SOON_DAYS = 30  # a value on course for its limit within this is flagged


def _tag(conn, fid: str) -> str:
    from .rotating import _tag as tag

    return tag(conn, fid)


def _point(conn, tag: str, *roles: str, what: str) -> dict:
    from .equipment import role_point

    p = role_point(conn, tag, *roles)
    if not p:
        raise CalcError(f"no {what} point on that unit")
    return p


def window_limits(conn, point_id: str, at: datetime | None = None) -> dict:
    """The point's operating window: {lo, hi, basis, record}. An IOW first (API RP 584), then its alarm settings, then
    the range learned from its own history; empty when none is on record."""
    from . import limits

    e = limits.for_points(conn, None, [point_id], at).get(point_id) or {}
    for w in e.get("iow") or []:
        if w.get("lo") is not None or w.get("hi") is not None:
            return {"lo": w["lo"], "hi": w["hi"], "basis": f"integrity operating window {w['iow']}", "record": w["record"]}
    alarm = e.get("alarm") or {}
    if alarm.get("hi") or alarm.get("lo"):
        hi, lo = alarm.get("hi"), alarm.get("lo")
        return {"lo": lo and float(lo["value"]), "hi": hi and float(hi["value"]), "basis": "alarm settings",
                "record": (hi or lo)["record"]}
    if learned := e.get("learned"):
        return {"lo": learned["lo"], "hi": learned["hi"], "basis": "range learned from its own history", "record": None}
    return {}


def _series(conn, p: dict, days: int, until: str | None, src, step: int = 60) -> tuple[dict, datetime, datetime]:
    start, end = window_of(conn, days, until)
    return read({"v": p}, start, end, step, src)["v"], start, end


def _flowing(v: dict[str, float], level: float | None = None) -> dict[str, float]:
    """The readings while the unit has flow through it: away from its idle level (zero, or a small share of normal)."""
    vals = [x for x in v.values() if x is not None]
    if not vals:
        return {}
    ref = level if level is not None else float(np.median(vals))
    return {t: x for t, x in v.items() if x is not None and abs(x) >= IDLE_SHARE * abs(ref)}


def contactor_foaming(conn, fid: str, days: int = 30, until: str | None = None, src=None) -> dict:
    """A contactor's differential pressure against its clean level: a sudden rise is foaming, a slow climb fouling."""
    tag = _tag(conn, fid)
    dp = _point(conn, tag, "differential_pressure", what="differential pressure")
    v, start, end = _series(conn, dp, days, until, src)
    cut = (end - timedelta(hours=RECENT_HOURS)).isoformat()
    before = {t: x for t, x in v.items() if t < cut and x is not None}
    if len(before) < 24 * 7:
        raise CalcError("too little differential pressure history before the last two days")
    clean = float(np.median(list(_flowing(before).values()) or [0.0]))
    if clean <= 0:
        raise CalcError("the column hardly had gas through it in that window")
    run = _flowing(v, clean)
    recent = sorted((t, x) for t, x in run.items() if t >= cut)
    if not recent:
        raise CalcError("the column had no gas through it in the last two days")
    peak_t, peak = max(recent, key=lambda r: r[1])
    lead = (datetime.fromisoformat(peak_t) - timedelta(hours=FOAM_HOURS)).isoformat()
    earlier = [x for t, x in sorted(run.items()) if lead <= t < peak_t]
    sudden = bool(earlier) and min(earlier) < 1.2 * clean
    week_cut = (end - timedelta(days=7)).isoformat()
    week = [x for t, x in run.items() if t >= week_cut]
    week_median = float(np.median(week)) if week else None
    episode = [t for t, x in recent if x >= FOAM_TIMES * clean]
    flag = ("foaming" if peak >= FOAM_TIMES * clean and sudden else
            "fouling" if week_median is not None and week_median >= FOUL_TIMES * clean else None)
    sources = [tag, dp["id"]]
    detail = {}
    from .equipment import role_point

    if gas := role_point(conn, tag, "composition"):
        w = window_limits(conn, gas["id"])
        g = read({"g": gas}, datetime.fromisoformat(cut), end, 15, src)["g"]
        vals = [(t, x) for t, x in g.items() if x is not None]
        if vals:
            gt, gx = max(vals, key=lambda r: r[1])
            over = sum(1 for _, x in vals if w.get("hi") is not None and x > w["hi"]) * 15 / 60
            detail["treated_gas"] = {"point": gas["point"], "description": gas["description"], "unit": gas["uom"],
                                     "peak": round(gx, 3), "peak_at": gt[:16], "limit": w.get("hi"),
                                     "limit_basis": w.get("basis"), "hours_over_limit": round(over, 2)}
            sources.append(gas["id"])
            if w.get("record"):
                sources.append(w["record"])
    return result(round(peak, 3), round(clean, 3), flag,
                  f"contactor differential pressure, hourly while gas flows through it (above {IDLE_SHARE:.0%} of its "
                  f"clean level), against its clean level (the median before the last {RECENT_HOURS} hours). Foaming "
                  f"when the last {RECENT_HOURS} hours peak at {FOAM_TIMES:g} times the clean level or more, reached "
                  f"within {FOAM_HOURS} hours; fouling when the last week's median is {FOUL_TIMES:g} times it or more "
                  "(GPSA Engineering Data Book practice: foaming shows first as a sudden rise in column differential "
                  "pressure)", sources, unit=dp["uom"], peak_at=peak_t[:16], times_clean=round(peak / clean, 2),
                  week_median=week_median and round(week_median, 3),
                  episode_from=episode[0][:16] if episode else None, episode_to=episode[-1][:16] if episode else None,
                  data_from=start.isoformat()[:10], data_to=end.isoformat()[:10], **detail)


def _window_check(conn, fid: str, roles: tuple[str, ...], what: str, sides: str, days: int, until: str | None, src,
                  flags: dict[str, str], basis: str) -> dict:
    """A point on the unit against its operating window: the latest day's median, its trend and the day it reaches a
    limit at that rate. `sides` is 'hi', 'lo' or 'both'; `flags` names the flag for each of past and soon, per side."""
    tag = _tag(conn, fid)
    p = _point(conn, tag, *roles, what=what)
    v, _start, end = _series(conn, p, days, until, src)
    w = window_limits(conn, p["id"], end)
    if not w:
        raise CalcError(f"no operating window (IOW, alarm settings or learned range) on that {what} point")
    run = _flowing(v)
    days_, ys = daily(run)
    if len(days_) < 7:
        raise CalcError(f"too few days of {what} readings in that window")
    latest = float(np.median(ys[-1:]))
    flag, flagged, trends = None, None, {}
    for side in (("hi", "lo") if sides == "both" else (sides,)):
        limit = w.get(side)
        if limit is None:
            continue
        e = eta(days_, ys, limit, side)
        trends[side] = {"limit": limit, "reaches": e["reaches"], "range": e["range"], "per_day": round(e["per_day"], 3),
                        "r2": e["r2"], "sufficiency": e["sufficiency"]["quotable"]}
        past = latest > limit if side == "hi" else latest < limit
        soon = (e["reaches"] not in (None, "now") and
                datetime.fromisoformat(e["reaches"]) <= datetime.fromisoformat(days_[-1]) + timedelta(days=SOON_DAYS))
        if past:
            flag, flagged = flags[f"{side}_past"], side
        elif soon and flag is None:
            flag, flagged = flags[f"{side}_soon"], side
    if not trends:
        raise CalcError(f"the {what} point's window has no {'limit' if sides == 'both' else sides + ' limit'}")
    expected = w.get(flagged or ("hi" if "hi" in trends else "lo"))  # the limit it is past or heading for
    sources = [tag, p["id"], *([w["record"]] if w.get("record") else [])]
    return result(round(latest, 3), expected, flag,
                  f"{basis}; daily medians while the unit runs, the latest day against the {w['basis']} "
                  f"(low {w.get('lo')}, high {w.get('hi')} {p['uom'] or ''}), with the last 30 days' trend and the day "
                  f"it reaches a limit at that rate (flagged within {SOON_DAYS} days)", sources, unit=p["uom"],
                  point=p["point"], description=p["description"], window={"lo": w.get("lo"), "hi": w.get("hi"),
                                                                           "basis": w["basis"]},
                  trend=trends, latest_day=days_[-1], data_from=days_[0], data_to=days_[-1],
                  daily=[{"day": d, "value": round(float(y), 3)} for d, y in zip(days_[-60:], ys[-60:], strict=True)])


def treated_gas(conn, fid: str, days: int = 60, until: str | None = None, src=None) -> dict:
    """The treated gas analyser on a contactor against its operating window's upper limit."""
    return _window_check(conn, fid, ("composition",), "treated gas analyser", "hi", days, until, src,
                         {"hi_past": "off_spec", "hi_soon": "approaching_spec"},
                         "the treated gas analyser on the contactor (acid gas after an amine column, water after a "
                         "glycol one: GPSA Engineering Data Book, API 12GDU for glycol units)")


def regenerator(conn, fid: str, days: int = 60, until: str | None = None, src=None) -> dict:
    """A reboiler's temperature against its window: too cold strips poorly, too hot degrades the solvent."""
    return _window_check(conn, fid, ("temperature", "outlet_temperature"), "reboiler temperature", "both", days, until,
                         src, {"hi_past": "reboiler_high", "hi_soon": "reboiler_rising", "lo_past": "reboiler_low",
                               "lo_soon": "reboiler_falling"},
                         "the reboiler temperature that regenerates the solvent (too cold leaves lean amine or glycol "
                         "under-stripped, too hot degrades it: GPSA Engineering Data Book, API 12GDU for glycol units)")


def chiller(conn, fid: str, days: int = 60, until: str | None = None, src=None) -> dict:
    """A chilled separator's temperature against its window's upper limit: warmer, the dew point is off spec."""
    return _window_check(conn, fid, ("temperature", "outlet_temperature"), "separator temperature", "hi", days, until,
                         src, {"hi_past": "chiller_warm", "hi_soon": "chiller_warming"},
                         "the temperature the gas is chilled to in the low temperature separator, which sets its "
                         "hydrocarbon dew point (GPSA Engineering Data Book, refrigeration and dew point control)")
