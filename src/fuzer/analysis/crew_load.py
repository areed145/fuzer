"""Crew load (#361): the maintenance backlog in crew-weeks, and where the work scheduled ahead asks a crew for more
people than it has, from the records alone: each open work order's planned hours and people (SAP operation work and
capacity count), the work center that does it, and the work center's crew and calendar.

    load(conn, as_of, weeks)   per work center: open orders, planned hours, the backlog in crew-weeks, and the weekly
                               load against capacity over the weeks ahead; crew conflicts (the days a crew is asked
                               for more people than it has, with the orders); unit conflicts (two crews' orders on
                               one unit on the same days)

An order scheduled ahead (starting on or after `as_of`) runs from its start for its planned hours over its people's
working days (hours a day, days a week from the work center's calendar), so a day's demand is the people its running
orders take. An order already past its start is backlog, not schedule: it counts in crew-weeks, not on a day. An
order with no planned hours is counted, and said to be unplanned, never guessed. People approve schedules and
commitments; this shows what the records ask of each crew.
"""

import math
from datetime import date, timedelta

from .calcs import CalcError

OPEN_ORDERS = ("CRTD", "REL", "PCNF")
HOURS_PER_DAY, DAYS_PER_WEEK = 8.0, 5  # a calendar the work center doesn't give


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _workdays(start: date, n: int, per_week: int) -> list[date]:
    """The first `n` working days from `start` (Monday to Friday for five a week, to Saturday for six, every day for
    seven)."""
    out, d = [], start
    while len(out) < n:
        if d.weekday() < per_week:
            out.append(d)
        d += timedelta(days=1)
    return out


def _centers(conn) -> dict[str, dict]:
    rows = conn.execute(
        """
        select e.fuzer_id as id, l.label, jsonb_object_agg(g.field, g.value #>> '{}') as f
        from entity e join entity_label l using (fuzer_id) join golden g using (fuzer_id)
        where e.kind = 'work_center' and e.retired_at is null and e.merged_into is null
        group by e.fuzer_id, l.label
        """).fetchall()
    return {r["id"]: {"id": r["id"], "center": r["label"], "description": r["f"].get("description"),
                      "crew": _num(r["f"].get("crew_size")),
                      "hours_per_day": _num(r["f"].get("hours_per_day")) or HOURS_PER_DAY,
                      "days_per_week": int(_num(r["f"].get("days_per_week")) or DAYS_PER_WEEK),
                      "calendar_given": r["f"].get("hours_per_day") is not None} for r in rows}


def _orders(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """
        select wo.fuzer_id as id, wl.label as aufnr, d.dst as center,
               max(g.value #>> '{}') filter (where g.field = 'start') as start,
               max(g.value #>> '{}') filter (where g.field = 'basic_finish') as due,
               max(g.value #>> '{}') filter (where g.field = 'planned_hours') as hours,
               max(g.value #>> '{}') filter (where g.field = 'people') as people,
               max(g.value #>> '{}') filter (where g.field = 'priority') as priority,
               max(g.value #>> '{}') filter (where g.field = 'text') as text,
               max(g.value #>> '{}') filter (where g.field = 'revision') as revision,
               (select string_agg(distinct coalesce(ul.label, c.dst), ', ') from edge c
                left join entity_label ul on ul.fuzer_id = c.dst
                where c.src = wo.fuzer_id and c.rel = 'concerns' and c.valid_to is null) as unit
        from entity wo
        join edge d on d.src = wo.fuzer_id and d.rel = 'done_by' and d.valid_to is null
        left join entity_label wl on wl.fuzer_id = wo.fuzer_id
        join golden g on g.fuzer_id = wo.fuzer_id
        where wo.kind = 'work_order' and wo.retired_at is null and wo.merged_into is null
          and exists (select 1 from golden s where s.fuzer_id = wo.fuzer_id and s.field = 'status'
                      and s.value #>> '{}' = any(%s))
        group by wo.fuzer_id, wl.label, d.dst
        """, (list(OPEN_ORDERS),)).fetchall()]


def _spans(days: list[date], per_week: int) -> list[tuple[date, date]]:
    """Consecutive working days as (first, last) spans."""
    out: list[list[date]] = []
    for d in sorted(days):
        if out and _workdays(out[-1][1] + timedelta(days=1), 1, per_week)[0] == d:
            out[-1][1] = d
        else:
            out.append([d, d])
    return [(a, b) for a, b in out]


def load(conn, as_of: str | None = None, weeks: int = 26) -> dict:
    """The backlog per work center in crew-weeks, the weekly load against capacity ahead, and the crew and unit
    conflicts in the work scheduled ahead."""
    from ..graph import operations as ops

    today = date.fromisoformat((as_of or ops.as_of(conn))[:10])
    horizon = today + timedelta(weeks=weeks)
    centers, orders = _centers(conn), _orders(conn)
    if not centers or not orders:
        raise CalcError("no open work orders with a work center on record" if centers else
                        "no work centers on record: the SAP work center export isn't loaded")
    per_center: dict[str, dict] = {}
    demand: dict[str, dict[date, list[dict]]] = {}
    windows = []
    for o in orders:
        c = centers.get(o["center"])
        if c is None:
            continue
        pc = per_center.setdefault(c["id"], {"orders": 0, "hours": 0.0, "unplanned": [], "scheduled": 0,
                                             "backlog_orders": 0})
        pc["orders"] += 1
        hours, people = _num(o["hours"]), _num(o["people"]) or 1.0
        if hours is None:
            pc["unplanned"].append(o["aufnr"])
            continue
        pc["hours"] += hours
        start = date.fromisoformat(o["start"][:10]) if o["start"] else None
        if start is None or start < today:
            pc["backlog_orders"] += 1
            continue
        if start > horizon:
            continue
        pc["scheduled"] += 1
        days = _workdays(start, max(1, math.ceil(hours / (people * c["hours_per_day"]))), c["days_per_week"])
        job = {"order": o["aufnr"], "id": o["id"], "people": people, "hours": hours, "unit": o["unit"],
               "text": o["text"], "from": days[0].isoformat(), "to": days[-1].isoformat(), "revision": o["revision"]}
        windows.append((c, job))
        for d in days:
            demand.setdefault(c["id"], {}).setdefault(d, []).append(job)
    rows, conflicts = [], []
    for cid, pc in sorted(per_center.items(), key=lambda kv: centers[kv[0]]["center"]):
        c = centers[cid]
        week_capacity = (c["crew"] or 0) * c["hours_per_day"] * c["days_per_week"]
        weekly: dict[str, float] = {}
        for d, jobs in demand.get(cid, {}).items():
            wk = (d - timedelta(days=d.weekday())).isoformat()
            weekly[wk] = weekly.get(wk, 0.0) + sum(j["people"] for j in jobs) * c["hours_per_day"]
        rows.append({"center": c["center"], "id": cid, "description": c["description"], "crew": c["crew"],
                     "hours_per_day": c["hours_per_day"], "days_per_week": c["days_per_week"],
                     "calendar_given": c["calendar_given"], "open_orders": pc["orders"],
                     "planned_hours": round(pc["hours"], 1),
                     "crew_weeks": round(pc["hours"] / week_capacity, 1) if week_capacity else None,
                     "unplanned_orders": pc["unplanned"], "scheduled_ahead": pc["scheduled"],
                     "past_start": pc["backlog_orders"], "week_capacity_hours": week_capacity,
                     "weekly": [{"week": w, "hours": round(h, 1), "capacity": week_capacity,
                                 "over": h > week_capacity} for w, h in sorted(weekly.items())]})
        if c["crew"] is None:
            continue
        over = [d for d, jobs in demand.get(cid, {}).items() if sum(j["people"] for j in jobs) > c["crew"]]
        for a, b in _spans(over, c["days_per_week"]):
            jobs = {j["order"]: j for d, js in demand[cid].items() if a <= d <= b for j in js}
            need = max(sum(j["people"] for j in demand[cid][d]) for d in demand[cid] if a <= d <= b)
            conflicts.append({
                "center": c["center"], "description": c["description"], "crew": c["crew"], "needed": need,
                "from": a.isoformat(), "to": b.isoformat(),
                "orders": sorted(jobs.values(), key=lambda j: (j["from"], j["order"])),
                "say": (f"{c['description'] or c['center']} ({c['center']}) has {c['crew']:g} people and the orders "
                        f"scheduled {a.isoformat()}" + (f" to {b.isoformat()}" if b != a else "")
                        + f" need {need:g}: " + ", ".join(sorted(jobs)))})
    unit_clash = []
    for i, (c1, j1) in enumerate(windows):
        for c2, j2 in windows[i + 1:]:
            if j1["unit"] and j1["unit"] == j2["unit"] and j1["from"] <= j2["to"] and j2["from"] <= j1["to"]:
                unit_clash.append({"unit": j1["unit"], "orders": [j1["order"], j2["order"]],
                                   "centers": sorted({c1["center"], c2["center"]}),
                                   "from": max(j1["from"], j2["from"]), "to": min(j1["to"], j2["to"])})
    return {"as_of": today.isoformat(), "weeks": weeks, "centers": rows, "conflicts": conflicts,
            "unit_conflicts": unit_clash,
            "verdict": "conflicts" if conflicts else "no_conflicts",
            "summary": {"centers": len(rows), "open_orders": sum(r["open_orders"] for r in rows),
                        "crew_weeks": {r["center"]: r["crew_weeks"] for r in rows},
                        "conflicts": [x["say"] for x in conflicts]},
            "source": "SAP work orders (planned hours, people, start), work centers (crew and calendar)",
            "note": ("Backlog in crew-weeks is open planned hours over the crew's weekly hours. A conflict is a day "
                     "the orders scheduled ahead need more people than the crew has; an order past its start is "
                     "backlog, not schedule. Orders without planned hours are listed, not guessed. People approve "
                     "schedules and commitments.")}
