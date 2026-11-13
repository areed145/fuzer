"""Score Fuzer's change detection on real well upsets from Petrobras's 3W dataset, replayed onto a fake well (#904).

`evals/3w/data/` keeps a slice of the 3W Dataset (CC BY 4.0, see its README): eight real instances, each
a few hours of one well's 1 Hz readings with a label per second. Two are normal operation. Six hold an
upset with a normal stretch before it: an abrupt rise in water cut (BSW), two spurious closures of the
downhole safety valve, a rapid loss of productivity, and two quick restrictions of the production choke.
The label turns from normal (0) to the upset's transient (100 + n) or the upset (n) at its onset.

**Replay.** `Overlay` serves an instance in place of one fake well's readings from a time `at`: the
pressure at the tree (P-TPT) as the well's WHP, the temperature there (T-TPT) as its WHT and the annulus
pressure (P-ANULAR) as its CASP, in 1-minute medians. Each is scaled to the well: the fake well's level
in the hour before `at` times the reading over its level in the instance's first 30 minutes, so the
real moves keep their size relative to the level, and the readings join on without a step. A record
stops soon after its onset (some 20 minutes in), but the well doesn't: after the record ends, the last
value holds for `HOLD_MINUTES`, as a closed valve or a lost well stays until someone acts. Everything
else, and the well outside the replay, comes from the source underneath. Any detector that reads
through a source can run over it.

**Score.** `score(src)` replays each instance onto `WELL` at `AT`, reads the three points from
`CONTEXT_HOURS` before (fake, normal) to the end of the hold, and runs `signals.detect_changes`, by default
as the SRE's steps detector reads (10-minute steps, 180-minute windows). An upset is found by a change on
any of the three that starts between `LEAD` minutes before its onset and `LAG` after it. A change that
starts in the replay before that, or anywhere on a normal instance, is a false alarm; one after it is
about the upset still.

Instrument health (`signals.health`) runs on the real minutes of each record (not the hold). Some 3W
readings are frozen in the source: WELL-00002's annulus pressure holds one value at 1 Hz for hours. Each
stretch where a reading never changed for `signals.FLAT_HOURS` or more is a frozen reading health should
find; anything else it raises before an onset is a false alarm.
"""

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from ..analysis import signals

ROOT = Path(__file__).resolve().parents[3] / "evals" / "3w"
EVENTS = {0: "normal", 1: "abrupt increase of BSW", 2: "spurious closure of DHSV", 3: "severe slugging",
          4: "flow instability", 5: "rapid productivity loss", 6: "quick restriction in PCK", 7: "scaling in PCK",
          8: "hydrate in production line", 9: "hydrate in service line"}
VARS = {"WHP": "P-TPT", "WHT": "T-TPT", "CASP": "P-ANULAR"}
WELL, AT = "OK_J01_01", datetime(2025, 2, 3)
HOLD_MINUTES, REF_MINUTES, CONTEXT_HOURS = 180, 30, 24
LEAD, LAG = 30, 60
STEP, WINDOW_MINUTES = 10, 180  # as the SRE's steps detector reads (sre.step_minutes, detect_changes' default)


@dataclass
class Instance:
    name: str
    event: int  # the upset's 3W label (0: normal operation)
    well: str  # 3W's anonymised well
    start: datetime  # naive UTC
    values: dict[str, np.ndarray]  # by VARS key, one median per minute from start (NaN where none)
    onset: int | None  # minutes from start to the first upset label
    flat: dict[str, list[tuple[int, int]]]  # by VARS key: [from, to) minutes where the 1 Hz reading never changed

    @property
    def minutes(self) -> int:
        return len(next(iter(self.values.values())))

    @property
    def kind(self) -> str:
        return EVENTS[self.event]


def load(path) -> Instance:
    """One 3W instance (a parquet file) as 1-minute medians of the three readings."""
    import pyarrow.parquet as pq

    path = Path(path)
    t = pq.read_table(path, columns=["timestamp", *VARS.values(), "class"])
    secs = t.column("timestamp").to_numpy().astype("datetime64[s]").astype(np.int64)
    minute = (secs - secs[0]) // 60
    n = int(minute[-1]) + 1
    values, flat = {}, {}
    for var, col in VARS.items():
        v = t.column(col).to_numpy(zero_copy_only=False).astype(float)
        ok = ~np.isnan(v)
        m = np.full(n, np.nan)
        for k in np.unique(minute[ok]):  # a few thousand minutes at most
            m[k] = np.median(v[ok & (minute == k)])
        values[var] = m
        edges = np.flatnonzero(np.concatenate([[True], np.diff(v) != 0, [True]]))  # where the value changes
        held = np.append(secs, secs[-1] + 1)  # a value holds until the next one (or a second past the last)
        flat[var] = [(int(minute[i]), int(minute[j - 1]) + 1) for i, j in itertools.pairwise(edges)
                     if ok[i] and held[j] - secs[i] >= signals.FLAT_HOURS * 3600]
    label = t.column("class").to_numpy(zero_copy_only=False).astype(float)
    upset = np.flatnonzero(label > 0)  # NaN (unlabelled) compares False
    event, well, _ = path.stem.split("_", 2)
    return Instance(path.stem, int(event[1:]), well, datetime.fromtimestamp(int(secs[0]), UTC).replace(tzinfo=None),
                    values, int(minute[upset[0]]) if len(upset) else None, flat)


def instances(root=ROOT) -> list[Instance]:
    """Every instance in the slice, normal ones first."""
    return [load(p) for p in sorted((Path(root) / "data").glob("*.parquet"))]


class Overlay:
    """A time-series source that serves `inst` in place of `well`'s WHP, WHT and CASP from `at` to the
    end of its record plus `hold` minutes, scaled to the well (see the module notes)."""

    def __init__(self, src, well: str, inst: Instance, at: datetime, hold: int = HOLD_MINUTES):
        self.src, self.well, self.inst, self.at, self.hold = src, well, inst, at, hold
        self.name = f"{src.name}+3w"
        self.end = at + timedelta(minutes=inst.minutes + hold)
        self._scale: dict[str, float] = {}

    def __getattr__(self, name):  # states() and the rest: the source underneath
        return getattr(self.src, name)

    def replayed(self, var: str) -> np.ndarray:
        """The instance's `var` on this well, a value per minute from `at` to the end of the hold."""
        v = self.inst.values[var]
        good = v[~np.isnan(v)]
        held = np.concatenate([v, np.full(self.hold, good[-1] if len(good) else np.nan)])
        if var not in self._scale:
            before = [x for _, x in self.src.series({"point": f"{self.well}.{var}", "uom": "", "upsets": []},
                                                    self.at - timedelta(hours=1), self.at - timedelta(minutes=1), 1)]
            ref = float(np.nanmedian(v[:REF_MINUTES])) if not np.isnan(v[:REF_MINUTES]).all() else float(np.median(good))
            self._scale[var] = float(np.median(before)) / ref if before and ref else 1.0
        return held * self._scale[var]

    def series(self, s, start, end, step_minutes):
        out = self.src.series(s, start, end, step_minutes)
        well, _, var = s["point"].rpartition(".")
        if well != self.well or var not in VARS or end < self.at or start >= self.end:
            return out
        rep = self.replayed(var)
        kept = []
        for t, x in out:
            i = int((datetime.fromisoformat(t) - self.at).total_seconds() // 60)
            if 0 <= i < len(rep):
                x = rep[i]
            if not np.isnan(x):
                kept.append((t, round(float(x), 4)))
        return kept

    def values(self, point, uom, start, end, step_minutes, upsets, description=""):
        return self.series({"point": point, "uom": uom, "upsets": upsets, "description": description}, start, end,
                           step_minutes)


def _read(src, point: str, start: datetime, end: datetime, step: int) -> tuple[list[datetime], np.ndarray]:
    vals = src.series({"point": point, "uom": "", "upsets": []}, start, end, step)
    return [datetime.fromisoformat(t) for t, _ in vals], np.array([x for _, x in vals], float)


def replay(src, inst: Instance, well: str = WELL, at: datetime = AT, window_minutes: int = WINDOW_MINUTES,
           step: int = STEP) -> dict:
    """One instance replayed onto `well` at `at`, with what change detection and instrument health found."""
    over = Overlay(src, well, inst, at)
    onset = at + timedelta(minutes=inst.onset) if inst.onset is not None else None
    lead = onset - timedelta(minutes=LEAD) if onset else None
    changes, health, false = [], [], []
    for var in VARS:
        times, v = _read(over, f"{well}.{var}", at - timedelta(hours=CONTEXT_HOURS), over.end, step)
        for d in signals.detect_changes(times, v, step, window_minutes=window_minutes):
            t = datetime.fromisoformat(d["start"])
            if t < at:  # the fake hours before are context, not scored
                continue
            d = {"point": var, **d, "right": bool(onset) and lead <= t <= onset + timedelta(minutes=LAG)}
            changes.append(d)
            if not onset or t < lead:
                false.append(f"{var} {d['kind']} {d['start']}")
        real = [i for i, t in enumerate(times) if at <= t < at + timedelta(minutes=inst.minutes)]
        flat = [(at + timedelta(minutes=a), at + timedelta(minutes=b)) for a, b in inst.flat[var]]
        for h in signals.health([times[i] for i in real], v[real], step) if real else []:
            a, b = datetime.fromisoformat(h["start"]), datetime.fromisoformat(h["end"])
            h = {"point": var, **h, "right": h["check"] == "frozen" and any(a < y and b > x for x, y in flat)}
            health.append(h)
            if not h["right"] and (not onset or a < lead):
                false.append(f"{var} {h['check']} {h['start']}")
    first = min((datetime.fromisoformat(d["start"]) for d in changes if d["right"]), default=None)
    frozen = [(var, a, b) for var, spans in inst.flat.items() for a, b in spans]
    return {"instance": inst.name, "event": inst.kind, "well": inst.well, "minutes": inst.minutes,
            "onset": onset.isoformat(timespec="minutes") if onset else None, "found": first is not None,
            "minutes_to_detect": round((first - onset).total_seconds() / 60) if first else None,
            "frozen": len(frozen),
            "frozen_found": sum(any(h["right"] and h["point"] == var and datetime.fromisoformat(h["start"]) <
                                    at + timedelta(minutes=b) and datetime.fromisoformat(h["end"]) > at + timedelta(minutes=a)
                                    for h in health) for var, a, b in frozen),
            "changes": changes, "health": health, "false_alarms": false}


def score(src, well: str = WELL, at: datetime = AT, window_minutes: int = WINDOW_MINUTES, step: int = STEP,
          root=ROOT) -> dict:
    """Every instance replayed onto `well`: upsets found, minutes to find them, frozen readings found, and
    false alarms."""
    runs = [replay(src, inst, well, at, window_minutes, step) for inst in instances(root)]
    upsets = [r for r in runs if r["onset"]]
    found = [r for r in upsets if r["found"]]
    times = [r["minutes_to_detect"] for r in found]
    normal = sum(r["minutes"] if not r["onset"] else
                 max(0.0, (datetime.fromisoformat(r["onset"]) - at).total_seconds() / 60 - LEAD) for r in runs)
    return {"well": well, "at": at.isoformat(timespec="minutes"), "window_minutes": window_minutes, "step": step,
            "upsets": len(upsets), "found": len(found), "recall": round(len(found) / len(upsets), 2) if upsets else None,
            "median_minutes_to_detect": float(np.median(times)) if times else None,
            "missed": [f"{r['instance']} ({r['event']})" for r in upsets if not r["found"]],
            "frozen": sum(r["frozen"] for r in runs), "frozen_found": sum(r["frozen_found"] for r in runs),
            "false_alarms": sum(len(r["false_alarms"]) for r in runs), "normal_hours": round(normal / 60, 1),
            "instances": runs,
            "basis": f"3W instances replayed onto {well} from {at:%Y-%m-%d %H:%M}, scaled to its level, the last value "
                     f"held {HOLD_MINUTES} min after each record, read at {step}-min steps; an upset is found by a "
                     f"change ({window_minutes}-min windows) on WHP, WHT or CASP from {LEAD} min before its onset to "
                     f"{LAG} min after; a frozen reading is one that never changed at 1 Hz for "
                     f"{signals.FLAT_HOURS} h or more; a change or health finding before an onset, or on a normal "
                     "instance, that isn't such a frozen reading is a false alarm"}

