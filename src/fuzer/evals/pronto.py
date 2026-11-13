"""Run Fuzer's change detection over the PRONTO multiphase flow rig's labelled faults (#904).

`evals/pronto/data/` keeps the three labelled days of the PRONTO heterogeneous benchmark (Cranfield
University's three-phase flow facility, CC BY 4.0, see its README), as the authors aligned and labelled
them: one row a second with the rig's process readings, its DCS alarms and a health label from the
operation log. Day 2 holds an air blockage and an air leakage, day 3 two air leakages and two diverted
flows, day 4 slugging that comes and goes. Between them the rig runs normally, at flow rates the
operators move by hand.

`score()` reads each day's process readings in `step`-minute medians (the air and water flow rates in,
`INPUTS`, are the operators' settings, so they explain changes rather than being scored) and runs
`signals.detect_changes` on every other reading. A fault is found by a change that starts between `LEAD`
minutes before its onset (where the label turns from Normal to the fault) and `LAG` after. A change in a
normal stretch is explained if it starts within `EXPLAIN` minutes after the operators moved a flow rate,
and is a false alarm otherwise. A change later in a fault is about the fault still: the rig's tests step a
valve through several openings within one.

The tests last minutes, not hours, so the default windows are 20 minutes at 1-minute steps; the SRE's
10-minute steps and 3-hour windows would see a day as a handful of values.
"""

import csv
import gzip
import itertools
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from ..analysis import signals

ROOT = Path(__file__).resolve().parents[3] / "evals" / "pronto"
ALARMS = 12  # the first columns are the DCS alarms (0 / 1), then the process readings, then the label
INPUTS = ("AirIn", "WaterIn")
NORMAL = "Normal"
STEP, WINDOW_MINUTES = 1, 20
LEAD, LAG, EXPLAIN = 5, 30, 15
MOVE = 0.05  # a flow rate moving by this fraction of its level in a step is the operators moving it
T0 = datetime(2017, 9, 1)  # the merged files carry no times: minutes count from here


def load(path, step: int = STEP) -> dict:
    """One labelled day: its process readings as `step`-minute medians, the label of each step (its
    first second's), and the faults' onsets [(step index, fault)]."""
    with gzip.open(path, "rt", newline="") as f:
        rows = list(csv.reader(f))
    head, rows = rows[0], rows[1:]
    per = step * 60
    n = len(rows) // per
    labels = [r[-1] for r in rows]
    readings = {}
    for j, col in enumerate(head[ALARMS:-1], ALARMS):
        v = np.array([float(r[j]) if r[j] else np.nan for r in rows[: n * per]]).reshape(n, per)
        readings[col] = np.nanmedian(v, axis=1)
    onsets = [(i // per, labels[i]) for i in range(1, n * per) if labels[i] != NORMAL and labels[i - 1] == NORMAL]
    return {"day": Path(path).name.split("_")[0], "step": step, "readings": readings,
            "labels": labels[: n * per : per], "onsets": onsets}


def days(root=ROOT, step: int = STEP) -> list[dict]:
    return [load(p, step) for p in sorted((Path(root) / "data").glob("*_merged.csv.gz"))]


def moves(day: dict) -> list[int]:
    """Steps at which the operators moved the air or water flow rate in."""
    out = set()
    for k in INPUTS:
        v = day["readings"][k]
        out |= {int(i) + 1 for i in np.flatnonzero(np.abs(np.diff(v)) > MOVE * np.nanmedian(np.abs(v)) + 1e-9)}
    return sorted(out)


def check(day: dict, window_minutes: int = WINDOW_MINUTES) -> dict:
    """One day: each fault found or not, and the changes in normal running that no flow move explains."""
    step = day["step"]
    times = [T0 + timedelta(minutes=i * step) for i in range(len(day["labels"]))]
    lead, lag, explain = LEAD // step, LAG // step, EXPLAIN // step
    changes = []
    for col, v in day["readings"].items():
        if col in INPUTS:
            continue
        for c in signals.detect_changes(times, v, step, window_minutes=window_minutes):
            i = int((datetime.fromisoformat(c["start"]) - T0).total_seconds() // 60) // step
            changes.append({"point": col, "at": i * step, **c, "i": i})
    mv = moves(day)
    faults = []
    for o, fault in day["onsets"]:
        hits = [c for c in changes if o - lead <= c["i"] <= o + lag]
        first = min((c["i"] for c in hits), default=None)
        faults.append({"fault": fault, "onset_minute": o * step, "found": bool(hits),
                       "minutes_to_detect": (first - o) * step if hits else None,
                       "by": sorted({c["point"] for c in hits})})
    false, explained = [], 0
    for c in changes:
        if day["labels"][c["i"]] != NORMAL or any(o - lead <= c["i"] <= o + lag for o, _ in day["onsets"]):
            continue
        if any(0 <= c["i"] - m <= explain for m in mv):
            explained += 1
        else:
            false.append(f"{c['point']} {c['kind']} at minute {c['at']}")
    normal = sum(1 for x in day["labels"] if x == NORMAL) * step
    for c in changes:
        del c["i"]
    return {"day": day["day"], "minutes": len(day["labels"]) * step, "normal_minutes": normal, "faults": faults,
            "flow_moves": len(mv), "changes": len(changes), "explained": explained, "false_alarms": false}


def score(step: int = STEP, window_minutes: int = WINDOW_MINUTES, root=ROOT) -> dict:
    """Every labelled day: faults found, minutes to find them, and false alarms in normal running."""
    runs = [check(d, window_minutes) for d in days(root, step)]
    faults = [f for r in runs for f in r["faults"]]
    found = [f for f in faults if f["found"]]
    kinds = {}
    for k, g in itertools.groupby(sorted(faults, key=lambda f: f["fault"]), key=lambda f: f["fault"]):
        g = list(g)
        kinds[k] = {"faults": len(g), "found": sum(f["found"] for f in g)}
    return {"step": step, "window_minutes": window_minutes, "faults": len(faults), "found": len(found),
            "recall": round(len(found) / len(faults), 2) if faults else None,
            "median_minutes_to_detect": float(np.median([f["minutes_to_detect"] for f in found])) if found else None,
            "by_fault": kinds,
            "missed": [f"{r['day']} {f['fault']} at minute {f['onset_minute']}" for r in runs for f in r["faults"]
                       if not f["found"]],
            "false_alarms": sum(len(r["false_alarms"]) for r in runs),
            "normal_hours": round(sum(r["normal_minutes"] for r in runs) / 60, 1), "days": runs,
            "basis": f"PRONTO's labelled days at {step}-min medians, changes in {window_minutes}-min windows on every "
                     f"process reading but the flow rates in; a fault is found by a change from {LEAD} min before its "
                     f"onset to {LAG} min after; a change in normal running is explained within {EXPLAIN} min after "
                     "the operators move a flow rate, else a false alarm"}
