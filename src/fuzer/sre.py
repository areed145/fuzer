"""Agentic SRE, detection (#96): a run every 6 hours that finds upsets in the process data itself, with
no Claude. Grouping into upsets, diagnosis and hand-off come later.

Each detector is a window function `fn(conn, pack, since, until, src) -> [detection]`, so the replay
harness (#103) scores it exactly as the schedule runs it, and it can move to streaming later. A detection
is {point, point_id, unit_id, start, end, key, detector, magnitude, evidence}; `point` is the PI point
name, `key` what was found on it, `end` None while it is still going at the window's end.

- excursions: outside alarm limits, IOWs or the learned normal range while the unit runs (#101).
- stops: a unit with its own run signal going from running to stopped (a trip, a stop).
- steps: a point stepping (signals.detect_changes) on a running unit.
- drifts: once a day, a slow climb in what wears and fouls (vibration, bearing and discharge
  temperatures, cooler outlets, differential pressure) over the last month.
- instruments: frozen, stuck, noisy or out-of-range readings (signals.health); instrument work, not
  upsets.
- models: once a day, equipment against its curve (calcs: pump head, choke Cv) and gas lifted wells
  against theirs (a valve failed open).

`scan` runs them over the time since the last run (with an hour's overlap so nothing spanning the
boundary is split), stores detections in `detection` and extends the ones still going instead of raising
them again.
"""

import re
import time
from datetime import datetime, timedelta

import numpy as np
from psycopg.types.json import Jsonb

from . import calcs, limits, signals

STEP = 10  # minutes between values the detectors read
OVERLAP = timedelta(hours=1)
LOOKBACK = timedelta(hours=24)  # steps judge the window against how the point moved the day before
DRIFT_DAYS, DRIFT_STEP = 30, 180  # a month at 3-hour steps: long enough for fouling to clear the daily swing
DRIFT_RISE = 0.05  # and up by at least this share of where it started
DRIFTING = re.compile(r"Vibration|Bearing Temp|Discharge Temp|Outlet Temp|Differential Pressure", re.IGNORECASE)
LEARNED_MINUTES = 60  # a learned-range excursion must last this long (no source set that limit)
MERGE = timedelta(minutes=30)  # the same finding again within this long carries on the one before


def _det(p: dict, unit: str | None, start, end, key: str, detector: str, magnitude=None, evidence=None) -> dict:
    return {"point": p["point"], "point_id": p["id"], "unit_id": unit, "start": start, "end": end, "key": key,
            "detector": detector, "magnitude": None if magnitude is None else round(float(magnitude), 4),
            "evidence": evidence or {}}


def _points(conn) -> list[dict]:
    return limits._all_points(conn)


def _grid(a: datetime, b: datetime, step: int = STEP) -> list[datetime]:
    return [a + timedelta(minutes=step * i) for i in range(int((b - a).total_seconds() // 60 // step) + 1)]


# ---- detectors ------------------------------------------------------------------------------------

def excursions(conn, pack, since, until, src) -> list[dict]:
    pids = [r["point_id"] for r in conn.execute(
        "select distinct point_id from point_limit union select distinct point_id from point_normal order by 1")]
    out = []
    for x in limits.excursions(conn, pack, pids, since, until, src=src):
        if x["basis"] == "learned" and x["minutes_outside"] < LEARNED_MINUTES and x["end"] is not None:
            continue  # a brief wander outside a range nobody set
        out.append(_det({"point": x["point"], "id": x["point_id"]}, x["unit_id"], x["start"], x["end"],
                        f"{x['basis']}:{x['limit_type']}", "excursions", x["peak"],
                        {"limit": x["limit_value"], "record": x["record"], "minutes_outside": x["minutes_outside"]}))
    return out


def stops(conn, pack, since, until, src) -> list[dict]:
    from .timeseries import _point

    sig = {u: s for u, s in limits.run_signals(conn, pack, set(limits.units(conn).values())).items() if "point" in s}
    if not sig:
        return []
    times = _grid(since, until)
    mode = limits.modes(conn, pack, set(sig), since - timedelta(minutes=STEP), until, STEP, src)
    out = []
    for u, s in sig.items():
        m = mode[u]
        running = (m == "running") | (m == "startup")
        stopped = m == "stopped"
        for i in range(1, len(m)):
            if stopped[i] and running[:i].any() and not stopped[i - 1]:
                back = np.flatnonzero(running[i:])
                end = times[min(i - 1 + back[0], len(times) - 1)] if len(back) else None
                p = _point(conn, s["point"]["id"]) or s["point"]
                out.append(_det(p, u, times[i - 1], end, "stopped", "stops", None, {"run_signal_below": s["off"]}))
    return out


def _running(conn, pack, pts, a, b, src) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    """Per point, whether its unit runs at each step (unknown counts as running); and each point's unit."""
    unit = limits.units(conn, [p["id"] for p in pts])
    mode = limits.modes(conn, pack, set(unit.values()), a, b, STEP, src)
    n = len(_grid(a, b))
    run = {}
    for p in pts:
        m = mode.get(unit.get(p["id"]), np.full(n, "unknown", dtype=object))
        run[p["id"]] = (m == "running") | (m == "unknown")
    return run, unit


def steps(conn, pack, since, until, src) -> list[dict]:
    pts = _points(conn)
    a = since - LOOKBACK
    times = _grid(a, until)
    run, unit = _running(conn, pack, pts, a, until, src)
    out = []
    for p in pts:
        try:
            v = signals._read(src, p, a, until, STEP)
        except Exception:  # one unreadable point shouldn't stop the run
            continue
        r = run[p["id"]]
        for c in signals.detect_changes(times, v, STEP):
            at = datetime.fromisoformat(c["start"])
            i = times.index(at) if at in times else None
            if c["kind"] != "step" or at < since or i is None or not r[max(0, i - 18):i + 18].all():
                continue  # only steps in this window, on a unit running either side (a stop is `stops`)
            out.append(_det(p, unit.get(p["id"]), at, None, "step", "steps", c["after"] - c["before"],
                            {"before": c["before"], "after": c["after"], "score": c["score"]}))
    return out


def _daily(since, until) -> bool:
    """Once a day: in the window holding midnight."""
    return any(t.hour == 0 and t.minute == 0 for t in _grid(since, until - timedelta(minutes=1), 60))


def drifts(conn, pack, since, until, src) -> list[dict]:
    if not _daily(since, until):
        return []
    pts = [p for p in _points(conn) if DRIFTING.search(p["description"] or "")]
    a = until - timedelta(days=DRIFT_DAYS)
    times = _grid(a, until, DRIFT_STEP)
    out = []
    for p in pts:
        try:
            v = signals._read(src, p, a, until, DRIFT_STEP)
        except Exception:
            continue
        for c in signals.detect_changes(times, v, DRIFT_STEP, window_minutes=1440):
            if c["kind"] != "drift" or c["after"] - c["before"] < DRIFT_RISE * abs(c["before"]) \
                    or c["end"] < (until - timedelta(days=1)).isoformat():
                continue  # climbing, by enough, and still at the end of the window
            out.append(_det(p, None, since, None, "drift_up", "drifts", c["per_day"],
                            {"from": c["start"], "before": c["before"], "now": c["after"], "per_day": c["per_day"]}))
    unit = limits.units(conn, [d["point_id"] for d in out])
    return [d | {"unit_id": unit.get(d["point_id"])} for d in out]


def _set_by_hand(pack) -> list[re.Pattern]:
    """Positions operators set (chokes, valves): they sit still or at an end on purpose."""
    return [re.compile(x) for x in limits.config(pack).get("not_learned", [])]


def instruments(conn, pack, since, until, src) -> list[dict]:
    skip = _set_by_hand(pack)
    pts = [p for p in _points(conn) if not any(x.search(p["point"]) or x.search(p["description"] or "") for x in skip)]
    times = _grid(since, until)
    base_a = since - timedelta(days=2)
    run, unit = _running(conn, pack, pts, since, until, src)
    out = []
    for p in pts:
        try:
            v = signals._read(src, p, since, until, STEP)
            base = signals._read(src, p, base_a, since - timedelta(minutes=STEP), STEP)
        except Exception:
            continue
        lo, hi = signals.RANGES.get(p["uom"] or "", (None, None))
        for f in signals.health(times, v, STEP, base, run[p["id"]], lo, hi):
            end = datetime.fromisoformat(f["end"])
            out.append(_det(p, unit.get(p["id"]), datetime.fromisoformat(f["start"]), None if end >= until else end,
                            f"instrument:{f['check']}", "instruments", f["value"], {"usual": f["expected"]}))
    return out


def models(conn, pack, since, until, src) -> list[dict]:
    """Equipment against its curve, once a day; a detection while the check flags it."""
    if not _daily(since, until):
        return []
    from .timeseries import _point

    out, when = [], until.isoformat(timespec="minutes")
    first = {}  # a unit's first PI point, to hang the detection on
    for r in conn.execute("select t.src, min(t.dst) as pid from edge t where t.rel = 'has_timeseries' group by t.src"):
        first[r["src"]] = r["pid"]
    checks = [("pump_curve", r["fuzer_id"], {"days": 60}) for r in conn.execute(
        "select distinct e.fuzer_id from equipment_curve c join edge i on i.dst = c.model_id and i.rel = 'instance_of' "
        "join edge x on x.src = i.src and x.rel = 'installed_at' and x.valid_to is null "
        "join entity e on e.fuzer_id = x.dst where c.curve = 'pump'")]
    checks += [("gas_lift", r["dst"], {}) for r in conn.execute(
        "select distinct dst from edge where rel = 'lift_gas_to' and dst in (select fuzer_id from entity where kind = 'well')")]
    for check, fid, kw in checks:
        try:
            res = calcs.CHECKS[check]["fn"](conn, fid, until=when, **kw)
        except calcs.CalcError:
            continue
        if res["flag"] in (None, "more_gas", "less_gas") or fid not in first:  # an opportunity, not an upset
            continue
        pid = next((s for s in res["sources"] if s.startswith("fz-timeseries")), first[fid])
        p = _point(conn, pid)
        if p:
            out.append(_det(p, fid, since, None, f"{check}:{res['flag']}", "models", res["deviation"],
                            {k: res[k] for k in ("value", "expected", "basis")}))
    return out


DETECTORS = {"excursions": excursions, "stops": stops, "steps": steps, "drifts": drifts, "instruments": instruments,
             "models": models}


def detect(conn, pack, since: datetime, until: datetime, src=None, only: list[str] | None = None) -> list[dict]:
    """Every detector over one window."""
    from .timeseries import provider

    src = src or provider()
    out = []
    for name, fn in DETECTORS.items():
        if only is None or name in only:
            out += fn(conn, pack, since, until, src)
    return sorted(out, key=lambda d: (d["start"], d["point"]))


# ---- the scheduled run ----------------------------------------------------------------------------

def watermark(conn) -> datetime | None:
    r = conn.execute("select max(until) as t from sre_run where status = 'done'").fetchone()
    return r["t"]


def store(conn, found: list[dict], run_id: int) -> dict:
    """Add detections, or extend the open one with the same point, detector and key."""
    new = extended = 0
    for d in found:
        prev = conn.execute(
            """
            select id from detection where point_id = %s and detector = %s and key = %s
              and ("end" is null or "end" >= %s) and start <= %s order by start desc limit 1
            """, (d["point_id"], d["detector"], d["key"], d["start"] - MERGE, d["start"] + MERGE)).fetchone()
        if prev:
            conn.execute('update detection set "end" = %s, last_run = %s, magnitude = coalesce(%s, magnitude) where id = %s',
                         (d["end"], run_id, d["magnitude"], prev["id"]))
            extended += 1
        else:
            conn.execute(
                """
                insert into detection (point_id, unit_id, detector, key, start, "end", magnitude, evidence, first_run, last_run)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (d["point_id"], d["unit_id"], d["detector"], d["key"], d["start"], d["end"], d["magnitude"],
                      Jsonb(_plain(d["evidence"])), run_id, run_id))
            new += 1
    return {"new": new, "extended": extended}


def _plain(x):
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    return x.isoformat() if isinstance(x, datetime) else x


def scan(conn, pack, since: str | None = None, until: str | None = None, src=None, max_days: int = 7) -> dict:
    """One scheduled run: from the last run's end (less an hour's overlap) to `until` (default: now, or
    the end of the data's today for a fake plant), in 6-hour steps so a missed run catches up. The first
    run covers the last day. At most `max_days` per call."""
    from . import operations

    end = datetime.fromisoformat(until) if until else min(datetime.now().replace(second=0, microsecond=0),
                                                          datetime.fromisoformat(operations.as_of(conn)) + timedelta(days=1))
    mark = watermark(conn)
    start = datetime.fromisoformat(since) if since else (mark - OVERLAP if mark else end - timedelta(days=1))
    start = max(start, end - timedelta(days=max_days))
    if start >= end:
        return {"since": start.isoformat(), "until": end.isoformat(), "windows": 0, "new": 0, "extended": 0}
    run_id = conn.execute("insert into sre_run (since, until, status) values (%s, %s, 'running') returning id",
                          (start, end)).fetchone()["id"]
    conn.commit()
    t0, totals, windows = time.perf_counter(), {"new": 0, "extended": 0}, 0
    try:
        a = start
        while a < end:
            b = min(a + timedelta(hours=6), end)
            for k, v in store(conn, detect(conn, pack, a, b, src), run_id).items():
                totals[k] += v
            windows += 1
            a = b
        conn.execute("update sre_run set status = 'done', finished_at = now(), detections = %s, seconds = %s where id = %s",
                     (totals["new"], round(time.perf_counter() - t0, 1), run_id))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.execute("update sre_run set status = 'failed', finished_at = now() where id = %s", (run_id,))
        conn.commit()
        raise
    return {"run": run_id, "since": start.isoformat(timespec="minutes"), "until": end.isoformat(timespec="minutes"),
            "windows": windows, **totals, "seconds": round(time.perf_counter() - t0, 1)}


def recent(conn, since: str | None = None, detector: str | None = None, limit: int = 200) -> list[dict]:
    """Stored detections, newest first, with the point and unit names."""
    return [_plain(dict(r)) for r in conn.execute(
        """
        select d.*, p.label as point, u.label as unit from detection d
        left join entity_label p on p.fuzer_id = d.point_id left join entity_label u on u.fuzer_id = d.unit_id
        where (%(since)s::timestamp is null or coalesce(d."end", 'infinity') >= %(since)s::timestamp)
          and (%(det)s::text is null or d.detector = %(det)s)
        order by d.start desc limit %(limit)s
        """, {"since": since, "det": detector, "limit": limit})]
