"""How many things are doing right now, read once for all of them (P5.16b): open work, active alarms and down now, the
same reads as the entity status card (`api/entities.entity_status`), so the counts on a 3D scene match each asset's
page.

    status(conn, ids, src)   {id: {"open_work", "alarms", "down"}} for the ids with anything to say, the totals, and
                             the alarm window

    open work     the open exceptions on it (or on what joined their case), `exceptions.listing`, read once
    active alarm  a point standing for it (`exceptions.anchor_map`) past an enabled alarm limit and still past it at
                  the end of the last `ALARM_HOURS` (`limits.excursions`), as the case page's alarms are read
    down now      its newest downtime has no end, as the status card's "Down since" reads it

Nothing here says a unit is running: no open downtime is only that, and an item with no alarm limits has no alarms
to show, not none sounding.
"""

from collections import Counter, defaultdict
from datetime import timedelta

from ..core import clock

ALARM_HOURS = 2
STEP = 5  # minutes between the historian values an alarm is read from, as around.STEP


def open_work(conn, ids: list[str]) -> Counter:
    from .exceptions import listing

    want, out = set(ids), Counter()
    for x in listing(conn, limit=100_000):
        for s in set(x["subjects"] or []) & want:
            out[s] += 1
    return out


def down(conn, ids: list[str]) -> dict[str, dict]:
    """Each id's newest downtime, where it has no end."""
    if not ids:
        return {}
    rows = conn.execute(
        """
        select distinct on (e.dst) e.dst as id, d.fuzer_id as downtime, s.value #>> '{}' as start,
               x.value #>> '{}' as "end", r.value #>> '{}' as reason
        from edge e join entity d on d.fuzer_id = e.src and d.kind = 'downtime' and d.retired_at is null
        left join golden s on s.fuzer_id = d.fuzer_id and s.field = 'start'
        left join golden x on x.fuzer_id = d.fuzer_id and x.field = 'end'
        left join golden r on r.fuzer_id = d.fuzer_id and r.field = 'reason'
        where e.rel in ('affects', 'caused_by') and e.dst = any(%s)
        order by e.dst, s.value #>> '{}' desc nulls last, d.fuzer_id
        """, (list(ids),)).fetchall()
    return {r["id"]: {"id": r["downtime"], "start": r["start"], "reason": r["reason"]} for r in rows if not r["end"]}


def alarms(conn, ids: list[str], src=None) -> dict[str, list[dict]]:
    """The alarms still sounding at the end of the window, on points standing for each id."""
    from ..analysis import limits
    from ..core.pack import resolve
    from .exceptions import anchor_map

    pids = [r["point_id"] for r in conn.execute(
        "select distinct point_id from point_limit where basis = 'alarm' and enabled order by 1")]
    want = set(ids)
    near = {p: anc & want for p, anc in anchor_map(conn, pids).items() if anc & want}
    if not near:
        return {}
    end = clock.now().replace(second=0, microsecond=0)
    out: dict[str, list[dict]] = defaultdict(list)
    for x in limits.excursions(conn, resolve(conn), sorted(near), end - timedelta(hours=ALARM_HOURS), end, STEP, src):
        if x["basis"] != "alarm" or x["end"] is not None:
            continue
        for i in sorted(near[x["point_id"]]):
            out[i].append({"point_id": x["point_id"], "point": x["point"], "type": x["limit_type"],
                           "start": x["start"].isoformat(timespec="minutes")})
    return dict(out)


def status(conn, ids: list[str], src=None) -> dict:
    work, gone, ringing = open_work(conn, ids), down(conn, ids), alarms(conn, ids, src)
    out = {}
    for i in ids:
        if work.get(i) or i in gone or ringing.get(i):
            out[i] = {"open_work": work.get(i, 0), "alarms": len(ringing.get(i, [])), "down": gone.get(i)}
    return {"status": out,
            "totals": {"open_work": sum(1 for s in out.values() if s["open_work"]),
                       "alarms": sum(1 for s in out.values() if s["alarms"]),
                       "down": sum(1 for s in out.values() if s["down"])},
            "alarm_hours": ALARM_HOURS}
