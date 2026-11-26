"""Re-plan when something breaks (P5.21, #1252): a trip, a late part or a lapsed permit that hits planned work is a
**plan change to review**, routed to the maintenance planner with the plan re-levelled around it.

    disruption(conn, row)        what an open trip, parts or permit exception means for the plan: the plant or the
                                 order it holds, from when until when, and the constraint it puts on the work
    watch(conn)                  the `plan_changes` detector: each disruption that hits planned work raised as
                                 `plan_change` (keyed by the disruption), its proposal worked out; cleared once
                                 nothing is hit any more
    change(conn, id)             a plan change as the planner reads it: what happened and the proposal

What each disruption holds, from the records, never the equipment type:

    trip     the work on the tripped machine that needs it running, for the hours a trip is allowed for
             (at_risk.TRIP_HOURS, the same allowance "If this trips" uses); work on it that needs it down and fits that
             window is listed as worth pulling forward
    part     the order's jobs, until its latest part arrives (the purchase order's date, or the earliest it can come
             if ordered now)
    permit   the work on the plant it holds isolated, until it is reissued (PERMIT_HOURS allowed)

The proposal is Fuzer's own levelling, not a second model: the held jobs move (a schedule activity gets the date as
its start no earlier than, and its schedule is levelled again with `schedule.apply`; other work slips), and
the result is read in a savepoint and rolled back: each move with the constraint behind it, and the finish, the
deferred oil (`at_risk.windows`) and the overloads (`schedule.constraints`) before and after. Deterministic: no model is
asked anything.
"""

from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from ..core import clock, detectors, work
from ..core.findings import DETERMINISTIC, Finding, at, fact
from ..core.jsonsafe import plain
from ..core.plan import PlanError
from ..core.wording import plural
from . import at_risk, materials, permits, schedule
from .options import DOWN_TYPES
from .plan import ACTIVE, BUNDLE_DAYS, SHUTDOWNS, _when

PLANNER = "maintenance_planner"
PERMIT_HOURS = 24  # allowed for reissuing a lapsed permit before work under it goes on: a planning allowance
HORIZON_DAYS = at_risk.HORIZON_DAYS  # how far past the disruption the proposal looks
CAUSES = (*DOWN_TYPES, materials.TYPE, permits.TYPE)


def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("plan_change") or exception_type(
        "plan_change", "Plan change to review", role=f"role:{PLANNER}", audience=("ioc",), joins=False,
        description="A trip, a late part or a lapsed permit has hit planned work. The plan is levelled again around "
                    "it: each job that moves and the constraint behind it, the work worth pulling forward, and the "
                    "finish, deferred oil and overloads before and after. It clears when nothing on the plan is hit "
                    "any more.")


TYPE = _type().name


def _say(t: datetime, hour: bool = True) -> str:
    return t.astimezone(clock.zone()).strftime("%a %d %b %H:%M" if hour else "%a %d %b")


# ---- what a disruption holds -------------------------------------------------------------------------------------

def disruption(conn, row: dict) -> dict | None:
    """What an open trip, parts or permit exception means for the plan: `subjects` (the plant it is about) or `order`
    (the work order its parts are for), the window it holds from `start` to `until`, `say` (what happened, as a
    field operator reads it on a moved job) and `constraint` (the constraint, with its date). None when the records
    don't say when."""
    ev = row["evidence"] or {}
    found = ev.get("finding") or {}
    title = row["title"] or ""
    out = {"key": f"{row['type']}:{row['id']}", "type": row["type"], "cause_id": row["id"], "case": row["item_id"],
           "subjects": list(row["subjects"] or []), "source": row["source"], "order": None}
    if row["type"] in DOWN_TYPES:
        start = _when(ev.get("tripped_at") or found.get("start") or row["first_seen"])
        until = start + timedelta(hours=at_risk.TRIP_HOURS)
        what = title.split(" tripped ")[0] if " tripped " in title else _label(conn, out["subjects"][:1])
        say = f"{what} tripped"
        constraint = f"{say}: down until {_say(until)}"
    elif row["type"] == materials.TYPE:
        order = ev.get("work_order") or row["key"]
        due = [_when(x["need_date"]) + timedelta(days=x["days_late"]) for x in ev.get("lines") or []
               if x.get("days_late") is not None and x.get("need_date")]
        due += [_when(x["earliest_if_ordered_now"]) for x in ev.get("lines") or [] if x.get("earliest_if_ordered_now")]
        if not due:
            return None
        until = max(due)
        start = min([_when(x["need_date"]) for x in ev.get("lines") or [] if x.get("need_date")] or [until])
        say = f"parts for {order} are late"
        constraint = f"{say}: due {_say(until, hour=False)}"
        out["order"] = order
    elif row["type"] == permits.TYPE:
        start = _when(found.get("start") or row["first_seen"])
        until = max(start, row["first_seen"]) + timedelta(hours=PERMIT_HOURS)
        say = f"permit {ev.get('permit') or row['key']} lapsed"
        constraint = f"{say}: held until it is reissued, {PERMIT_HOURS} h allowed"
    else:
        return None
    return {**out, "start": start, "until": until, "say": say, "constraint": constraint}


def _label(conn, ids: list[str]) -> str:
    row = conn.execute("select label from entity_label where fuzer_id = any(%s) order by label limit 1", (ids,)).fetchone()
    return row["label"] if row else "the machine"


# a job, not the SAP order its schedule's activities roll up into, and not work already started
_JOBS = """
    select p.* from plan_item p
    where p.status = any(%(active)s) and p.status <> 'in_progress'
      and not exists (select 1 from plan_item c where c.parent_id = p.id)
"""


def hits(conn, d: dict) -> list[dict]:
    """The planned jobs the disruption holds: the order's jobs starting before its parts arrive, the work on a tripped
    machine inside its window that needs it running, or the work not yet done on the plant a lapsed permit holds."""
    args = {"active": list(ACTIVE), "start": d["start"], "until": d["until"], "subjects": d["subjects"],
            "order": d["order"], "sd": list(SHUTDOWNS)}
    if d["order"]:
        sql = (_JOBS + " and (p.source = 'sap' and p.source_key = %(order)s or p.parent_id in "
               "(select id from plan_item where source = 'sap' and source_key = %(order)s)) and p.start_at < %(until)s")
    elif d["type"] in DOWN_TYPES:
        sql = (_JOBS + " and p.subjects && %(subjects)s and p.start_at < %(until)s and p.end_at > %(start)s"
               " and not p.needs_shutdown and p.kind <> all(%(sd)s)")
    else:  # a lapsed permit holds every job on its plant not yet done, overdue ones too, until it is reissued
        sql = _JOBS + " and p.subjects && %(subjects)s and p.start_at < %(until)s"
    return conn.execute(sql + " order by p.start_at, p.id", args).fetchall()


def pull_forward(conn, d: dict) -> list[dict]:
    """For a trip: work on the machine that needs it down, due within BUNDLE_DAYS of its restart, short enough to do
    while it is down anyway. Schedule activities stay with their logic."""
    if d["type"] not in DOWN_TYPES:
        return []
    rows = conn.execute(
        _JOBS + " and p.subjects && %(subjects)s and (p.needs_shutdown or p.kind = any(%(sd)s)) "
                "and p.source <> 'schedule' and p.start_at >= %(until)s and p.start_at < %(until)s + %(near)s "
                "and p.end_at - p.start_at <= %(span)s order by p.start_at, p.id",
        {"active": list(ACTIVE), "subjects": d["subjects"], "sd": list(SHUTDOWNS), "until": d["until"],
         "near": timedelta(days=BUNDLE_DAYS), "span": d["until"] - d["start"]}).fetchall()
    return [{"item_id": r["id"], "title": r["title"], "start": r["start_at"], "end": r["end_at"],
             "order": r["source_key"] if r["source"] == "sap" else None,
             "because": f"needs {_say(r['start_at'], hour=False)}'s shutdown; {d['say']}, down from {_say(d['start'])} "
                        f"to {_say(d['until'])}"} for r in rows]


# ---- the proposal ------------------------------------------------------------------------------------------------

def _apply(conn, d: dict, by: str) -> dict:
    """Write the disruption onto the plan and level each schedule it touches again. The proposal runs this in a
    savepoint it rolls back."""
    holds: dict[int, tuple[datetime, str]] = {h["id"]: (d["until"], d["constraint"]) for h in hits(conn, d)}
    rows = {r["id"]: r for r in conn.execute("select * from plan_item where id = any(%s)", (list(holds),))}
    projects = set()
    for i, (when, _why) in holds.items():
        r = rows.get(i)
        if r is None:
            continue
        if r["source"] == "schedule":
            nb = max(r["not_before"], when) if r["not_before"] else when
            conn.execute("update plan_item set not_before = %s, updated_by = %s, updated_at = now() where id = %s",
                         (nb, by, i))
            projects.add(r["project"])
            continue
        start = max(r["start_at"], when)
        if start != r["start_at"]:
            conn.execute("update plan_item set start_at = %s, end_at = %s, moved_by = %s, updated_by = %s, "
                         "updated_at = now() where id = %s", (start, start + (r["end_at"] - r["start_at"]), by, by, i))
    for p in sorted(x for x in projects if x):
        schedule.apply(conn, p, by)
    return {"holds": holds, "projects": sorted(x for x in projects if x)}


def _snapshot(conn, start: datetime, end: datetime, ids=()) -> dict[int, dict]:
    return {r["id"]: r for r in conn.execute(
        "select id, title, kind, source, source_key, project, code, parent_id, start_at, end_at, crew, item_id, "
        "subjects, released_at from plan_item where status = any(%s) and ((start_at < %s and end_at > %s) or id = any(%s))",
        (list(ACTIVE), end, start, list(ids)))}


def _kpis(conn, rows: dict[int, dict], scope: set[int], start: datetime, days: int) -> dict:
    """The finish of the work in the change, the oil deferred by shutdowns over the window (at_risk.windows) and the
    overloads on every shared resource (the constraint check), as the plan stands."""
    oil = [x for w in at_risk.windows(conn, start, days)["windows"] for x in w["at_risk"] if x["quantity"] == "oil_rate"]
    return {"finish": max((rows[i]["end_at"] for i in scope if i in rows), default=None),
            "deferred_oil": round(sum(x["value"] for x in oil), 1), "deferred_unit": oil[0]["unit"] if oil else "bbl",
            "overloads": len(schedule.constraints(conn, start, days, moves=False)["overloads"])}


def _why(i: int, done: dict, after: dict, acts: dict, moved: set[int], names: dict[str, str]) -> str:
    """The constraint behind one move."""
    if i in done["holds"]:
        return done["holds"][i][1]
    r = after[i]
    if r["source"] == "schedule" and i in acts:
        a = acts[i]
        if a["held_back_by"]:
            res = a["held_back_by"]["resource"]
            return f"waits for {names.get(res, res)}" + (f" ({', '.join(a['held_back_by']['activities'])})"
                                                   if a["held_back_by"]["activities"] else "")
        code = {x["code"]: x["id"] for x in acts.values()}
        preds = [p["code"] for p in a["predecessors"] if code.get(p["code"]) in moved]
        return f"follows {', '.join(preds)}" if preds else f"levelled with the rest of {r['project']}"
    if any(x["parent_id"] == i for x in after.values()):
        return "spans its operations"
    return "levelled"


def work_out(conn, d: dict, by: str) -> dict:
    """The proposal for a disruption: the plan with it applied, read in a savepoint and rolled back. Each move (before and after, and the constraint behind it), the work worth pulling
    forward, the schedules levelled again and the KPIs before and after."""
    t0, t1 = d["start"] - timedelta(days=1), d["start"] + timedelta(days=HORIZON_DAYS)
    before = _snapshot(conn, t0, t1, [h["id"] for h in hits(conn, d)])
    conn.execute("savepoint plan_change")
    try:
        done = _apply(conn, d, by)
        after = _snapshot(conn, t0, t1, list(before))
        moved = {i for i in before if i in after
                 and (before[i]["start_at"], before[i]["end_at"]) != (after[i]["start_at"], after[i]["end_at"])}
        acts = {a["id"]: a for p in done["projects"] for a in schedule.analyse(conn, p)["activities"]}
        names = {r["code"]: r["name"] for r in conn.execute("select code, name from plan_resource")}
        scope = moved | set(done["holds"]) | {i for i, r in after.items() if r["project"] in done["projects"]}
        times = [t for i in moved for r in (before[i], after[i]) for t in (r["start_at"], r["end_at"])] or [d["start"]]
        start = min(times) - timedelta(days=1)
        days = max(1, min(366, (max(times) - start).days + 2))
        kpi_after = _kpis(conn, after, scope, start, days)
        moves = sorted(({"item_id": i, "title": before[i]["title"], "code": before[i]["code"],
                         "project": before[i]["project"], "kind": before[i]["kind"], "crew": before[i]["crew"],
                         "case": before[i]["item_id"], "released": bool(before[i]["released_at"]),
                         "order": before[i]["source_key"] if before[i]["source"] == "sap" else None,
                         "before": {"start": before[i]["start_at"], "end": before[i]["end_at"]},
                         "after": {"start": after[i]["start_at"], "end": after[i]["end_at"]},
                         "hours": round((after[i]["start_at"] - before[i]["start_at"]).total_seconds() / 3600, 1),
                         "because": _why(i, done, after, acts, moved, names)} for i in moved),
                       key=lambda m: (m["before"]["start"], m["item_id"]))
    finally:
        conn.execute("rollback to savepoint plan_change")
        conn.execute("release savepoint plan_change")
    return {"say": d["say"], "constraint": d["constraint"], "start": d["start"], "until": d["until"],
            "moves": moves, "pull_forward": pull_forward(conn, d), "schedules": done["projects"],
            "kpis": {"before": _kpis(conn, before, scope, start, days), "after": kpi_after, "start": start, "days": days},
            "basis": "Fuzer's levelling and constraint check on the plan as it stands, with the held jobs moved; "
                     "deferred oil is the wells behind each shutdown at their latest "
                     "test rate"}


def _finding(d: dict, p: dict) -> Finding:
    b, a = p["kpis"]["before"], p["kpis"]["after"]
    items = [fact("What happened", d["say"]), fact("Constraint", d["constraint"]),
             fact("Jobs to move", len(p["moves"])),
             at("Finish now", b["finish"]), at("Finish after", a["finish"]),
             fact("Deferred oil now", b["deferred_oil"], b["deferred_unit"]),
             fact("Deferred oil after", a["deferred_oil"], a["deferred_unit"]),
             fact("Overloads now", b["overloads"]), fact("Overloads after", a["overloads"]),
             fact("Worth pulling forward", len(p["pull_forward"]) or None)]
    return Finding("replan.plan_change", DETERMINISTIC,
                   method="the held jobs moved and each schedule they touch levelled again against every shared "
                          "resource (schedule.apply), with the constraint check and production at risk before and after",
                   start=d["start"], end=d["until"], data={"cause": d["key"], "moves": len(p["moves"])},
                   items=tuple(i for i in items if i))


def _store(conn, d: dict, by: str) -> dict:
    """Work the proposal out, and raise (or update) the plan change."""
    from ..core.exceptions import raise_exception

    p = work_out(conn, d, by)
    n = len(p["moves"])
    title = f"{d['say'][:1].upper()}{d['say'][1:]}: {plural(n, 'planned job')} to move"
    x = raise_exception(conn, TYPE, d["key"], title, by, source=d["source"], refs=d["subjects"], priority=0.6,
                        finding=_finding(d, p))
    conn.execute(
        """
        insert into plan_change (key, exception_id, proposal) values (%s, %s, %s)
        on conflict (key) do update set exception_id = excluded.exception_id, proposal = excluded.proposal,
            updated_at = now()
        """, (d["key"], x["id"], Jsonb(plain(p))))
    return x


def _planned(conn, pack) -> bool:
    return conn.execute("select 1 from plan_item limit 1").fetchone() is not None


@detectors.register("plan_changes", title="Planned work a trip, a late part or a lapsed permit has hit",
                    kind=detectors.RULE, cadence="hourly", raises=(TYPE,),
                    needs={"planned work": _planned},
                    eval="test:tests/ops/test_replan.py::test_a_trip_on_planned_work_is_a_plan_change_with_each_move_and_why",
                    found=lambda out: len(out["raised"]))
def watch(conn, by: str = "agent:planner") -> dict:
    """Each open trip, late part or lapsed permit that holds planned work, raised as `plan_change` for the maintenance
    planner (keyed by the disruption) with its proposal worked out; open ones nothing is hit by any more (the cause
    cleared, or the work moved) are cleared."""
    from ..core.exceptions import ExceptionError, clear_exception

    held = [r["key"] for r in conn.execute("select key from exception_record where type = %s and state = 'open'", (TYPE,))]
    raised, cleared, errors, seen = [], [], [], set()
    for row in conn.execute("select * from exception_record where state = 'open' and type = any(%s) order by id",
                            (list(CAUSES),)).fetchall():
        d = disruption(conn, row)
        if d is None or not hits(conn, d):
            continue
        seen.add(d["key"])
        try:
            x = _store(conn, d, by)
        except (work.WorkError, ExceptionError, PlanError) as e:
            errors.append(f"{d['key']}: {e}")
            continue
        raised.append({"key": d["key"], "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
    for key in set(held) - seen:
        clear_exception(conn, TYPE, key, by, reason="nothing on the plan is held by it any more")
        cleared.append(key)
    conn.commit()
    return {"raised": raised, "cleared": cleared, "errors": errors}


# ---- the planner's side --------------------------------------------------------------------------------------------

def _open(conn, exception_id: int) -> tuple[dict, dict]:
    """The plan change and its disruption, as they stand."""
    pc = conn.execute("select c.*, e.state as exception_state from plan_change c join exception_record e "
                      "on e.id = c.exception_id where c.exception_id = %s", (exception_id,)).fetchone()
    if pc is None:
        raise PlanError(f"no plan change {exception_id}")
    cause = conn.execute("select * from exception_record where id = %s",
                         (int(pc["key"].rsplit(":", 1)[1]),)).fetchone()
    d = disruption(conn, cause) if cause else None
    if d is None:
        raise PlanError(f"plan change {exception_id}: what caused it is no longer on record")
    return pc, d


def change(conn, exception_id: int) -> dict:
    """A plan change as the planner reads it: what happened and the constraint it puts on the work, and the proposal
    (moves, pull-forwards, KPIs before and after)."""
    pc, d = _open(conn, exception_id)
    return {"exception_id": exception_id, "key": pc["key"], "say": d["say"], "constraint": d["constraint"],
            "start": d["start"], "until": d["until"], "cause_case": d["case"], "proposal": pc["proposal"]}
