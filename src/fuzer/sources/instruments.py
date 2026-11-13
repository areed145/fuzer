"""What instruments and the historian do to a fake plant's clean values (#868).

The process model (process_model.py) gives what the process did. Real readings also carry what the
instruments and the historian did to them, and a fake plant can list that in `instrument_faults.json`
next to its process model. The file is also the answer key: each fault says what it is, where, when,
and which instrument health check should find it.

- `frozen`: the reading holds its last value (a stale tag after a comms fault).
- `stuck`: it barely moves (a sticking transmitter or positioner): the change from where it stuck is
  cut to `factor` of the true change.
- `noisy`: noise of `sigma` (in the point's units) on top (a loose connection, a hunting loop).
- `pinned`: at the top of its range (`value`), for a transmitter driven past it.
- `drift`: walks away linearly, by `offset` at the end, then is recalibrated back (a fouled impulse
  line or a failing cell).
- `bad`: the historian stores a digital state (`state`, such as Bad Input) instead of a value.

`outages` are interface outages: every point on the interface (`points`) stores I/O Timeout for the
span, the way PI marks a gap when its interface node stops sending.

A fault applies in absolute time, so any window or step reads the same value, as the model does. Bad
values and outages come back as NaN from `apply`, and providers leave those times out, as the PI Web
API reader leaves out values that aren't good.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

KINDS = ("frozen", "stuck", "noisy", "pinned", "drift", "bad")
# What instrument health (analysis/signals.py) reports for each kind; None where no check should.
FINDS = {"frozen": "frozen", "stuck": "stuck", "noisy": "noisy", "pinned": "out_of_range", "drift": None, "bad": None}


def epoch(t: "datetime | str") -> float:
    """Seconds since 1970 of a time as the plants write them: naive, read as UTC (the model's absolute time)."""
    t = datetime.fromisoformat(t) if isinstance(t, str) else t
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).timestamp()


def naive(secs: float) -> datetime:
    return datetime.fromtimestamp(secs, UTC).replace(tzinfo=None)


def _noise(key: str, secs: np.ndarray) -> np.ndarray:
    from .timeseries import _unit_noise

    seed = int.from_bytes(key.encode()[:4].ljust(4, b"\0"), "big") ^ (len(key) << 20)
    u1 = np.clip(_unit_noise(seed, secs // 60), 1e-12, 1.0)
    u2 = _unit_noise(seed ^ 0x5BD1E995, secs // 60)
    return np.sqrt(-2 * np.log(u1)) * np.cos(2 * np.pi * u2)  # standard normal, fixed per (fault, minute)


class Faults:
    """A plant's planted instrument faults and interface outages, by point."""

    def __init__(self, spec: dict):
        self.faults = spec.get("faults", [])
        self.outages = spec.get("outages", [])
        self.by_point: dict[str, list[dict]] = {}
        for f in self.faults:
            self.by_point.setdefault(f["point"], []).append({**f, "a": epoch(f["start"]), "b": epoch(f["end"])})
        for o in self.outages:
            for p in o["points"]:
                self.by_point.setdefault(p, []).append({"kind": "bad", "state": o.get("state", "I/O Timeout"),
                                                        "a": epoch(o["start"]), "b": epoch(o["end"]), "id": o["id"]})

    def touches(self, point: str, start: datetime, end: datetime) -> list[dict]:
        a, b = epoch(start), epoch(end)
        return [f for f in self.by_point.get(point, ()) if f["a"] <= b and f["b"] >= a]

    def apply(self, point: str, secs: np.ndarray, v: np.ndarray, at) -> np.ndarray:
        """Values `v` at epoch seconds `secs` as the historian stores them. `at(t)` reads the clean value at
        epoch seconds t (for where a reading froze or stuck)."""
        v = np.array(v, float)
        for f in self.by_point.get(point, ()):
            inside = (secs >= f["a"]) & (secs < f["b"])
            if not inside.any():
                continue
            kind = f["kind"]
            if kind == "frozen":
                v[inside] = at(f["a"])
            elif kind == "stuck":
                held = at(f["a"])
                v[inside] = held + f.get("factor", 0.02) * (v[inside] - held)
            elif kind == "noisy":
                v[inside] += f["sigma"] * _noise(f["id"], secs[inside])
            elif kind == "pinned":
                v[inside] = f["value"]
            elif kind == "drift":
                v[inside] += f["offset"] * (secs[inside] - f["a"]) / (f["b"] - f["a"])
            elif kind == "bad":
                v[inside] = np.nan
        return v

    def states(self, point: str, start: datetime, end: datetime) -> list[tuple[float, float, str]]:
        """Digital states the historian holds for the point over the window: [(from, to, state)] in epoch seconds."""
        return [(f["a"], f["b"], f.get("state", "Bad Input")) for f in self.touches(point, start, end) if f["kind"] == "bad"]


def load(data_dir) -> Faults | None:
    path = Path(data_dir) / "instrument_faults.json" if data_dir else None
    if not path or not path.is_file():
        return None
    return _cached(str(path), path.stat().st_mtime)


_CACHE: dict[tuple[str, float], Faults] = {}


def _cached(path: str, mtime: float) -> Faults:
    if (path, mtime) not in _CACHE:
        _CACHE[(path, mtime)] = Faults(json.loads(Path(path).read_text()))
    return _CACHE[(path, mtime)]


def swinging_door(secs: np.ndarray, v: np.ndarray, dev: float) -> list[int]:
    """Indexes of the values a PI archive keeps under swinging-door compression with deviation `dev`:
    a value is kept when the line from the last kept value can no longer pass within `dev` of everything
    since. NaN (no value) is kept as its own event, as a digital state would be."""
    keep, n = [], len(v)
    if not n:
        return keep
    i = 0
    keep.append(0)
    while i < n - 1:
        if np.isnan(v[i]):
            j = i + 1
            while j < n and np.isnan(v[j]):
                j += 1
            if j < n:
                keep.append(j)
            i = j
            continue
        lo, hi, j = -np.inf, np.inf, i + 1
        while j < n and not np.isnan(v[j]):
            dt = secs[j] - secs[i]
            lo, hi = max(lo, (v[j] - dev - v[i]) / dt), min(hi, (v[j] + dev - v[i]) / dt)
            if lo > hi:
                break
            j += 1
        # the last value the door still covered is kept, and the next segment starts there
        last = j - 1 if j - 1 > i else j
        if last >= n:
            break
        keep.append(last)
        i = last
    if keep[-1] != n - 1:
        keep.append(n - 1)
    return sorted(set(keep))
