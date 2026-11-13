"""Process values around a time, through a pluggable provider.

Fuzer never copies time series: it asks a provider for values when needed. Each project chooses with its
`timeseries` setting (the first project may use FUZER_TIMESERIES instead):

- `synthetic` (default): deterministic, plausible signals. A fake plant with a process model
  (process_model.json; process_model.py) gets values that move together the way the plant does
  (vibration before bearing temperature, a compressor trip before the lift gas header falls); other
  points follow their units, with the asset's incidents showing up in them. Needs nothing running.
- `pi`: interpolated values from PI Web API, by point name (pi_web_api.py).
- `none`: no historian yet; every series is empty.
- `adx`: values from Azure Data Explorer through the pointer the series catalog put on each
  timeseries entity (adx.py).

`uv run fakeplant historian` serves a fake PI Web API and ADX over the fake plant to try `pi` / `adx`.
"""

import hashlib
from datetime import datetime, timedelta
from typing import Protocol

import numpy as np

BASE = {"barg": 18.0, "bar": 1.2, "degC": 65.0, "mm/s": 2.5, "%": 50.0, "rpm": 3600.0, "Sm3/d": 6500.0,
        "psi": 250.0, "degF": 180.0, "Hz": 55.0, "bbl/d": 400.0, "ft": 8.0}
# Typical levels by what a point measures (its description), ahead of the unit default above. A fake plant's process
# model adds its own (`levels`: [[description text, level], ...]), checked first, for points named after its sites.
BASE_BY_DESCRIPTION = [("Lift Gas Header Pressure", 88.0), ("Lift Gas Header Flow", 1800.0),
                       ("Gas Lift Supply Meter", 75.0), ("Gas Lift Injection Rate", 12.5), ("Casing Pressure", 72.0),
                       ("Gas Export Meter", 1700.0), ("Gas Rate", 600.0)]


def base_level(uom: str, description: str = "", levels=()) -> float:
    return next((float(v) for k, v in [*levels, *BASE_BY_DESCRIPTION] if k in (description or "")),
                BASE.get(uom, 10.0))


# How an upset shows in each kind of signal: multiplier at the peak, just before the incident.
UPSET = {"mm/s": 3.5, "degC": 1.35, "degF": 1.3, "barg": 0.7, "psi": 0.75, "rpm": 0.9, "Hz": 0.9}


LEAD_IN_HOURS = 12
# An incident's first timed signal (log entry time or downtime start; notifications only have a day).
FIRST_TIMED = """
    select min(g.value #>> '{{}}') from edge inc
    join golden g on g.fuzer_id = inc.dst and g.field in ('timestamp', 'start') and length(g.value #>> '{{}}') > 10
    where inc.src = {incident} and inc.rel = 'includes'
"""


class Provider(Protocol):
    name: str

    def series(self, s: dict, start: datetime, end: datetime, step_minutes: int) -> list[tuple[str, float]]:
        """Values of one series (`point`, `uom`, ADX pointer fields, `upsets`) as (minute, value) pairs."""
        ...


class SyntheticProvider:
    """Points a fake plant's process model covers (process_model.json in its data folder) come from
    that model, so they move together; the rest are drawn per point, shaped around upsets."""

    name = "synthetic"

    def __init__(self, data_dir=None):
        from ..core import projects
        from . import instruments, process_model

        data_dir = data_dir or projects.field("data")
        self.model = process_model.load(data_dir)
        self.faults = instruments.load(data_dir)

    def series(self, s, start, end, step_minutes):
        return self.values(s["point"], s["uom"] or "", start, end, step_minutes, s["upsets"], s.get("description") or "")

    def values(self, point, uom, start, end, step_minutes, upsets, description=""):
        out = self._clean(point, uom, start, end, step_minutes, upsets, description)
        if not out or not self.faults or not self.faults.touches(point, start, end):
            return out
        # What the instruments and the historian did to the clean values (instruments.py): bad values are left out.
        from .instruments import epoch, naive

        secs = np.array([epoch(t) for t, _ in out])
        at = lambda t: self._clean(point, uom, naive(t), naive(t), 1, upsets, description)[0][1]
        v = self.faults.apply(point, secs, np.array([x for _, x in out]), at)
        return [(t, round(float(x), 3)) for (t, _), x in zip(out, v) if not np.isnan(x)]

    def states(self, point, start, end):
        """Digital states the historian holds for the point over the window: [(from, to, state)], epoch seconds."""
        return self.faults.states(point, start, end) if self.faults else []

    def _clean(self, point, uom, start, end, step_minutes, upsets, description=""):
        if self.model and self.model.covers(point):
            return self.model.values(point, start, end, step_minutes)
        if end < start:
            return []
        seed = int(hashlib.sha1(point.encode()).hexdigest()[:8], 16)
        base = base_level(uom, description, self.model.levels if self.model else ()) * (0.85 + (seed % 30) / 100)
        n = int((end - start).total_seconds() // 60 // step_minutes) + 1
        secs = start.timestamp() + np.arange(n, dtype=float) * step_minutes * 60
        # The noise at a minute depends only on the point and that minute, so overlapping reads agree.
        v = base * (1 + 0.03 * np.sin(secs / 5400 + seed % 7) + 0.03 * (_unit_noise(seed, secs // 60) - 0.5))
        for u in upsets:  # ramp over the 12 h before the upset, recover over 6 h after
            hours = (secs - u.timestamp()) / 3600
            weight = np.where(hours <= 0, (hours + 12) / 12, np.maximum(0.0, 1 - hours / 6))
            v = np.where((hours >= -12) & (hours <= 6), v * (1 + (UPSET.get(uom, 1.0) - 1) * weight), v)
        step = timedelta(minutes=step_minutes)
        return [((start + i * step).isoformat(timespec="minutes"), x) for i, x in enumerate(np.round(v, 3).tolist())]


def _unit_noise(seed: int, minutes: np.ndarray) -> np.ndarray:
    """Uniform [0, 1) per (seed, minute), from a splitmix64 hash: one numpy pass instead of one
    random.Random per value (seeding one cost most of an SRE scan on a big fake plant)."""
    with np.errstate(over="ignore"):
        z = (np.uint64(seed) << np.uint64(32)) ^ minutes.astype(np.int64).astype(np.uint64)
        z = z + np.uint64(0x9E3779B97F4A7C15)
        z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        z = z ^ (z >> np.uint64(31))
    return (z >> np.uint64(11)).astype(float) / float(1 << 53)


class NoProvider:
    """A project with no historian yet (`timeseries: none`): every series is empty, so pages and tools say there
    are no values instead of failing."""

    name = "none"

    def series(self, s, start, end, step_minutes):
        return []


def provider() -> Provider:
    """The time-series provider of the project this code runs for (its `timeseries`, else FUZER_TIMESERIES)."""
    from ..core import projects

    name = projects.field("timeseries") or "synthetic"
    if name == "synthetic":
        return SyntheticProvider()
    if name == "none":
        return NoProvider()
    if name == "pi":
        from . import pi_web_api
        return pi_web_api.Provider()
    if name == "adx":
        from . import adx
        return adx.Provider()
    raise ValueError(f"unknown time-series provider {name!r} (timeseries: synthetic, pi, adx or none)")


STEPS = [5, 10, 15, 30, 60, 120, 240, 360, 720, 1440]


def auto_step(hours: float, values: int = 144) -> int:
    """The smallest round step (minutes) that keeps a window to about `values` values: 72 h -> 30 min."""
    return next((s for s in STEPS if s * values >= hours * 60), STEPS[-1])


def _unit_points(conn, unit: str, n: int, children: bool = True) -> list[dict]:
    """The PI points on a unit (and its instruments, with children), one of each kind of instrument first."""
    return conn.execute(
        """
        with tags as (select %(u)s::text as id
                      union select src from edge where %(children)s and rel = 'part_of' and dst = %(u)s),
        pts as (
            select distinct t.dst as id, l.label as point, gd.value #>> '{}' as description, gu.value #>> '{}' as uom,
                   ac.value #>> '{}' as adx_cluster, ad.value #>> '{}' as adx_database,
                   atb.value #>> '{}' as adx_table, ai.value #>> '{}' as adx_series_id
            from edge t
            left join entity_label l on l.fuzer_id = t.dst
            left join golden gd on gd.fuzer_id = t.dst and gd.field = 'description'
            left join golden gu on gu.fuzer_id = t.dst and gu.field = 'uom'
            left join golden ac on ac.fuzer_id = t.dst and ac.field = 'adx_cluster'
            left join golden ad on ad.fuzer_id = t.dst and ad.field = 'adx_database'
            left join golden atb on atb.fuzer_id = t.dst and atb.field = 'adx_table'
            left join golden ai on ai.fuzer_id = t.dst and ai.field = 'adx_series_id'
            where t.rel = 'has_timeseries' and t.src in (select id from tags) and l.label not like '%%.SP'
              and l.label not like '%%.OP'
        )
        -- one of each kind of instrument first ("... Pressure" before "... Pressure 2"), then the rest
        select * from pts order by coalesce(description, '') ~ ' [0-9]+$', point limit %(n)s
        """,
        {"u": unit, "n": n, "children": children},
    ).fetchall()


def _point(conn, fuzer_id: str) -> dict | None:
    """One timeseries entity as a series to read: its PI point name, units and ADX pointer."""
    row = conn.execute(
        """
        select e.fuzer_id as id, l.label as point,
               max(g.value #>> '{}') filter (where g.field = 'description') as description,
               max(g.value #>> '{}') filter (where g.field = 'uom') as uom,
               max(g.value #>> '{}') filter (where g.field = 'adx_cluster') as adx_cluster,
               max(g.value #>> '{}') filter (where g.field = 'adx_database') as adx_database,
               max(g.value #>> '{}') filter (where g.field = 'adx_table') as adx_table,
               max(g.value #>> '{}') filter (where g.field = 'adx_series_id') as adx_series_id
        from entity e left join entity_label l using (fuzer_id) left join golden g using (fuzer_id)
        where e.fuzer_id = %s group by e.fuzer_id, l.label
        """,
        (fuzer_id,),
    ).fetchone()
    return row if row and row["point"] else None


def summary(vals: list[tuple[str, float]], centre: datetime) -> dict:
    """min / max / mean / last, and how the lead-in (the 12 h before the centre) compares with before."""
    nums = [v for _, v in vals]
    out = {"min": min(nums), "max": max(nums), "mean": round(sum(nums) / len(nums), 3), "last": nums[-1]}
    cut, c = (centre - timedelta(hours=LEAD_IN_HOURS)).isoformat(timespec="minutes"), centre.isoformat(timespec="minutes")
    before = [v for t, v in vals if t < cut]
    lead = [v for t, v in vals if cut <= t <= c]
    if before and lead:
        base = sum(before) / len(before)
        lead_mean = sum(lead) / len(lead)
        peak = max(lead, key=lambda v: abs(v - base))
        out["lead_in"] = {"baseline_mean": round(base, 3), "mean": round(lead_mean, 3), "extreme": peak,
                          "change_pct": round((peak - base) / base * 100, 1) if base else None}
    return out
