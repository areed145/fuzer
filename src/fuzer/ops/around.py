"""Around an event (P3.5a, #692): the alarms that sounded and the operator log entries written about the assets a
case's event is on, around its window. Neither is an exception (an alarm is a point past an enabled alarm limit,
`limits.excursions`; a log entry is a record that mentions what it is about), so they don't join the case: they are
shown on it, beside the detections and notifications that did.

    context(conn, item_id) -> {"window", "alarms", "log", "basis"}

The event's window runs from the earliest start to the latest end of the findings on the case (a finding that gives
only a day, an SAP notification's, counts when nothing gives a time), widened by `exceptions.around_before_hours`
and `exceptions.around_after_hours`: a trip's vibration climbs before it, and the restart is logged after. "The
assets it is on" is read as the join reads them (`exceptions.anchors`), so what joins a case and what is shown
around it agree, whatever the asset type.
"""

from datetime import datetime, timedelta

from ..core import clock, settings
from .exceptions import anchor_map, anchors

STEP = 5  # minutes between the historian values an alarm is read from
MAX_ROWS = 25  # of each, the earliest first: what reads on one page


def _t(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)[:19]) if v else None
    except ValueError:
        return None


def window(conn, item_id: int) -> tuple[datetime, datetime, list[str]] | None:
    """The event's window, widened, and every subject on the case; None when the case has no exception."""
    rows = conn.execute(
        "select subjects, first_seen, last_seen, evidence->'finding'->>'start' as s, evidence->'finding'->>'end' as e "
        "from exception_record where item_id = %s", (item_id,)).fetchall()
    if not rows:
        return None
    timed = [r for r in rows if r["s"] and "T" in r["s"]]
    picked = timed or [r for r in rows if r["s"]]
    if picked:
        a = min(_t(r["s"]) for r in picked)
        b = max(_t(r["e"]) or _t(r["s"]) for r in picked)
        if not timed:  # a day: the whole of it
            b += timedelta(days=1)
    else:
        a = min(r["first_seen"] for r in rows).astimezone(clock.zone()).replace(tzinfo=None)
        b = max(r["last_seen"] for r in rows).astimezone(clock.zone()).replace(tzinfo=None)
    before = settings.get(conn, "exceptions.around_before_hours")
    after = settings.get(conn, "exceptions.around_after_hours")
    return (a - timedelta(hours=before), b + timedelta(hours=after),
            list(dict.fromkeys(s for r in rows for s in r["subjects"] or [])))


def alarms(conn, mine: set[str], a: datetime, b: datetime, src=None) -> list[dict]:
    """The alarms that sounded in [a, b] on points standing for one of `mine`."""
    from ..analysis import limits
    from ..core.pack import resolve

    pids = [r["point_id"] for r in conn.execute(
        "select distinct point_id from point_limit where basis = 'alarm' and enabled order by 1")]
    near = [p for p, anc in anchor_map(conn, pids).items() if anc & mine]
    if not near:
        return []
    out = [x for x in limits.excursions(conn, resolve(conn), near, a, b, STEP, src) if x["basis"] == "alarm"]
    return [{"point_id": x["point_id"], "point": x["point"], "type": x["limit_type"], "level": x.get("level"),
             "limit": x["limit_value"], "peak": x["peak"], "start": x["start"].isoformat(timespec="minutes"),
             "end": x["end"].isoformat(timespec="minutes") if x["end"] else None} for x in out][:MAX_ROWS]


def log(conn, mine: set[str], a: datetime, b: datetime) -> list[dict]:
    """The operator log entries written in [a, b] that mention one of `mine` (or what stands for one)."""
    rows = conn.execute(
        """
        select en.fuzer_id as id, t.value #>> '{}' as at, x.value #>> '{}' as text, w.value #>> '{}' as author,
               array_agg(distinct m.dst) as mentions
        from entity en
        join golden t on t.fuzer_id = en.fuzer_id and t.field = 'timestamp'
        join edge m on m.src = en.fuzer_id and m.rel = 'mentions'
        left join golden x on x.fuzer_id = en.fuzer_id and x.field = 'text'
        left join golden w on w.fuzer_id = en.fuzer_id and w.field = 'author'
        where en.kind = 'log_entry' and en.retired_at is null and en.merged_into is null
          and t.value #>> '{}' >= %s and t.value #>> '{}' <= %s
        group by en.fuzer_id, t.value, x.value, w.value order by at, en.fuzer_id
        """, (a.isoformat(timespec="minutes"), b.isoformat(timespec="minutes"))).fetchall()
    stands = anchor_map(conn, list({m for r in rows for m in r["mentions"]}))
    return [{"id": r["id"], "at": r["at"], "author": r["author"], "text": r["text"]} for r in rows
            if any(stands.get(m, set()) & mine for m in r["mentions"])][:MAX_ROWS]


def context(conn, item_id: int, src=None) -> dict:
    """What sounded and what was written around the event a case works."""
    w = window(conn, item_id)
    if w is None:
        return {"window": None, "alarms": [], "log": [], "basis": "This case works no exception, so it has no event."}
    a, b, subjects = w
    mine = anchors(conn, subjects)
    return {"window": {"start": a.isoformat(timespec="minutes"), "end": b.isoformat(timespec="minutes")},
            "alarms": alarms(conn, mine, a, b, src), "log": log(conn, mine, a, b),
            "basis": "Alarms on points of the assets this event is on, and operator log entries that mention them, "
                     "from the earliest finding on the case to the latest, widened by "
                     f"{settings.get(conn, 'exceptions.around_before_hours')} h before and "
                     f"{settings.get(conn, 'exceptions.around_after_hours')} h after."}
