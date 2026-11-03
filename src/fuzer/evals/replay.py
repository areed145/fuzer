"""Replay history to score the operations detectors (#103) against a fake plant's injected events.

The SRE (#96) runs every 6 hours over the window since its last run. The replay does the same over a
past period: window by window, with the clock set to each window's end. The detector reads values
through a source that refuses anything later than that, so it can't peek. What it raises is scored
against the process model's events (process_model.json), the ground truth:

- **upsets** are what a detector should find: trips, shutdowns, shut-ins, bearing wear, fouling, a gas
  lift valve failing open, hydrates, bad instruments, ...
- **routine** events (choke moves, pump changeovers, planned downtime, curtailments) explain what a
  detector raises without it counting as a false alarm, and without needing to be found.

An upset is found by a detection on its own unit (for a train, any of its machines; for a field
shutdown, anything) that starts between an hour before it and 6 hours after it ends. A detection is
right if it finds an upset, explained if it is on something an upset or routine event affects in that
time (downstream: a trip reaches the lift gas headers and the wells on them; a slow ramp such as
fouling only its own unit), else a false alarm. Scores: recall and time to detect per kind of upset,
precision, false alarms per week, and seconds per window. Detection only for now; grouping, diagnosis,
reports and the opportunity scan come later (#96, #98, #102).

A detector is `fn(conn, pack, since, until, src) -> [{point, start, end, key}]`: PI point name, the
times it started and ended (None while still going), and a key that ties one finding across windows
(an excursion still open at the end of one window carries on in the next).
"""

import json
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..analysis import limits
from ..core import db

STEP = timedelta(hours=6)
LEAD = timedelta(hours=1)  # a detection this long before an event is still about it (lags, noise)
TAIL = timedelta(hours=6)  # and this long after it ends (restart, settling)
UPSETS = {"trip", "shutdown", "shut_in", "vibration", "fouling", "discharge_temperature", "level", "erratic",
          "valve_failed_open", "restriction", "head_loss", "choke_wear", "flowline_restriction"}
ROUTINE = {"choke", "stopped", "covering", "open", "curtail", "down", "setpoint", "reroute"}


class Peek(BaseException):
    """A detector asked for values after the replay clock. A BaseException, so a detector's own "skip a
    point that can't be read" handling (except Exception) can't hide a look into the future."""


class Clocked:
    """A time-series source that serves nothing after `now`."""

    def __init__(self, src, now: datetime):
        self.src, self.now, self.name = src, now, f"{src.name}@clock"

    def series(self, s, start, end, step_minutes):
        if end > self.now:
            raise Peek(f"{s.get('point')} asked up to {end:%Y-%m-%d %H:%M}, clock is {self.now:%Y-%m-%d %H:%M}")
        return self.src.series(s, start, end, step_minutes)


# ---- ground truth -------------------------------------------------------------------------------

def _span(e: dict) -> tuple[datetime, datetime]:
    """An event's start and end; a ramp ({points: [[time, value]]}) runs from where it leaves 0 to where
    it returns to 0 (or its last point)."""
    if "at" in e:
        t = datetime.fromisoformat(e["at"])
        return t, t
    if "points" not in e:
        return datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"])
    pts = [(datetime.fromisoformat(t), v) for t, v in e["points"]]
    first = next(i for i, (_, v) in enumerate(pts) if v)
    start = pts[max(first - 1, 0)][0]
    end = next((t for t, v in pts[first:] if not v), pts[-1][0])
    return start, end


def scope(units: dict) -> dict[str, set[str]]:
    """The units each unit's events affect: itself and everything downstream of it. A train and its
    compressors, turbines and coolers affect each other and the lift gas headers the train feeds; a header
    its child headers, the wells and meters on it and its bypass; a gas well the header it supplies; a
    well its jacket; a meter the pumps that carry its flow, and a pump its meter and the pumps covering
    for it. A field event affects everything."""
    down: dict[str, set[str]] = {u: set() for u in units}
    for u, x in units.items():
        if x.get("train"):
            down[u].add(x["train"])
            down.setdefault(x["train"], set()).add(u)
        for t in x.get("trains", []):
            down.setdefault(t, set()).add(u)
        if x.get("parent"):
            down.setdefault(x["parent"], set()).add(u)
        if x.get("bypass"):
            down[u].add(x["bypass"])
        if x["type"] in ("well", "meter") and x.get("header"):
            down.setdefault(x["header"], set()).add(u)
        if x["type"] == "well":
            down[u] |= {x.get("jacket")} - {None}
            if x.get("kind") == "gas" and x.get("header"):
                down[u].add(x["header"])
        if x["type"] == "pump":
            down[u] |= {x.get("meter")} - {None}
            down.setdefault(x.get("meter"), set()).add(u)  # and the meter's flow is what the pumps carry
            down[u] |= {p for p, y in units.items() if y.get("group") and y["group"] == x.get("group")}
    out = {}
    for u in down:
        seen, todo = {u}, [u]
        while todo:
            for v in down.get(todo.pop(), ()):
                if v not in seen:
                    seen.add(v)
                    todo.append(v)
        out[u] = seen
    out["field"] = set(units)
    return out


def own(units: dict) -> dict[str, set[str]]:
    """The units an event is on: itself, or a train with its machines; for the field, everything."""
    out = {u: {u} for u in units}
    for u, x in units.items():
        if x.get("train"):
            out.setdefault(x["train"], {x["train"]}).add(u)
    out["field"] = set(units)
    return out


def truth(model: dict, since: datetime, until: datetime) -> list[dict]:
    """The model's upsets and routine events in [since, until], each with its span, the units it is on and
    the ones it affects."""
    sc, on = scope(model["units"]), own(model["units"])
    out = []
    for e in model["events"]:
        if e["kind"] not in UPSETS | ROUTINE:
            continue
        a, b = _span(e)
        if b < since or a > until:
            continue
        out.append({"unit": e["unit"], "kind": e["kind"], "start": a, "end": b, "upset": e["kind"] in UPSETS,
                    "own": on.get(e["unit"], {e["unit"]}),
                    "scope": on.get(e["unit"], {e["unit"]}) if "points" in e else sc.get(e["unit"], {e["unit"]})})
    return out


# ---- detectors ----------------------------------------------------------------------------------

def limits_detector(conn, pack, since: datetime, until: datetime, src) -> list[dict]:
    """The #101 excursions (alarm limits, IOWs, learned normal ranges, while units run): a baseline until
    the SRE's own checks (#96) arrive."""
    pids = [r["point_id"] for r in conn.execute(
        "select distinct point_id from point_limit union select distinct point_id from point_normal order by 1")]
    return [{"point": x["point"], "start": x["start"], "end": x["end"], "key": f"{x['record']}:{x['limit_type']}",
             "basis": x["basis"]} for x in limits.excursions(conn, pack, pids, since, until, src=src)]


def sre_detector(conn, pack, since: datetime, until: datetime, src, only: list[str] | None = None) -> list[dict]:
    """The SRE's own detectors (sre.py), all of them or `only` some."""
    from ..ops import sre

    return sre.detect(conn, pack, since, until, src, only)


def _only(*names):
    fn = lambda conn, pack, since, until, src: sre_detector(conn, pack, since, until, src, list(names))  # noqa: E731
    fn.__name__ = "sre:" + "+".join(names)
    return fn


DETECTORS = {"limits": limits_detector, "sre": sre_detector,
             **{f"sre:{n}": _only(n) for n in ("excursions", "stops", "steps", "drifts", "instruments", "models",
                                                       "hydraulics", "multivariate")}}


def default_window(model: dict, lead_days: int = 90, days: int = 7) -> tuple[str, str]:
    """A week to score: the first injected trip (else any upset) at least `lead_days` into the data, so normal
    ranges can be learned before it, starting the day before it. A trip scores the most: detection, grouping,
    first cause, diagnosis and the day's report leading with it."""
    starts = [_start(e) for e in model["events"] if _start(e)]
    if not starts:
        raise ValueError("the process model has no events")
    first = min(starts) + timedelta(days=lead_days)
    evs = [e for e in truth(model, first, max(starts) + timedelta(days=1)) if e["upset"] and e["start"] >= first]
    ups = sorted(e["start"] for e in evs if e["kind"] == "trip") or sorted(e["start"] for e in evs)
    if not ups:
        raise ValueError(f"no injected upsets {lead_days} days or more into the data")
    since = datetime.combine(ups[0].date() - timedelta(days=1), datetime.min.time())
    return since.isoformat(timespec="minutes"), (since + timedelta(days=days)).isoformat(timespec="minutes")


def _start(e: dict) -> datetime | None:
    if e.get("start"):
        return datetime.fromisoformat(e["start"])
    if e.get("points"):
        return datetime.fromisoformat(e["points"][0][0])
    return None


# ---- replay -------------------------------------------------------------------------------------

def _windows(since: datetime, until: datetime, step: timedelta):
    t = since
    while t < until:
        yield t, min(t + step, until)
        t += step


def _merge(found: list[dict], d: dict, a: datetime) -> None:
    """Add one window's detection, or carry on the same one from the window before."""
    for f in reversed(found):
        if f["point"] == d["point"] and f["key"] == d["key"] and (f["end"] is None or f["end"] >= a - timedelta(minutes=30)):
            if d["start"] <= a + timedelta(minutes=30):
                f["end"] = d["end"]
                return
            break
    found.append({**d, "detected_at": None})


def run(conn, pack, detector, since: str, until: str, data_dir=None, src=None, learn_days: int | None = 90,
        relearn_days: int = 30, step: timedelta = STEP, say=None) -> dict:
    """Replay [since, until] in `step` windows with `detector` (a name in DETECTORS or a function) and score
    it. Normal ranges are learned from the `learn_days` before `since` and again every `relearn_days`, as
    the schedule would have had them, and put back afterwards; learn_days None keeps the ones in the
    database. (Learning reaches back before the data starts early in a fake plant's history, which leaves
    the ranges too narrow: start a replay at least `learn_days` in.) `say(msg)` gets progress lines."""
    from ..sources.timeseries import provider

    path = Path(data_dir or ".") / "process_model.json"
    if not data_dir or not path.is_file():
        raise ValueError("no process_model.json in the data folder: the replay scores against a fake plant's events")
    name = detector if isinstance(detector, str) else getattr(detector, "__name__", "detector")
    fn = DETECTORS[detector] if isinstance(detector, str) else detector
    src = src or provider()
    spec = json.loads(path.read_text())
    a, b = datetime.fromisoformat(since), datetime.fromisoformat(until)
    saved = conn.execute("select * from point_normal").fetchall() if learn_days else None
    found: list[dict] = []
    seconds, learned = [], None
    n = len(list(_windows(a, b, step)))
    t_start = time.perf_counter()
    try:
        for i, (w0, w1) in enumerate(_windows(a, b, step), 1):
            if learn_days and (learned is None or w0 - learned >= timedelta(days=relearn_days)):
                limits.learn(conn, pack, (w0 - timedelta(days=learn_days)).isoformat(), w0.isoformat(), src=src)
                learned = w0
            t0 = time.perf_counter()
            for d in sorted(fn(conn, pack, w0, w1, Clocked(src, w1)), key=lambda d: d["start"]):
                _merge(found, d, w0)
            for f in found:  # one picked up again keeps when it was first seen
                f["detected_at"] = f["detected_at"] or w1
            seconds.append(time.perf_counter() - t0)
            if say and (i == n or i % 4 == 0):  # about once a replayed day
                left = (time.perf_counter() - t_start) / i * (n - i)
                say(f"replay {name}: window {i}/{n} to {w1:%Y-%m-%d %H:%M}, {len(found)} detections, "
                    f"about {left / 60:.0f} min left")
    finally:
        if saved is not None:
            conn.execute("delete from point_normal")
            if saved:
                cols = list(saved[0])
                db.copy_rows(conn, "point_normal", cols, [tuple(r[c] for c in cols) for r in saved])
            conn.commit()
    res = score(spec, found, a, b)
    if found and "unit_id" in found[0]:  # the SRE's detections carry their unit: score its upsets too
        from ..ops import sre

        res["grouping"] = score_groups(spec, sre.group_detections(found, sre.topology(conn)), a, b)
    return {"detector": name, "since": since, "until": until, "windows": len(seconds),
            "seconds_per_window": round(statistics.mean(seconds), 2) if seconds else None,
            "learn_days": learn_days, "relearn_days": relearn_days if learn_days else None, **res}


# ---- scoring ------------------------------------------------------------------------------------

def _hits(d: dict, unit: str | None, e: dict, where: str = "scope") -> bool:
    end = d["end"] or d["detected_at"]
    return unit in e[where] and d["start"] <= e["end"] + TAIL and end >= e["start"] - LEAD \
        and d["start"] >= e["start"] - LEAD


def score(spec: dict, found: list[dict], since: datetime, until: datetime) -> dict:
    """Detections against the model's events (upsets that start in the period): see the module notes."""
    events = truth(spec, since, until)
    upsets = [e for e in events if e["upset"] and e["start"] >= since]
    unit_of = {p: u for p, (u, _) in spec["points"].items()}
    kinds: dict[str, dict] = {}
    for e in upsets:
        hits = [d for d in found if _hits(d, unit_of.get(d["point"]), e, "own")]
        k = kinds.setdefault(e["kind"], {"events": 0, "detected": 0, "hours": [], "missed": []})
        k["events"] += 1
        if hits:
            k["detected"] += 1
            k["hours"].append(max(0.0, (min(d["detected_at"] for d in hits) - e["start"]).total_seconds() / 3600))
        elif len(k["missed"]) < 5:
            k["missed"].append(f"{e['unit']} {e['start']:%Y-%m-%d %H:%M}")
    right = explained = 0
    false = []
    for d in found:
        u = unit_of.get(d["point"])
        if any(_hits(d, u, e, "own") for e in events if e["upset"]):
            right += 1
        elif any(_hits(d, u, e) for e in events):
            explained += 1
        else:
            false.append(d)
    n_ev = sum(k["events"] for k in kinds.values())
    n_det = sum(k["detected"] for k in kinds.values())
    weeks = max((until - since).total_seconds() / 604800, 1 / 7)
    by_kind = {k: {"events": v["events"], "detected": v["detected"], "recall": round(v["detected"] / v["events"], 3),
                   "median_hours_to_detect": round(statistics.median(v["hours"]), 1) if v["hours"] else None,
                   "missed": v["missed"]} for k, v in sorted(kinds.items())}
    return {"upsets": n_ev, "detected": n_det, "recall": round(n_det / n_ev, 3) if n_ev else None,
            "detections": len(found), "right": right, "explained": explained, "false_alarms": len(false),
            "precision": round(right / (right + len(false)), 3) if right + len(false) else None,
            "false_alarms_per_week": round(len(false) / weeks, 1), "by_kind": by_kind,
            "false_alarm_examples": [f"{d['point']} {d['key']} {d['start']:%Y-%m-%d %H:%M}" for d in false[:10]]}


def score_groups(spec: dict, groups: list[dict], since: datetime, until: datetime) -> dict:
    """Upsets (grouped detections) against the model's events: how many groups each found upset was
    split into (1 is right), whether a trip's group starts on the trip or on an upset in it that began
    before (its first cause), and groups that match nothing (false upsets)."""
    events = truth(spec, since, until)
    upsets = [e for e in events if e["upset"] and e["start"] >= since]
    unit_of = {p: u for p, (u, _) in spec["points"].items()}

    def hit(g, e, where):
        return any(_hits(d, unit_of.get(d["point"]), e, where) for d in g["detections"])

    right = [g for g in groups if any(hit(g, e, "own") for e in events if e["upset"])]
    explained = [g for g in groups if g not in right and any(hit(g, e, "scope") for e in events)]
    false = [g for g in groups if g not in right and g not in explained]
    split = [sum(1 for g in groups if g["kind"] == "process" and hit(g, e, "own")) for e in upsets
             if e["kind"] in ("trip", "shutdown")]
    first = []  # the group's first detection is on the trip, or on an upset in it that began before (its cause)
    for g in groups:
        mine = [e for e in events if e["upset"] and hit(g, e, "own")]
        for t in (e for e in mine if g["kind"] == "process" and e["kind"] in ("trip", "shutdown")):
            first.append(any(_hits(g["first"], unit_of.get(g["first"]["point"]), e, "own") for e in mine
                             if e["start"] <= t["start"]))
    weeks = max((until - since).total_seconds() / 604800, 1 / 7)
    return {"groups": len(groups), "by_kind": dict(statistics_counter(g["kind"] for g in groups)),
            "right": len(right), "explained": len(explained), "false_upsets": len(false),
            "false_upsets_per_week": round(len(false) / weeks, 1),
            "groups_per_trip": round(statistics.mean(split), 1) if split else None,
            "first_cause_right": round(sum(first) / len(first), 2) if first else None,
            "false_upset_examples": [f"{g['first']['point']} {g['first']['key']} {g['start']:%Y-%m-%d %H:%M} "
                                     f"({len(g['detections'])} detections)" for g in false[:8]]}


def statistics_counter(items) -> dict:
    out: dict = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out


# ---- stored runs --------------------------------------------------------------------------------

def store(conn, res: dict) -> dict:
    """Keep a run's scores; returns the previous run of the same detector over the same period (if any)
    and the kinds whose recall fell since, so a detector change that loses upsets fails the check."""
    prev = conn.execute(
        "select scores from ops_eval where detector = %s and since = %s and until = %s order by id desc limit 1",
        (res["detector"], res["since"], res["until"])).fetchone()
    conn.execute("insert into ops_eval (detector, since, until, scores) values (%s, %s, %s, %s)",
                 (res["detector"], res["since"], res["until"], json.dumps(res, default=str)))
    conn.commit()
    if not prev:
        return {"previous": None, "recall_dropped": []}
    p = prev["scores"]
    dropped = [k for k, v in res["by_kind"].items() if v["recall"] < p["by_kind"].get(k, {}).get("recall", 0)]
    if (res["recall"] or 0) < (p["recall"] or 0):
        dropped.insert(0, "overall")
    return {"previous": {k: p[k] for k in ("recall", "precision", "false_alarms_per_week")}, "recall_dropped": dropped}
