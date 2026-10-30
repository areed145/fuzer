"""Process values around a time, through a pluggable provider.

Fuzer never copies time series: it asks a provider for values when needed. Choose with
FUZER_TIMESERIES:

- `synthetic` (default): deterministic, plausible signals from each PI point's units, with the
  asset's incidents showing up in them (vibration and temperature climb before a trip, pressure
  dips). Needs nothing running.
- `pi`: interpolated values from PI Web API, by point name (pi_web_api.py).
- `adx`: values from Azure Data Explorer through the pointer the series catalog put on each
  timeseries entity (adx.py).

`uv run fakeplant historian` serves a fake PI Web API and ADX over the fake plant to try `pi` / `adx`.
"""

import hashlib
import math
import os
import random
from datetime import datetime, timedelta
from typing import Protocol

BASE = {"barg": 18.0, "bar": 1.2, "degC": 65.0, "mm/s": 2.5, "%": 50.0, "rpm": 3600.0, "Sm3/d": 6500.0,
        "psi": 250.0, "degF": 180.0, "Hz": 55.0, "bbl/d": 400.0, "ft": 8.0}
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
    name = "synthetic"

    def series(self, s, start, end, step_minutes):
        return self.values(s["point"], s["uom"] or "", start, end, step_minutes, s["upsets"])

    def values(self, point, uom, start, end, step_minutes, upsets):
        seed = int(hashlib.sha1(point.encode()).hexdigest()[:8], 16)
        base = BASE.get(uom, 10.0) * (0.85 + (seed % 30) / 100)
        out, t = [], start
        while t <= end:
            rng = random.Random(seed ^ int(t.timestamp() // 60))
            v = base * (1 + 0.03 * math.sin(t.timestamp() / 5400 + seed % 7) + rng.uniform(-0.015, 0.015))
            for u in upsets:  # ramp over the 12 h before the upset, recover over 6 h after
                hours = (t - u).total_seconds() / 3600
                if -12 <= hours <= 6:
                    weight = (hours + 12) / 12 if hours <= 0 else max(0.0, 1 - hours / 6)
                    v *= 1 + (UPSET.get(uom, 1.0) - 1) * weight
            out.append((t.isoformat(timespec="minutes"), round(v, 3)))
            t += timedelta(minutes=step_minutes)
        return out


def provider() -> Provider:
    name = os.environ.get("FUZER_TIMESERIES", "synthetic")
    if name == "synthetic":
        return SyntheticProvider()
    if name == "pi":
        from . import pi_web_api
        return pi_web_api.Provider()
    if name == "adx":
        from . import adx
        return adx.Provider()
    raise ValueError(f"unknown time-series provider {name!r} (FUZER_TIMESERIES: synthetic, pi or adx)")


def around(conn, fuzer_id: str, at: str | None = None, hours_before: int = 48, hours_after: int = 24,
           step_minutes: int | None = None, max_points: int = 12, src: Provider | None = None) -> dict:
    """Values of the PI points on a unit (its own and its instruments') around a time. For an
    incident, `at` defaults to its first timed signal (a log entry or downtime start), else its start
    day, and its unit is used. For a timeseries, only that point, centred by default on the latest
    incident on the tag or well it belongs to (else now). The step is chosen from the window (about
    150 values) unless given. Each series has a summary for agents: min / max / mean / last, and
    `lead_in`: the mean before the last 12 h against the mean and peak in them."""
    row = conn.execute(
        """
        select e.kind, (select a.dst from edge a where a.src = e.fuzer_id and a.rel = 'about' limit 1) as unit,
               (select g.value #>> '{}' from golden g where g.fuzer_id = e.fuzer_id and g.field = 'start') as start
        from entity e where e.fuzer_id = %s
        """,
        (fuzer_id,),
    ).fetchone()
    if not row:
        return {"error": "unknown entity"}
    unit = row["unit"] if row["kind"] == "incident" else fuzer_id
    if row["kind"] == "timeseries":  # the tag or well it measures
        unit = (conn.execute("select src from edge where dst = %s and rel = 'has_timeseries' order by src limit 1",
                             (fuzer_id,)).fetchone() or {"src": fuzer_id})["src"]
    # equipment -> the position it's installed at
    unit = (conn.execute("select dst from edge where src = %s and rel = 'installed_at' and valid_to is null",
                         (unit,)).fetchone() or {"dst": unit})["dst"]
    if not at and row["kind"] == "incident":
        at = conn.execute(f"select ({FIRST_TIMED.format(incident='%s')}) as t", (fuzer_id,)).fetchone()["t"]
    upsets = sorted(datetime.fromisoformat(r["start"]) for r in conn.execute(
        f"""
        select coalesce(({FIRST_TIMED.format(incident="a.src")}), g.value #>> '{{}}') as start
        from edge a join golden g on g.fuzer_id = a.src and g.field = 'start'
        join entity i on i.fuzer_id = a.src and i.kind = 'incident'
        where a.dst = %s and a.rel = 'about'
        """,
        (unit,),
    ))
    if not at and row["kind"] == "timeseries" and upsets:
        at = upsets[-1].isoformat()
    centre = datetime.fromisoformat(at or row["start"] or datetime.now().isoformat(timespec="minutes"))
    if row["kind"] == "timeseries":
        points = [p for p in [_point(conn, fuzer_id)] if p]
    else:
        points = _unit_points(conn, unit, max_points)
    start, end = centre - timedelta(hours=hours_before), centre + timedelta(hours=hours_after)
    step_minutes = step_minutes or auto_step(hours_before + hours_after)
    near = [u for u in upsets if start - timedelta(hours=12) <= u <= end + timedelta(hours=6)]
    src = src or provider()
    series, errors = [], []
    for p in points:
        try:
            vals = src.series({**p, "upsets": near}, start, end, step_minutes)
        except Exception as e:  # one unreachable or unknown point shouldn't hide the others
            errors.append(f"{p['point']}: {e}")
            continue
        if vals:
            series.append({**{k: p[k] for k in ("id", "point", "description", "uom")}, "values": vals,
                           **summary(vals, centre)})
    out = {"unit": unit, "centre": centre.isoformat(timespec="minutes"), "start": start.isoformat(timespec="minutes"),
           "end": end.isoformat(timespec="minutes"), "step_minutes": step_minutes, "provider": src.name,
           "upsets": [u.isoformat(timespec="minutes") for u in near], "series": series}
    return out | ({"errors": errors[:5]} if errors else {})


STEPS = [5, 10, 15, 30, 60, 120, 240, 360, 720, 1440]


def auto_step(hours: float, values: int = 144) -> int:
    """The smallest round step (minutes) that keeps a window to about `values` values: 72 h -> 30 min."""
    return next((s for s in STEPS if s * values >= hours * 60), STEPS[-1])


def _unit_points(conn, unit: str, n: int) -> list[dict]:
    """The PI points on a unit and its instruments, one of each kind of instrument first."""
    return conn.execute(
        """
        with tags as (select %(u)s::text as id union select src from edge where rel = 'part_of' and dst = %(u)s),
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
        {"u": unit, "n": n},
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
