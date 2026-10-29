"""Process values around a time, through a pluggable provider.

Fuzer never copies time series: it asks a provider for values when needed. PI Web API and ADX
providers (#15, #16) implement the same `values()` call; until then the synthetic provider makes
deterministic, plausible signals from each PI point's units, with the asset's incidents showing up
in them (vibration and temperature climb before a trip, pressure dips), so the UI, agents and the
troubleshooting brief can be built and tested now. Choose with FUZER_TIMESERIES (default synthetic).
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


class Provider(Protocol):
    def values(self, point: str, uom: str, start: datetime, end: datetime, step_minutes: int,
               upsets: list[datetime]) -> list[tuple[str, float]]: ...


class SyntheticProvider:
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
    raise ValueError(f"time-series provider {name!r} isn't available yet (PI Web API / ADX: #15, #16)")


def around(conn, fuzer_id: str, at: str | None = None, hours_before: int = 48, hours_after: int = 24,
           step_minutes: int = 30, max_points: int = 12) -> dict:
    """Values of the PI points on a unit (its own and its instruments') around a time. For an
    incident, `at` defaults to its start and its unit is used."""
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
    # equipment -> the position it's installed at
    unit = (conn.execute("select dst from edge where src = %s and rel = 'installed_at' and valid_to is null",
                         (unit,)).fetchone() or {"dst": unit})["dst"]
    centre = datetime.fromisoformat(at or row["start"] or datetime.now().isoformat()[:10])
    upsets = [datetime.fromisoformat(r["start"]) for r in conn.execute(
        """
        select g.value #>> '{}' as start from edge a join golden g on g.fuzer_id = a.src and g.field = 'start'
        join entity i on i.fuzer_id = a.src and i.kind = 'incident'
        where a.dst = %s and a.rel = 'about'
        """,
        (unit,),
    )]
    points = conn.execute(
        """
        with tags as (select %(u)s::text as id union select src from edge where rel = 'part_of' and dst = %(u)s),
        pts as (
            select distinct t.dst as id, l.label as point, gd.value #>> '{}' as description, gu.value #>> '{}' as uom
            from edge t
            left join entity_label l on l.fuzer_id = t.dst
            left join golden gd on gd.fuzer_id = t.dst and gd.field = 'description'
            left join golden gu on gu.fuzer_id = t.dst and gu.field = 'uom'
            where t.rel = 'has_timeseries' and t.src in (select id from tags) and l.label not like '%%.SP'
              and l.label not like '%%.OP'
        )
        -- one of each kind of instrument first ("... Pressure" before "... Pressure 2"), then the rest
        select * from pts order by coalesce(description, '') ~ ' [0-9]+$', point limit %(n)s
        """,
        {"u": unit, "n": max_points},
    ).fetchall()
    start, end = centre - timedelta(hours=hours_before), centre + timedelta(hours=hours_after)
    near = [u for u in upsets if start - timedelta(hours=12) <= u <= end + timedelta(hours=6)]
    src = provider()
    series = []
    for p in points:
        vals = src.values(p["point"], p["uom"] or "", start, end, step_minutes, near)
        nums = [v for _, v in vals]
        series.append({"id": p["id"], "point": p["point"], "description": p["description"], "uom": p["uom"],
                       "values": vals, "min": min(nums), "max": max(nums), "mean": round(sum(nums) / len(nums), 3),
                       "last": nums[-1]})
    return {"unit": unit, "centre": centre.isoformat(timespec="minutes"), "start": start.isoformat(timespec="minutes"),
            "end": end.isoformat(timespec="minutes"), "provider": os.environ.get("FUZER_TIMESERIES", "synthetic"),
            "upsets": [u.isoformat(timespec="minutes") for u in near], "series": series}
