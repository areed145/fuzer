"""Live operations for a demonstration facility: each calendar day brings its own process events (a train trip, a
separator level excursion, a compressor running rough, a controlled value wandering, a cooler fouling), so the plant
behaves like a site in operation instead of replaying one story shifted a day at a time.

A process model opts in with `"live": true` (or `{"rates": {...}}` to tune how often each kind happens). The events
are a pure function of the model's seed and the date: a day reads the same from any window, on any instance and on
every day after it, so history never rewrites itself. Today's events sit at their times of day and show on the trends
as the clock reaches them; detectors, alarms and the work they raise follow from the values as they would on a real
site.

    generate(spec, start, end)      the events for each day in [start, end], in process_model.json's event shape
    of_day(spec, day)               one day's events
"""

import hashlib
import os
import random
from datetime import date, datetime, timedelta

# How often each kind happens on one facility, per day. About one and a half events a day: enough that every shift
# has something new to look at, few enough that each one can be followed from alarm to closed case.
RATES = {
    "trip": 0.2,           # a compression train trips and restarts after half an hour to four hours
    "rough_running": 0.3,  # a compressor's vibration climbs for some hours, then settles (or trips the train)
    "level": 0.45,         # a vessel's level runs away and comes back as the operator takes it in hand
    "wander": 0.4,         # a controlled value (a temperature, a dP, an analyser) drifts off and recovers
    "fouling": 0.04,       # a cooler fouls over days until it is cleaned
}
LOOKBACK_DAYS = 120  # days of live history kept in the model: older days fall back to the facility's own story
ENV = "FUZER_LIVE_EVENTS"  # "off" keeps a live facility to its own story (the test suites set it)


def enabled(spec: dict) -> bool:
    return bool(spec.get("live")) and os.environ.get(ENV, "").lower() != "off"


def _rng(seed, day: date, what: str) -> random.Random:
    return random.Random(int(hashlib.sha256(f"{seed}:{day.isoformat()}:{what}".encode()).hexdigest()[:16], 16))


def _at(day: date, minute: float) -> str:
    return (datetime(day.year, day.month, day.day) + timedelta(minutes=round(minute))).isoformat(timespec="minutes")


def _of(units: dict, *types: str) -> list[str]:
    return sorted(k for k, u in units.items() if u.get("type") in types)


def of_day(spec: dict, day: date) -> list[dict]:
    """The facility's live events on `day`, each tagged `live: "<day>#<kind>"`."""
    if not enabled(spec):
        return []
    cfg = spec["live"]
    rates = {**RATES, **(cfg.get("rates", {}) if isinstance(cfg, dict) else {})}
    units, seed = spec.get("units", {}), spec.get("seed", 0)
    out: list[dict] = []

    def add(kind: str, events: list[dict]) -> None:
        for e in events:
            out.append({**e, "live": f"{day.isoformat()}#{kind}"})

    trains = _of(units, "train")
    machines = _of(units, "compressor", "recip")
    meters = _of(units, "meter")

    r = _rng(seed, day, "trip")
    if trains and r.random() < rates["trip"]:
        train = r.choice(trains)
        start = r.uniform(30, 1380)
        end = start + r.uniform(30, 240)
        evs = [{"unit": train, "kind": "trip", "start": _at(day, start), "end": _at(day, end)}]
        cause = [m for m in machines if units[m].get("train") == train]
        if cause and r.random() < 0.6:  # the machine ran rough first: vibration climbing into the trip
            evs.append({"unit": r.choice(cause), "kind": "vibration",
                        "points": [[_at(day, start - r.uniform(60, 180)), 0], [_at(day, start - 5), r.uniform(5, 9)],
                                   [_at(day, start + 30), 0]]})
        share = 1 / max(len(trains), 1)  # one train of several: the plant loses its share while it is down
        evs += [{"unit": m, "kind": "curtail", "start": _at(day, start + 3), "end": _at(day, end),
                 "factor": round(share, 2)} for m in meters]
        add("trip", evs)

    r = _rng(seed, day, "rough_running")
    if machines and r.random() < rates["rough_running"]:
        start, rise, hold = r.uniform(0, 1200), r.uniform(60, 180), r.uniform(120, 480)
        top = r.uniform(3, 6)
        add("rough_running", [{"unit": r.choice(machines), "kind": "vibration",
                               "points": [[_at(day, start), 0], [_at(day, start + rise), top],
                                          [_at(day, start + rise + hold), top],
                                          [_at(day, start + rise + hold + 60), 0]]}])

    r = _rng(seed, day, "level")
    vessels = _of(units, "vessel")
    if vessels and r.random() < rates["level"]:
        vessel = r.choice(vessels)
        start, up, back = r.uniform(0, 1380), r.uniform(15, 40), r.uniform(40, 150)
        sign = 1 if r.random() < 0.7 else -1  # mostly high (a slug, a stuck dump valve), sometimes low
        size = sign * r.uniform(25, 45)
        add("level", [{"unit": vessel, "kind": "level",
                       "points": [[_at(day, start), 0], [_at(day, start + up), size],
                                  [_at(day, start + up + back), 0]]}])

    r = _rng(seed, day, "wander")
    held = [k for k in _of(units, "controlled") if units[k].get("value")]
    if held and r.random() < rates["wander"]:
        unit = r.choice(held)
        value = abs(float(units[unit]["value"]))
        start, off, back = r.uniform(0, 1300), r.uniform(30, 120), r.uniform(60, 240)
        size = (1 if r.random() < 0.5 else -1) * value * r.uniform(0.15, 0.35)
        add("wander", [{"unit": unit, "kind": "drift",
                        "points": [[_at(day, start), 0], [_at(day, start + off), round(size, 3)],
                                   [_at(day, start + off + back), 0]]}])

    r = _rng(seed, day, "fouling")
    coolers = _of(units, "cooler")
    if coolers and r.random() < rates["fouling"]:
        days = r.uniform(4, 9)  # fouls until the next clean
        start = r.uniform(0, 1440)
        add("fouling", [{"unit": r.choice(coolers), "kind": "fouling",
                         "points": [[_at(day, start), 0], [_at(day, start + days * 1440), r.uniform(3, 7)],
                                    [_at(day, start + days * 1440 + 1), 0]]}])
    return out


def generate(spec: dict, start: date, end: date) -> list[dict]:
    """Every live event from `start` to `end` (whole days, both included)."""
    out: list[dict] = []
    d = start
    while d <= end:
        out += of_day(spec, d)
        d += timedelta(days=1)
    return out
