"""Re-plan when something breaks (P5.21, #1252): a trip, a late part or a lapsed permit that hits planned work is a
**plan change to review**, routed to the maintenance planner with the plan re-levelled around it.

    disruption(conn, row)        what an open trip, parts or permit exception means for the plan: the plant or the
                                 order it holds, from when until when, and the constraint it puts on the work
    watch(conn)                  the `plan_changes` detector: each disruption that hits planned work raised as
                                 `plan_change` (keyed by the disruption), its proposal worked out; cleared once
                                 nothing is hit any more
    change(conn, id)             a plan change as the planner reads it: the proposal, the constraints typed, and once
                                 approved the SAP changes and the notices
    add_constraint / remove_constraint   a constraint in words (`read`), and the proposal worked out again
    approve(conn, id, by)        the plan moves, SAP's schedule change waits in the write-back queue for the planner
                                 who approved, and a notice is drafted for each crew, field operator and the unit
                                 downstream
    send_notice(conn, id, n, by) a person sends a notice, edited: it goes on each moved job's case
    auto_assign(conn, by, start, end)  a planner asks for crews (P5.23b): each field job with no crew or person booked
                                 gets the crew that does its work and has room, within the planner's rules, as a plan
                                 change to review; `pick` changes or clears one, and approve books them
    revert(conn, id, by)         a planner puts back what an approved plan change changed (P5.23c): each job, booking,
                                 capacity window and schedule start as it was, and the SAP changes not yet sent dropped
    outage_windows(conn, key, by)  a planner asks for windows for an outage (P5.22c): three proposed, each with its
                                 reasons and what it costs the customers it hits; `pick_window` takes another, and
                                 approve moves the outage there, drafts its crews' and customers' notices and records
                                 the choice against the recommendation (P5.20)

Each plan change keeps its history (P5.23c): each thing done to it, when and by whom (Fuzer working it out, a
constraint or a pick, the approval, a notice sent, the revert), and once approved what it changed, before and after.

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
before and after. Approve applies the same writes for good. Deterministic: no model is asked anything.
"""

import re
from collections import defaultdict
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from ..core import clock, detectors, events, gates, work
from ..core.findings import DETERMINISTIC, Finding, at, fact
from ..core.jsonsafe import plain
from ..core.plan import PlanError
from ..core.wording import plural
from . import at_risk, materials, permits, schedule
from .options import DOWN_TYPES
from .plan import ACTIVE, BUNDLE_DAYS, SHUTDOWNS, _note, _when

PLANNER = "maintenance_planner"
PERMIT_HOURS = 24  # allowed for reissuing a lapsed permit before work under it goes on: a planning allowance
HORIZON_DAYS = at_risk.HORIZON_DAYS  # how far past the disruption a typed constraint reaches
CAUSES = (*DOWN_TYPES, materials.TYPE, permits.TYPE)
ASSIGN = "auto_assign"  # a plan change a planner asked for: a crew for each field job with none (its key's prefix)
AGENT = "agent:planner"  # who works a plan change out
ASSIGN_DAYS = 28  # how far ahead auto-assign looks when it is not told
OUTAGE = "outage_window"  # a plan change a planner asked for: a window for an outage (its key's prefix)
WINDOWS = 3  # windows proposed for an outage
NOTICE_DAYS = 7  # a window starts at least this far ahead, so customers are told a week before
SEARCH_DAYS = 56  # how far ahead a window may start
TRIED = 12  # candidate windows levelled for real, cheapest first, to find the WINDOWS that keep within capacity
FORMS = ("keep A0200 on 12 Apr", "no crane work before 10 Apr", "crew MECH max 4")


def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("plan_change") or exception_type(
        "plan_change", "Plan change to review", role=f"role:{PLANNER}", audience=("ioc",), joins=False,
        description="A trip, a late part or a lapsed permit has hit planned work. The plan is levelled again around "
                    "it: each job that moves and the constraint behind it, the work worth pulling forward, and the "
                    "finish, deferred oil and overloads before and after. Add a constraint in words and work it out "
                    "again, then approve: the plan moves, SAP's schedule change waits for approval, and the people "
                    "affected get a notice to send. It clears when nothing on the plan is hit any more. A planner's "
                    "ask is one too: crews for field jobs with none, or three windows for an outage, each with what "
                    "it costs the customers it hits, one approved and its crews' and customers' notices drafted.")


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
    # keyed by the cause's own type and key (parts_not_ready:50000277), as stable as the cause is
    out = {"key": f"{row['type']}:{row['key']}", "type": row["type"], "cause_id": row["id"], "case": row["item_id"],
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
    machine inside its window that needs it running, or the work not yet done on the plant a lapsed permit holds. An
    outage's window holds nothing: its jobs move with the window picked."""
    if d["type"] == OUTAGE:
        return []
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
    proposal runs this in a savepoint it rolls back; approve runs it for good."""
    holds: dict[int, tuple[datetime, str]] = {h["id"]: (d["until"], d["constraint"]) for h in hits(conn, d)}
    keeps = set()
    for c in constraints:
        if c["form"] == "window" and c.get("project"):  # the schedule starts with its window, levelled from there
            conn.execute("update plan_project set start_at = %s, updated_by = %s, updated_at = now() where code = %s",
                         (_when(c["at"]), by, c["project"]))
        elif c["form"] in ("keep", "window"):
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
    for c in constraints:
        if c["form"] == "window" and c.get("project"):
            projects.add(c["project"])
            for i in c["item_ids"]:
                holds.setdefault(i, (_when(c["at"]), c["text"]))
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


def _store(conn, d: dict, by: str, said: tuple[str, str] | None = None) -> dict:
    """Work the proposal out with the constraints typed so far, and raise (or update) the plan change. `said` is what
    the person who asked did, for its history, ahead of Fuzer working it out again."""
    from ..core.exceptions import raise_exception

    if d["type"] == OUTAGE:
        return _store_window(conn, d, said=said)
    pc = conn.execute("select * from plan_change where key = %s", (d["key"],)).fetchone()
    constraints = list(pc["constraints"]) if pc and pc["state"] == "open" else []
    p = work_out(conn, d, constraints, by)
    n = len(p["moves"])
    title = f"{d['say'][:1].upper()}{d['say'][1:]}: {plural(n, 'planned job')} to move"
    x = raise_exception(conn, TYPE, d["key"], title, by, source=d["source"], refs=d["subjects"], priority=0.6,
                        finding=_finding(d, p))
    conn.execute(
        """
        insert into plan_change (key, exception_id, constraints, proposal, state) values (%s, %s, %s, %s, 'open')
        on conflict (key) do update set exception_id = excluded.exception_id, proposal = excluded.proposal,
            constraints = case when plan_change.state = 'open' then plan_change.constraints else '[]' end,
            state = 'open', approved_by = case when plan_change.state = 'open' then plan_change.approved_by end,
            changed = case when plan_change.state = 'open' then plan_change.changed else '[]' end,
            reverted_by = null, reverted_at = null, updated_at = now()
        """, (d["key"], x["id"], Jsonb(plain(constraints)), Jsonb(plain(p))))
    if said:
        _did(conn, d["key"], *said)
    _did(conn, d["key"], by, f"Worked out: {plural(n, 'planned job')} to move"
         + (f", {', '.join(p['schedules'])} levelled again" if p["schedules"] else "")
         + (f", with {plural(len(constraints), 'constraint')}" if constraints else ""), again=False)
    return x


def _planned(conn, pack) -> bool:
    return conn.execute("select 1 from plan_item limit 1").fetchone() is not None


@detectors.register("plan_changes", title="Planned work a trip, a late part or a lapsed permit has hit",
                    kind=detectors.RULE, cadence="hourly", raises=(TYPE,),
                    needs={"planned work": _planned},
                    eval="test:tests/ops/test_replan.py::test_a_trip_on_planned_work_is_a_plan_change_with_each_move_and_why",
                    found=lambda out: len(out["raised"]))
def watch(conn, by: str = AGENT) -> dict:
    """Each open trip, late part or lapsed permit that holds planned work, raised as `plan_change` for the maintenance
    planner (keyed by the disruption) with its proposal worked out; open ones nothing is hit by any more (the cause
    cleared, or the work moved) are cleared."""
    from ..core.exceptions import ExceptionError, clear_exception

    held = [r["key"] for r in conn.execute("select key from exception_record where type = %s and state = 'open' "
                                           "and key not like %s and key not like %s",
                                           (TYPE, f"{ASSIGN}:%", f"{OUTAGE}:%"))]
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
    pc = conn.execute("select c.*, e.state as exception_state, e.item_id as case_id from plan_change c join "
                      "exception_record e on e.id = c.exception_id where c.exception_id = %s", (exception_id,)).fetchone()
    if pc is None:
        raise PlanError(f"no plan change {exception_id}")
    kind, key = pc["key"].split(":", 1)
    if kind == ASSIGN:
        p = pc["proposal"] or {}
        return pc, _asked(pc["key"], _when(p["start"]), _when(p["until"]))
    if kind == OUTAGE:
        return pc, _outage((pc["proposal"] or {})["outage"])
    cause = conn.execute("select * from exception_record where type = %s and key = %s "
                         "order by state = 'open' desc, id desc limit 1", (kind, key)).fetchone()
    d = disruption(conn, cause) if cause else None
    if d is None:
        raise PlanError(f"plan change {exception_id}: what caused it is no longer on record")
    return pc, d


def change(conn, exception_id: int) -> dict:
    """A plan change as the planner reads it: what happened and the constraint it puts on the work, the proposal
    (moves, pull-forwards, KPIs before and after), the constraints typed, the forms Fuzer reads and, once approved,
    who approved it, the SAP changes queued and the notices."""
    pc, d = _open(conn, exception_id)
    wbs = conn.execute("select id, record, state, item_id, approver_role from writeback where id = any(%s) order by id",
                       (list(pc["writebacks"]),)).fetchall()
    return {"exception_id": exception_id, "key": pc["key"], "state": pc["state"], "say": d["say"],
            "constraint": d["constraint"], "start": d["start"], "until": d["until"], "cause_case": d["case"],
            "constraints": [{"text": c["text"], "form": c["form"]} for c in pc["constraints"]], "forms": list(FORMS),
            "proposal": pc["proposal"], "approved_by": pc["approved_by"], "approved_at": pc["approved_at"],
            "writebacks": wbs, "notices": pc["notices"], "case": pc["case_id"], "picks": pc["picks"],
            "history": pc["history"], "changed": pc["changed"], "reverted_by": pc["reverted_by"],
            "reverted_at": pc["reverted_at"],
            "open": pc["state"] == "open" and pc["exception_state"] == "open"}


def _editable(conn, exception_id: int, by: str) -> tuple[dict, dict]:
    if gates.machine(by):
        raise PlanError("a plan change is worked by a named person")
    pc, d = _open(conn, exception_id)
    if pc["state"] != "open" or pc["exception_state"] != "open":
        raise PlanError(f"plan change {exception_id} is {pc['state'] if pc['state'] != 'open' else 'closed'}")
    return pc, d


def add_constraint(conn, exception_id: int, text: str, by: str) -> dict:
    """A constraint typed in words: read (`read`), kept on the plan change, and the proposal worked out again."""
    pc, d = _editable(conn, exception_id, by)
    if d["type"] == ASSIGN:
        raise PlanError("this plan change books crews: pick a crew on a job instead")
    c = read(conn, text, d["start"])
    conn.execute("update plan_change set constraints = constraints || %s, updated_at = now() where key = %s",
                 (Jsonb([plain(c)]), pc["key"]))
    _store(conn, d, AGENT, said=(by, f"Added the constraint “{c['text']}”"))
    return change(conn, exception_id)


def remove_constraint(conn, exception_id: int, n: int, by: str) -> dict:
    pc, d = _editable(conn, exception_id, by)
    if not 0 <= n < len(pc["constraints"]):
        raise PlanError(f"plan change {exception_id} has no constraint {n + 1}")
    conn.execute("update plan_change set constraints = constraints - %s, updated_at = now() where key = %s",
                 (n, pc["key"]))
    _store(conn, d, AGENT, said=(by, f"Removed the constraint “{pc['constraints'][n]['text']}”"))
    return change(conn, exception_id)


def _notices(conn, d: dict, moves: list[dict]) -> list[dict]:
    """A notice for each crew whose work moved, each person or queue booked on a moved job (a field operator's says
    why it moved on their job too) and, for a trip, the units downstream that lose their feed. Drafts: a person edits
    and sends each."""
    from ..core.wording import queue as queue_name
    from .readiness import field_operator

    def line(ms):
        return "; ".join(f"{m['code'] or m['title']} from {_say(_when(m['before']['start']))} to "
                         f"{_say(_when(m['after']['start']))}" for m in ms)

    head = f"{d['say'][:1].upper()}{d['say'][1:]}."
    out = []
    by_crew: dict[tuple, list[int]] = defaultdict(list)
    for r in conn.execute("select x.item_id, r.code, r.name from plan_demand_all x join plan_resource r on r.code = x.resource "
                          "where x.item_id = any(%s) and r.kind = 'crew' order by r.code", ([m["item_id"] for m in moves],)):
        by_crew[(r["code"], r["name"])].append(r["item_id"])
    for (code, name), ids in by_crew.items():
        ms = [m for m in moves if m["item_id"] in ids]
        out.append({"audience": "crew", "to": code, "name": name, "jobs": ids,
                    "text": f"{head} The plan moves your work: {line(ms)}."})
    people: dict[str, list[dict]] = defaultdict(list)
    for m in moves:
        if m["crew"]:
            people[m["crew"]].append(m)
    for who, ms in people.items():
        field = field_operator(conn, who)
        out.append({"audience": "field" if field else "crew", "to": who,
                    "name": queue_name(who.removeprefix("role:")) if who.startswith("role:") else who,
                    "jobs": [m["item_id"] for m in ms],
                    "text": f"{head} Your job{'s' if len(ms) > 1 else ''} moved: {line(ms)}."})
    if d["type"] in DOWN_TYPES:
        starved = {}
        for s in d["subjects"]:
            for u in at_risk.trip(conn, s)["starved"]:
                starved.setdefault(u["id"], u["label"])
        if starved:
            out.append({"audience": "downstream", "to": "role:ops_lead", "name": ", ".join(sorted(starved.values())),
                        "jobs": [m["item_id"] for m in moves],
                        "text": f"{head} Downstream of it, {', '.join(sorted(starved.values()))} lose their feed until "
                                f"{_say(d['until'])}. {plural(len(moves), 'planned job')} moved."})
    return [{**x, "n": i, "sent_by": None, "sent_at": None} for i, x in enumerate(out)]


def approve(conn, exception_id: int, by: str) -> dict:
    """A maintenance planner approves the plan change: the plan moves as proposed (each moved job keeps why), each
    moved SAP order's new dates are drafted through the write-back queue for this planner to approve, the notices are
    drafted, and the plan change's case closes."""
    from ..core.exceptions import clear_exception
    from . import writeback

    pc, d = _editable(conn, exception_id, by)
    if PLANNER not in work.roles_of(conn, by):
        raise PlanError(f"{by} isn't a maintenance planner: a planner approves a plan change")
    if d["type"] == ASSIGN:
        return _approve_assign(conn, pc, d, by)
    cons = _constraints(pc, d)
    p = work_out(conn, d, cons, by)
    if d["type"] == OUTAGE:
        p = {**pc["proposal"], **p}
    was = _state(conn)
    _apply(conn, d, cons, by)
    for m in p["moves"]:
        row = conn.execute("update plan_item set moved_because = %s, released_at = null, released_by = null, "
                           "release_reason = null where id = %s returning *", (d["say"], m["item_id"])).fetchone()
        if m["case"]:
            _note(conn, row, by, f"Moved from {_say(m['before']['start'])} to {_say(m['after']['start'])} because "
                                 f"{d['say']}" + (" It was released: release it again for the new dates."
                                                  if m.get("released") else ""))
    queued = []
    for m in p["moves"]:
        if not m["order"]:
            continue
        a = m["after"]
        payload = {"action": "reschedule", "order": m["order"], "start": a["start"].isoformat(),
                   "finish": a["end"].isoformat(), "was_start": m["before"]["start"].isoformat(),
                   "was_finish": m["before"]["end"].isoformat(), "because": d["say"]}
        try:
            wb = writeback.propose(conn, "sap", "order", payload, by, record=m["order"], action="reschedule",
                                   key=f"sap:order:{m['order']}:reschedule:{payload['start']}",
                                   title=f"SAP order {m['order']}: move to {_say(a['start'], hour=False)}",
                                   source_ref=f"plan_change:{exception_id}", approver_role=PLANNER, assignee=by)
        except writeback.WritebackError:
            continue  # SAP's pack takes no schedule changes: the plan moves, SAP is told by its planner
        queued.append(wb["id"])
    notices = _notices(conn, d, p["moves"])
    if d["type"] == OUTAGE:
        notices = _customer_notices(d, p, notices)
        _chose(conn, pc, p, by)
    conn.execute("update plan_change set state = 'approved', approved_by = %s, approved_at = now(), proposal = %s, "
                 "writebacks = %s, notices = %s, changed = %s, updated_at = now() where key = %s",
                 (by, Jsonb(plain(p)), queued, Jsonb(plain(notices)), Jsonb(_changes(conn, was)), pc["key"]))
    what = "Approved"
    if d["type"] == OUTAGE:
        what = f"Approved window {p['picked'] + 1}" + (" as recommended" if p["picked"] == p["recommended"]
                                                        else f" instead of the recommended window {p['recommended'] + 1}")
    _did(conn, pc["key"], by, f"{what}: {plural(len(p['moves']), 'job')} moved, {plural(len(queued), 'SAP change')} "
                              f"queued, {plural(len(notices), 'notice')} drafted")
    clear_exception(conn, TYPE, pc["key"], by, reason=f"approved by {by}: {plural(len(p['moves']), 'job')} moved")
    events.publish(conn, "plan.changed", f"plan_change:{pc['key']}",
                   {"change": "plan change approved", "by": by, "moved": [m["item_id"] for m in p["moves"]],
                    "because": d["say"]})
    return change(conn, exception_id)


def send_notice(conn, exception_id: int, n: int, by: str, text: str | None = None) -> dict:
    """A person sends a drafted notice, as they edited it: it goes on the case of each moved job it is about, so
    whoever holds the job reads it there, and the plan change records who sent it."""
    if gates.machine(by):
        raise PlanError("a notice is sent by a named person")
    pc, _ = _open(conn, exception_id)
    if pc["state"] == "reverted":
        raise PlanError(f"plan change {exception_id} was reverted: nothing moved, so there is nothing to tell")
    notices = list(pc["notices"])
    if not 0 <= n < len(notices):
        raise PlanError(f"plan change {exception_id} has no notice {n + 1}")
    x = notices[n]
    if x.get("sent_at"):
        raise PlanError(f"that notice was sent by {x['sent_by']}")
    x = {**x, "text": (text or "").strip() or x["text"], "sent_by": by, "sent_at": clock.aware(clock.now()).isoformat()}
    for r in conn.execute("select distinct item_id from plan_item where id = any(%s) and item_id is not null",
                          (x["jobs"],)):
        work.comment(conn, r["item_id"], by, x["text"])
    notices[n] = x
    conn.execute("update plan_change set notices = %s, updated_at = now() where key = %s", (Jsonb(notices), pc["key"]))
    _did(conn, pc["key"], by, f"Sent the notice to {x['name']}")
    events.publish(conn, "plan.notice", f"plan_change:{pc['key']}", {"to": x["to"], "by": by, "text": x["text"]})
    return change(conn, exception_id)


# ---- auto-assign (P5.23b): a crew for each field job with none, as a plan change to review ---------------------------

def _asked(key: str, start: datetime, until: datetime) -> dict:
    """An auto-assign plan change's window, in the shape of a disruption: what the case says it is about."""
    return {"key": key, "type": ASSIGN, "cause_id": None, "case": None, "subjects": [], "source": "plan", "order": None,
            "start": start, "until": until, "say": "Field jobs with no crew",
            "constraint": f"a crew proposed for each, {_say(start, hour=False)} to {_say(until, hour=False)}"}


def unassigned(conn, start: datetime, until: datetime) -> list[dict]:
    """Field jobs still to do in the window that no person and no crew is booked on: not a schedule's activity (its
    schedule books its crews), not an SAP order's summary, not a field operator's look."""
    return conn.execute(
        _JOBS + " and p.place = 'field' and p.crew is null and p.project is null and p.kind <> 'exception' "
                "and p.start_at < %(until)s and p.end_at > %(start)s and not exists (select 1 from plan_demand_all x "
                "join plan_resource r on r.code = x.resource where x.item_id = p.id and r.kind = 'crew') "
                "order by p.start_at, p.id",
        {"active": list(ACTIVE), "start": start, "until": until}).fetchall()


def _crews_for(conn, job: dict, crews: list[dict]) -> list[tuple[str, str]]:
    """The crews that do a job's work, best first, each with why: the crew of its SAP order's work center, a crew
    named for its kind of work (inspection), then the crews booked on the plan's other jobs of its kind."""
    out: dict[str, str] = {}
    if job["source"] == "sap" and job["source_key"]:
        wc = conn.execute("select min(l.label) as wc from entity_key k join edge x on x.src = k.fuzer_id and "
                          "x.rel = 'done_by' join entity_label l on l.fuzer_id = x.dst where k.kind = 'work_order' "
                          "and k.key = %s", (job["source_key"],)).fetchone()["wc"]
        for c in crews:
            if wc and c["work_center"] == wc:
                out.setdefault(c["code"], f"does SAP work center {wc}'s orders")
    kind = job["kind"].replace("_", " ")
    for c in crews:
        if kind in c["name"].lower():
            out.setdefault(c["code"], f"does {kind} work")
    for r in conn.execute(
            "select x.resource, count(distinct p.id) as n from plan_demand_all x join plan_item p on p.id = x.item_id "
            "join plan_resource r on r.code = x.resource and r.kind = 'crew' where p.kind = %s and p.status = any(%s) "
            "and p.id <> %s group by 1 order by 2 desc, 1", (job["kind"], list(ACTIVE), job["id"])):
        out.setdefault(r["resource"], f"is booked on {plural(r['n'], f'other {kind} job')}")
    return list(out.items())


def _rule_breaks(conn) -> set[tuple]:
    from . import plan_rules

    return {(i, b["rule"], b["say"]) for i, bs in plan_rules.breaks(conn).items() for b in bs}


def _breaks(conn, job: dict, crew: dict, seen: set[tuple]) -> list[str]:
    """What booking `crew` on `job` breaks, as the plan stands in this transaction: the crew over its capacity, or a
    planner's rule broken that was not broken before."""
    from . import plan_rules
    from .plan import _overloads

    out = [f"{crew['name']} would be {o['peak']:g} of {o['capacity']:g} from {_say(o['from'])}"
           for o in _overloads(conn, crew["code"], job)][:1]
    new = _rule_breaks(conn) - seen
    if new:
        said = {r["id"]: r["rule"]["say"] for r in conn.execute("select id, rule from plan_rule")}
        titles = {r["id"]: r["title"] for r in plan_rules._jobs(conn)}
        out += [f"{said.get(rule, 'a rule')}: {titles.get(i, 'a job')} {how}" for i, rule, how in sorted(new)]
    return out


def work_assign(conn, start: datetime, until: datetime, picks: dict) -> dict:
    """The proposal: each unassigned field job in the window (the ones a work center names first) with the first crew
    that does its work and can take it without going over capacity or breaking a rule, one person for the job's dates,
    booked in turn in a savepoint and rolled back. A planner's pick (`picks`, by job id: a crew's code, or "" for none)
    is taken as it is, with anything it breaks."""
    crews = {c["code"]: c for c in conn.execute("select * from plan_resource where kind = 'crew' order by code")}
    jobs = unassigned(conn, start, until)
    options = {j["id"]: _crews_for(conn, j, list(crews.values())) for j in jobs}
    lines = []
    conn.execute("savepoint auto_assign")
    try:
        seen = _rule_breaks(conn)
        for j in sorted(jobs, key=lambda j: (not options[j["id"]] or "work center" not in options[j["id"]][0][1],
                                             j["start_at"], j["id"])):
            line = {"item_id": j["id"], "title": j["title"], "kind": j["kind"], "start": j["start_at"], "end": j["end_at"],
                    "case": j["item_id"], "order": j["source_key"] if j["source"] == "sap" else None, "crew": None,
                    "name": None, "people": 1, "because": None, "breaks": [], "picked": str(j["id"]) in picks}
            pick = picks.get(str(j["id"]))
            tries = ([(pick, "picked by the planner")] if pick in crews else []) if line["picked"] else options[j["id"]]
            tried = []
            for code, why in tries:
                conn.execute("savepoint crew")
                conn.execute("insert into plan_demand (item_id, resource, quantity) values (%s, %s, 1)", (j["id"], code))
                broken = _breaks(conn, j, crews[code], seen)
                if broken and not line["picked"]:
                    conn.execute("rollback to savepoint crew")
                    conn.execute("release savepoint crew")
                    tried.append(broken[0])
                    continue
                conn.execute("release savepoint crew")
                line.update(crew=code, name=crews[code]["name"], because=why, breaks=broken)
                seen = _rule_breaks(conn)
                break
            if line["crew"] is None:
                line["because"] = ("left with no crew by the planner" if line["picked"]
                                   else "no crew has room: " + "; ".join(tried) if tried
                                   else "no crew's work center or work matches: pick one")
            lines.append(line)
    finally:
        conn.execute("rollback to savepoint auto_assign")
        conn.execute("release savepoint auto_assign")
    lines.sort(key=lambda x: (x["start"], x["item_id"]))
    return {"start": start, "until": until, "assign": lines, "moves": [], "pull_forward": [], "schedules": [],
            "basis": "the crew of each job's SAP work center, a crew named for its kind of work, or the crews on the "
                     "plan's other jobs of its kind, one person for the job's dates, checked against each crew's "
                     "capacity and the planner's rules"}


def _store_assign(conn, d: dict, picks: dict, said: tuple[str, str] | None = None) -> dict:
    from ..core.exceptions import raise_exception

    p = work_assign(conn, d["start"], d["until"], picks)
    n = sum(1 for a in p["assign"] if a["crew"])
    title = f"Crews for {n} of {plural(len(p['assign']), 'field job')}"
    found = Finding("replan.auto_assign", DETERMINISTIC, method=p["basis"], start=d["start"], end=d["until"],
                    data={"jobs": len(p["assign"]), "assigned": n},
                    items=(fact("Field jobs with no crew", len(p["assign"])), fact("Crews proposed", n)))
    x = raise_exception(conn, TYPE, d["key"], title, AGENT, source="plan", refs=[], priority=0.5, finding=found)
    conn.execute(
        """
        insert into plan_change (key, exception_id, proposal, picks, state) values (%s, %s, %s, %s, 'open')
        on conflict (key) do update set exception_id = excluded.exception_id, proposal = excluded.proposal,
            picks = excluded.picks, updated_at = now()
        """, (d["key"], x["id"], Jsonb(plain(p)), Jsonb(picks)))
    if said:
        _did(conn, d["key"], *said)
    _did(conn, d["key"], AGENT, f"Proposed crews for {n} of {plural(len(p['assign']), 'field job')}", again=False)
    return x


def auto_assign(conn, by: str, start=None, end=None) -> dict:
    """A planner asks for crews on the field jobs with none between `start` and `end` (from now, ASSIGN_DAYS when not
    told): the proposal is a plan change for the maintenance planner, worked out by Fuzer. One open at a time: asking
    again works the open one out again for the new dates, keeping the planner's picks."""
    if gates.machine(by):
        raise PlanError("auto-assign is asked for by a named person")
    start = _when(start) if start else clock.aware(clock.now())
    until = _when(end) if end else start + timedelta(days=ASSIGN_DAYS)
    if until <= start:
        raise PlanError("auto-assign needs an end after its start")
    pc = conn.execute("select c.key, c.picks from plan_change c join exception_record e on e.id = c.exception_id "
                      "where c.key like %s and c.state = 'open' and e.state = 'open' order by c.updated_at desc limit 1",
                      (f"{ASSIGN}:%",)).fetchone()
    key = pc["key"] if pc else f"{ASSIGN}:{clock.now():%Y%m%d%H%M%S}"
    x = _store_assign(conn, _asked(key, start, until), dict(pc["picks"]) if pc else {},
                      said=(by, f"Asked for crews on field jobs, {_say(start, hour=False)} to {_say(until, hour=False)}"))
    return change(conn, x["id"])


def pick(conn, exception_id: int, item_id: int, crew: str | None, by: str) -> dict:
    """The planner changes one job's crew on an auto-assign plan change: a crew's code, "" for none, or None to let
    Fuzer choose again. The proposal is worked out again around it, and says what the pick breaks."""
    pc, d = _editable(conn, exception_id, by)
    if d["type"] != ASSIGN:
        raise PlanError("a crew is picked on an auto-assign plan change")
    if not any(a["item_id"] == item_id for a in (pc["proposal"] or {}).get("assign", [])):
        raise PlanError(f"plan change {exception_id} has no job {item_id}")
    if crew and not conn.execute("select 1 from plan_resource where code = %s and kind = 'crew'", (crew,)).fetchone():
        raise PlanError(f"no crew is called {crew}")
    picks = {k: v for k, v in dict(pc["picks"]).items() if k != str(item_id)}
    if crew is not None:
        picks[str(item_id)] = crew
    [job] = [a for a in pc["proposal"]["assign"] if a["item_id"] == item_id]
    name = conn.execute("select name from plan_resource where code = %s", (crew,)).fetchone() if crew else None
    _store_assign(conn, d, picks, said=(by, f"Picked {name['name'] if name else 'no crew'} for {job['title']}"
                                       if crew is not None else f"Left the crew for {job['title']} to Fuzer"))
    return change(conn, exception_id)


def _approve_assign(conn, pc: dict, d: dict, by: str) -> dict:
    """Book each job on its crew as proposed, refused while a line breaks a rule or a crew's capacity."""
    from ..core.exceptions import clear_exception
    from .plan import book

    p = work_assign(conn, d["start"], d["until"], dict(pc["picks"]))
    for a in p["assign"]:
        if a["breaks"]:
            raise PlanError(f"{a['title']} on {a['name']} breaks {a['breaks'][0]}: pick another crew or none")
    booked = [a for a in p["assign"] if a["crew"]]
    was = _state(conn)
    for a in booked:
        row = book(conn, a["item_id"], by, a["crew"], a["people"])
        _note(conn, row, by, f"Booked on {a['name']}: {a['because']}")
    conn.execute("update plan_change set state = 'approved', approved_by = %s, approved_at = now(), proposal = %s, "
                 "changed = %s, updated_at = now() where key = %s", (by, Jsonb(plain(p)), Jsonb(_changes(conn, was)),
                                                                    pc["key"]))
    _did(conn, pc["key"], by, f"Approved: {plural(len(booked), 'job')} booked")
    clear_exception(conn, TYPE, pc["key"], by, reason=f"approved by {by}: {plural(len(booked), 'job')} booked")
    events.publish(conn, "plan.changed", f"plan_change:{pc['key']}",
                   {"change": "crews booked", "by": by, "booked": [a["item_id"] for a in booked]})
    return change(conn, pc["exception_id"])


# ---- history and Revert (P5.23c) -----------------------------------------------------------------------------------

# what Revert puts back on a job: its dates, the start no earlier than, its status and who it is booked on, and what
# approving wrote on it (who moved it and why, its release cleared)
JOB_FIELDS = ("start_at", "end_at", "not_before", "status", "crew", "moved_by", "moved_because", "released_at",
              "released_by", "release_reason")
_TIMES = ("start_at", "end_at", "not_before", "released_at")


def _did(conn, key: str, by: str, did: str, again: bool = True) -> None:
    """One thing done to a plan change, for its history: when, by whom, what. `again=False` leaves out a repeat of the
    last thing that actor did (the detector working out the same proposal every hour)."""
    if not again:
        last = conn.execute("select h from plan_change, jsonb_array_elements(history) h where key = %s "
                            "and h ->> 'by' = %s order by (h ->> 'at') desc limit 1", (key, by)).fetchone()
        if last and last["h"]["did"] == did:
            return
    conn.execute("update plan_change set history = history || %s where key = %s",
                 (Jsonb([{"at": clock.aware(clock.now()).isoformat(), "by": by, "did": did}]), key))


def _state(conn) -> dict:
    """Everything approving a plan change can write: every job's fields Revert puts back, every booking, every
    capacity window."""
    return {"jobs": {r["id"]: r for r in conn.execute(f"select id, title, code, item_id, {', '.join(JOB_FIELDS)} "
                                                      "from plan_item")},
            "demand": {(r["item_id"], r["resource"]): r["quantity"] for r in conn.execute("select * from plan_demand")},
            "windows": {r["id"]: r for r in conn.execute("select id, resource, start_at, end_at, capacity, reason "
                                                         "from plan_resource_capacity")},
            "projects": {r["code"]: r for r in conn.execute("select code, title, start_at from plan_project")}}


def _changes(conn, was: dict) -> list[dict]:
    """What changed since `was`, as the plan change keeps it: each job's fields before and after, each booking and
    each capacity window added."""
    now = _state(conn)
    names = {r["code"]: r["name"] for r in conn.execute("select code, name from plan_resource")}
    out = []
    for i, r in now["jobs"].items():
        b = was["jobs"].get(i)
        fields = {f: [b[f], r[f]] for f in JOB_FIELDS if b is not None and b[f] != r[f]}
        if fields:
            out.append({"what": "job", "id": i, "title": r["title"], "code": r["code"], "case": r["item_id"],
                        "fields": fields})
    for item, res in sorted(set(was["demand"]) | set(now["demand"])):
        if was["demand"].get((item, res)) != now["demand"].get((item, res)):
            out.append({"what": "booking", "id": item, "resource": res, "name": names.get(res, res),
                        "title": now["jobs"].get(item, {}).get("title"), "before": was["demand"].get((item, res)),
                        "after": now["demand"].get((item, res))})
    for wid in sorted(set(now["windows"]) - set(was["windows"])):
        w = now["windows"][wid]
        out.append({"what": "capacity", "id": wid, "resource": w["resource"], "name": names.get(w["resource"]),
                    "title": w["reason"], "start": w["start_at"], "end": w["end_at"], "before": None,
                    "after": w["capacity"]})
    for code, p in now["projects"].items():
        b = was["projects"].get(code)
        if b is not None and b["start_at"] != p["start_at"]:
            out.append({"what": "schedule", "id": code, "title": p["title"], "before": b["start_at"],
                        "after": p["start_at"]})
    return plain(out)


def revert(conn, exception_id: int, by: str) -> dict:
    """A maintenance planner reverts an approved plan change: each job, booking and capacity window it changed is put
    back as it was, each SAP change it queued and not yet sent is dropped, and the moved jobs' cases and the plan
    change's own case say so. Refused, naming them, when any of it has changed again since: those are put back by
    hand."""
    from . import writeback

    if gates.machine(by):
        raise PlanError("a plan change is reverted by a named person")
    pc, d = _open(conn, exception_id)
    if pc["state"] != "approved":
        raise PlanError(f"plan change {exception_id} is {pc['state']}: only an approved one is reverted")
    if PLANNER not in work.roles_of(conn, by):
        raise PlanError(f"{by} isn't a maintenance planner: a planner reverts a plan change")
    now = _state(conn)
    stale = []
    for x in pc["changed"]:
        if x["what"] == "job":
            r = now["jobs"].get(x["id"])
            if r is None or any(plain(r[f]) != after for f, (_, after) in x["fields"].items()):
                stale.append(x["title"])
        elif x["what"] == "booking" and now["demand"].get((x["id"], x["resource"])) != x["after"]:
            stale.append(f"{x['title']} on {x['name']}")
        elif (x["what"] == "capacity" and x["id"] not in now["windows"]) or (
                x["what"] == "schedule" and plain((now["projects"].get(x["id"]) or {}).get("start_at")) != x["after"]):
            stale.append(x["title"])
    if stale:
        raise PlanError(f"changed again since it was approved: {', '.join(sorted(set(stale))[:5])}. Put those back by hand")
    for x in pc["changed"]:
        if x["what"] == "job":
            sets = {f: _when(b) if b and f in _TIMES else b for f, (b, _) in x["fields"].items()}
            row = conn.execute("update plan_item set " + ", ".join(f"{f} = %({f})s" for f in sets) +
                               ", updated_by = %(by)s, updated_at = now() where id = %(id)s returning *",
                               {**sets, "by": by, "id": x["id"]}).fetchone()
            if "start_at" in sets and row["item_id"]:
                _note(conn, row, by, f"Moved back to {_say(row['start_at'])}: the plan change was reverted")
        elif x["what"] == "booking":
            if x["before"] is None:
                conn.execute("delete from plan_demand where item_id = %s and resource = %s", (x["id"], x["resource"]))
            else:
                conn.execute("insert into plan_demand (item_id, resource, quantity) values (%s, %s, %s) on conflict "
                             "(item_id, resource) do update set quantity = excluded.quantity",
                             (x["id"], x["resource"], x["before"]))
        elif x["what"] == "capacity":
            conn.execute("delete from plan_resource_capacity where id = %s", (x["id"],))
        elif x["what"] == "schedule":
            conn.execute("update plan_project set start_at = %s, updated_by = %s, updated_at = now() where code = %s",
                         (_when(x["before"]), by, x["id"]))
    sent = []
    for wid in pc["writebacks"]:
        try:
            writeback.reject(conn, wid, by, reason="the plan change was reverted")
        except writeback.WritebackError:
            sent.append(writeback.get(conn, wid)["record"])
    did = f"Reverted: {plural(len(pc['changed']), 'change')} put back" + (
        f"; SAP order {', '.join(str(r) for r in sent)} was already approved to go: change it there" if sent else "")
    conn.execute("update plan_change set state = 'reverted', reverted_by = %s, reverted_at = now(), updated_at = now() "
                 "where key = %s", (by, pc["key"]))
    _did(conn, pc["key"], by, did)
    if pc["case_id"]:
        work.comment(conn, pc["case_id"], by, did)
    events.publish(conn, "plan.changed", f"plan_change:{pc['key']}",
                   {"change": "plan change reverted", "by": by, "put_back": len(pc["changed"]), "because": d["say"]})
    return change(conn, exception_id)


# ---- windows for an outage (P5.22c): three proposed, one picked, approved as a plan change ---------------------------

def _outage(o: dict) -> dict:
    """An outage-window plan change's outage, in the shape of a disruption: what the case says it is about."""
    a, b = _when(o["start"]), _when(o["end"])
    units = [u["id"] if isinstance(u, dict) else u for u in o["units"]]
    return {"key": f"{OUTAGE}:{o['key']}", "type": OUTAGE, "cause_id": None, "case": None, "subjects": units,
            "source": "plan", "order": None, "start": a, "until": b, "say": o["title"],
            "constraint": f"a window to choose, planned {_say(a)} to {_say(b)}",
            "outage": {"key": o["key"], "title": o["title"], "project": o["project"], "item_ids": list(o["item_ids"]),
                       "start": a, "end": b, "units": units}}


def _window(o: dict, at: datetime, n: int) -> dict:
    """The constraint a window puts on the plan: the schedule starts then (or the job does), levelled from there."""
    return {"text": f"Window {n + 1}: starts {_say(at)}", "form": "window", "project": o["project"],
            "item_ids": list(o["item_ids"]), "at": at}


def _constraints(pc: dict, d: dict) -> list[dict]:
    """The constraints a plan change is worked out with: those typed, and an outage's window picked."""
    out = list(pc["constraints"])
    if d["type"] == OUTAGE:
        p = pc["proposal"] or {}
        if p.get("windows"):
            out.append(p["windows"][p["picked"]]["constraint"])
    return out


def _outage_overloads(conn, ids: list[int]) -> list[dict]:
    start = clock.aware(clock.now())
    return [o for o in schedule.constraints(conn, start, HORIZON_DAYS, moves=False)["overloads"]
            if any(j.get("item_id") in ids for j in o["jobs"])]


def _reasons(w: dict, planned: datetime, over: list[dict]) -> list[str]:
    days = round((w["start"] - planned).total_seconds() / 86400)
    out = ["as planned" if not days else f"{plural(abs(days), 'day')} {'later' if days > 0 else 'earlier'} than planned"]
    if w["alongside"]:
        out.append(f"shares its hours with {', '.join(w['alongside'])}: a contract both hit loses those hours once")
    if w["not_running"]:
        out.append(f"{', '.join(w['not_running'])} not running then")
    twice = [c for c in w["customers"] if c["flagged"]]
    out.append("; ".join(f"{c['customer']} still hit {c['times']} times" for c in twice) if twice
               else "no customer it hits is hit at another time")
    out.append("; ".join(f"{o['name']} over capacity from {_say(_when(o['from']))}" for o in over) if over
               else "crews within capacity, no rule broken")
    out.append(f"done {_say(w['end'])}")
    return out


def work_windows(conn, d: dict, typed: list[dict], picks: dict) -> dict:
    """The proposal for an outage: its jobs moved as one, a day at a time, to every start from NOTICE_DAYS to
    SEARCH_DAYS ahead, ranked by what each costs the customers it hits (outages.impact: hours it shares with another
    outage on a contract are lost once), nearest the plan first among equals. The cheapest TRIED are levelled for real
    with the typed constraints, in a savepoint rolled back; the first WINDOWS that do not overlap, put no crew of its over
    capacity and break no rule are proposed, the cheapest recommended. The plan is then worked out for the window
    picked (the recommended one until the planner picks another)."""
    from ..core.wording import money
    from . import outages

    o = d["outage"]
    planned = _when(o["start"])
    span = _when(o["end"]) - planned
    now = clock.aware(clock.now())
    first, last = now + timedelta(days=NOTICE_DAYS), now + timedelta(days=SEARCH_DAYS)
    starts = [t for t in (planned + timedelta(days=k) for k in range(-SEARCH_DAYS, SEARCH_DAYS + 1)) if first <= t <= last]
    rigid = outages.impact(conn, o["key"], [(t, t + span) for t in starts])
    order = sorted(rigid, key=lambda w: (w["usd"], abs((w["start"] - planned).total_seconds()), w["start"]))
    before = len(_outage_overloads(conn, o["item_ids"]))
    seen = _rule_breaks(conn)
    windows, tried, held = [], 0, 0
    for w in order:
        if len(windows) >= WINDOWS or tried >= TRIED:
            break
        if any(w["start"] < x["end"] and w["end"] > x["start"] for x in windows):
            continue
        tried += 1
        c = _window(o, w["start"], len(windows))
        conn.execute("savepoint outage_window")
        try:
            _apply(conn, d, [*typed, c], AGENT)
            r = conn.execute("select min(start_at) as a, max(end_at) as b from plan_item where id = any(%s)",
                             (o["item_ids"],)).fetchone()
            over = _outage_overloads(conn, o["item_ids"])
            broke = _rule_breaks(conn) - seen
        finally:
            conn.execute("rollback to savepoint outage_window")
            conn.execute("release savepoint outage_window")
        if len(over) > before or broke or any(r["a"] < x["end"] and r["b"] > x["start"] for x in windows):
            held += 1
            continue
        [real] = outages.impact(conn, o["key"], [(r["a"], r["b"])])
        windows.append({**real, "n": len(windows), "constraint": c, "reasons": _reasons(real, planned, over)})
    windows.sort(key=lambda w: (w["usd"], abs((w["start"] - planned).total_seconds()), w["start"]))
    for n, w in enumerate(windows):
        w["n"], w["constraint"] = n, _window(o, w["constraint"]["at"], n)
    picked = picks.get("window", 0) if windows and picks.get("window", 0) < len(windows) else 0
    p = (work_out(conn, d, [*typed, windows[picked]["constraint"]], AGENT) if windows
         else {"moves": [], "pull_forward": [], "schedules": [], "kpis": None})
    found = outages.find(conn, o["key"])
    hist = outages.past(conn, found)
    return {**p, "outage": o, "windows": windows, "recommended": 0, "picked": picked, "reason": picks.get("reason"),
            "recommend": (f"Costs the customers least ({money(windows[0]['usd'])})"
                          + (f": it shares its hours with {', '.join(windows[0]['alongside'])}" if windows[0]["alongside"] else "")
                          if windows else "No window keeps every crew within capacity and every rule: add a constraint"),
            "past": hist, "maintenance": outages.maintenance(conn, found),
            "basis": f"its jobs moved as one to each start from {NOTICE_DAYS} to {SEARCH_DAYS} days ahead, ranked by what "
                     f"the customers it hits lose (each contract's daily volume and rate for the hours it is hit, hours "
                     f"shared with another outage lost once); the {tried} cheapest levelled against every crew and the "
                     f"planner's rules, {held} of them held back"}


def _store_window(conn, d: dict, said: tuple[str, str] | None = None) -> dict:
    from ..core.exceptions import raise_exception
    from ..core.wording import money

    pc = conn.execute("select * from plan_change where key = %s", (d["key"],)).fetchone()
    live = pc is not None and pc["state"] == "open"
    typed = list(pc["constraints"]) if live else []
    picks = dict(pc["picks"]) if live else {}
    p = work_windows(conn, d, typed, picks)
    ws = p["windows"]
    found = Finding("replan.outage_window", DETERMINISTIC, method=p["basis"], start=d["start"], end=d["until"],
                    data={"outage": d["outage"]["key"], "windows": len(ws)},
                    items=tuple(fact(f"Window {w['n'] + 1}", f"{_say(w['start'])} to {_say(w['end'])}", "")
                                for w in ws) + tuple(fact(f"Window {w['n'] + 1} cost", w["usd"], "USD") for w in ws))
    x = raise_exception(conn, TYPE, d["key"], f"{d['say']}: {plural(len(ws), 'window')} to choose", AGENT,
                        source="plan", refs=d["subjects"], priority=0.5, finding=found)
    conn.execute(
        """
        insert into plan_change (key, exception_id, proposal, picks, state) values (%s, %s, %s, %s, 'open')
        on conflict (key) do update set exception_id = excluded.exception_id, proposal = excluded.proposal,
            picks = excluded.picks, state = 'open',
            constraints = case when plan_change.state = 'open' then plan_change.constraints else '[]' end,
            changed = case when plan_change.state = 'open' then plan_change.changed else '[]' end,
            approved_by = case when plan_change.state = 'open' then plan_change.approved_by end,
            reverted_by = null, reverted_at = null, updated_at = now()
        """, (d["key"], x["id"], Jsonb(plain(p)), Jsonb(picks)))
    if said:
        _did(conn, d["key"], *said)
    _did(conn, d["key"], AGENT, f"Proposed {plural(len(ws), 'window')}" + (
        f": window 1 recommended, {money(ws[0]['usd'])} to customers" if ws else ""), again=False)
    return x


def outage_windows(conn, key: str, by: str) -> dict:
    """A planner asks for windows for an outage on the plan (a schedule's code, or item:<id>): three proposed as a plan
    change for the maintenance planner, worked out by Fuzer. Asking again works the open one out again."""
    from . import outages

    if gates.machine(by):
        raise PlanError("windows for an outage are asked for by a named person")
    o = outages.find(conn, key)
    if not o["hits"] and not outages.contracts(conn):
        raise PlanError("no contract register is loaded: there is no customer to weigh a window by")
    d = _outage(o)
    pc = conn.execute("select exception_id, state, proposal from plan_change where key = %s", (d["key"],)).fetchone()
    if pc and pc["state"] == "open":
        d = _outage(pc["proposal"]["outage"])
    x = _store_window(conn, d, said=(by, f"Asked for windows for {o['title']}"))
    return change(conn, x["id"])


def pick_window(conn, exception_id: int, n: int, by: str, reason: str | None = None) -> dict:
    """The planner picks another of the windows proposed (n counts from 0). Not the recommended one only with a reason,
    as a choice between options is (P5.20). The plan is worked out again for it."""
    pc, d = _editable(conn, exception_id, by)
    if d["type"] != OUTAGE:
        raise PlanError("a window is picked on an outage's plan change")
    p = pc["proposal"] or {}
    if not 0 <= n < len(p.get("windows") or []):
        raise PlanError(f"plan change {exception_id} has no window {n + 1}")
    reason = (reason or "").strip() or None
    if n != p["recommended"] and not reason:
        raise PlanError(f"say why window {n + 1} rather than the recommended window {p['recommended'] + 1}")
    conn.execute("update plan_change set picks = %s, updated_at = now() where key = %s",
                 (Jsonb({"window": n, "reason": reason}), pc["key"]))
    _store_window(conn, d, said=(by, f"Picked window {n + 1}" + (f": {reason}" if reason else "")))
    return change(conn, exception_id)


def _customer_notices(d: dict, p: dict, notices: list[dict]) -> list[dict]:
    """A notice for each customer the window hits: when, the contracts it hits there and about how much each loses."""
    w = p["windows"][p["picked"]]
    o = d["outage"]
    by: dict[str, list[dict]] = defaultdict(list)
    for h in w["hits"]:
        by[h["customer"] or h["contract"]].append(h)
    out = list(notices)
    for name, hs in sorted(by.items()):
        lines = "; ".join(f"{h['contract']} ({h['service']}) at {h['point']}, about {h['volume_bbl']:,} bbl over "
                          f"{h['hours']:g} h" for h in hs)
        out.append({"audience": "customer", "to": name, "name": name, "jobs": list(o["item_ids"]),
                    "text": f"{o['title']}: planned from {_say(_when(w['start']))} to {_say(_when(w['end']))}. "
                            f"Your contracts it affects: {lines}."})
    return [{**x, "n": i, "sent_by": None, "sent_at": None} for i, x in enumerate(out)]


def _chose(conn, pc: dict, p: dict, by: str) -> None:
    """The window approved against the one recommended, kept as a choice between options is (P5.20 option_choice), so
    how often Fuzer's window is taken is measured with the rest."""
    kept = {"recommended": p["recommended"], "reason": p["recommend"],
            "options": [{k: w.get(k) for k in ("n", "start", "end", "usd", "volume_bbl", "reasons")} for w in p["windows"]]}
    conn.execute(
        """
        insert into option_choice (exception_id, item_id, type, chosen, recommended, accepted, reason, by, offered)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (pc["exception_id"], pc["case_id"], TYPE, f"window {p['picked'] + 1}", f"window {p['recommended'] + 1}",
              p["picked"] == p["recommended"], p.get("reason"), by, Jsonb(plain(kept))))
