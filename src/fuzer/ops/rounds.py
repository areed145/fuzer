"""Operator rounds (P5.4, #716): a work source of checklists per area and equipment, done on the phone each shift.

    points(conn)          the checklist points from the `rounds` source pack (round_point entities): what to read or
                          check, on which equipment, with its unit and limits, in round order
    open_shift(conn)      one run of every round for the current shift (`handover.shift_times`), for the operator
                          role; a run whose shift is over is closed as done, partly done or missed
    runs(conn)            this shift's runs with how far each has got
    run(conn, id)         a run's points, each with what was taken

Read from the records, never from equipment type: a site's rounds are its own export.
"""

from datetime import UTC, datetime, timedelta

from ..core.exceptions import ExceptionError

ROLE = "operator"


def points(conn) -> list[dict]:
    """Every checklist point, grouped in round order, each with the equipment it is on."""
    rows = conn.execute(
        """
        select p.fuzer_id as id, pl.label as point_id, c.dst as asset, al.label as asset_label,
               max(case when g.field = 'round' then g.value #>> '{}' end) as round,
               max(case when g.field = 'area' then g.value #>> '{}' end) as area,
               max(case when g.field = 'what' then g.value #>> '{}' end) as what,
               max(case when g.field = 'kind' then g.value #>> '{}' end) as kind,
               max(case when g.field = 'unit' then g.value #>> '{}' end) as unit,
               max(case when g.field = 'low' then (g.value #>> '{}')::numeric end) as low,
               max(case when g.field = 'high' then (g.value #>> '{}')::numeric end) as high,
               max(case when g.field = 'sort' then (g.value #>> '{}')::numeric end) as sort
        from entity p
        left join entity_label pl on pl.fuzer_id = p.fuzer_id
        left join edge c on c.src = p.fuzer_id and c.rel = 'checks'
        left join entity_label al on al.fuzer_id = c.dst
        left join golden g on g.fuzer_id = p.fuzer_id
        where p.kind = 'round_point'
        group by p.fuzer_id, pl.label, c.dst, al.label
        """).fetchall()
    out = [{**r, "kind": (r["kind"] or "reading").lower(), "unit": r["unit"] or None,
            "low": float(r["low"]) if r["low"] is not None else None,
            "high": float(r["high"]) if r["high"] is not None else None} for r in rows if r["round"]]
    return sorted(out, key=lambda p: (p["round"], p["sort"] if p["sort"] is not None else 1e9, p["point_id"] or ""))


def rounds(conn) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in points(conn):
        out.setdefault(p["round"], []).append(p)
    return out


def shift(conn, now: datetime | None = None) -> tuple[datetime, datetime]:
    """The shift under way at `now`: (the latest shift change at or before it, the next one)."""
    from .handover import _tz, shift_times

    now = now or datetime.now(UTC)
    tz = _tz()
    local = now.astimezone(tz)
    changes = sorted(datetime.combine(local.date() + timedelta(days=d), t, tz)
                     for d in (-1, 0, 1) for t in shift_times(conn))
    start = max(c for c in changes if c <= local)
    end = min(c for c in changes if c > local)
    return start.astimezone(UTC), end.astimezone(UTC)


def _close_past(conn, now: datetime) -> int:
    """Runs whose shift is over: done when every point was taken, partly done when some were, else missed."""
    return conn.execute(
        """
        update round_run r set closed_at = %(now)s,
               state = case when x.taken >= r.points then 'done' when x.taken > 0 then 'partly_done' else 'missed' end
        from (select r2.id, (select count(*) from round_reading t where t.run_id = r2.id) as taken
              from round_run r2 where r2.state = 'open' and r2.shift_end <= %(now)s) x
        where r.id = x.id
        """, {"now": now}).rowcount


def open_shift(conn, now: datetime | None = None) -> dict:
    """Open one run of every round for the shift under way (once), and close the runs of shifts that are over."""
    now = now or datetime.now(UTC)
    closed = _close_past(conn, now)
    start, end = shift(conn, now)
    opened = 0
    for name, pts in rounds(conn).items():
        opened += conn.execute(
            "insert into round_run (round, shift_start, shift_end, role, points) values (%s, %s, %s, %s, %s) "
            "on conflict (round, shift_start) do nothing", (name, start, end, ROLE, len(pts))).rowcount
    return {"opened": opened, "closed": closed, "shift_start": start, "shift_end": end}


def runs(conn, now: datetime | None = None) -> list[dict]:
    """This shift's runs, each with its area, points, how many are taken and how many found something."""
    now = now or datetime.now(UTC)
    open_shift(conn, now)
    start, _ = shift(conn, now)
    by_round = rounds(conn)
    rows = conn.execute(
        """
        select r.*, (select count(*) from round_reading t where t.run_id = r.id)::int as taken,
               (select count(*) from round_reading t where t.run_id = r.id and t.passed is false)::int as found
        from round_run r where r.shift_start = %s order by r.round
        """, (start,)).fetchall()
    return [{**r, "area": (by_round.get(r["round"]) or [{}])[0].get("area")} for r in rows]


def run(conn, run_id: int) -> dict:
    """A run with each of its points in round order and what was taken on it."""
    r = conn.execute("select * from round_run where id = %s", (run_id,)).fetchone()
    if r is None:
        raise ExceptionError(f"no round run {run_id}")
    taken = {t["point"]: t for t in conn.execute(
        "select * from round_reading where run_id = %s order by id", (run_id,)).fetchall()}
    pts = [{**p, "taken": taken.get(p["id"])} for p in rounds(conn).get(r["round"], [])]
    return {**r, "points": pts, "taken": len(taken), "area": pts[0]["area"] if pts else None}
