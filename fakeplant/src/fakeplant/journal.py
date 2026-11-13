"""A DCS alarm and event journal from the process model (#859): what the alarm summary printer would have shown.

Each enabled alarm in the DCS alarm export is run against its point's modelled values on the model's 5-minute grid:
an alarm sounds when the value goes past its setpoint and returns once it is back inside by a deadband (1 % of the
setpoint, at least 0.2 % of the point's normal value), the way a DCS alarm block does. What a real journal also has:

- times to the second (the crossing fell somewhere between two samples);
- acknowledgements by the console operator, after a delay that is longer at night and for low priorities, and none
  for an alarm that cleared before anyone got to it;
- chattering: an alarm whose value only just crossed rings two to five more times within seconds;
- state-based suppression: a stopped unit's alarms are suppressed 30 minutes after it stops, so a trip floods the
  console with its low alarms and then goes quiet, and a standby pump doesn't stand in alarm for months;
- shelving: an alarm still active after a few hours is shelved by the operator for 12 hours, with a reason, and
  rings again when the shelf runs out if it is still active.

The journal is deterministic: every random choice is seeded by the point, the alarm and the time.
"""

import hashlib
import math
import random
import re
from datetime import datetime, timedelta

import numpy as np

from fuzer.sources.process_model import EPOCH, ProcessModel

PSI = 14.5038
TAG = re.compile(r"(\d\d|[A-Z]\d)[-_.]?([A-Z]+)[-_]?(\d{4})([A-Z]?)\b")
LIMIT = {"PVHH": ("hi", 1), "PVHI": ("hi", 2), "PVLO": ("lo", 2), "PVLL": ("lo", 1)}
NAMES = {"PVHH": "HIHI", "PVHI": "HI", "PVLO": "LO", "PVLL": "LOLO"}
SUPPRESS_AFTER = 30  # minutes a stopped unit's alarms keep sounding before state-based suppression
SHELVE_AFTER = (2, 6)  # hours an alarm stands before the operator shelves it
SHELF_HOURS = 12
SHELVE_REASONS = ["Known issue, notification raised", "Instrument fault, tech called out", "Awaiting MOC on setpoint",
                  "Standby equipment, alarm not relevant", "Shelved per shift supervisor"]
# Instrument noise at one reading a second, as a share of the normal value (of the span for levels in %)
FAST_SIGMA = {"%": 0.008, "Sm3/d": 0.012, "m3/h": 0.012, "kSm3/d": 0.012, "MMscfd": 0.012, "ksm3/d": 0.012,
              "barg": 0.004, "psig": 0.004, "bara": 0.004, "degC": 0.0015, "mm/s": 0.04, "um": 0.03, "rpm": 0.002}
MAX_SHELVES = 3  # then the alarm is taken out of service until it clears


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


def dcs_tag(point: str, well: bool) -> str:
    """The DCS's name for a modelled point: the compact tag (20PT2300A, B1PT2001), or a well RTU's SCADA point."""
    if well:
        return point
    m = TAG.search(point.split(":")[-1])
    return "".join(m.groups()) if m else point


def _run_unit(units: dict, unit: str) -> str | None:
    """The unit whose run signal decides whether this unit's alarms are live (None: always live)."""
    u = units.get(unit, {})
    if u.get("type") in ("train", "pump"):
        return unit
    if u.get("train") in units:
        return u["train"]
    return None


def journal(spec: dict, alarms: list[dict], points: list[dict], start: datetime, end: datetime,
            console=lambda tag: tag[:2]) -> list[dict]:
    """The journal over [start, end) for the alarm export rows (`tag, alarm, setpoint, eu, priority, enabled`),
    the PI point list (`tag, descriptor`) and the model spec. `console(tag)` names the operator console a DCS tag
    is on."""
    model = ProcessModel(spec)
    units = spec["units"]
    desc = {p["tag"]: p.get("descriptor", "") for p in points}
    by_tag: dict[str, str] = {}
    for p, (u, _) in spec["points"].items():
        if u in units:
            by_tag.setdefault(dcs_tag(p, units[u].get("type") == "well"), p)
    rows = [a for a in alarms if a.get("enabled", "Y") == "Y" and a["alarm"] in LIMIT and a["tag"] in by_tag]
    events: list[dict] = []
    w = model.window(start, end)  # one window, so an alarm standing across a month end stays one alarm
    keep = (w.t >= _minutes(start)) & (w.t < _minutes(end))
    times = w.t[keep]
    field_up = 1 - w.shutdown()[keep]
    run_cache: dict[str | None, np.ndarray] = {}
    run_of_tag: dict[str, str | None] = {}
    for a in rows:
        p = by_tag[a["tag"]]
        unit, var = spec["points"][p]
        y = w.get(unit, var)[keep]
        if a["eu"] == "psig":  # an older DCS configured in psig
            y = y * PSI
        ru = run_of_tag[a["tag"]] = _run_unit(units, unit)
        if ru not in run_cache:
            run_cache[ru] = (w.get(ru, "running")[keep] if ru else np.ones_like(times)) * field_up
        events += _alarm_events(a, p, desc.get(p, ""), y, times, _live(run_cache[ru] > 0.5), console(a["tag"]))
    events += _state_events(spec, {u: run for u, run in run_cache.items() if u}, times, events, run_of_tag,
                            console)
    first = start.isoformat(timespec="seconds")
    events = [e for e in events if e["timestamp"] >= first]
    events.sort(key=lambda e: (e["timestamp"], e["tag"], e["alarm"], e["event"]))
    for n, e in enumerate(events, 1):
        e["event_id"] = f"ALM-{n:07d}"
    return [{"event_id": e.pop("event_id"), **e} for e in events]


def _machine(units: dict, ru: str) -> str:
    """What the console calls a run unit: a train by the machine that drives it (B1-K-2001), else the unit itself."""
    if units.get(ru, {}).get("type") == "train":
        for name, u in units.items():
            if u.get("train") == ru and u.get("type") in ("compressor", "recip", "gas_turbine"):
                return name
    return ru


def _state_events(spec: dict, running: dict[str, np.ndarray], times: np.ndarray, alarms: list[dict],
                  run_of_tag: dict[str, str | None], console) -> list[dict]:
    """A STATE event each time a run unit stops or starts: TRIPPED when the model has it trip then, else STOPPED,
    with the first-out alarm (the last HIHI or LOLO on the unit's points in the half hour before), and RUNNING."""
    units = spec["units"]
    trips = {}
    for e in spec.get("events", []):
        if e.get("kind") == "trip" and e.get("start"):
            trips.setdefault(e["unit"], []).append(_minutes(datetime.fromisoformat(e["start"])))
    out = []
    for ru, run in running.items():
        on = run > 0.5
        name = _machine(units, ru)
        m = TAG.search(name)
        tag = "".join(m.groups()) if m else name.split(":")[-1]
        crit = sorted((e for e in alarms if e["event"] == "ALARM" and e["alarm"] in ("PVHH", "PVLL")
                       and run_of_tag.get(e["tag"]) == ru), key=lambda e: e["timestamp"])
        for i in np.flatnonzero(on[:-1] != on[1:]) + 1:
            r = _rng(ru, int(times[i]), "state")
            at = times[i] - r.uniform(0, 5)
            row = {"timestamp": _stamp(at), "tag": tag, "alarm": "STATE", "priority": "", "setpoint": "", "eu": "",
                   "console": console(tag), "value": "", "operator": "", "comment": ""}
            if on[i]:
                out.append({**row, "event": "RUNNING", "message": f"{name} RUNNING".upper()})
                continue
            tripped = any(abs(t - at) <= 10 for t in trips.get(ru, []) + trips.get(name, []))
            stamp = _stamp(at)
            before = _stamp(at - 30)
            first = [e for e in crit if before <= e["timestamp"] <= stamp]
            note = f"First out: {first[-1]['tag']} {NAMES[first[-1]['alarm']]}" if tripped and first else ""
            kind = "TRIPPED" if tripped else "STOPPED"
            out.append({**row, "event": kind, "message": f"{name} {kind}".upper(), "comment": note})
    return out


def _minutes(t: datetime) -> float:
    return (t - EPOCH).total_seconds() / 60


def _live(running: np.ndarray) -> np.ndarray:
    """Alarms are live while the unit runs and for the first SUPPRESS_AFTER minutes after it stops."""
    steps = SUPPRESS_AFTER // 5
    live = running.copy()
    stop = np.flatnonzero(running[:-1] & ~running[1:]) + 1
    for i in stop:
        live[i:i + steps] = True
    return live


def _fast_noise(eu: str, y: np.ndarray, key: str) -> np.ndarray:
    """How far the second-by-second value strays from the 5-minute sample within each interval: the largest of
    readings held for the alarm on-delay (about 15 seconds), so about 1.5 standard deviations."""
    level = float(np.median(np.abs(y))) or 1.0
    sigma = FAST_SIGMA.get(eu, 0.004) * (100.0 if eu == "%" else level)
    r = np.random.default_rng(int(hashlib.sha1(key.encode()).hexdigest()[:12], 16))
    return sigma * (1.5 + r.gumbel(0, 0.3, len(y)).clip(-1, 2.5))


def _alarm_events(a: dict, point: str, descriptor: str, y: np.ndarray, times: np.ndarray, live: np.ndarray,
                  console: str) -> list[dict]:
    side, _ = LIMIT[a["alarm"]]
    sp = float(a["setpoint"])
    db = max(0.02 * abs(sp), 0.002 * float(np.median(np.abs(y))), 1e-6)
    key = f"{point}|{a['alarm']}|{int(times[0])}"
    sign = 1 if side == "hi" else -1
    # the worst reading in each interval can raise the alarm; the best one can clear it
    worst = sign * (y + sign * _fast_noise(a["eu"], y, key + "w"))
    best = sign * (y - sign * _fast_noise(a["eu"], y, key + "b"))
    on, off = worst > sign * sp, best < sign * sp - db
    both = on & off  # a value hovering at the setpoint: the alarm chatters within the interval
    ends_on = np.where(both, sign * y > sign * sp, on)
    last = np.where(on | off, ends_on.astype(int), -1)
    idx = np.where(last >= 0, np.arange(len(y)), 0)
    np.maximum.accumulate(idx, out=idx)
    state = (last[idx] == 1) & (last[idx] >= 0) & live
    chatter = both & live
    if not state.any() and not chatter.any():
        return []
    out = []
    base = {"tag": a["tag"], "alarm": a["alarm"], "priority": a["priority"], "setpoint": a["setpoint"], "eu": a["eu"],
            "console": console}
    text = f"{a['tag']} {NAMES[a['alarm']]} {descriptor}".strip().upper()

    def ev(kind, minute, value=None, operator="", comment=""):
        out.append({"timestamp": _stamp(minute), **base, "event": kind,
                    "value": "" if value is None else _fmt(value), "message": text, "operator": operator,
                    "comment": comment})

    inside, outside = sp + sign * 0.2 * db, sp - sign * 0.3 * db
    for i in np.flatnonzero(chatter):  # extra rings within the interval, leaving the state as it was found
        r = _rng(point, a["alarm"], int(times[i]), "chatter")
        s = times[i] - 5 + r.uniform(0, 2)
        was_on = bool(state[i - 1]) if i else False
        for _ in range(r.randint(1, 3)):
            if was_on:
                ev("RTN", s, outside)
                s += r.uniform(5, 30) / 60
                ev("ALARM", s, inside)
            else:
                ev("ALARM", s, inside)
                s += r.uniform(5, 30) / 60
                ev("RTN", s, outside)
            s += r.uniform(10, 50) / 60
    edges = np.diff(np.concatenate([[False], state, [False]]).astype(np.int8))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    for i, j in zip(starts, ends):
        r = _rng(point, a["alarm"], int(times[i]))
        t_on = times[i] - r.uniform(0, 2) if not chatter[i] else times[i] - r.uniform(0, 0.1)
        t_off = times[j] - r.uniform(2, 5) if j < len(times) else None
        ev("ALARM", t_on, sign * worst[i])
        hour = (t_on / 60) % 24
        median = (0.6 if a["priority"] == "1" else 1.5) * (1.8 if hour < 6 else 1.0)
        ack = t_on + r.lognormvariate(math.log(median), 0.7)
        cleared = t_off if t_off is not None else times[-1] + 5
        if ack < cleared or r.random() < 0.3:
            ev("ACK", ack, operator=f"{console}-CRO")
        # standing: shelved after a few hours, for SHELF_HOURS, ringing again if still active
        s, shelves = t_on + 60 * r.uniform(*SHELVE_AFTER), 0
        while s < cleared:
            shelves += 1
            if shelves > MAX_SHELVES:  # shelved over and over: taken out of service until it clears
                ev("SUPPRESS", s, operator=f"{console}-SUPV", comment="Out of service pending alarm review")
                if t_off is not None:
                    ev("UNSUPPRESS", t_off, operator=f"{console}-SUPV", comment="Returned to service")
                break
            ev("SHELVE", s, operator=f"{console}-CRO", comment=r.choice(SHELVE_REASONS))
            u = s + 60 * SHELF_HOURS
            if u >= cleared:
                if t_off is not None:
                    ev("UNSHELVE", u if u < times[-1] else cleared, operator="SYSTEM", comment="Shelf expired")
                break
            ev("UNSHELVE", u, operator="SYSTEM", comment="Shelf expired")
            k = int(np.searchsorted(times, u))
            ev("ALARM", u + 0.1, y[min(k, len(y) - 1)])
            s = u + 60 * r.uniform(*SHELVE_AFTER)
        if t_off is not None:
            ev("RTN", t_off, y[j])
    return _quiet_while_shelved(out)


def _quiet_while_shelved(events: list[dict]) -> list[dict]:
    """A shelved or suppressed alarm doesn't ring: what it would have done in the meantime (ringing, being
    acknowledged, being shelved again) is not journalled; only its return to normal is."""
    out, quiet, dropped = [], False, 0
    for e in sorted(events, key=lambda e: e["timestamp"]):
        kind = e["event"]
        if kind in ("UNSHELVE", "UNSUPPRESS"):
            if dropped:
                dropped -= 1
                continue
            quiet = False
        elif quiet and kind in ("SHELVE", "SUPPRESS"):
            dropped += 1
            continue
        elif kind in ("SHELVE", "SUPPRESS"):
            quiet = True
        elif quiet and kind in ("ALARM", "ACK"):
            continue
        out.append(e)
    return out


def _stamp(minute: float) -> str:
    return (EPOCH + timedelta(seconds=round(minute * 60))).isoformat(timespec="seconds")


def _fmt(v: float) -> str:
    v = float(v)
    return f"{v:.0f}" if abs(v) >= 1000 else f"{v:.2f}" if abs(v) >= 10 else f"{v:.3f}"


def summary(rows: list[dict], start: datetime, end: datetime) -> dict:
    """Alarm counts the way an alarm management report gives them: per hour per console, peak and floods per
    10 minutes, chattering and shelved alarms."""
    alarms = [r for r in rows if r["event"] == "ALARM"]
    hours = (end - start).total_seconds() / 3600
    per10: dict[str, int] = {}
    for r in alarms:
        k = r["timestamp"][:15]
        per10[k] = per10.get(k, 0) + 1
    consoles = sorted({r["console"] for r in alarms})
    return {"events": len(rows), "alarms": len(alarms),
            "per_hour_per_console": {c: round(sum(r["console"] == c for r in alarms) / hours, 1) for c in consoles},
            "peak_10_min": max(per10.values(), default=0), "flood_periods": sum(v > 10 for v in per10.values()),
            "shelved": sum(r["event"] == "SHELVE" for r in rows),
            "acked_share": round(sum(r["event"] == "ACK" for r in rows) / max(len(alarms), 1), 2)}


def relative(rows: list[dict], today) -> list[dict]:
    """The journal with its times written relative to `today` (`today-1d 03:12:40`), for a hand-written bundle whose
    records follow the calendar (fuzer.sources.relative_dates)."""
    out = []
    for r in rows:
        t = datetime.fromisoformat(r["timestamp"])
        days = (t.date() - today).days
        rel = "today" if days == 0 else f"today{days:+d}d"
        out.append({**r, "timestamp": f"{rel} {t.time().isoformat(timespec='seconds')}"})
    return out


def for_bundle(bundle, days: int = 100) -> dict:
    """Write `exports/dcs_alarm_journal.csv` for a hand-written bundle (Kestrel, Harrier): the last `days` days of its
    process model against its DCS alarm export, on one console named for the facility, in relative dates."""
    import csv
    from datetime import date
    from pathlib import Path

    from fuzer.sources.process_model import read

    from .common import _write_csv

    exports = Path(bundle) / "exports"
    today = date.today()
    spec = read(exports, today)
    alarms = list(csv.DictReader((exports / "dcs_alarms.csv").open()))
    points = list(csv.DictReader((exports / "pi_points.csv").open()))
    facility = next(iter(spec["points"])).split(":")[0]
    end = datetime.combine(today, datetime.min.time())
    start = end - timedelta(days=days)
    rows = journal(spec, alarms, points, start, end, lambda tag: facility)
    _write_csv(exports / "dcs_alarm_journal.csv", relative(rows, today))
    return {"bundle": Path(bundle).name, **summary(rows, start, end)}
