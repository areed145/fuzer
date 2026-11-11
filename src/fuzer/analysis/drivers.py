"""Driver checks: the gas turbine or gas engine that turns the compressor.

- `exhaust_spread(ref)`: a gas turbine's exhaust thermocouples: the spread (hottest less coldest) against its own
  clean level, and which thermocouple has wandered from the others. A widening spread is a combustion problem (a
  plugged fuel nozzle, a cracked liner or transition piece) or a failed thermocouple; left alone it burns the hot
  section. Flags `exhaust_spread` when the spread has doubled and is more than 25 degC wider than it was.
- `hot_section(ref)`: fired hours since the last hot section inspection, from the maintenance plan that schedules it
  ("every 30,000 fired hours") and the driver's own running hours since it was last done, and the date it falls due
  at the recent running rate. Flags `inspection_overdue` (past the interval) and `inspection_due` (90 % used, or due
  within 90 days).
- `engine_cylinders(ref)`: a gas engine driver's exhaust temperature at each cylinder against the others: a cylinder
  running cold is misfiring (a spark plug, its coil or a gas valve) and one running hot is overloaded or detonating.
  Flags `cylinder_misfire` (a cylinder 60 degC or more under the others' median) and `cylinder_hot` (50 degC over).
"""

import re
from datetime import datetime, timedelta

import numpy as np

from .calcs import CalcError, _label, read, result, window_of
from .condition import daily, points_like, running

SPREAD_TIMES, SPREAD_WIDER = 2.0, 25.0
DUE_SHARE, DUE_DAYS = 0.9, 90
MISFIRE, HOT = 60.0, 50.0
STEP = 120  # minutes: fired hours are counted on a two-hour grid over years of history


def _tag(conn, fid: str) -> str:
    from .rotating import _tag as tag

    return tag(conn, fid)


def exhaust_spread(conn, fid: str, days: int = 120, until: str | None = None, src=None) -> dict:
    """The exhaust thermocouples' spread against its clean level, and the one that wandered."""
    tag = _tag(conn, fid)
    pts = points_like(conn, tag, r"exhaust thermocouple \d+")
    if len(pts) < 3:
        raise CalcError("fewer than three exhaust thermocouples on that machine")
    start, end = window_of(conn, days, until)
    keep = running(conn, tag, start, end, 60, src)
    v = read({p["id"]: p for p in pts}, start, end, 60, src)
    times = sorted(set.intersection(*[set(v[p["id"]]) for p in pts]) & (keep if keep is not None else set(v[pts[0]["id"]])))
    if len(times) < 14 * 12:
        raise CalcError("the turbine hardly ran in that window")
    spread = {t: max(v[p["id"]][t] for p in pts) - min(v[p["id"]][t] for p in pts) for t in times}
    days_, ys = daily(spread)
    base = float(np.median(ys[: max(14, len(ys) // 4)]))
    now = float(np.median(ys[-3:]))
    recent = [t for t in times if t >= (end - timedelta(days=3)).isoformat()]
    mean = {t: float(np.median([v[p["id"]][t] for p in pts])) for t in recent}
    dev = {p["point"]: round(float(np.median([v[p["id"]][t] - mean[t] for t in recent])), 1) for p in pts}
    early = [t for t in times if t < (start + timedelta(days=30)).isoformat()] or times[:240]
    mean0 = {t: float(np.median([v[p["id"]][t] for p in pts])) for t in early}
    dev0 = {p["point"]: float(np.median([v[p["id"]][t] - mean0[t] for t in early])) for p in pts}
    moved = max(dev, key=lambda k: abs(dev[k] - dev0[k]))
    flag = "exhaust_spread" if now >= SPREAD_TIMES * base and now - base >= SPREAD_WIDER else None
    return result(round(now, 1), round(base, 1), flag,
                  f"exhaust thermocouple spread (hottest less coldest), daily medians of hourly readings while running; "
                  f"expected is its own clean level (the window's first quarter); flagged at {SPREAD_TIMES:g} times "
                  f"and {SPREAD_WIDER:g} degC wider. The thermocouple that wandered most from the others since the "
                  f"window began names the combustor (or the thermocouple) to look at",
                  [tag, *[p["id"] for p in pts]], unit="degC", machine=_label(conn, tag), thermocouples=len(pts),
                  wandered=moved, wandered_by=round(dev[moved] - dev0[moved], 1), deviations=dev,
                  data_from=days_[0], data_to=days_[-1], readings=len(times),
                  daily=[{"day": d, "spread": round(float(y), 1)} for d, y in zip(days_, ys)][-60:])


def _plan(conn, tag: str) -> dict | None:
    """The maintenance plan for the machine's hot section inspection, with its interval in fired hours."""
    rows = conn.execute(
        """
        select p.fuzer_id, jsonb_object_agg(g.field, g.value) as f from entity p
        join golden g on g.fuzer_id = p.fuzer_id
        where p.kind = 'maintenance_plan' and p.retired_at is null
          and exists (select 1 from edge c where c.src = p.fuzer_id and c.rel = 'concerns'
                      and (c.dst = %(t)s or exists (select 1 from edge i where i.src = c.dst and i.rel = 'installed_at'
                                                     and i.dst = %(t)s)))
        group by p.fuzer_id
        """, {"t": tag}).fetchall()
    for r in rows:
        task = str(r["f"].get("task") or "")
        if re.search(r"hot section|HSI|borescope|major overhaul", task, re.IGNORECASE):
            m = re.search(r"([\d,]+)\s*(fired|operating|running)?\s*hours", task, re.IGNORECASE)
            return {"id": r["fuzer_id"], "task": task, "last_done": str(r["f"].get("last_done") or "")[:10],
                    "next_due": str(r["f"].get("next_due") or "")[:10],
                    "interval_hours": float(m.group(1).replace(",", "")) if m else None,
                    "interval_days": r["f"].get("interval_days")}
    return None


def hot_section(conn, fid: str, until: str | None = None, src=None) -> dict:
    """Fired hours since the last hot section inspection against its interval, and when it falls due."""
    tag = _tag(conn, fid)
    plan = _plan(conn, tag)
    if not plan:
        raise CalcError("no hot section inspection plan for that machine in the maintenance plans")
    if not plan["last_done"]:
        raise CalcError("the hot section inspection plan has no last-done date")
    interval = plan["interval_hours"] or (float(plan["interval_days"]) * 24 if plan["interval_days"] else None)
    if not interval:
        raise CalcError("the hot section inspection plan gives no interval")
    _, end = window_of(conn, 1, until)
    since = datetime.fromisoformat(plan["last_done"])
    if since >= end:
        raise CalcError(f"the last hot section inspection on file ({plan['last_done']}) is after that date")
    keep = running(conn, tag, since, end, STEP, src)
    if keep is None:
        raise CalcError("no speed point to count the fired hours from")
    hours = float(len(keep)) * STEP / 60
    recent_from = (end - timedelta(days=90)).isoformat()
    span = min(90.0, max((end - since).days, 1))
    rate = sum(1 for t in keep if t >= recent_from) * STEP / 60 / (span * 24)  # share of the time it ran lately
    left = interval - hours
    due = (end + timedelta(hours=left / max(rate, 0.05))).date().isoformat() if left > 0 else "now"
    used = hours / interval
    soon = due != "now" and due <= (end + timedelta(days=DUE_DAYS)).date().isoformat()
    flag = "inspection_overdue" if left <= 0 else "inspection_due" if used >= DUE_SHARE or soon else None
    return result(round(hours, 0), interval, flag,
                  f"fired hours since the last hot section inspection ({plan['last_done']}), counted from the "
                  f"driver's speed above half of normal (every {STEP // 60} h); the interval from the maintenance plan ('{plan['task']}'); "
                  f"the due date at the last 90 days' running rate ({rate * 100:.0f} % of the time). The plan's "
                  f"calendar date ({plan['next_due'] or 'none'}) is SAP's equivalent, not the hours",
                  [tag, plan["id"]], unit="fired hours", machine=_label(conn, tag), used_pct=round(used * 100, 1),
                  hours_left=round(left, 0), due=due, last_done=plan["last_done"], plan_next_due=plan["next_due"],
                  running_share_pct=round(rate * 100, 1),
                  etas=[{"what": f"hot section inspection due at {interval:,.0f} fired hours", "reaches": due,
                         "range": None}])


def engine_cylinders(conn, fid: str, days: int = 30, until: str | None = None, src=None) -> dict:
    """A gas engine's cylinder exhaust temperatures, each against the others."""
    tag = _tag(conn, fid)
    pts = [p for p in points_like(conn, tag, r"cylinder \d+ exhaust temp")]
    if len(pts) < 3:
        raise CalcError("no engine cylinder exhaust temperatures on that machine")
    start, end = window_of(conn, days, until)
    keep = running(conn, tag, start, end, 60, src)
    v = read({p["id"]: p for p in pts}, start, end, 60, src)
    recent = sorted(t for t in set.intersection(*[set(v[p["id"]]) for p in pts])
                    if t >= (end - timedelta(days=2)).isoformat() and (keep is None or t in keep))
    if len(recent) < 12:
        raise CalcError("the engine hardly ran in the last two days")
    dev = {}
    for p in pts:
        others = [q for q in pts if q is not p]
        dev[p["description"]] = round(float(np.median([v[p["id"]][t] - np.median([v[q["id"]][t] for q in others])
                                                       for t in recent])), 1)
    cold = min(dev, key=dev.get)
    hot = max(dev, key=dev.get)
    num = lambda d: int(m.group(1)) if (m := re.search(r"cylinder (\d+)", d, re.IGNORECASE)) else None
    flag = "cylinder_misfire" if dev[cold] <= -MISFIRE else "cylinder_hot" if dev[hot] >= HOT else None
    worst = cold if flag != "cylinder_hot" else hot
    return result(dev[worst], 0.0, flag,
                  f"each cylinder's exhaust temperature less the median of the others, median of the last 2 days' "
                  f"hourly readings while running; a misfire at {MISFIRE:g} degC or more under, hot at {HOT:g} over",
                  [tag, *[p["id"] for p in pts]], unit="degC from the others", machine=_label(conn, tag),
                  cylinder=num(worst), cylinders=len(pts), deviations={num(k): x for k, x in dev.items()},
                  data_from=recent[0][:10], data_to=recent[-1][:10], readings=len(recent) * len(pts))
