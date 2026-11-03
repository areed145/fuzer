"""Alarms, trips and states (#105 D).

- `alarm_analysis(scope, since, until)`: ISA-18.2 / EEMUA 191 measures over the alarms that sounded. The
  alarm source is each point going past an enabled DCS / AF alarm limit (#101, `limits.excursions` on the
  alarm basis), since there is no alarm journal yet; one from the operator logs or PI can be added. It gives
  alarms per hour and per 10 minutes, flood periods (over 10 in 10 minutes), chattering (3 or more on one
  point within a minute of each other's end), standing alarms (on for 24 h or more), and the top bad-actor
  points.
- `trip_analysis(scope, since, until)`: per unit with a run signal, stops (trips and stops), starts, failed
  starts (stopped again within 30 minutes), time between trips and the share of time running.
- `states(scope, since, until)`: each unit's state over time (running, startup, stopped, transition) as
  spans, for the swimlane chart of an upset spreading through a unit.
"""

from datetime import datetime, timedelta

import numpy as np

from ..core import settings
from ..core.jsonsafe import plain as _plain
from .calcs import CalcError, _label, result

# Defaults; the values in effect are settings (#131): alarms.flood_per_10min, .target_per_hour, .standing_hours,
# .chatter, .failed_start_minutes
FLOOD = 10  # alarms in 10 minutes (EEMUA 191)
TARGET_PER_HOUR = 6  # an operator can handle about one every 10 minutes
STANDING_HOURS = 24
CHATTER = 3  # alarms on one point, each within a minute of the previous one clearing
FAILED_START_MINUTES = 30


def _scope_points(conn, scope: str | None) -> list[str]:
    from . import studies

    if scope in (None, "facility"):
        return [r["point_id"] for r in conn.execute(
            "select distinct point_id from point_limit where basis = 'alarm' and enabled order by 1")]
    return [p["id"] for p in studies.points(conn, scope)]


def _window(since: str, until: str) -> tuple[datetime, datetime]:
    a, b = datetime.fromisoformat(since), datetime.fromisoformat(until)
    if b <= a:
        raise CalcError("the window ends before it starts")
    return a, b


def alarm_analysis(conn, pack, scope: str | None, since: str, until: str, src=None, top: int = 10) -> dict:
    from . import limits

    flood, target, standing_h, chatter_n = (settings.get(conn, f"alarms.{k}") for k in (
        "flood_per_10min", "target_per_hour", "standing_hours", "chatter"))
    a, b = _window(since, until)
    pids = _scope_points(conn, scope)
    ex = [x for x in limits.excursions(conn, pack, pids, a, b, 10, src) if x["basis"] == "alarm"]
    hours = (b - a).total_seconds() / 3600
    starts = sorted(x["start"] for x in ex)
    floods, i = [], 0
    for j, t in enumerate(starts):  # sliding 10 minutes
        while starts[i] < t - timedelta(minutes=10):
            i += 1
        if j - i + 1 > flood and (not floods or t - floods[-1]["end"] > timedelta(minutes=10)):
            floods.append({"start": starts[i], "end": t, "alarms": j - i + 1})
        elif floods and j - i + 1 > flood:
            floods[-1].update(end=t, alarms=max(floods[-1]["alarms"], j - i + 1))
    by_point: dict[str, list[dict]] = {}
    for x in ex:
        by_point.setdefault(x["point"], []).append(x)
    chatter = []
    for p, xs in by_point.items():
        xs.sort(key=lambda x: x["start"])
        quick = sum(1 for u, v in zip(xs, xs[1:]) if u["end"] and v["start"] - u["end"] <= timedelta(minutes=1))
        if quick + 1 >= chatter_n:
            chatter.append({"point": p, "alarms": len(xs), "repeats_within_a_minute": quick})
    standing = [{"point": x["point"], "limit": f"{x['limit_type']} {x['limit_value']:g}", "since": x["start"],
                 "hours": round(((x["end"] or b) - x["start"]).total_seconds() / 3600, 1)}
                for x in ex if ((x["end"] or b) - x["start"]) >= timedelta(hours=standing_h)]
    rate = len(ex) / hours if hours else 0
    worst = sorted(by_point.items(), key=lambda kv: -len(kv[1]))[:top]
    return result(round(rate, 2), target, "flood" if floods else "overloaded" if rate > target else None,
                  "each point going past an enabled DCS / AF alarm limit while its unit runs (#101) is an alarm; "
                  f"flood: over {flood} in 10 minutes; standing: on {standing_h:g} h or more; chattering: {chatter_n} or "
                  "more on one point within a minute of clearing", [x["point_id"] for x in ex][:20],
                  unit="alarms per hour", window=[since, until], alarms=len(ex), points_alarming=len(by_point),
                  flood_per_10min=flood, peak_10_min=max((f["alarms"] for f in floods), default=_peak(starts)), floods=_plain(floods[:10]),
                  chattering=chatter[:10], standing=_plain(sorted(standing, key=lambda s: -s["hours"])[:10]),
                  bad_actors=[{"point": p, "alarms": len(xs), "share": round(len(xs) / max(len(ex), 1), 3)} for p, xs in worst])


def _peak(starts: list[datetime]) -> int:
    best, i = 0, 0
    for j, t in enumerate(starts):
        while starts[i] < t - timedelta(minutes=10):
            i += 1
        best = max(best, j - i + 1)
    return best


def _run_units(conn, pack, scope: str | None) -> dict[str, dict]:
    from . import limits

    sig = {u: s for u, s in limits.run_signals(conn, pack, set(limits.units(conn).values())).items() if "point" in s}
    if scope not in (None, "facility"):
        keep = {scope} | {r["src"] for r in conn.execute("select src from edge where rel = 'part_of' and dst = %s", (scope,))}
        sig = {u: s for u, s in sig.items() if u in keep}
    if not sig:
        raise CalcError("no units with a run signal in that scope")
    return sig


def states(conn, pack, scope: str | None, since: str, until: str, step: int = 10, src=None) -> dict:
    """Each unit's state spans over the window."""
    from . import limits

    a, b = _window(since, until)
    sig = _run_units(conn, pack, scope)
    mode = limits.modes(conn, pack, set(sig), a, b, step, src)
    times = [a + timedelta(minutes=step * i) for i in range(len(next(iter(mode.values()))))]
    out = {}
    for u, m in mode.items():
        spans, s0 = [], 0
        for i in range(1, len(m) + 1):
            if i == len(m) or m[i] != m[s0]:
                spans.append({"state": str(m[s0]), "start": times[s0].isoformat(timespec="minutes"),
                              "end": times[min(i, len(times) - 1)].isoformat(timespec="minutes")})
                s0 = i
        out[_label(conn, u)] = {"id": u, "spans": spans, "running_share": round(float(np.mean(m == "running")), 3)}
    return {"window": [since, until], "step_minutes": step, "units": out}


def trip_analysis(conn, pack, scope: str | None, since: str, until: str, src=None) -> dict:
    s = states(conn, pack, scope, since, until, src=src)
    failed_min = settings.get(conn, "alarms.failed_start_minutes")
    rows = []
    for name, u in s["units"].items():
        sp = u["spans"]

        def before(i):  # the state before span i, looking past transitions
            return next((sp[j]["state"] for j in range(i - 1, -1, -1) if sp[j]["state"] != "transition"), None)

        stops = [x for i, x in enumerate(sp) if x["state"] == "stopped" and before(i) in ("running", "startup")]
        starts = [x for i, x in enumerate(sp) if x["state"] in ("startup", "running") and before(i) == "stopped"]
        failed = []
        for x in starts:
            end = datetime.fromisoformat(x["end"])
            after = next((y for y in sp if y["start"] >= x["end"] and y["state"] != "transition"), None)
            if after and after["state"] == "stopped" and (end - datetime.fromisoformat(x["start"])) <= timedelta(minutes=failed_min):
                failed.append(x["start"])
        gaps = [(datetime.fromisoformat(b_["start"]) - datetime.fromisoformat(a_["start"])).total_seconds() / 86400
                for a_, b_ in zip(stops, stops[1:])]
        rows.append({"unit": name, "id": u["id"], "stops": len(stops), "starts": len(starts), "failed_starts": len(failed),
                     "days_between_stops": round(float(np.median(gaps)), 1) if gaps else None,
                     "running_share": u["running_share"], "stop_times": [x["start"] for x in stops][:10]})
    rows.sort(key=lambda r: -r["stops"])
    total = sum(r["stops"] for r in rows)
    return result(total, None, "failed_starts" if any(r["failed_starts"] for r in rows) else None,
                  "stops and starts from each unit's run signal (#101 run modes), 10-minute steps; a failed start "
                  f"stops again within {failed_min} minutes", [r["id"] for r in rows][:20], unit="stops",
                  window=s["window"], units=rows[:30],
                  note="no first-out alarm in the data; the operator logs record one where Claude has read them")
