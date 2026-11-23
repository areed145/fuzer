"""Around an event (P3.5a, #692): the alarms that sounded and the operator log entries written about the assets a
case's event is on, around its window. Neither is an exception (an alarm is a point past an enabled alarm limit,
`limits.excursions`; a log entry is a record that mentions what it is about), so they don't join the case: they are
shown on it, beside the detections and notifications that did.

    context(conn, item_id) -> {"window", "units", "alarms", "log", "basis"}
    trend(conn, item_id) -> {"focus": [point id, ...], "marks": [...]} | None

`trend` is what the case's trend opens on: the points its findings were seen on and the points that alarmed in its
window, with the event itself shaded on the chart and its start marked with what it was ("Step change", "Low
alarm"), so the chart is about the case, not every value of its unit.

The event's window runs from the earliest start to the latest end of the findings on the case (a finding that gives
only a day, an SAP notification's, counts when nothing gives a time), widened by `exceptions.around_before_hours`
and `exceptions.around_after_hours`: a trip's vibration climbs before it, and the restart is logged after. "The
assets it is on" is read as the join reads them (`exceptions.anchors`), so what joins a case and what is shown
around it agree, whatever the asset type. `units` are the units the case's subjects are read on (`values.unit_of`),
so the case page can draw their trend over the same window (P5.13c).
"""

from datetime import datetime, timedelta

from ..core import clock, settings
from .exceptions import anchor_map, anchors

STEP = 5  # minutes between the historian values an alarm is read from
MAX_ROWS = 25  # of each, the earliest first: what reads on one page
MAX_UNITS = 4  # trends a case page offers


def _t(v) -> datetime | None:
    """A finding's start or end on the plant's wall clock: sources write some with a zone and some without (#1107)."""
    return clock.wall(v)


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
        # Raised on today's clock over data that ends earlier (a historical or synthetic scenario): the event is in
        # the data, so its window is the data's last day, not today's synthetic values (#1082).
        from ..graph.operations import data_through

        end = data_through(conn)
        if end and a.date() > datetime.fromisoformat(end[:10]).date():
            a = datetime.fromisoformat(end[:10])
            b = a + timedelta(days=1)
    before = settings.get(conn, "exceptions.around_before_hours")
    after = settings.get(conn, "exceptions.around_after_hours")
    return (a - timedelta(hours=before), b + timedelta(hours=after),
            list(dict.fromkeys(s for r in rows for s in r["subjects"] or [])))


def alarms(conn, mine: set[str], a: datetime, b: datetime, src=None, limit: int = MAX_ROWS) -> list[dict]:
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
             "end": x["end"].isoformat(timespec="minutes") if x["end"] else None} for x in out][:limit]


def log(conn, mine: set[str], a: datetime, b: datetime, limit: int = MAX_ROWS) -> list[dict]:
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
            if any(stands.get(m, set()) & mine for m in r["mentions"])][:limit]


def units(conn, subjects: list[str]) -> list[dict]:
    """The units the subjects' values are read on, in the subjects' order, each once, with their labels."""
    from ..analysis import values

    seen = list(dict.fromkeys(values.unit_of(conn, s)[1] for s in subjects))[:MAX_UNITS]
    # a drawing or other document has no values to trend
    papers = {r["fuzer_id"] for r in conn.execute(
        "select fuzer_id from entity where fuzer_id = any(%s) and kind = 'document'", (seen,))}
    seen = [u for u in seen if u not in papers]
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (seen,))}
    return [{"id": u, "label": labels.get(u)} for u in seen]


def context(conn, item_id: int, src=None) -> dict:
    """What sounded and what was written around the event a case works."""
    w = window(conn, item_id)
    if w is None:
        return {"window": None, "units": [], "alarms": [], "log": [], "basis": "This case works no exception, so it has no event."}
    a, b, subjects = w
    mine = anchors(conn, subjects)
    return {"window": {"start": a.isoformat(timespec="minutes"), "end": b.isoformat(timespec="minutes")},
            "units": units(conn, subjects), "alarms": alarms(conn, mine, a, b, src), "log": log(conn, mine, a, b),
            "basis": "Alarms on points of the assets this event is on, and operator log entries that mention them, "
                     "from the earliest finding on the case to the latest, widened by "
                     f"{settings.get(conn, 'exceptions.around_before_hours')} h before and "
                     f"{settings.get(conn, 'exceptions.around_after_hours')} h after."}


def _what(title: str) -> str:
    """What an event was, from its title: "Step change on 20-GT-2000A from ..." reads "Step change"."""
    return title.split(" on ")[0].split(" from ")[0].strip() or "Event"


def trend(conn, item_id: int, src=None) -> dict | None:
    """What the case's trend opens on and what it marks; None when the case works no exception."""
    rows = conn.execute(
        "select e.title, e.subjects, e.evidence->'finding'->>'start' as s, e.evidence->'finding'->>'end' as e "
        "from exception_record e where e.item_id = %s order by e.first_seen, e.id", (item_id,)).fetchall()
    w = window(conn, item_id)
    if not rows or w is None:
        return None
    a, b, subjects = w
    kinds = {r["fuzer_id"]: r["kind"] for r in conn.execute(
        "select fuzer_id, kind from entity where fuzer_id = any(%s)", (subjects,))}
    points = [s for s in subjects if kinds.get(s) == "timeseries"]
    points += [x["point_id"] for x in alarms(conn, anchors(conn, subjects), a, b, src)]
    marks, seen = [], set()
    for r in rows:
        start, end = _t(r["s"]) if r["s"] and "T" in r["s"] else None, _t(r["e"]) if r["e"] and "T" in (r["e"] or "") else None
        if not start or start in seen:
            continue
        seen.add(start)
        what = _what(r["title"])
        marks.append({"kind": "window", "start": start.isoformat(timespec="minutes"),
                      "end": (end or b).isoformat(timespec="minutes"), "label": what if end else f"{what}, still going",
                      "tone": "accent"})
        marks.append({"kind": "time", "at": start.isoformat(timespec="minutes"), "label": what, "tone": "danger"})
    return {"focus": list(dict.fromkeys(points)), "marks": marks}
