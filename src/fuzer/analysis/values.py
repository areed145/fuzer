"""Process values around a time or an incident, with each point's limits (#101) and the upsets nearby: what
the Values tab, the assistant's `process_values` tool and the troubleshooting brief show."""

from datetime import datetime, timedelta

from ..graph.operations import data_through
from ..sources.timeseries import (
    FIRST_TIMED,
    Provider,
    _point,
    _unit_points,
    auto_step,
    provider,
    summary,
)
from . import limits


def around(conn, fuzer_id: str, at: str | None = None, hours_before: int = 48, hours_after: int = 24,
           step_minutes: int | None = None, max_points: int = 12, src: Provider | None = None,
           direct: bool = False, pack=None) -> dict:
    """Values of the PI points on a unit (its own and its instruments') around a time. For an
    incident, `at` defaults to its first timed signal (a log entry or downtime start), else its start
    day, and its unit is used. For a timeseries, only that point; with `direct`, only the points linked
    to the entity itself, not its instruments'. Anything but an incident is centred by default on the
    latest incident on its tag or well (else now). The step is chosen from the window (about
    150 values) unless given. Each series has a summary for agents: min / max / mean / last, and
    `lead_in`: the mean before the last 12 h against the mean and peak in them; and its `limits` at the
    centre (alarm limits, IOWs, or the learned normal range; limits.lines)."""
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
    # instrument tag -> the equipment tag it belongs to, which incidents are about (incidents.py)
    unit = (conn.execute(
        """
        select p.dst from edge p join entity a on a.fuzer_id = p.src and a.kind = 'tag'
        join entity b on b.fuzer_id = p.dst and b.kind = 'tag' where p.src = %s and p.rel = 'part_of' limit 1
        """, (unit,)).fetchone() or {"dst": unit})["dst"]
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
    if not at and row["kind"] != "incident" and upsets:
        at = upsets[-1].isoformat()
    src = src or provider()
    if not (at or row["start"]):
        at = _latest(conn, src, hours_after)
    centre = datetime.fromisoformat(at or row["start"])
    if row["kind"] == "timeseries":
        points = [p for p in [_point(conn, fuzer_id)] if p]
    else:
        points = _unit_points(conn, fuzer_id if direct else unit, max_points, children=not direct)
    start, end = centre - timedelta(hours=hours_before), centre + timedelta(hours=hours_after)
    step_minutes = step_minutes or auto_step(hours_before + hours_after)
    near = [u for u in upsets if start - timedelta(hours=12) <= u <= end + timedelta(hours=6)]
    series, errors = [], []
    for p in points:
        try:
            vals = src.series({**p, "upsets": near}, start, end, step_minutes)
        except Exception as e:  # one unreachable or unknown point shouldn't hide the others
            errors.append(f"{p['point']}: {e}")
            continue
        if vals:
            modelled = bool(getattr(src, "model", None) and src.model.covers(p["point"]))
            series.append({**{k: p[k] for k in ("id", "point", "description", "uom")}, "values": vals,
                           **summary(vals, centre), **({"modelled": True} if modelled else {})})
    if series:
        found = limits.for_points(conn, pack, [s["id"] for s in series], centre)
        for s in series:
            s["limits"] = limits.lines(found[s["id"]])
    out = {"unit": unit, "centre": centre.isoformat(timespec="minutes"), "start": start.isoformat(timespec="minutes"),
           "end": end.isoformat(timespec="minutes"), "step_minutes": step_minutes, "provider": src.name,
           "upsets": [u.isoformat(timespec="minutes") for u in near], "series": series}
    return out | ({"errors": errors[:5]} if errors else {})


def _latest(conn, src, hours_after: int) -> str:
    """Where the window sits with nothing to centre on: now for a live historian, but for synthetic values the
    end of the loaded data's last day, so they line up with the scenario's records instead of today's clock (#389)."""
    last = data_through(conn) if src.name == "synthetic" else None
    if last:
        end = datetime.fromisoformat(last) + timedelta(days=1)
        return (end - timedelta(hours=hours_after)).isoformat(timespec="minutes")
    return datetime.now().isoformat(timespec="minutes")
