"""Alarm performance as improvement work (P3.10a, #840): the alarm system measured against ISA-18.2 and EEMUA 191, with
what it finds raised for the alarm owner, never a live alarm list or anything to acknowledge. The alarms are each
point going past an enabled alarm limit while its unit runs (`limits.excursions` on the alarm basis), the same
source as `alarms.alarm_analysis`, so nothing here knows a field, a console or a tag.

- `review(conn, pack, until, days)`: over the window,
  - floods: more than the flood rate (`alarms.flood_per_10min`, EEMUA 191: 10 in 10 minutes) starting within any 10
    minutes, each with its top contributing points. Alarms already on when the window opens are not counted as new.
  - standing: an alarm still on at the end of the window that has been on for `alarms.standing_hours` or more, one
    per point (its limits together).
  - repeating: a point alarming `REPEATS` times or more in the window, the chattering and fleeting alarms an operator
    learns to ignore (a historian at 10 minute resolution can't see the one minute chatter of the standards, so the
    count over the window stands in for it).
- The `alarm_performance` detector raises a flood as one `alarm_flood` case with its top contributors (not one case
  per alarm), and a standing or repeating point as one `alarm_bad_actor` case: rationalize the alarm, fix the
  instrument or review the setpoint. A bad actor already raised stays quiet while it goes on; once a person closes
  its case it is raised again only if it recurs after that. A run opens at most `MAX_NEW` new bad actor cases, the
  worst first, so a plant with many standing alarms gets a worklist that fills as cases are closed, not a wall.
"""

from collections import Counter
from datetime import datetime, timedelta

from ..core import detectors, settings
from ..core.findings import DETERMINISTIC, Finding, at, fact

REPEATS = 12  # alarms on one point within the window
MAX_NEW = 10  # new bad actor cases a run opens, the worst first; the rest wait for the next run
TOP = 5
DAYS = 2


def _alarms(conn, pack, start: datetime, end: datetime, src=None) -> list[dict]:
    from . import limits
    from .alarms import _scope_points

    return [x for x in limits.excursions(conn, pack, _scope_points(conn, None), start, end, 10, src)
            if x["basis"] == "alarm"]


def _dt(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def _floods(ex: list[dict], start: datetime, rate: int) -> list[dict]:
    new = sorted((x for x in ex if _dt(x["start"]) > start), key=lambda x: _dt(x["start"]))
    floods, i = [], 0
    for j, x in enumerate(new):
        t = _dt(x["start"])
        while _dt(new[i]["start"]) < t - timedelta(minutes=10):
            i += 1
        n = j - i + 1
        if n <= rate:
            continue
        if floods and t - floods[-1]["end"] <= timedelta(minutes=10):
            floods[-1]["end"] = t
            floods[-1]["peak"] = max(floods[-1]["peak"], n)
        else:
            floods.append({"start": _dt(new[i]["start"]), "end": t, "peak": n})
    for f in floods:
        within = [x for x in new if f["start"] <= _dt(x["start"]) <= f["end"]]
        f["alarms"] = len(within)
        f["top"] = [{"point": p, "alarms": n} for p, n in Counter(x["point"] for x in within).most_common(TOP)]
        f["point_ids"] = sorted({x["point_id"] for x in within})
    return floods


def review(conn, pack, until: str | None = None, days: int = DAYS, src=None) -> dict:
    """The floods, standing alarms and repeating points over the `days` up to `until`."""
    from .calcs import window_of

    start, end = window_of(conn, days, until)
    rate, standing_h = settings.get(conn, "alarms.flood_per_10min"), settings.get(conn, "alarms.standing_hours")
    ex = _alarms(conn, pack, start, end, src)
    bad: dict[str, dict] = {}
    for x in ex:
        if x["end"] is None and end - _dt(x["start"]) >= timedelta(hours=standing_h):
            b = bad.setdefault(x["point_id"], {"point_id": x["point_id"], "point": x["point"], "unit_id": x["unit_id"],
                                               "kinds": [], "limits": [], "since": _dt(x["start"]), "alarms": 0})
            b["limits"].append(f"{x['limit_type']} {x['limit_value']:g}")
            b["since"] = min(b["since"], _dt(x["start"]))
            if "standing" not in b["kinds"]:
                b["kinds"].append("standing")
    counts = Counter(x["point_id"] for x in ex if _dt(x["start"]) > start)
    for x in ex:
        if counts[x["point_id"]] >= REPEATS:
            b = bad.setdefault(x["point_id"], {"point_id": x["point_id"], "point": x["point"], "unit_id": x["unit_id"],
                                               "kinds": [], "limits": [], "since": _dt(x["start"]), "alarms": 0})
            if "repeating" not in b["kinds"]:
                b["kinds"].append("repeating")
                b["alarms"] = counts[x["point_id"]]
                b["starts"] = sorted(_dt(y["start"]) for y in ex if y["point_id"] == x["point_id"])
    hours = (end - start).total_seconds() / 3600
    return {"window": [start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")],
            "alarms": len(ex), "per_hour": round(len(ex) / hours, 2) if hours else 0.0, "target_per_hour":
            settings.get(conn, "alarms.target_per_hour"), "floods": _floods(ex, start, rate),
            "bad_actors": sorted(bad.values(), key=lambda b: (-b["alarms"], b["since"], b["point"]))}


def _flood_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("alarm_flood") or exception_type(
        "alarm_flood", "Alarm flood", role="role:engineer", audience=("ioc",), joins=False,
        description="More alarms than an operator can handle (EEMUA 191: over 10 in 10 minutes), raised once per "
                    "flood with the points that drove it, for the alarm owner to rationalize. Not an alarm to "
                    "acknowledge.")


def _actor_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("alarm_bad_actor") or exception_type(
        "alarm_bad_actor", "Alarm bad actor", role="role:engineer", audience=("ioc",), joins=False,
        description="An alarm standing for a day or more, or one point alarming over and over: rationalize the "
                    "alarm, fix the instrument or review the setpoint. Raised once; quiet until it is resolved or "
                    "recurs after a fix. Not an alarm to acknowledge.")


FLOOD_TYPE, ACTOR_TYPE = _flood_type().name, _actor_type().name
_WHAT = "Alarm performance work for the alarm owner, not an alarm to acknowledge."


def _closed(conn) -> dict[str, datetime]:
    """Each bad actor whose last case a person closed, and the end of the data its case last saw: in data time, so
    a replay of history and a live plant read the same."""
    return {r["key"]: datetime.fromisoformat(r["seen"]) for r in conn.execute(
        "select key, max(evidence #>> '{finding,end}') as seen from exception_record "
        "where type = %s and state <> 'open' group by key", (ACTOR_TYPE,)) if r["seen"]}


def _recurs(b: dict, after: datetime | None) -> bool:
    """Whether a bad actor is new since its last case was closed: a standing alarm that came on after it, or the
    repeats counted again from it."""
    if after is None:
        return True
    if "standing" in b["kinds"] and b["since"] > after:
        return True
    return "repeating" in b["kinds"] and sum(1 for t in b.get("starts", []) if t > after) >= REPEATS


@detectors.register("alarm_performance", title="Alarm floods and bad actors against ISA-18.2 and EEMUA 191",
                    kind=detectors.RULE, cadence="daily", raises=(FLOOD_TYPE, ACTOR_TYPE),
                    needs={"historian points": detectors.POINTS,
                           "alarm limits": "select 1 from point_limit where basis = 'alarm' limit 1"},
                    eval="test:tests/analysis/test_alarm_performance.py::"
                         "test_a_flood_is_one_case_and_a_bad_actor_is_raised_once",
                    found=lambda out: len(out["raised"]))
def watch(conn, pack, until: str | None = None, days: int = DAYS, by: str = "agent:alarm_performance",
          src=None) -> dict:
    """Each flood raised as one `alarm_flood` case with its top contributors, and each standing or repeating point
    as one `alarm_bad_actor` case (keyed by the point), quiet while it goes on and raised again only if it recurs
    after its case was closed."""
    from ..core import work
    from ..core.exceptions import ExceptionError, raise_exception, reconcile

    reconcile(conn)  # a case a person finished closes its exception first, so it reads as closed, not held
    r = review(conn, pack, until, days, src)
    closed = _closed(conn)
    held = {x["key"] for x in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (ACTOR_TYPE,))}
    raised, quiet, waiting, errors = [], [], [], []
    for f in r["floods"]:
        key = f"flood · {f['start'].isoformat(timespec='minutes')}"
        found = Finding(
            "alarm_performance.flood", DETERMINISTIC, method="EEMUA 191 alarm flood", standard="EEMUA 191",
            start=f["start"], end=f["end"], data={"peak_10_min": f["peak"], "alarms": f["alarms"]},
            items=(at("From", f["start"]), at("To", f["end"]), fact("Alarms in the flood", f["alarms"]),
                   fact("Peak in 10 minutes", f["peak"]),
                   *(fact(f"Top contributor {i + 1}", f"{t['point']}: {t['alarms']} alarms")
                     for i, t in enumerate(f["top"])),
                   fact("What this is", _WHAT)))
        try:
            x = raise_exception(conn, FLOOD_TYPE, key, f"Alarm flood from {f['start']:%Y-%m-%d %H:%M}: {f['alarms']} "
                                f"alarms, {f['peak']} in 10 minutes, led by {f['top'][0]['point']}", by,
                                source="alarm_performance", refs=f["point_ids"][:20], priority=0.5, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{key}: {e}")
            continue
        raised.append({"type": FLOOD_TYPE, "key": key, "exception_id": x["id"], "new": x["raised"]})
    for b in r["bad_actors"]:
        key = b["point"]
        if key not in held and not _recurs(b, closed.get(key)):
            quiet.append(key)  # closed by a person and not back since: stays quiet
            continue
        if key not in held and sum(1 for x in raised if x["type"] == ACTOR_TYPE and x["new"]) >= MAX_NEW:
            waiting.append(key)
            continue
        kinds = " and ".join(b["kinds"])
        found = Finding(
            "alarm_performance.bad_actor", DETERMINISTIC, method="ISA-18.2 bad actor alarm", standard="ISA-18.2",
            start=b["since"], end=r["window"][1], data={"kinds": b["kinds"], "alarms": b["alarms"]},
            items=(fact("Point", b["point"]), fact("Found", kinds), fact("Limits standing", ", ".join(b["limits"])),
                   at("On since", b["since"]) if "standing" in b["kinds"] else None,
                   fact(f"Alarms in the {days} days to {r['window'][1].replace('T', ' ')}", b["alarms"] or None),
                   fact("Asks", "Rationalize the alarm, fix the instrument, or review the setpoint."),
                   fact("What this is", _WHAT)))
        try:
            x = raise_exception(conn, ACTOR_TYPE, key, f"{b['point']}: {kinds} alarm", by, source="alarm_performance",
                                refs=[b["point_id"]], priority=0.3, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{key}: {e}")
            continue
        raised.append({"type": ACTOR_TYPE, "key": key, "exception_id": x["id"], "new": x["raised"]})
    conn.commit()
    return {"window": r["window"], "alarms": r["alarms"], "per_hour": r["per_hour"], "floods": len(r["floods"]),
            "bad_actors": len(r["bad_actors"]), "raised": raised, "quiet": quiet, "waiting": waiting, "errors": errors}


def summary(conn, pack, days: int = DAYS, until: str | None = None, src=None) -> dict:
    """The alarm-performance view: the window's rate against the target, its floods and bad actors, and the alarm
    cases open now. Measures and work, never a list of alarms to acknowledge."""
    from ..sources import timeseries

    src = src or timeseries.provider()
    r = review(conn, pack, until, days, src)
    cases = [dict(x) for x in conn.execute(
        """
        select e.id, e.type, e.key, e.title, e.item_id, e.first_seen from exception_record e
        where e.type in (%s, %s) and e.state = 'open' order by e.first_seen desc limit 50
        """, (FLOOD_TYPE, ACTOR_TYPE))]
    case_of = {(c["type"], c["key"]): c for c in cases}
    def case(type_: str, key: str) -> int | None:
        return (case_of.get((type_, key)) or {}).get("item_id")

    floods = [{"start": f["start"].isoformat(timespec="minutes"), "end": f["end"].isoformat(timespec="minutes"),
               "alarms": f["alarms"], "peak_10_min": f["peak"], "top": f["top"],
               "case": case(FLOOD_TYPE, f"flood · {f['start'].isoformat(timespec='minutes')}")} for f in r["floods"]]
    actors = [{"point": b["point"], "point_id": b["point_id"], "kinds": b["kinds"], "limits": b["limits"],
               "since": b["since"].isoformat(timespec="minutes"), "alarms": b["alarms"],
               "case": case(ACTOR_TYPE, b["point"])} for b in r["bad_actors"]]
    return {"window": r["window"], "days": days, "alarms": r["alarms"], "per_hour": r["per_hour"],
            "target_per_hour": r["target_per_hour"], "flood_per_10min": settings.get(conn, "alarms.flood_per_10min"),
            "standing_hours": settings.get(conn, "alarms.standing_hours"), "repeats": REPEATS,
            "floods": floods, "bad_actors": actors,
            "open_cases": [{"id": c["id"], "type": c["type"], "title": c["title"], "item_id": c["item_id"]}
                           for c in cases],
            "synthetic": getattr(src, "name", None) == "synthetic",
            "note": "Measured against ISA-18.2 and EEMUA 191 from each point going past an enabled alarm limit while "
                    "its unit runs. Alarm performance work for the alarm owner, not alarms to acknowledge."}
