"""Re-plan when something breaks (P5.21, #1252): a trip, a late part or a lapsed permit that hits planned work is a
**plan change to review**, routed to the maintenance planner with the plan re-levelled around it.

    disruption(conn, row)        what an open trip, parts or permit exception means for the plan: the plant or the
                                 order it holds, from when until when, and the constraint it puts on the work
    watch(conn)                  the `plan_changes` detector: each disruption that hits planned work raised as
                                 `plan_change` (keyed by the disruption), its proposal worked out; cleared once
                                 nothing is hit any more
    change(conn, id)             a plan change as the planner reads it: the proposal and the constraints typed
    add_constraint / remove_constraint   a constraint in words (`read`), and the proposal worked out again

What each disruption holds, from the records, never the equipment type:

    trip     the work on the tripped machine that needs it running, for the hours a trip is allowed for
             (at_risk.TRIP_HOURS, the same allowance "If this trips" uses); work on it that needs it down and fits that
             window is listed as worth pulling forward
    part     the order's jobs, until its latest part arrives (the purchase order's date, or the earliest it can come
             if ordered now)
    permit   the work on the plant it holds isolated, until it is reissued (PERMIT_HOURS allowed)

The proposal is Fuzer's own levelling, not a second model: the held jobs move (a schedule activity gets the date as
its start no earlier than, and its schedule is levelled again with `schedule.apply`; other work slips), typed
constraints become capacity windows and holds, and the result is read in a savepoint and rolled back: each move with
the constraint behind it, and the finish, the deferred oil (`at_risk.windows`) and the overloads (`schedule.constraints`)
before and after. Deterministic: no model is asked anything.
"""

import re
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from ..core import clock, detectors, gates, work
from ..core.findings import DETERMINISTIC, Finding, at, fact
from ..core.jsonsafe import plain
from ..core.plan import PlanError
from ..core.wording import plural
from . import at_risk, materials, permits, schedule
from .options import DOWN_TYPES
from .plan import ACTIVE, BUNDLE_DAYS, SHUTDOWNS, _when

PLANNER = "maintenance_planner"
PERMIT_HOURS = 24  # allowed for reissuing a lapsed permit before work under it goes on: a planning allowance
HORIZON_DAYS = at_risk.HORIZON_DAYS  # how far past the disruption a typed constraint reaches
CAUSES = (*DOWN_TYPES, materials.TYPE, permits.TYPE)
FORMS = ("keep A0200 on 12 Apr", "no crane work before 10 Apr", "crew MECH max 4")


def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("plan_change") or exception_type(
        "plan_change", "Plan change to review", role=f"role:{PLANNER}", audience=("ioc",), joins=False,
        description="A trip, a late part or a lapsed permit has hit planned work. The plan is levelled again around "
                    "it: each job that moves and the constraint behind it, the work worth pulling forward, and the "
                    "finish, deferred oil and overloads before and after. Add a constraint in words and work it out "
                    "again. It clears when nothing on the plan is hit any more.")


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


# ---- constraints in words ----------------------------------------------------------------------------------------

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def _day(text: str, ref: datetime) -> datetime:
    """A day as a planner types it: 12 Apr, Apr 12, 12 April 2026, 2026-04-12, with an optional HH:MM. Without a year,
    the first such day from a month before `ref`."""
    t = text.strip().lower().rstrip(".")
    try:
        return clock.aware(datetime.fromisoformat(t))
    except ValueError:
        pass
    m = (re.fullmatch(r"(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?P<m>[a-z]{3})[a-z]*(?:\s+(?P<y>\d{4}))?(?:\s+(?P<h>\d{1,2}):(?P<n>\d{2}))?", t)
         or re.fullmatch(r"(?P<m>[a-z]{3})[a-z]*\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<y>\d{4}))?(?:\s+(?P<h>\d{1,2}):(?P<n>\d{2}))?", t))
    if not m or m["m"] not in MONTHS:
        raise PlanError(f"not a day: {text.strip()!r} (write 12 Apr, or 2026-04-12)")
    ref = ref.astimezone(clock.zone())
    year = int(m["y"] or ref.year)
    try:
        when = datetime(year, MONTHS.index(m["m"]) + 1, int(m["d"]), int(m["h"] or 0), int(m["n"] or 0),
                        tzinfo=clock.zone())
    except ValueError as e:
        raise PlanError(f"not a day: {text.strip()!r}") from e
    if not m["y"] and when < ref - timedelta(days=31):
        when = when.replace(year=year + 1)
    return when


def _resources(conn, word: str) -> list[dict]:
    """The shared resources a word names: by code, by kind (crane) or by a word of the name (fitters)."""
    w = word.strip().lower().removesuffix("s")
    rows = conn.execute("select code, name, kind from plan_resource order by code").fetchall()
    exact = [r for r in rows if r["code"].lower() == word.strip().lower()]
    return exact or [r for r in rows if r["kind"] == w or w in r["name"].lower()]


def read(conn, text: str, start: datetime) -> dict:
    """A constraint typed in words, read without a model. Three forms: keep <activity or order> on <day> (it starts
    that day), no <resource> work before <day> (the resource has nothing until then), crew <code> max <n> (that many
    for the window). Anything else is refused, naming the forms. `start` is the disruption's: the day a date with no
    year is read from, and where the windows begin."""
    t = " ".join((text or "").split())
    if m := re.fullmatch(r"keep (\S+) (?:on|from|at) (.+)", t, re.IGNORECASE):
        jobs = conn.execute(
            "select id from plan_item where status = any(%(a)s) and (code = %(r)s or (source = 'sap' and source_key = %(r)s "
            "and not exists (select 1 from plan_item c where c.parent_id = plan_item.id))) order by id",
            {"a": list(ACTIVE), "r": m[1]}).fetchall()
        if not jobs:
            raise PlanError(f"nothing on the plan is called {m[1]}: name an activity (A0200) or an SAP order")
        return {"text": t, "form": "keep", "item_ids": [j["id"] for j in jobs], "at": _day(m[2], start)}
    if m := re.fullmatch(r"no (.+?) work before (.+)", t, re.IGNORECASE):
        found = _resources(conn, m[1])
        if not found:
            raise PlanError(f"no shared resource is called {m[1]}")
        return {"text": t, "form": "before", "resources": [r["code"] for r in found], "capacity": 0,
                "from": start, "until": _day(m[2], start)}
    if m := re.fullmatch(r"crew (\S+) (?:max|at most) (\d+(?:\.\d+)?)", t, re.IGNORECASE):
        found = [r for r in _resources(conn, m[1]) if r["kind"] == "crew"]
        if not found:
            raise PlanError(f"no crew is called {m[1]}")
        return {"text": t, "form": "max", "resources": [found[0]["code"]], "capacity": float(m[2]),
                "from": start, "until": start + timedelta(days=HORIZON_DAYS)}
    raise PlanError(f"Fuzer reads three forms: {'; '.join(FORMS)}")


# ---- the proposal ------------------------------------------------------------------------------------------------

def _apply(conn, d: dict, constraints: list[dict], by: str) -> dict:
    """Write the disruption and the typed constraints onto the plan and level each schedule they touch again. The
    proposal runs this in a savepoint it rolls back."""
    holds: dict[int, tuple[datetime, str]] = {h["id"]: (d["until"], d["constraint"]) for h in hits(conn, d)}
    keeps = set()
    for c in constraints:
        if c["form"] == "keep":
            for i in c["item_ids"]:
                holds[i] = (_when(c["at"]), c["text"])
                keeps.add(i)
    rows = {r["id"]: r for r in conn.execute("select * from plan_item where id = any(%s)", (list(holds),))}
    projects = set()
    for i, (when, _why) in holds.items():
        r = rows.get(i)
        if r is None:
            continue
        if r["source"] == "schedule":
            nb = when if i in keeps or not r["not_before"] else max(r["not_before"], when)
            conn.execute("update plan_item set not_before = %s, updated_by = %s, updated_at = now() where id = %s",
                         (nb, by, i))
            projects.add(r["project"])
            continue
        start = when if i in keeps else max(r["start_at"], when)
        if start != r["start_at"]:
            conn.execute("update plan_item set start_at = %s, end_at = %s, moved_by = %s, updated_by = %s, "
                         "updated_at = now() where id = %s", (start, start + (r["end_at"] - r["start_at"]), by, by, i))
    windows = []
    for c in constraints:
        if c["form"] not in ("before", "max"):
            continue
        a, b = _when(c["from"]), _when(c["until"])
        for res in c["resources"]:
            if b > a:
                conn.execute("insert into plan_resource_capacity (resource, start_at, end_at, capacity, reason, created_by) "
                             "values (%s, %s, %s, %s, %s, %s)", (res, a, b, c["capacity"], f"Plan change: {c['text']}", by))
                windows.append({"resource": res, "text": c["text"]})
            projects |= {r["project"] for r in conn.execute(
                "select distinct i.project from plan_item i join plan_demand_all x on x.item_id = i.id "
                "where i.source = 'schedule' and x.resource = %s and i.status = any(%s) and i.start_at < %s and i.end_at > %s",
                (res, list(ACTIVE), _when(c["from"]) + timedelta(days=HORIZON_DAYS), _when(c["from"])))}
    for p in sorted(x for x in projects if x):
        schedule.apply(conn, p, by)
    return {"holds": holds, "windows": windows, "projects": sorted(x for x in projects if x)}


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
            typed = next((w["text"] for w in done["windows"] if w["resource"] == res), None)
            return typed or f"waits for {names.get(res, res)}" + (f" ({', '.join(a['held_back_by']['activities'])})"
                                                   if a["held_back_by"]["activities"] else "")
        code = {x["code"]: x["id"] for x in acts.values()}
        preds = [p["code"] for p in a["predecessors"] if code.get(p["code"]) in moved]
        return f"follows {', '.join(preds)}" if preds else f"levelled with the rest of {r['project']}"
    if any(x["parent_id"] == i for x in after.values()):
        return "spans its operations"
    return "levelled"


def work_out(conn, d: dict, constraints: list[dict], by: str) -> dict:
    """The proposal for a disruption with the constraints typed so far: the plan with them applied, read in a
    savepoint and rolled back. Each move (before and after, and the constraint behind it), the work worth pulling
    forward, the schedules levelled again and the KPIs before and after."""
    t0, t1 = d["start"] - timedelta(days=1), d["start"] + timedelta(days=HORIZON_DAYS)
    before = _snapshot(conn, t0, t1, [h["id"] for h in hits(conn, d)])
    conn.execute("savepoint plan_change")
    try:
        done = _apply(conn, d, constraints, by)
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
            "basis": "Fuzer's levelling and constraint check on the plan as it stands, with the held jobs moved and "
                     "the typed constraints applied; deferred oil is the wells behind each shutdown at their latest "
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
    """Work the proposal out with the constraints typed so far, and raise (or update) the plan change."""
    from ..core.exceptions import raise_exception

    pc = conn.execute("select * from plan_change where key = %s", (d["key"],)).fetchone()
    constraints = list(pc["constraints"]) if pc else []
    p = work_out(conn, d, constraints, by)
    n = len(p["moves"])
    title = f"{d['say'][:1].upper()}{d['say'][1:]}: {plural(n, 'planned job')} to move"
    x = raise_exception(conn, TYPE, d["key"], title, by, source=d["source"], refs=d["subjects"], priority=0.6,
                        finding=_finding(d, p))
    conn.execute(
        """
        insert into plan_change (key, exception_id, constraints, proposal) values (%s, %s, %s, %s)
        on conflict (key) do update set exception_id = excluded.exception_id, proposal = excluded.proposal,
            updated_at = now()
        """, (d["key"], x["id"], Jsonb(plain(constraints)), Jsonb(plain(p))))
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
    """A plan change as the planner reads it: what happened and the constraint it puts on the work, the proposal
    (moves, pull-forwards, KPIs before and after), the constraints typed and the forms Fuzer reads."""
    pc, d = _open(conn, exception_id)
    return {"exception_id": exception_id, "key": pc["key"], "say": d["say"], "constraint": d["constraint"],
            "start": d["start"], "until": d["until"], "cause_case": d["case"],
            "constraints": [{"text": c["text"], "form": c["form"]} for c in pc["constraints"]], "forms": list(FORMS),
            "proposal": pc["proposal"], "open": pc["exception_state"] == "open"}


def _editable(conn, exception_id: int, by: str) -> tuple[dict, dict]:
    if gates.machine(by):
        raise PlanError("a plan change is worked by a named person")
    pc, d = _open(conn, exception_id)
    if pc["exception_state"] != "open":
        raise PlanError(f"plan change {exception_id} is closed")
    return pc, d


def add_constraint(conn, exception_id: int, text: str, by: str) -> dict:
    """A constraint typed in words: read (`read`), kept on the plan change, and the proposal worked out again."""
    pc, d = _editable(conn, exception_id, by)
    c = read(conn, text, d["start"])
    conn.execute("update plan_change set constraints = constraints || %s, updated_at = now() where key = %s",
                 (Jsonb([plain(c)]), pc["key"]))
    _store(conn, d, by)
    return change(conn, exception_id)


def remove_constraint(conn, exception_id: int, n: int, by: str) -> dict:
    pc, d = _editable(conn, exception_id, by)
    if not 0 <= n < len(pc["constraints"]):
        raise PlanError(f"plan change {exception_id} has no constraint {n + 1}")
    conn.execute("update plan_change set constraints = constraints - %s, updated_at = now() where key = %s",
                 (n, pc["key"]))
    _store(conn, d, by)
    return change(conn, exception_id)
