"""Slow drifts against a long baseline and a point's peers (P3.11a, #1020): the statistical detector the agentic watch
leans on for what wears and plugs over months.

A choke plugging with scale, a pump's wear rings opening up, a cooler fouling: each moves one point a few percent a
month while everything else holds, so the week's multivariate model learns it as normal and no limit is crossed. The
detector compares each point's level now with where it sat in the first third of the last `drift.lookback_days`, as a
share of that level, and sets the change against its peers: the points with the same measurement role on the same
class of equipment across the asset (P1.4), so a gas well is judged against the other wells' rates and a pump's
discharge pressure against the other pumps', with no tag names. What the peers share (a field declining, the season)
is not a drift; what a point does on its own is. The rules of `clusters.drifted` apply: a change made mostly in one
day is a step, not a drift, and it must already show in the week before. It must also still be on its way (the middle
of the stretch sits between the baseline and now), so a point back to normal after an upset early in the baseline has
not drifted. It runs in the daily tier, once a day after midnight, not in every scan.

- **Daily levels.** Reading months of history for every point every day would cost the instance its memory, so each
  point's level is kept a day at a time in `point_daily` (the median of its hourly values: a pump on standby for part
  of a day still reads its running level), filled a week at a time for the days not yet kept and pruned past
  `KEEP_DAYS`. Days of a stopped or shut in unit are left out (`IDLE`).
- **Moved by hand.** A drift on equipment whose own choke or valve the operators moved over the same months was
  deliberate (a choke trimmed, a well brought back), so it is not raised; the watch reads the operator log for those.
- **Peers.** A point needs at least `MIN_PEERS` peers to be judged: one with none can't say what is its own.
  Positions, running status, speed and lab values are not read for drifts, nor the weather.

A detection is raised on the point (`peer_drift_down` or `_up`) for the day it measured and carried on each day it is
found again, with its baseline, its level now and how far its peers moved as evidence. The watch reads it like any
other detection, so it explains a drift instead of having to find one.
"""

import warnings
from datetime import date, datetime, timedelta

import numpy as np

from ..analysis import clusters, limits
from ..core import db, detectors, settings
from ..core import roles as vocabulary
from ..core.detectors import POINTS, STATISTICAL
from . import sre

READ_STEP = 60  # minutes between the values a day's level is worked out from
CHUNK_DAYS = 7  # days read from the historian at once when filling levels not yet kept
IDLE = 0.5  # a level under this share of what the point reads running: its equipment stopped or shut in
MIN_PEERS = 1  # a pump with one running peer and a spare that stands is still judged
KEEP_DAYS = 400
NOT_READ = {"position", "state", "composition", "speed"}  # set by hand, on or off, lab values, speed follows load
# The weather drifts with the season on its own, and a wellhead's temperature follows it and the well's rate, each well
# by its own amount: a rate or pressure drifting says it first.
NOT_EQUIPMENT = {"ambient_temperature", "wellhead_temperature"}
SET_BY_HAND = {"choke_position", "valve_position", "anti_surge_valve_position"}
ROLES = "select 1 from measurement_role limit 1"


def watched(conn) -> tuple[list[dict], dict[str, tuple], dict[str, str], dict[str, list[str]]]:
    """The points read for drifts, each one's peer group (its role on its class of equipment), the asset each point
    measures, and the positions operators set on each asset."""
    from ..sources import timeseries

    rows = conn.execute("select point_id, role, asset from measurement_role where asset is not null").fetchall()
    assets = {r["asset"] for r in rows}
    cls = detectors.classes_of(conn, assets)
    wells = {r["fuzer_id"] for r in conn.execute("select fuzer_id from entity where kind = 'well' and fuzer_id = any(%s)",
                                                  (list(assets),))}
    peers, asset, hand = {}, {}, {}
    for r in rows:
        role = vocabulary.ROLES.get(r["role"])
        if role is None:
            continue
        asset[r["point_id"]] = r["asset"]
        if r["role"] in SET_BY_HAND:
            hand.setdefault(r["asset"], []).append(r["point_id"])
        if role.quantity in NOT_READ or r["role"] in NOT_EQUIPMENT:
            continue
        peers[r["point_id"]] = (r["role"], "well" if r["asset"] in wells else cls.get(r["asset"]))
    pts = sorted(timeseries._points(conn, list(peers)).values(), key=lambda p: p["id"])
    return pts, peers, asset, hand


def _last_day(until: datetime) -> date:
    """The last whole day before `until`."""
    return until.date() - timedelta(days=1)


def keep_levels(conn, ids: list[str], first: date, last: date, src) -> int:
    """Fill `point_daily` for the days in [first, last] it doesn't hold for every one of these points, a week at a time;
    returns the days read. A day with no values is kept as null, so it is not read again."""
    from ..analysis import studies
    from ..sources import timeseries

    full = {r["day"] for r in conn.execute(
        "select day from point_daily where point_id = any(%s) and day between %s and %s group by day "
        "having count(*) >= %s", (ids, first, last, len(ids)))}
    todo = [d for i in range((last - first).days + 1) if (d := first + timedelta(days=i)) not in full]
    if not todo:
        return 0
    pts = list(timeseries._points(conn, ids).values())
    per_day = 1440 // READ_STEP
    i = 0
    while i < len(todo):
        a = todo[i]
        b = min(a + timedelta(days=CHUNK_DAYS - 1), last)
        chunk = [d for d in todo[i:] if d <= b]
        i += len(chunk)
        t0 = datetime.combine(a, datetime.min.time())
        t1 = datetime.combine(b + timedelta(days=1), datetime.min.time()) - timedelta(minutes=READ_STEP)
        _, X = studies.matrix(pts, t0, t1, READ_STEP, src, cache=False)
        n = (b - a).days + 1
        with warnings.catch_warnings():  # a day with no values is kept as none
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(X[:n * per_day].reshape(n, per_day, len(pts)), axis=1)
        rows = [(p["id"], a + timedelta(days=k), None if np.isnan(med[k, j]) else float(med[k, j]))
                for k in range(n) if a + timedelta(days=k) in chunk for j, p in enumerate(pts)]
        conn.execute("delete from point_daily where point_id = any(%s) and day = any(%s)", (ids, chunk))
        db.copy_rows(conn, "point_daily", ["point_id", "day", "value"], rows)
    conn.execute("delete from point_daily where day < %s", (last - timedelta(days=KEEP_DAYS),))
    return len(todo)


def levels(conn, ids: list[str], first: date, last: date) -> tuple[np.ndarray, np.ndarray]:
    """(days, values): the kept daily levels, a row per day from first to last and a column per point (nan: none)."""
    days = np.arange(np.datetime64(first, "D"), np.datetime64(last + timedelta(days=1), "D")).astype("datetime64[m]")
    col = {i: j for j, i in enumerate(ids)}
    X = np.full((len(days), len(ids)), np.nan)
    for r in conn.execute("select point_id, array_agg(day - %s::date) as at, array_agg(value) as v from point_daily "
                          "where point_id = any(%s) and day between %s and %s and value is not null group by point_id",
                          (first, ids, first, last)):
        X[r["at"], col[r["point_id"]]] = r["v"]
    return days, X


def moved_by_hand(X: np.ndarray, ids: list[str]) -> set[str]:
    """The position points (columns of daily levels) that operators moved: their running level changed by more than
    2% of where it sits, or half a unit."""
    out = set()
    for j, i in enumerate(ids):
        v = X[:, j]
        if np.isnan(v).all():
            continue
        v = v[np.abs(v) >= IDLE * abs(float(np.nanpercentile(v, 99)))]  # closed for a shut in: not a move
        if len(v) and float(np.nanmax(v) - np.nanmin(v)) > max(0.02 * abs(float(np.nanmedian(v))), 0.5):
            out.add(i)
    return out


def find(conn, pack, since: datetime, until: datetime, src) -> list[dict]:
    """The drifts found on the last whole day before `until`, when [since, until] holds a midnight (as a window
    detector reads it, so the replay scores it as the schedule runs it)."""
    if not sre._daily(since, until):
        return []
    pts, peers, asset, hand = watched(conn)
    if not pts:
        return []
    days = int(settings.get(conn, "drift.lookback_days"))
    last = _last_day(until)
    first = last - timedelta(days=days)
    ids = [p["id"] for p in pts]
    positions = sorted({i for a in {asset[p] for p in ids} for i in hand.get(a, [])})
    keep_levels(conn, ids + positions, first, last, src)
    conn.commit()
    times, X = levels(conn, ids, first, last)
    found = clusters.drifted(pts, times, X, datetime.combine(last, datetime.min.time()), days, 1,
                             float(settings.get(conn, "drift.min_change")), float(settings.get(conn, "drift.peer_z")),
                             None, peers, IDLE, MIN_PEERS, progressive=True)
    if not found:
        return []
    on = {asset[d["point_id"]] for d in found}
    pos = [i for a in on for i in hand.get(a, [])]
    moved = {asset[i] for i in moved_by_hand(levels(conn, pos, first, last)[1], pos)} if pos else set()
    unit = limits.units(conn, [d["point_id"] for d in found])
    day = datetime.combine(last, datetime.min.time())
    out = []
    for d in found:
        if d["stepwise"] or asset[d["point_id"]] in moved:
            continue  # held between moves, or its choke or valve moved: set by hand
        role, cls = peers[d["point_id"]]
        own = d["change"] - d["peers_change"]
        out.append(sre._det({"point": d["point"], "id": d["point_id"]}, unit.get(d["point_id"]), day, until,
                            "peer_drift_up" if own > 0 else "peer_drift_down", "peer_drifts", own,
                            {"baseline": d["baseline"], "now": d["now"], "change": d["change"],
                             "peers_change": d["peers_change"], "peers": d["peers"], "role": role, "class": cls,
                             "baseline_from": d["since"], "days": days}))
    return out


find.__name__ = "peer_drifts"  # as the replay and its scores name it


@detectors.register("peer_drifts", title="A point drifting from its own long baseline and from its peers",
                    kind=STATISTICAL, cadence="daily", raises=("upset",), eval="replay:peer_drifts",
                    needs={"historian points": POINTS, "measurement roles": ROLES},
                    params=("drift.lookback_days", "drift.min_change", "drift.peer_z"),
                    quieter={"drift.min_change": "up", "drift.peer_z": "up"}, found=lambda out: out.get("new", 0))
def peer_drifts(conn, pack=None, since: str | None = None, until: str | None = None, src=None) -> dict:
    """Once a day: each point's level against where it sat months before and against its peers (the same measurement
    role on the same class of equipment); one that moved on its own, slowly and not by hand, is a drift. Each day
    since the last run is read (a missed day catches up), and a drift found again carries on the detection before."""
    from ..sources.timeseries import provider

    src = src or provider()
    b = datetime.fromisoformat(until) if until else datetime.now().replace(minute=0, second=0, microsecond=0)
    a = datetime.fromisoformat(since) if since else b - timedelta(days=1)
    midnight = a if a.time() == datetime.min.time() else datetime.combine(a.date() + timedelta(days=1), datetime.min.time())
    totals = {"new": 0, "extended": 0, "days": 0}
    while midnight < b:  # each midnight in the window, read as the window up to it
        day_end = midnight + timedelta(minutes=1)
        found = find(conn, pack, midnight, day_end, src)
        # The day before's detection is carried on: seen up to its own day's end, a day before this window starts.
        for k, v in sre.store(conn, found, None, (day_end - timedelta(days=1), day_end)).items():
            totals[k] += v
        totals["days"] += 1
        conn.commit()
        midnight += timedelta(days=1)
    return {"since": a.isoformat(timespec="minutes"), "until": b.isoformat(timespec="minutes"), **totals}
