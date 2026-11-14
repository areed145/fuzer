"""Coupled synthetic process values (#91): a small model of each unit, evaluated over time.

A fake plant can describe itself in `process_model.json` in its data folder: its units (compressor trains,
compressors, coolers, vessels, lift gas headers, jackets, wells, pumps, meters, valves, values a controller
holds, control loops with their setpoint, output and mode), which PI point measures which of their variables, and timed events (trips, shutdowns, a bearing
wearing, a cooler fouling, a gas lift valve failing open, choke moves, hydrates, a control loop cycling). Values
then follow from simple relations with first-order lags rather than being drawn per point, so causes lead effects:
vibration before bearing temperature, a compressor trip before the lift gas header falls, the header before the
wells' gas lift rates.

Everything is a function of absolute time on a fixed 5-minute grid (inputs are stateless; lags are
truncated convolutions with a warm-up before the window), so a timestamp reads the same value from any
window or step, as with the per-point provider. Points the model doesn't cover fall back to it.
"""

import json
import math
import threading
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

DT = 5  # minutes per grid step
WARM = timedelta(hours=12)  # history evaluated before a window, so lags settle the same way
EPOCH = datetime(1970, 1, 1)


def minutes(t: datetime | str) -> float:
    if isinstance(t, str):
        t = datetime.fromisoformat(t)
    return (t.replace(tzinfo=None) - EPOCH).total_seconds() / 60


@lru_cache(maxsize=4)
def _load(path: str, mtime: float, day: str) -> "ProcessModel":  # `day` keys the cache: a new day reads it again
    return ProcessModel(read(Path(path)))


def read(path, today: date | None = None) -> dict:
    """A process_model.json (or the folder holding one) as a spec, its relative event times (`today-1d 03:10`,
    relative_dates) placed on the calendar, so a demonstration facility's upsets stay recent (#456)."""
    from . import relative_dates

    path = Path(path)
    path = path / "process_model.json" if path.is_dir() else path
    spec = json.loads(path.read_text())
    if "events" in spec:
        spec["events"] = relative_dates.resolve_all(spec["events"], today)
    return spec


def load(data_dir) -> "ProcessModel | None":
    """The model in a data folder, if it has one (cached until the file changes or the day turns)."""
    if not data_dir:
        return None
    p = Path(data_dir) / "process_model.json"
    return _load(str(p), p.stat().st_mtime, _today()) if p.is_file() else None


class ProcessModel:
    def __init__(self, spec: dict):
        self.seed = int(spec.get("seed", 0))
        self.levels = [tuple(x) for x in spec.get("levels", [])]  # typical levels of uncovered points, by description
        # The site's air temperature (degC): its mean, diurnal and annual swings and the day of the year it peaks.
        # Offshore tropics are the default; an onshore plant inland sets its own. `coolant` is what the
        # coolers reject heat to: "sea" (seawater at `sea_mean` +- `sea_annual`, the default) or "air" (ambient).
        self.climate = {"mean": 27.5, "diurnal": 2.5, "annual": 1.5, "peak_day": 80, "coolant": "sea",
                        "sea_mean": 26.5, "sea_annual": 1.5, **spec.get("climate", {})}
        self.points: dict[str, tuple[str, str]] = {k: tuple(v) for k, v in spec["points"].items()}
        self.units: dict[str, dict] = spec["units"]
        self.events: dict[str, list[dict]] = {}
        from . import relative_dates

        for e in relative_dates.resolve_all(spec.get("events", [])):
            self.events.setdefault(e["unit"], []).append(e)
        self._windows: dict[tuple[int, int], Window] = {}
        self._lock = threading.Lock()

    def at(self, unit: str, var: str, when: datetime) -> float:
        """One variable of one unit at one time (the generator scales well tests with `bp_factor`)."""
        w = self.window(when - timedelta(hours=1), when)
        return float(np.interp(minutes(when), w.t, w.get(unit, var)))

    def covers(self, point: str) -> bool:
        p = self.points.get(point)
        return bool(p) and p[0] in self.units

    def values(self, point: str, start: datetime, end: datetime, step_minutes: int) -> list[tuple[str, float]]:
        unit, var = self.points[point]
        w = self.window(start, end)
        y = w.get(unit, var)
        times, t = [], start
        while t <= end:
            times.append(t)
            t += timedelta(minutes=step_minutes)
        out = np.interp([minutes(x) for x in times], w.t, y)
        return [(x.isoformat(timespec="minutes"), round(float(v), 3)) for x, v in zip(times, out) if not np.isnan(v)]

    def fine(self, point: str, start: datetime, end: datetime, step_seconds: int) -> list[tuple[str, float]]:
        """Values every `step_seconds` (#358). The grid is 5 minutes, so between grid times a value is interpolated,
        except across a loop's step test, where the output and the value follow the test as a fast historian would
        record them."""
        unit, var = self.points[point]
        n = int((end - start).total_seconds() // step_seconds) + 1
        secs = np.arange(n) * step_seconds
        tm = minutes(start) + secs / 60
        w = self.window(start, end)
        y = np.interp(tm, w.t, w.get(unit, var))
        u = self.units[unit]
        for e in self.events.get(unit, []) if u["type"] == "loop" and var != "sp" else []:
            if e["kind"] != "step_test":
                continue
            on = (tm >= minutes(e["start"])) & (tm < minutes(e["end"]))
            if not on.any():
                continue
            op, move = step_response(e, tm)
            if var == "mode":
                y = np.where(on, 0.0, y)
            elif var == "op":
                y = np.where(on, op, y)
            else:
                h = sum(ord(c) * 131 ** i for i, c in enumerate(point)) % (2 ** 61)
                z = _splitmix(np.round(tm * 60).astype(np.int64).astype(np.uint64) ^ np.uint64(h ^ self.seed))
                noise = (z >> np.uint64(11)).astype(float) / float(1 << 53) * 2 - 1
                sd = 0.3 * float(u.get("pv_noise", 0.5))
                y = np.where(on, np.interp(tm, w.t, w.get(unit, "sp")) + move + sd * noise, y)
        return [((start + timedelta(seconds=int(x))).isoformat(timespec="seconds"), round(float(v), 4))
                for x, v in zip(secs, y)]

    def window(self, start: datetime, end: datetime) -> "Window":
        a = math.floor(minutes(start - WARM) / DT) * DT
        b = math.ceil(minutes(end) / DT) * DT + DT
        with self._lock:
            w = self._windows.get((a, b))
            if w is None:
                if len(self._windows) > 8:
                    self._windows.pop(next(iter(self._windows)))
                w = self._windows[(a, b)] = Window(self, a, b)
        return w


def step_response(e: dict, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A `step_test` event's controller output and the process value's move from where it started (#358), at times `t`
    (minutes since the epoch): the output steps from `op_from` to `op_to` at `at`, and the value answers as a first
    order process, gain `k` (value per % of output), time constant `tau_s` and dead time `dead_s` (seconds)."""
    dt = (t - minutes(e["at"])) * 60
    step = float(e["op_to"]) - float(e["op_from"])
    op = float(e["op_from"]) + step * (dt >= 0)
    late = np.maximum(dt - float(e["dead_s"]), 0)
    return op, float(e["k"]) * step * (1 - np.exp(-late / float(e["tau_s"])))


def _splitmix(x: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore"):
        x = (x + np.uint64(0x9E3779B97F4A7C15)).astype(np.uint64)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        return x ^ (x >> np.uint64(31))


class Window:
    """One stretch of grid time: variables computed on demand and kept for the window's other points."""

    def __init__(self, model: ProcessModel, a: int, b: int):
        self.m = model
        self.t = np.arange(a, b + DT, DT, dtype=float)
        self.idx = (self.t // DT).astype(np.int64)
        self.memo: dict[tuple[str, str], np.ndarray] = {}

    # ---- building blocks: all stateless in absolute time, except lag (a convolution) ----

    def noise(self, key: str, tau: float = 0.0) -> np.ndarray:
        """Deterministic noise in about [-1, 1] at each grid time; smoothed over tau minutes."""
        h = sum(ord(c) * 131 ** i for i, c in enumerate(key)) % (2 ** 61)
        x = _splitmix(self.idx.astype(np.uint64) ^ np.uint64(h ^ self.m.seed))
        n = (x >> np.uint64(11)).astype(float) / float(2 ** 53) * 2 - 1
        return self.lag(n, tau) * (1 + tau / 20) ** 0.5 if tau else n

    def lag(self, x: np.ndarray, tau: float) -> np.ndarray:
        """First-order lag (time constant tau minutes) as a truncated causal convolution."""
        if tau <= 0:
            return x
        k = np.exp(-np.arange(0, int(5 * tau / DT) + 1) * DT / tau)
        k /= k.sum()
        pad = np.concatenate([np.full(len(k) - 1, x[0]), x])
        return np.convolve(pad, k, mode="valid")

    def between(self, start: str, end: str | None) -> np.ndarray:
        a, b = minutes(start), minutes(end) if end else math.inf
        return ((self.t >= a) & (self.t < b)).astype(float)

    def path(self, points: list[list], default: float = 0.0) -> np.ndarray:
        """Piecewise linear through [(time, value)], `default` outside."""
        ts = [minutes(p[0]) for p in points]
        vs = [float(p[1]) for p in points]
        y = np.interp(self.t, ts, vs)
        return np.where((self.t < ts[0]) | (self.t > ts[-1]), default, y)

    def diurnal(self, peak_hour: float = 15) -> np.ndarray:
        return np.cos((self.t / 60 % 24 - peak_hour) / 24 * 2 * math.pi)

    def annual(self, peak_day: float = 80) -> np.ndarray:
        return np.cos((self.t / 1440 % 365.25 - peak_day) / 365.25 * 2 * math.pi)

    def ev(self, unit: str, kind: str) -> list[dict]:
        return [e for e in self.m.events.get(unit, []) if e["kind"] == kind]

    def any_between(self, unit: str, kind: str) -> np.ndarray:
        out = np.zeros_like(self.t)
        for e in self.ev(unit, kind):
            out = np.maximum(out, self.between(e["start"], e.get("end")))
        return out

    def cycles(self, unit: str) -> np.ndarray:
        """`oscillation` events: a control loop cycling (P4.2b, #354), a sine for a loop that hunts or a sawtooth for
        one limit-cycling on a sticking valve, of `amplitude` about the value and `period` minutes."""
        out = np.zeros_like(self.t)
        for e in self.ev(unit, "oscillation"):
            phase = (self.t - minutes(e["start"])) / float(e["period"])
            wave = 2 * (phase % 1) - 1 if e.get("shape") == "sawtooth" else np.sin(2 * math.pi * phase)
            out = out + self.between(e["start"], e.get("end")) * float(e["amplitude"]) * wave
        return out

    def heading(self, unit: str) -> np.ndarray:
        """`heading` events: a gas-lifted well heading (P3.1c, #341), its casing pressure and wellhead pressure swinging
        by `amplitude` (a fraction) about their level every `period` minutes as lift gas surges through the valve."""
        out = np.zeros_like(self.t)
        for e in self.ev(unit, "heading"):
            phase = (self.t - minutes(e["start"])) / float(e["period"])
            out = out + self.between(e["start"], e.get("end")) * float(e["amplitude"]) * np.sin(2 * math.pi * phase)
        return out

    def get(self, unit: str, var: str) -> np.ndarray:
        key = (unit, var)
        if key not in self.memo:
            u = self.m.units[unit]
            fn = getattr(self, f"_{u['type']}")
            self.memo[key] = np.asarray(fn(unit, u, var), dtype=float)
        return self.memo[key]

    # ---- field ----

    def ambient(self) -> np.ndarray:
        c = self.m.climate
        return c["mean"] + c["diurnal"] * self.diurnal() + c["annual"] * self.annual(c["peak_day"])

    def shutdown(self) -> np.ndarray:
        """1 while the whole field is down for planned maintenance."""
        return self.any_between("field", "shutdown")

    # ---- compression ----

    def _train(self, unit, u, var):
        if var == "running":
            down = np.maximum(self.any_between(unit, "trip"), self.any_between(unit, "down"))
            down = np.maximum(down, self.shutdown())
            return self.lag(1 - down, 10)
        if var == "load":  # recycle during well tests etc. is noise around full load
            return self.get(unit, "running") * (0.92 + 0.04 * self.noise(unit + "load", 60))
        raise KeyError(var)

    def _compressor(self, unit, u, var):
        run = self.get(u["train"], "running")
        load = self.get(u["train"], "load")
        if var == "suction_pressure":
            return u["p_suction"] * (1 + 0.01 * self.noise(unit + "ps", 30))
        if var == "discharge_pressure":
            ps = self.get(unit, "suction_pressure")
            settle = u.get("p_settle", (u["p_suction"] + u["p_discharge"]) / 2)
            pd = ps + (u["p_discharge"] - ps) * load
            return self.lag(run * pd + (1 - run) * settle, 8) + 0.4 * self.noise(unit + "pd", 10)
        if var in ("forward", "recycle", "flow", "asv"):
            return self._surge_control(unit, u, var, run, load)
        if var == "vibration":
            wear = sum((self.path(e["points"]) for e in self.ev(unit, "vibration")), np.zeros_like(self.t))
            v = (u.get("vibration", 2.2) + wear) * (1 + 0.06 * self.noise(unit + "v", 15)) + 3.0 * self._surging(unit, 5)
            return self.lag(run * v + (1 - run) * 0.3, 5)
        if var == "shaft_vibration":  # um pk-pk at the proximity probes: follows the casing, about 9 um per mm/s
            return run * 9.0 * self.get(unit, "vibration") * (1 + 0.03 * self.noise(unit + "sv", 10)) + (1 - run) * 3.0
        if var in ("axial_position", "thrust_temperature"):
            # the rotor's axial position (mm toward the active thrust pads) and those pads' temperature: a worn thrust
            # bearing walks it over weeks; a surge reverses the thrust for a moment
            shift = sum((self.path(e["points"]) for e in self.ev(unit, "thrust")), np.zeros_like(self.t))
            if var == "axial_position":
                return (run * (0.12 + shift - 0.35 * self._surging(unit, 5))
                        + (1 - run) * 0.02 + 0.008 * self.noise(unit + "ax", 10))
            hot = 74 + 0.3 * (self.ambient() - 27) + 70 * shift + 4 * load
            return self.lag(run * hot + (1 - run) * (self.ambient() + 15), 20) + 0.4 * self.noise(unit + "tb", 15)
        if var == "journal_temperature":  # babbitt pads: oil temperature plus friction, more with vibration
            vib_excess = np.maximum(self.get(unit, "vibration") - u.get("vibration", 2.2) * 1.3, 0)
            hot = 79 + 0.3 * (self.ambient() - 27) + 3 * load + 2.5 * self.lag(vib_excess, 30)
            return self.lag(run * hot + (1 - run) * (self.ambient() + 15), 20) + 0.4 * self.noise(unit + "jb", 15)
        if var == "seal_vent":  # Nm3/h through the primary vent of the dry gas seals; a worn seal passes more
            wear = sum((self.path(e["points"]) for e in self.ev(unit, "seal_wear")), np.zeros_like(self.t))
            base = u.get("seal_vent", 3.5) * (0.6 + 0.4 * u["p_suction"] / 37)
            return (run + (1 - run) * 0.3) * base * (1 + wear) * (1 + 0.04 * self.noise(unit + "sg", 20))
        if var == "discharge_temperature":
            # polytropic rise from suction temperature, plus heat from a failing bearing after a lag
            ts = self.get(u["cooler_before"], "outlet_temperature") if u.get("cooler_before") else self.ambient() + 8
            if u.get("recycle_cooler"):  # anti-surge recycle through the stage's own after-cooler
                r = u.get("recycle", 0.3)
                ts = (1 - r) * ts + r * self.get(u["recycle_cooler"], "outlet_temperature")
            ratio = (u["p_discharge"] + 1) / (u["p_suction"] + 1)
            td = (ts + 273.15) * ratio ** u.get("t_exponent", 0.27) - 273.15  # (k - 1) / (k eta_p) for its gas
            extra = sum((self.path(e["points"]) for e in self.ev(unit, "discharge_temperature")), np.zeros_like(self.t))
            vib_excess = np.maximum(self.get(unit, "vibration") - u.get("vibration", 2.2) * 1.3, 0)
            hot = run * (td + extra + 12 * self._surging(unit, 10)) + 3.5 * self.lag(vib_excess, 30)
            return self.lag(hot + (1 - run) * self.ambient(), 20) + 0.6 * self.noise(unit + "td", 15)
        raise KeyError(var)

    def _recip(self, unit, u, var):
        """A two-stage reciprocating compressor: suction, interstage and discharge pressure, a discharge temperature per
        cylinder (`throws` maps each throw to its stage), speed and frame vibration. A `valve_leak` on a throw makes
        that cylinder run hot (the leaking valve recompresses gas it already discharged) and costs its stage capacity,
        so a leak on the 2nd stage pushes the interstage pressure up and loads the 1st stage throws harder."""
        run = self.get(u["train"], "running")
        load = self.get(u["train"], "load")
        throws = {int(k): int(v) for k, v in u.get("throws", {"1": 1, "2": 2}).items()}
        leak = {n: sum((self.path(e["points"]) for e in self.ev(unit, "valve_leak") if int(e.get("throw", 0)) == n),
                       np.zeros_like(self.t)) for n in throws}
        settle = u.get("p_settle", (u["p_suction"] + u["p_discharge"]) / 2)
        if var == "suction_pressure":
            return u["p_suction"] * (1 + 0.01 * self.noise(unit + "ps", 30))
        if var == "speed":
            return self.lag(run * u.get("rpm", 1180) * (1 + 0.002 * self.noise(unit + "n", 10)), 2)
        if var in ("interstage_pressure", "discharge_pressure"):
            ps = self.get(unit, "suction_pressure")
            target = u["p_discharge"] if var == "discharge_pressure" else (
                math.sqrt((u["p_suction"] + 1) * (u["p_discharge"] + 1)) - 1
                + 4.0 * sum((leak[n] for n, st in throws.items() if st == 2), np.zeros_like(self.t))
                - 4.0 * sum((leak[n] for n, st in throws.items() if st == 1), np.zeros_like(self.t)))
            p = ps + (target - ps) * load
            return self.lag(run * p + (1 - run) * settle, 8) + 0.3 * self.noise(unit + var, 10)
        if var == "vibration":
            wear = sum((self.path(e["points"]) for e in self.ev(unit, "vibration")), np.zeros_like(self.t))
            v = (u.get("vibration", 2.4) + wear) * (1 + 0.05 * self.noise(unit + "v", 15))
            # a failed transmitter or a loose probe cable reads what it reads, whatever the frame does
            fault = sum((self.path(e["points"]) for e in self.ev(unit, "signal_fault") if e.get("var") == var),
                        np.zeros_like(self.t))
            return np.where(fault > 0, fault, self.lag(run * v + (1 - run) * 0.3, 5))
        if var == "lube_pressure":  # barg in the frame's oil gallery: the shaft-driven pump makes it with speed
            n = self.get(unit, "speed") / u.get("rpm", 1180)
            weak = np.maximum(self.any_between(unit, "lube_fault"), 0)  # no prelube: it never builds
            return (4.3 * np.clip(n, 0, 1) * (1 - 0.75 * weak) + 0.2 * (1 - np.clip(n, 0, 1))
                    + 0.04 * self.noise(unit + "lo", 10))
        if var.startswith("engine_cylinder_"):  # the gas engine driver's exhaust temperature at each cylinder; a
            n = int(var.rsplit("_", 1)[1])  # misfiring cylinder passes unburnt mixture and reads cold
            off = {1: 6.0, 2: -4.0, 3: 3.0, 4: -7.0, 5: 2.0, 6: -2.0, 7: 5.0, 8: -3.0}.get(n, 0.0)
            miss = sum((self.path(e["points"]) for e in self.ev(unit, "misfire") if int(e.get("cylinder", 0)) == n),
                       np.zeros_like(self.t))
            amb = self.ambient()
            hot = 470 + 60 * load + 0.3 * amb + off - 160 * miss
            return self.lag(run * hot + (1 - run) * (amb + 20), 10) + 2.0 * self.noise(f"{unit}ec{n}", 10)
        if var.startswith("cylinder_temperature_") or var == "discharge_temperature":
            ps, pi, pd = (self.get(unit, k) for k in ("suction_pressure", "interstage_pressure", "discharge_pressure"))
            amb = self.ambient()
            stage_td = {1: (amb + 8 + 273.15) * np.maximum((pi + 1) / (ps + 1), 1) ** 0.45 - 273.15,
                        2: (amb + 12 + 273.15) * np.maximum((pd + 1) / (pi + 1), 1) ** 0.45 - 273.15}
            offset = {1: 0.8, 2: -0.6, 3: 0.4, 4: -0.5}

            def cyl(n):
                hot = stage_td[throws[n]] + offset.get(n, 0.0) + 20 * leak[n]
                return self.lag(run * hot + (1 - run) * amb, 20) + 0.5 * self.noise(f"{unit}td{n}", 15)
            if var != "discharge_temperature":
                return cyl(int(var.rsplit("_", 1)[1]))
            last = [n for n, st in throws.items() if st == max(throws.values())]
            td = sum(cyl(n) for n in last) / len(last)
            extra = sum((self.path(e["points"]) for e in self.ev(unit, "discharge_temperature")), np.zeros_like(self.t))
            vib_excess = np.maximum(self.get(unit, "vibration") - u.get("vibration", 2.4) * 1.3, 0)
            return td + self.lag(run * extra, 5) + 2.0 * self.lag(vib_excess, 30)
        raise KeyError(var)

    def _surging(self, unit, span: float) -> np.ndarray:
        """1 for `span` minutes from each `surge` event on a compressor (an outright surge), else 0."""
        out = np.zeros_like(self.t)
        for e in self.ev(unit, "surge"):
            a = minutes(e["at"])
            out = np.maximum(out, ((self.t >= a) & (self.t < a + span)).astype(float))
        return out

    def _surge_control(self, unit, u, var, run, load):
        """Flow through a stage (actual inlet m3/h) and its anti-surge valve: the forward flow follows the train's
        load (its speed) and its share of the inlet gas; when that falls below the control line (`control_line`
        times the surge flow at this speed) the valve opens and recycles the difference, after `asv_lag` minutes."""
        if var == "forward":
            share = np.ones_like(self.t)
            for e in self.ev(u["train"], "share"):
                share = share * self.path(e["points"], 1.0)
            q = u.get("q_design", 1000.0) * load * share * (1 + 0.015 * self.noise(unit + "q", 20))
            for e in self.ev(unit, "swings"):  # load swings: the forward flow dips for half an hour or so
                q = q * (1 - e["depth"] * np.maximum(self.noise(unit + "sw", 30), 0) * self.between(e["start"], e.get("end")))
            return q
        if var == "recycle":
            line = u.get("q_surge", 0.62 * u.get("q_design", 1000.0)) * load * u.get("control_line", 1.1)
            return self.lag(np.maximum(line - self.get(unit, "forward"), 0), u.get("asv_lag", 5))
        if var == "flow":  # a surge: flow collapses and reverses for a few minutes
            return run * (self.get(unit, "forward") + self.get(unit, "recycle")) * (1 - 0.6 * self._surging(unit, 5))
        open_pct = np.clip(self.get(unit, "recycle") / (0.4 * u.get("q_design", 1000.0)) * 100, 0, 100)
        snap = np.zeros_like(self.t)  # the controller's surge response: the valve steps fully open, then eases back
        for e in self.ev(unit, "surge"):
            a = minutes(e["at"])
            snap = np.maximum(snap, np.interp(self.t, [a - DT, a, a + 15, a + 45], [0, 100, 100, 0], left=0, right=0))
        return run * np.maximum(open_pct, snap) + (1 - run) * 100  # asv: fully open while the train is stopped

    def _gas_turbine(self, unit, u, var):
        load = self.get(u["train"], "load")
        if var == "speed":
            return self.lag(load * u.get("rpm", 14950) * (1 + 0.003 * self.noise(unit + "n", 20)), 5)
        if var == "exhaust_temperature":
            return self.lag(self.ambient() + load * (u.get("exhaust", 485) - 30 + 0.4 * (self.ambient() - 27)), 25)
        if var == "power":  # kW the compressors take: the driver's duty share of ISO output at the train's load and
            share = np.ones_like(self.t)  # its share of the inlet gas, never under what its stages recycle to hold
            for e in self.ev(u["train"], "share"):  # their control line
                share = share * self.path(e["points"], 1.0)
            share = np.maximum(share, 0.62 * 1.1)
            return load * share * u.get("duty", 0.57) * u.get("iso_kw", 5670.0) * (1 + 0.01 * self.noise(unit + "kw", 20))
        if var == "lube_pressure":  # barg at the lube oil header: the pumps run while it turns; a plugging filter
            plug = sum((self.path(e["points"]) for e in self.ev(unit, "lube_filter")), np.zeros_like(self.t))
            return u.get("lube_pressure", 2.6) - plug + 0.02 * self.noise(unit + "lp", 10)
        if var == "lube_temperature":  # degC at the supply, after the oil cooler
            hot = 45 + 0.4 * (self.ambient() - 27) + 3 * load
            return self.lag(hot, 30) + 0.3 * self.noise(unit + "lt", 15)
        if var.startswith("exhaust_tc_"):  # each thermocouple around the exhaust: the mean, its own offset, and a
            n = int(var.rsplit("_", 1)[1])  # lean combustor upstream of it reads colder
            off = {1: 4.0, 2: -3.0, 3: 6.0, 4: -2.0, 5: -6.0, 6: 1.0}.get(n, 0.0)
            cold = sum((self.path(e["points"]) for e in self.ev(unit, "combustor") if int(e.get("tc", 0)) == n),
                       np.zeros_like(self.t))
            return self.get(unit, "exhaust_temperature") + load * (off + cold) + 1.2 * self.noise(f"{unit}tc{n}", 10)
        if var == "fuel_flow":  # Sm3/h: output times the heat rate at that load and ambient, worse as it fouls
            amb = self.ambient()
            avail = u.get("iso_kw", 5670.0) * (1 - 0.0075 * (amb - 15))
            pw = self.get(unit, "power")
            frac = np.clip(pw / np.maximum(avail, 1), 0.3, 1.0)
            hr = u.get("iso_heat_rate", 11300.0) * (1 + 0.55 * (1 - frac) ** 1.6) * (1 + 0.0032 * (amb - 15))
            foul = sum((self.path(e["points"]) for e in self.ev(unit, "heat_rate")), np.zeros_like(self.t))
            return pw * hr * (1 + foul) / (u.get("lhv", 36.0) * 1000) * (1 + 0.008 * self.noise(unit + "ff", 15))
        raise KeyError(var)

    def _weather(self, unit, u, var):
        if var != "ambient":
            raise KeyError(var)
        return self.ambient() + 0.3 * self.noise(unit + "amb", 30)

    def _cooler(self, unit, u, var):
        if var != "outlet_temperature":
            raise KeyError(var)
        run = self.get(u["train"], "running") if u.get("train") else 1.0
        fouling = sum((self.path(e["points"]) for e in self.ev(unit, "fouling")), np.zeros_like(self.t))
        c = self.m.climate
        medium = (self.ambient() if u.get("coolant", c["coolant"]) == "air"
                  else c["sea_mean"] + c["sea_annual"] * self.annual())
        approach = u.get("approach", 9.0) * (1 + 0.15 * self.diurnal()) + fouling * (1 + 0.5 * np.maximum(self.diurnal(), 0))
        hot = medium + approach
        return self.lag(run * hot + (1 - run) * self.ambient(), 30) + 0.3 * self.noise(unit + "to", 15)

    def _vessel(self, unit, u, var):
        run = self.get(u["train"], "running") if u.get("train") else 1 - self.shutdown()
        if var == "setpoint":  # its pressure controller's setpoint, moved by `setpoint` events
            p = np.full_like(self.t, float(u.get("pressure", 12.0)))
            for e in sorted(self.ev(unit, "setpoint"), key=lambda e: e["at"]):
                p = np.where(self.t >= minutes(e["at"]), float(e["to"]), p)
            return p
        if var == "vessel_pressure":
            return self.lag(run * self.get(unit, "setpoint") + (1 - run) * 1.0, 15) * (1 + 0.01 * self.noise(unit + "p", 15))
        if var in ("liquid_level", "true_level"):
            lvl = u.get("level", 45.0) + 4 * self.noise(unit + "l", 20)  # the level controller hunting a little
            lvl = lvl + sum((self.path(e["points"]) for e in self.ev(unit, "level")), np.zeros_like(self.t))
            lvl = lvl + self.cycles(unit)  # its level controller cycling
            if u.get("carryover_from"):  # liquid arriving from upstream
                up = self.get(u["carryover_from"], "true_level")
                lvl = lvl + u.get("carryover_gain", 1.4) * self.lag(np.maximum(up - 75, 0), 20)
            lvl = np.clip(lvl, 0, 100)
            if var == "true_level":
                return lvl
            erratic = self.any_between(unit, "erratic")  # a transmitter hunting, not the level
            return np.clip(lvl + erratic * 22 * self.noise(unit + "err", 10), 0, 100)
        raise KeyError(var)

    # ---- lift gas and wells ----

    def _header(self, unit, u, var):
        if var == "supply":  # compressed lift gas available against what the wells want, 0..1+
            if u.get("parent"):
                return self.get(u["parent"], "supply")
            trains = sum(self.get(t, "running") for t in u["trains"]) / len(u["trains"])
            gas = 1 - sum(u.get("gas_share", {}).get(w, 0) * (1 - self.get(w, "flowing")) for w in u.get("gas_wells", []))
            bypass = self.get(u["bypass"], "position") / 100 * 0.05 if u.get("bypass") else 0
            return np.minimum(trains * 1.15, gas * 1.15) + bypass
        if var == "pressure":
            if u.get("parent"):
                return self.get(u["parent"], "pressure") - u.get("dp", 3.0) + 0.3 * self.noise(unit + "p", 20)
            s = np.minimum(self.get(unit, "supply"), 1.0)
            return self.lag(u["p0"] * s ** 1.5, 20) + 0.5 * self.noise(unit + "p", 20)
        if var == "flow":
            s = np.minimum(self.get(unit, "supply"), 1.0)
            return self.lag(u["flow0"] * s ** 0.8, 10) * (1 + 0.01 * self.noise(unit + "f", 30))
        raise KeyError(var)

    def _jacket(self, unit, u, var):
        if var == "gas_lift_supply":
            return sum((self.get(w, "glir") for w in u["gl_wells"]), np.zeros_like(self.t)) * \
                (1 + 0.01 * self.noise(unit + "gl", 20))
        if var == "production_header_pressure0":  # at the separator's normal setpoint, from the wells' rates
            q = sum((self.get(w, "liquid0") for w in u["wells"]), np.zeros_like(self.t))
            return 10.5 + 0.004 * q
        if var == "production_header_pressure":  # follows the separator it flows to when its setpoint moves
            sep = u.get("separator")
            offset = self.get(sep, "setpoint") - float(self.m.units[sep].get("pressure", 12.0)) if sep in self.m.units else 0.0
            return self.get(unit, "production_header_pressure0") + offset + 0.2 * self.noise(unit + "php", 20)
        if var == "esdv":
            return 100 * (1 - self.shutdown())
        raise KeyError(var)

    def _well(self, unit, u, var):
        if var == "flowing":  # shut in for interventions, a planned shutdown or a hydrate
            off = np.maximum(self.any_between(unit, "shut_in"), self.shutdown())
            return self.lag(1 - off, 15)
        if var == "choke":
            c = np.full_like(self.t, float(u["choke0"]))
            for e in sorted(self.ev(unit, "choke"), key=lambda e: e["at"]):
                c = np.where(self.t >= minutes(e["at"]), float(e["to"]), c)
            return c * self.get(unit, "flowing") ** 0.2
        if var == "glir":
            if u.get("lift") != "GL":
                return np.zeros_like(self.t)
            hp = self.get(u["header"], "pressure") / self.m.units[u["header"]].get("p0", 88)
            failed = self.any_between(unit, "valve_failed_open")
            g = u["glir0"] * np.clip(hp, 0, 1.05) ** 2 * (1 + 0.45 * failed)
            return self.lag(g * self.get(unit, "flowing"), 10) * (1 + 0.02 * self.noise(unit + "g", 20))
        if var == "casp":
            if u.get("lift") != "GL":
                return np.zeros_like(self.t)
            failed = self.any_between(unit, "valve_failed_open")
            hp = self.get(u["header"], "pressure")
            return self.lag(hp * (0.82 - 0.14 * failed) * (0.6 + 0.4 * self.get(unit, "flowing")), 15) * \
                (1 + self.heading(unit)) + 0.4 * self.noise(unit + "c", 20)
        if var == "cv":  # the choke's Cv at its opening (its characteristic), times wear: + eroded, - plugged
            c, o = u["choke"], np.clip(self.get(unit, "choke"), 0, 100)
            cv = c["cv_max"] * o / 100 if c["trim"] == "linear" else c["cv_max"] * 30 ** (o / 100 - 1)
            wear = sum((self.path(e["points"]) for e in self.ev(unit, "choke_wear")), np.zeros_like(self.t))
            return np.maximum(cv * (1 + wear), 1e-3)
        if var == "back":  # the header it flows into: its jacket's, or another one after a reroute
            back = self.get(u["jacket"], "production_header_pressure") if u.get("jacket") else np.full_like(self.t, 11.0)
            for e in sorted(self.ev(unit, "reroute"), key=lambda e: e["at"]):
                other = self.get(e["to"], "production_header_pressure")
                back = np.where(self.t >= minutes(e["at"]), other, back)
            return back
        if var == "back0":  # the back-pressure the well's tests were taken against
            return self.get(u["jacket"], "production_header_pressure0") if u.get("jacket") else np.full_like(self.t, 11.0)
        if var == "flowline":  # bar per (Sm3/d)^2 a restriction (wax, scale) adds downstream of the choke
            r = sum((self.path(e["points"]) for e in self.ev(unit, "flowline_restriction")), np.zeros_like(self.t))
            return 4.0 * np.clip(r, 0, None) / u["q_ref"] ** 2
        if var in ("bp_factor", "limit"):
            # Inflow at the wellhead: q = J (Psi - WHP), WHP = back + (choke + flowline) q^2 while the choke is
            # subcritical; J is set so the well makes its tested rate against the back-pressure of its tests.
            # At critical flow (back / WHP under 0.55) the back-pressure doesn't reach the well.
            q0 = np.maximum(self.get(unit, "liquid0"), 1e-6)
            c = u.get("sg", 0.9) * (4.403 / 24 / self.get(unit, "cv")) ** 2 / 14.5038
            psi = float(u.get("shut_in_whp", 45.0))
            pb0, pb = self.get(unit, "back0"), self.get(unit, "back")
            whp0 = pb0 + c * q0 ** 2
            crit0 = (pb0 + 1.013) / (whp0 + 1.013) < 0.55
            ct = c + self.get(unit, "flowline")
            room = np.maximum(psi - whp0, 1e-6)
            J = q0 / room
            d = np.maximum(psi - pb, 0.0)
            q = np.where(J * ct > 0, (-1 + np.sqrt(1 + 4 * J * ct * J * d)) / (2 * J * ct), J * d)
            crit = (pb + 1.013) / (pb + ct * np.minimum(q, q0) ** 2 + 1.013) < 0.55
            f = np.where(crit0 & crit, 1.0, np.clip(q / q0, 0, 3))
            if var == "bp_factor":
                return np.where(u.get("kind") == "gas", 1.0, f)
            flowing = self.get(unit, "flowing") > 0.5
            return np.where(~flowing, 0.0, np.where(crit, 1.0, 2.0))  # 0 shut in, 1 choke-limited, 2 back-pressure
        if var in ("liquid", "liquid0"):  # Sm3/d through the choke: the rate the well tests show, moved by lift gas now
            if u.get("kind") == "gas":
                return np.zeros_like(self.t)
            if u.get("liquid_path"):  # tests already carry valve failures and breakthroughs
                ts = [minutes(p[0]) for p in u["liquid_path"]]
                base = np.interp(self.t, ts, [p[1] for p in u["liquid_path"]])
                failed = 0.0
            else:
                base, failed = u["q_ref"], self.any_between(unit, "valve_failed_open")
            if u.get("lift") == "GL":
                g = self.get(unit, "glir")
                lift = (0.55 + 0.45 * (1 - np.exp(-g / u["knee"]))) / (0.55 + 0.45 * (1 - math.exp(-u["glir0"] / u["knee"])))
                lift = lift * (1 - 0.45 * failed)
            else:
                lift = 1.0
            q = base * lift * self.get(unit, "flowing") * (1 + 0.01 * self.noise(unit + "q", 60))
            return q if var == "liquid0" or not u.get("jacket") else q * self.get(unit, "bp_factor")
        if var == "qgas":  # kSm3/d through the choke against the reservoir's drawdown
            if u.get("kind") != "gas":
                return np.zeros_like(self.t)
            hyd = sum((self.path(e["points"]) for e in self.ev(unit, "restriction")), np.zeros_like(self.t))
            kc = u["k_gas"] * self.get(unit, "cv")
            q = kc * (u["p_res"] + 1.013) / (1 + kc * u["c_gas"])
            return self.lag(q * self.get(unit, "flowing") * (1 - 0.95 * np.clip(hyd, 0, 1)), 15) * \
                (1 + 0.01 * self.noise(unit + "qg", 30))
        if var == "whp":
            shut = 1 - self.get(unit, "flowing")
            if u.get("kind") == "gas":
                hyd = sum((self.path(e["points"]) for e in self.ev(unit, "restriction")), np.zeros_like(self.t))
                p = u["p_res"] - u["c_gas"] * self.get(unit, "qgas") + 30 * hyd  # a hydrate backs pressure up
                return p * (1 - shut) + shut * u["p_res"] + 0.2 * self.noise(unit + "whp", 10)
            q = self.get(unit, "liquid")
            gpm = q / 24 * 4.403
            p = self.get(unit, "back") + u.get("sg", 0.9) * (gpm / self.get(unit, "cv")) ** 2 / 14.5038 \
                + self.get(unit, "flowline") * q ** 2
            p = p * (1 + 0.5 * self.heading(unit))  # heading on lift: the wellhead swings with the casing
            return p * (1 - shut) + shut * u.get("shut_in_whp", 45) + 0.1 * self.noise(unit + "whp", 10)
        if var == "wht":
            if u.get("kind") == "gas":
                flow = self.get(unit, "qgas") / u["qgas0"]
                hyd = sum((self.path(e["points"]) for e in self.ev(unit, "restriction")), np.zeros_like(self.t))
                return self.lag(self.ambient() + 30 * flow - 12 * hyd, 30) + 0.3 * self.noise(unit + "t", 15)
            flow = self.get(unit, "liquid") / u["q_ref"]
            return self.lag(self.ambient() + 48 * np.clip(flow, 0, 1.5), 45) + 0.3 * self.noise(unit + "t", 15)
        raise KeyError(var)

    # ---- pumps, meters, valves ----

    def _pump(self, unit, u, var):
        if var == "running":  # duty pumps run unless stopped; the standby runs while it covers for one
            if u.get("duty", True):
                on = 1 - self.any_between(unit, "stopped")
            else:
                on = self.any_between(unit, "covering")
            return self.lag(on * (1 - self.shutdown()), 3)
        run = self.get(unit, "running")
        if var == "flow":  # m3/h: the group's flow shared by the pumps running
            group = [p for p, x in self.m.units.items() if x.get("type") == "pump" and x.get("group") == u["group"]]
            n = sum(self.get(p, "running") for p in group)
            total = self.get(u["meter"], "flow") / 24 if u.get("meter") else u["q_bep"] * 2
            spill = sum((self.path(e["points"]) for e in self.ev(unit, "recirculation")), np.zeros_like(self.t))
            return run * total / np.maximum(n, 0.5) * (1 + spill) * (1 + 0.01 * self.noise(unit + "q", 20))
        if var == "head":  # m: the pump's curve through shut-off head and the best efficiency point, less wear
            q = self.get(unit, "flow")
            loss = sum((self.path(e["points"]) for e in self.ev(unit, "head_loss")), np.zeros_like(self.t))
            return run * (u["h0"] - (u["h0"] - u["h_bep"]) * (q / u["q_bep"]) ** 2) * (1 - loss)
        if var == "suction_pressure":
            return u.get("p_suction", 3.0) * (1 + 0.01 * self.noise(unit + "ps", 20))
        if var == "discharge_pressure":
            dp = 850 * 9.81 * self.get(unit, "head") / 1e5
            return self.get(unit, "suction_pressure") + dp + 0.08 * self.noise(unit + "pd", 10)
        if var == "vibration":
            q = self.get(unit, "flow") / u["q_bep"]
            v = u.get("vibration", 1.8) * (1 + 1.2 * (q - 1) ** 2) * (1 + 0.05 * self.noise(unit + "v", 15))
            v = v + sum((self.path(e["points"]) for e in self.ev(unit, "vibration_rise")), np.zeros_like(self.t))  # mm/s
            return self.lag(run * v + (1 - run) * 0.2, 3)
        if var == "bearing_temperature":
            q = self.get(unit, "flow") / u["q_bep"]
            hot = self.ambient() + 28 * np.clip(q, 0, 1.5) + 4 * self.get(unit, "vibration")
            return self.lag(run * hot + (1 - run) * self.ambient(), 40) + 0.3 * self.noise(unit + "bt", 10)
        if var == "power":  # kW
            q = self.get(unit, "flow")
            loss = sum((self.path(e["points"]) for e in self.ev(unit, "head_loss")), np.zeros_like(self.t))
            eff = np.clip(u.get("eff_bep", 0.78) * (1 - (q / u["q_bep"] - 1) ** 2) * (1 - 0.6 * loss), 0.2, 0.85)
            return run * 850 * 9.81 * (q / 3600) * self.get(unit, "head") / eff / 1000
        raise KeyError(var)

    def _meter(self, unit, u, var):
        if var != "flow":
            raise KeyError(var)
        base = u["base"]
        up = 1 - self.shutdown()
        if u.get("header"):  # oil depends on lift gas
            s = np.minimum(self.get(u["header"], "supply"), 1.0)
            up = up * (1 - u.get("lift_share", 0.3) * (1 - s))
        cut = sum((self.between(e["start"], e["end"]) * e["factor"] for e in self.ev(unit, "curtail")),
                  np.zeros_like(self.t))
        swing = 1 + 0.04 * self.noise(unit + "slow", 360) + 0.01 * self.noise(unit + "m", 30)  # production, surge drum
        # `bias` events: the meter reads off the true flow by a share (a damaged orifice plate, a fouled ultrasonic
        # path), so it no longer agrees with the other meters on its stream
        bias = sum((self.path(e["points"]) for e in self.ev(unit, "bias")), np.zeros_like(self.t))
        return self.lag(base * up * (1 - np.clip(cut, 0, 1)), 20) * swing * (1 + bias)

    def _controlled(self, unit, u, var):
        """A value a controller or the process holds near one level: a reboiler or heater temperature, a filter or
        column differential pressure, an analyser reading, a utility header pressure, a flare's purge flow.
        `setpoint` events step it, `drift` events add a path (a burner fouling, foaming after a restart, a trip's
        flaring), and with a `train` it settles at `idle` while that train is down. With `since` there is no reading
        before it (an instrument not yet installed)."""
        if var != "value":
            raise KeyError(var)
        v = np.full_like(self.t, float(u["value"]))
        for e in sorted(self.ev(unit, "setpoint"), key=lambda e: e["at"]):
            v = np.where(self.t >= minutes(e["at"]), float(e["to"]), v)
        v = v + sum((self.path(e["points"]) for e in self.ev(unit, "drift")), np.zeros_like(self.t))
        if u.get("train"):
            run = self.get(u["train"], "running")
            v = run * v + (1 - run) * float(u.get("idle", u["value"]))
        noise = float(u.get("noise", 0.01 * abs(float(u["value"]))))
        v = self.lag(v, float(u.get("tau", 10))) + self.cycles(unit) + noise * self.noise(unit + "c", 15)
        return np.where(self.t < minutes(u["since"]), np.nan, v) if u.get("since") else v

    def _loop(self, unit, u, var):
        """A control loop (#355): its setpoint `sp`, controller output `op` (%), mode (1 auto, 0 manual) and process
        value `pv`. In auto the output works against a slow load disturbance and holds the value at the setpoint.
        `setpoint` events step the setpoint; `manual` events put it in manual with the output frozen at its normal
        level, so the value wanders with the load; `saturate` events add a demand (a path, in % of output) the output
        can't meet past 100 %, so the value falls away from the setpoint; `stiction` events make the valve stick and
        slip: the output ramps (a triangle) while the value jumps between two levels (a square), every `period`
        minutes; `step_test` events put it in manual for an open-loop step of the output (step_response); a `retune`
        changes how tightly the value is held from then on (`pv_noise_scale`)."""
        sp = np.full_like(self.t, float(u["sp"]))
        for e in sorted(self.ev(unit, "setpoint"), key=lambda e: e["at"]):
            sp = np.where(self.t >= minutes(e["at"]), float(e["to"]), sp)
        if var == "sp":
            return sp
        manual = np.maximum(self.any_between(unit, "manual"), self.any_between(unit, "step_test"))
        if var == "mode":
            return 1 - manual
        swing, gain = float(u.get("op_swing", 8.0)), float(u.get("gain", 0.15))
        load = self.noise(unit + "load", 240)
        demand = float(u["op"]) + swing * load + float(u.get("op_noise", 0.6)) * self.noise(unit + "o", 5)
        demand = demand + sum((self.path(e["points"]) for e in self.ev(unit, "saturate")), np.zeros_like(self.t))
        op = np.clip(demand, 0, 100)
        held = np.ones_like(self.t)  # a `retune` scales how far the value strays from setpoint from then on (#359)
        for e in self.ev(unit, "retune"):
            held = held * (1 - (1 - float(e["pv_noise_scale"])) * self.between(e["at"], None))
        pv = sp + float(u.get("pv_noise", 0.5)) * held * self.noise(unit + "p", 10)
        pv = pv - gain * self.lag(np.maximum(demand - 100, 0), 30)  # what the pinned output can't deliver
        op = np.where(manual > 0, float(u["op"]), op)
        pv = pv + manual * gain * swing * load  # the load the frozen output no longer answers
        for e in self.ev(unit, "stiction"):
            on = self.between(e["start"], e.get("end"))
            phase = (self.t - minutes(e["start"])) / float(e["period"])
            tri = 2 * np.abs(2 * (phase % 1) - 1) - 1
            op = np.where(on > 0, float(u["op"]) + float(e.get("op_amplitude", 4.0)) * tri, op)
            pv = np.where(on > 0, sp + float(e["amplitude"]) * np.tanh(4 * np.sin(2 * math.pi * phase))
                          + 0.3 * float(u.get("pv_noise", 0.5)) * self.noise(unit + "p", 10), pv)
        for e in self.ev(unit, "step_test"):  # in manual, the output stepped by hand and the value answering (#358)
            on = self.between(e["start"], e["end"]) > 0
            step_op, move = step_response(e, self.t)
            op = np.where(on, step_op, op)
            pv = np.where(on, sp + move + 0.3 * float(u.get("pv_noise", 0.5)) * self.noise(unit + "p", 10), pv)
        return op if var == "op" else pv

    def _valve(self, unit, u, var):
        if var != "position":
            raise KeyError(var)
        pos = np.full_like(self.t, float(u.get("normal", 100)))
        for e in self.ev(unit, "open"):
            pos = np.where(self.between(e["start"], e["end"]) > 0, float(e["position"]), pos)
        if u.get("closes_with_field", True) and u.get("normal", 100) > 0:
            pos = pos * (1 - self.shutdown())
        return self.lag(pos, 2)


def _today() -> str:
    from .relative_dates import day

    return day()
