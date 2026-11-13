"""Plant instrument faults and interface outages over a fake plant's process model (#868).

Writes `instrument_faults.json` (read by fuzer.sources.instruments, which documents the kinds). The same
file is the answer key: each fault names its point, span and the instrument health check that should
find it. Faults go on modelled points with no DCS alarm and no IOW and that aren't a unit's run signal,
so they damage the readings without moving the limit checks, the excursions or the operating modes the
plant's other stories rely on. They are sized from the point's own behaviour the week before, so each
one is plainly there: frozen and stuck points are ones that move, and noise is several times the usual.
"""

import random
import re
from datetime import datetime, timedelta

import numpy as np

from fuzer.analysis import signals
from fuzer.sources.process_model import ProcessModel

COUNTS = {"frozen": 6, "stuck": 4, "noisy": 5, "pinned": 2, "drift": 4, "bad": 4}
HOURS = {"frozen": (6, 36), "stuck": (12, 48), "noisy": (4, 18), "pinned": (2, 10), "drift": (240, 480), "bad": (2, 20)}
BAD_STATES = ["Bad Input", "Bad Input", "Comm Fail", "Comm Fail"]
RUN_SIGNAL = re.compile(r"Motor Power|Speed|Vibration|Choke|Running|Status", re.IGNORECASE)
# Transmitters: pressures, temperatures, flows, levels, and wells' pressures, temperatures and rates. Not valve
# positions, which an operator or a controller sets.
MEASURED = re.compile(r"(TT|PT|FT|LT)-?\d{4}|\.(WHT|WHP|CASP|GLIR|QGAS)$")
STEP = 10


def _usual(model: ProcessModel, point: str, start: datetime) -> tuple[float, np.ndarray]:
    v = np.array([x for _, x in model.values(point, start - timedelta(days=7), start, STEP)], float)
    return signals.usual_spread(v, STEP) or 0.0, v


def generate(spec: dict, points: list[dict], seed: int, start: datetime, end: datetime, avoid=(), skip=(),
             interfaces: dict[str, list[str]] | None = None, outages: int = 3) -> dict:
    """Faults on `points` (pi_points rows) between start and end, outside the `avoid` spans [(a, b)] and off
    the tags in `skip`; interface outages on `interfaces` ({name: [points]})."""
    rng = random.Random(seed)
    model = ProcessModel(spec)
    pool = [p for p in points if model.covers(p["tag"]) and p["tag"] not in skip
            and MEASURED.search(p["tag"]) and not RUN_SIGNAL.search(p.get("descriptor") or "")]
    rng.shuffle(pool)
    hours_total = int((end - start).total_seconds() // 3600)

    def when(hours: float) -> tuple[datetime, datetime]:
        while True:
            a = start + timedelta(hours=rng.randrange(hours_total - int(hours) - 1))
            a = a.replace(minute=0)
            b = a + timedelta(hours=hours)
            if not any(a < y and b > x for x, y in avoid):
                return a, b

    faults, used = [], set()
    for kind, count in COUNTS.items():
        tries = 0
        while sum(f["kind"] == kind for f in faults) < count and tries < 400:
            tries += 1
            p = pool[tries % len(pool)] if kind != "pinned" else rng.choice([q for q in pool if q["engunits"] == "%"])
            if p["tag"] in used:
                continue
            hours = round(rng.uniform(*HOURS[kind]) * 6) / 6  # on the 10-minute grid
            a, b = when(hours)
            usual, base = _usual(model, p["tag"], a)
            during = np.array([x for _, x in model.values(p["tag"], a, b, STEP)], float)
            level = float(np.nanmedian(np.abs(base))) if len(base) else 0.0
            moving = usual > 1e-4 * max(level, 1.0) and np.mean(np.diff(during) != 0) > 0.95
            if kind in ("frozen", "stuck", "noisy") and not moving:
                continue
            if kind == "pinned" and float(np.nanmax(during)) > 90:  # already near the top: not a planted fault
                continue
            f = {"id": f"INST-{len(faults) + 1:03d}", "point": p["tag"], "kind": kind,
                 "start": a.isoformat(timespec="minutes"), "end": b.isoformat(timespec="minutes"),
                 "finds": {"frozen": "frozen", "stuck": "stuck", "noisy": "noisy", "pinned": "out_of_range"}.get(kind)}
            if kind == "stuck":
                f["factor"] = 0.02
            elif kind == "noisy":
                f["sigma"] = round(float(8 * usual), 4)
            elif kind == "pinned":
                f["value"] = 100.0
            elif kind == "drift":
                f["offset"] = round(float(rng.choice([-1, 1]) * max(0.05 * level, 10 * usual)), 3)
            elif kind == "bad":
                f["state"] = rng.choice(BAD_STATES)
            f["why"] = WHY[kind]
            faults.append(f)
            used.add(p["tag"])
    out_list = []
    names = sorted(interfaces or {})
    for i in range(outages if names else 0):
        name = names[rng.randrange(len(names))]
        a, b = when(round(rng.uniform(1, 6) * 6) / 6)
        out_list.append({"id": f"IFACE-{i + 1:02d}", "interface": name, "state": "I/O Timeout",
                         "start": a.isoformat(timespec="minutes"), "end": b.isoformat(timespec="minutes"),
                         "points": sorted(interfaces[name]),
                         "why": "the interface node stopped sending; PI stored I/O Timeout until it reconnected"})
    return {"faults": sorted(faults, key=lambda f: f["start"]), "outages": sorted(out_list, key=lambda o: o["start"])}


WHY = {
    "frozen": "comms to the transmitter failed and the tag went stale at its last value",
    "stuck": "the transmitter stuck: it follows the process at a fraction of its true change",
    "noisy": "a loose connection: noise several times the usual on top of the reading",
    "pinned": "driven past the top of its range: the reading sits at 100 %",
    "drift": "the reading walked away over days (a fouled impulse line), then was recalibrated",
    "bad": "the historian stored a digital state instead of a value",
}
