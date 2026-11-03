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

from ..analysis import calcs, limits, signals
from ..core.jsonsafe import plain as _plain
from ..graph.topology import HOPS, LINKS, TRAIN, _near, topology  # noqa: F401  (sre.topology, as before)

STEP = 10  # minutes between values the detectors read
OVERLAP = timedelta(hours=1)
LOOKBACK = timedelta(hours=24)  # steps judge the window against how the point moved the day before
DRIFT_DAYS, DRIFT_STEP = 30, 180  # a month at 3-hour steps: long enough for fouling to clear the daily swing
DRIFT_RISE = 0.05  # and up by at least this share of where it started
DRIFTING = re.compile(r"Vibration|Bearing Temp|Discharge Temp|Outlet Temp|Differential Pressure", re.IGNORECASE)
LEARNED_MINUTES = 60  # a learned-range excursion must last this long (no source set that limit)
SPE_MINUTES = 120  # a multivariate anomaly lasts this long before it is raised
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
    from ..sources.timeseries import _point

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
    from ..sources.timeseries import _point

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


def hydraulics(conn, pack, since, until, src) -> list[dict]:
    """Once a day: each jacket well's wellhead-to-header pressure drop still rising (a flowline waxing
    or scaling, #107)."""
    if not _daily(since, until):
        return []
    from ..analysis import hydraulics as h

    out = []
    for r in conn.execute("select distinct e.src as well from edge e join entity w on w.fuzer_id = e.src and w.kind = 'well' "
                          "join edge c on c.dst = e.src and c.rel = 'part_of' join entity_label l on l.fuzer_id = c.src "
                          "and l.label like '%%-HCV-%%' where e.rel = 'part_of'"):
        try:
            d = h.dp_drift(conn, r["well"], 45, until.isoformat(timespec="minutes"), src)
        except calcs.CalcError:
            continue
        if d["flag"] == "dp_rising":
            p = {"point": _point_name(conn, d["sources"][1]), "id": d["sources"][1]}
            out.append(_det(p, r["well"], since, None, "dp_rising", "hydraulics", d["deviation"],
                            {"since": d["rising_since"], "dp_now": d["value"], "dp_before": d["expected"]}))
    return out


def multivariate(conn, pack, since, until, src) -> list[dict]:
    """Once a day: each process area's PCA model of the week before against the last day; a stretch the
    model can't explain (squared prediction error past its 99.9th percentile for `SPE_MINUTES`) on the point
    contributing most (#106)."""
    if not _daily(since, until):
        return []
    from ..analysis import clusters

    out = []
    areas = conn.execute(
        """
        select a.fuzer_id, count(distinct t.dst) as n from entity a
        join edge p on p.dst = a.fuzer_id and p.rel = 'part_of' join edge t on t.src = p.src and t.rel = 'has_timeseries'
        where a.kind = 'area' group by a.fuzer_id having count(distinct t.dst) between 10 and 300
        """).fetchall()
    day0 = until - timedelta(days=1)
    for a in areas:
        try:
            r = clusters.spe(conn, a["fuzer_id"], ((day0 - timedelta(days=7)).isoformat(), day0.isoformat()),
                             (day0.isoformat(), until.isoformat()), src=src, min_minutes=SPE_MINUTES)
        except calcs.CalcError:
            continue
        for x in r["anomalies"]:
            top = x["top_points"][0]
            out.append(_det({"point": top["point"], "id": top["point_id"]}, None, datetime.fromisoformat(x["start"]),
                            datetime.fromisoformat(x["end"]), "spe", "multivariate", x["peak_spe"],
                            {"area": a["fuzer_id"], "top_points": x["top_points"], "limit": r["limit"]}))
    unit = limits.units(conn, [d["point_id"] for d in out])
    return [d | {"unit_id": unit.get(d["point_id"])} for d in out]


def _point_name(conn, pid: str) -> str:
    r = conn.execute("select label from entity_label where fuzer_id = %s", (pid,)).fetchone()
    return r["label"] if r else pid


DETECTORS = {"excursions": excursions, "stops": stops, "steps": steps, "drifts": drifts, "instruments": instruments,
             "models": models, "hydraulics": hydraulics, "multivariate": multivariate}


def detect(conn, pack, since: datetime, until: datetime, src=None, only: list[str] | None = None) -> list[dict]:
    """Every detector over one window."""
    from ..sources.timeseries import provider

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


def store(conn, found: list[dict], run_id: int, window: tuple[datetime, datetime] | None = None) -> dict:
    """Add detections, or extend the open one with the same point, detector and key that the window just
    before saw (one left open by a scan that didn't reach this window isn't carried on)."""
    new = extended = 0
    a, b = window or (None, None)
    for d in found:
        prev = conn.execute(
            """
            select id from detection where point_id = %s and detector = %s and key = %s
              and ("end" is null or "end" >= %s) and start <= %s
              and (%s::timestamp is null or seen_until is null or seen_until >= %s::timestamp)
            order by start desc limit 1
            """, (d["point_id"], d["detector"], d["key"], d["start"] - MERGE, d["start"] + MERGE, a,
                  a - OVERLAP if a else None)).fetchone()
        if prev:
            conn.execute('update detection set "end" = %s, last_run = %s, magnitude = coalesce(%s, magnitude), '
                         "seen_until = %s where id = %s", (d["end"], run_id, d["magnitude"], b, prev["id"]))
            extended += 1
        else:
            conn.execute(
                """
                insert into detection (point_id, unit_id, detector, key, start, "end", magnitude, evidence, first_run, last_run,
                                       seen_until)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (d["point_id"], d["unit_id"], d["detector"], d["key"], d["start"], d["end"], d["magnitude"],
                      Jsonb(_plain(d["evidence"])), run_id, run_id, b))
            new += 1
    return {"new": new, "extended": extended}


def scan(conn, pack, since: str | None = None, until: str | None = None, src=None, max_days: int = 7) -> dict:
    """One scheduled run: from the last run's end (less an hour's overlap) to `until` (default: now, or
    the end of the data's today for a fake plant), in 6-hour steps so a missed run catches up. The first
    run covers the last day. At most `max_days` per call."""
    from ..graph import operations

    end = datetime.fromisoformat(until) if until else min(datetime.now().replace(second=0, microsecond=0),
                                                          datetime.fromisoformat(operations.as_of(conn)) + timedelta(days=1))
    mark = watermark(conn)
    start = datetime.fromisoformat(since) if since else (mark - OVERLAP if mark else end - timedelta(days=1))
    start = max(start, end - timedelta(days=max_days))
    if start >= end:
        return {"since": start.isoformat(), "until": end.isoformat(), "windows": 0, "new": 0, "extended": 0, "upsets": 0}
    run_id = conn.execute("insert into sre_run (since, until, status) values (%s, %s, 'running') returning id",
                          (start, end)).fetchone()["id"]
    conn.commit()
    t0, totals, windows = time.perf_counter(), {"new": 0, "extended": 0}, 0
    try:
        a = start
        while a < end:
            b = min(a + timedelta(hours=6), end)
            for k, v in store(conn, detect(conn, pack, a, b, src), run_id, (a, b)).items():
                totals[k] += v
            windows += 1
            a = b
        totals["upsets"] = regroup(conn, start)["upsets"]
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


# ---- grouping into upsets (#96b) ------------------------------------------------------------------

GAP = timedelta(hours=2)  # a detection joins an upset starting within this long of its latest detection
SLOW = {"drifts", "models", "hydraulics"}  # multivariate findings group with the upsets they belong to  # days to months: one upset per unit and finding, never joined to others
FIRST = {"stops": 0, "excursions": 1, "steps": 2}  # at the same time, the likelier first cause


def group_detections(found: list[dict], adj: dict[str, set[str]]) -> list[dict]:
    """Detections into upsets: close in time (each joins while within GAP of the upset's latest start) and
    connected in the plant model. Bad instruments and slow findings stay apart, one per unit and finding.
    Each upset: {kind, detections, units, first (the earliest detection: its likely first cause), start,
    end}."""
    groups: list[dict] = []
    open_: list[dict] = []
    cache: dict = {}
    keyed: dict[tuple, dict] = {}
    for d in sorted(found, key=lambda d: (d["start"], FIRST.get(d["detector"], 9))):
        if d["detector"] == "instruments" or d["detector"] in SLOW:
            kind = "instrument" if d["detector"] == "instruments" else "slow"
            k = (kind, d.get("unit_id") or d["point"], d["key"])
            g = keyed.get(k)
            if g is None:
                g = keyed[k] = {"kind": kind, "detections": []}
                groups.append(g)
            g["detections"].append(d)
            continue
        open_ = [g for g in open_ if d["start"] - g["last"] <= GAP]
        home = next((g for g in open_ if any(_near(adj, d.get("unit_id"), x.get("unit_id"), cache)
                                             or d["point"] == x["point"] for x in g["detections"])), None)
        if home is None:
            home = {"kind": "process", "detections": [], "last": d["start"]}
            groups.append(home)
            open_.append(home)
        home["detections"].append(d)
        home["last"] = max(home["last"], d["start"])
    out = []
    for g in groups:
        ds = g["detections"]
        ends = [x["end"] for x in ds]
        # a multivariate finding names its biggest contributor, not a cause: the first cause is the earliest other one
        first = next((x for x in ds if x["detector"] != "multivariate"), ds[0])
        out.append({"kind": g["kind"], "detections": ds, "first": first, "start": ds[0]["start"],
                    "end": None if any(e is None for e in ends) else max(ends),
                    "units": list(dict.fromkeys(x.get("unit_id") for x in ds if x.get("unit_id")))})
    return out


def regroup(conn, since: datetime) -> dict:
    """Group the detections going on after `since` (less a day) into upsets. An upset keeps its id, and
    the status a person gave it, when most of its detections stay together; upsets left with no
    detections and no decision go."""
    from collections import Counter

    rows = [dict(r) for r in conn.execute(
        """
        with recent as (select * from detection where coalesce("end", 'infinity') >= %(s)s)
        select d.*, l.label as point from detection d left join entity_label l on l.fuzer_id = d.point_id
        where d.id in (select id from recent)
           or d.upset_id in (select upset_id from recent where upset_id is not null)  -- all of each upset it touches
        order by d.start
        """, {"s": since - timedelta(days=1)})]
    groups = group_detections(rows, topology(conn))
    used: set[int] = set()
    for g in groups:
        old = Counter(d["upset_id"] for d in g["detections"] if d["upset_id"] and d["upset_id"] not in used)
        uid = old.most_common(1)[0][0] if old else None
        vals = (g["kind"], g["start"], g["end"], g["first"]["id"], g["first"]["unit_id"], g["units"], len(g["detections"]))
        if uid:
            conn.execute('update upset set kind = %s, start = %s, "end" = %s, first_detection = %s, first_unit = %s, units = %s, '
                         "detections = %s, updated_at = now() where id = %s", (*vals, uid))
        else:
            uid = conn.execute('insert into upset (kind, start, "end", first_detection, first_unit, units, detections) '
                               "values (%s, %s, %s, %s, %s, %s, %s) returning id", vals).fetchone()["id"]
        used.add(uid)
        conn.execute("update detection set upset_id = %s where id = any(%s)", (uid, [d["id"] for d in g["detections"]]))
    conn.execute("delete from upset u where status = 'detected' and not exists (select 1 from detection d where d.upset_id = u.id)")
    conn.commit()
    return {"upsets": len(groups), "process": sum(g["kind"] == "process" for g in groups)}


def upsets(conn, since: str | None = None, status: str | None = None, kind: str | None = "process",
           limit: int = 50, ids: list[int] | None = None) -> list[dict]:
    """Upsets, newest first: first cause (unit and detection), units, size and status."""
    return [_plain(dict(r)) for r in conn.execute(
        """
        select u.id, u.kind, u.status, u.start, u."end", u.detections, u.work_item_id, u.priority_usd,
               l.label as first_unit,
               d.detector as first_detector, d.key as first_key, p.label as first_point,
               (select array_agg(x.label order by x.label) from entity_label x where x.fuzer_id = any(u.units)) as units
        from upset u left join entity_label l on l.fuzer_id = u.first_unit
        left join detection d on d.id = u.first_detection left join entity_label p on p.fuzer_id = d.point_id
        where (%(since)s::timestamp is null or coalesce(u."end", 'infinity') >= %(since)s::timestamp)
          and (%(status)s::text is null or u.status = %(status)s) and (%(kind)s::text is null or u.kind = %(kind)s)
          and (%(ids)s::bigint[] is null or u.id = any(%(ids)s))
        order by u.start desc limit %(limit)s
        """, {"since": since, "status": status, "kind": kind, "limit": limit, "ids": ids})]


def upset_detail(conn, upset_id: int, limit: int = 60) -> dict | None:
    """One upset: its detections in time order (the first is the likely first cause) and how many on
    each unit."""
    u = next(iter(upsets(conn, kind=None, ids=[upset_id])), None)
    if u is None:
        return None
    dets = [_plain(dict(r)) for r in conn.execute(
        """
        select d.id, d.detector, d.key, d.start, d."end", d.magnitude, d.evidence, p.label as point, l.label as unit
        from detection d left join entity_label p on p.fuzer_id = d.point_id left join entity_label l on l.fuzer_id = d.unit_id
        where d.upset_id = %s order by d.start, d.id limit %s
        """, (upset_id, limit))]
    by_unit: dict[str, int] = {}
    for d in dets:
        by_unit[d["unit"]] = by_unit.get(d["unit"], 0) + 1
    diag = conn.execute("select diagnosis from upset where id = %s", (upset_id,)).fetchone()["diagnosis"]
    return {**u, "detections_shown": len(dets), "by_unit": by_unit, "timeline": dets,
            **({"diagnosis": {k: diag.get(k) for k in ("causes", "actions", "answer", "error")}} if diag else {})}
