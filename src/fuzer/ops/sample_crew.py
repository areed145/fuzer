"""A sample crew for the fictional demonstration facilities (#1073): without people working it, a demo's queue only
ages, so every count on Home, the board, performance, rounds and the handover says the work is not being done. The
crew is the shipped sample team on the project's roster, working the queue on today's clock through the ordinary
domain operations, as people would, every work tick:

    the ops lead (riley)      triages new work, takes the field's reports back and closes them, sends could-not-dos
                              round again, plans some work instead of sending it now, and publishes the daily report
    the desks                 riley (ops lead and engineer, HSE in a pinch), jordan (production engineer), pat
                              (maintenance planner) and casey (data steward) take work from their queues a few at a
                              time and close it after hours to days; what waits gets a realistic due date at triage
    the field (sam, alex)     one job each at a time: on site after the drive out, then done or could not do with a
                              note; between jobs they take from the operator queue, and they walk this shift's rounds

Each piece of work takes its own path, picked from its id so a run is repeatable and the mix holds: about one in ten
is left as nobody's yet, so a few cases are genuinely overdue and the demo account finds work to take. Nothing is
backdated: each step happens at the tick it is due, so history builds up as it would on a staffed site.

Only a project whose bundle says `sample_crew: true` (the fakeplant bundles) gets a crew; a real deployment never does.
The crew acts only on work nobody else holds, never on a real account's work or the demo account's roster row.
"""

import hashlib
import logging
from datetime import datetime, timedelta

from ..core import projects

log = logging.getLogger(__name__)

LEAD = "riley"
FIELD = ("sam", "alex")
# The shipped sample team (ui SAMPLE_TEAM, offered on a fresh local demo) and alex, a second field operator: the
# roster template staffs two operator seats.
CREW = {
    "riley": ["ops_lead", "engineer"],
    "sam": ["operator"],
    "alex": ["operator"],
    "pat": ["maintenance_planner"],
    "jordan": ["production_engineer"],
    "casey": ["data_steward"],
}
DESK = {"ops_lead": "riley", "engineer": "riley", "hse_specialist": "riley", "production_engineer": "jordan",
        "maintenance_planner": "pat", "data_steward": "casey"}
WIP = {"data_steward": 8}  # cases a desk person works at once: stewards work their questions in batches
WIP_DEFAULT = 4
QUEUED = 3  # field jobs waiting in the operator queue before the lead holds the next back
PER_TICK = 4  # of each kind of step the crew takes in one tick, so a cold start fills the board over the first hour

LEFT = 0.10  # share of work nobody takes (genuinely overdue, or there for a visitor to take)
PLANNED = 0.35  # of the work raised with no queue, the share the lead plans instead of sending now
TO_QUEUE = 0.3  # of the field jobs, the share sent to the operator queue rather than to an operator
COULD_NOT = 0.15  # field jobs that come back could not do on a first visit
ROUND_PARTLY, ROUND_MISSED = 0.08, 0.03  # rounds left part done, or not walked, this shift
OUTSIDE = 0.04  # readings and checks found outside their limits: the field's own work for the IOC
FIELD_SHARE = {True: 0.7, False: 0.35}  # of desk work a field operator could check (by type) or not, the share sent out

DONE_NOTES = (
    "Checked on site: reading confirmed locally, no leak or damage found. Back in its normal range.",
    "Found a loose impulse line fitting, tightened and leak tested. Reading steady since.",
    "Local gauge agrees with the DCS. Cleared a blocked strainer and flushed the line.",
    "Walked down the skid with the panel operator; reset and restarted. Running normally.",
    "Topped up the oil and replaced a weeping gasket. No further alarms over 30 minutes.",
)
COULD_NOT_NOTES = (
    "No access: the area is barricaded for a crane lift until tomorrow's day shift.",
    "Needs a scaffold to reach the valve. Asked the planner for a slot.",
    "Spare seal kit is not on board; next boat is tomorrow morning.",
    "Weather: wind over the limit for working at height on the cellar deck.",
)
DESK_NOTES = {  # by queue: what a desk says when it closes work it worked
    "data_steward": (
        "Raised with the source system's owner to correct at source; tracked there.",
        "Checked against the drawings and the SAP record: corrected request sent to the master data team.",
        "Confirmed with the area engineer and logged for the next master data load.",
    ),
    "production_engineer": (
        "Reviewed against the last well test and the lift gas allocation: within its normal behaviour.",
        "Adjusted the lift gas setpoint with the control room; heading has settled.",
    ),
    "maintenance_planner": (
        "Planned onto the next maintenance window with parts reserved.",
        "Covered by a notification already in SAP; closed against it.",
    ),
    None: (
        "Reviewed the trend and the history: within what this equipment does in this season. Nothing to do now.",
        "Raised with the vendor and logged on the asset's watch list; closed here.",
        "Duplicate of work already planned on the equipment; closed against that plan.",
        "Checked with the control room: setpoint change during a planned test. No action.",
        "Root cause recorded and the fix added to the next planned maintenance on this equipment.",
    ),
}


def enabled(conn) -> bool:
    """Whether this project runs a sample crew: its bundle says `sample_crew: true`."""
    name = projects.current(conn)
    cfg = projects.config(conn, name) if name else None
    return bool((cfg or {}).get("sample_crew"))


def _u(*key) -> float:
    """A number in [0, 1) fixed by `key`: the same piece of work takes the same path on every tick."""
    return int(hashlib.sha256(":".join(map(str, key)).encode()).hexdigest()[:12], 16) / 16 ** 12


def _minutes(lo: float, hi: float, *key) -> timedelta:
    return timedelta(minutes=lo + (hi - lo) * _u(*key))


def _pick(options: tuple, *key):
    return options[int(_u(*key) * len(options))]


def staff(conn) -> list[str]:
    """Put the crew on the roster (once): a roster entry someone changed since is left as they set it."""
    from ..core import work

    added = []
    for name, roles in CREW.items():
        if not conn.execute("select 1 from work_person where name = %s", (name,)).fetchone():
            work.add_person(conn, name, roles, by="sample crew")
            added.append(name)
    return added


class _Tick:
    def __init__(self, conn, now: datetime):
        self.conn, self.now = conn, now
        self.done: dict[str, int] = {}
        self.errors: list[str] = []

    def act(self, what: str, fn, *args, **kwargs) -> bool:
        """One step as one transaction; a refused step is noted and the crew goes on with the rest."""
        if self.done.get(what, 0) >= PER_TICK:
            return False
        try:
            fn(*args, **kwargs)
            self.conn.commit()
        except Exception as e:  # a step the work refused (a case closed meanwhile, a gate): try again next tick
            self.conn.rollback()
            self.errors.append(f"{what}: {e}")
            log.info("sample crew could not %s: %s", what, e)
            return False
        self.done[what] = self.done.get(what, 0) + 1
        return True


def _since(conn, item_id: int | None, fallback: datetime) -> datetime:
    """When a case last moved: its latest event, else when its exception was raised."""
    if not item_id:
        return fallback
    row = conn.execute("select max(at) as at from work_event where item_id = %s", (item_id,)).fetchone()
    return row["at"] or fallback


def _field_jobs(rows: list[dict]) -> dict[str, int]:
    """Each operator's open field jobs: held by them and not yet reported back."""
    held = {p: 0 for p in FIELD}
    for r in rows:
        last = _last_field(r)
        if r["assignee"] in held and not (last and last["status"] in ("done", "could_not_do")
                                          and last["by"] == r["assignee"]):
            held[r["assignee"]] += 1
    return held


def _last_field(r: dict) -> dict | None:
    field = (r.get("evidence") or {}).get("field") or []
    return field[-1] if field else None


def _visits(r: dict) -> int:
    return sum(1 for f in (r.get("evidence") or {}).get("field") or [] if f.get("status") in ("done", "could_not_do"))


def work_queue(t: _Tick) -> None:
    """Every open piece of work one step on, where its next step is due."""
    from ..core import work
    from . import exceptions, plan

    conn, now = t.conn, t.now
    rows = exceptions.listing(conn, state="open", limit=2000)  # most valuable first: what the desks take first
    types = exceptions.types(conn)
    dues = {i["id"]: i["due"] for i in conn.execute("select id, due from work_item where id = any(%s)",
                                                    ([r["item_id"] for r in rows if r["item_id"]],))}
    held = _field_jobs(rows)
    queued = sum(1 for r in rows if r["item_id"] and r["role"] == "operator" and not r["assignee"])
    wip = {p: 0 for p in DESK.values()}
    for r in rows:
        if r["assignee"] in wip and r["role"] != "operator":
            wip[r["assignee"]] += 1
    room = {p: max(WIP.get(q, WIP_DEFAULT) for q, d in DESK.items() if d == p) for p in wip}
    crew = set(CREW)

    def free_operator() -> str | None:
        free = [p for p in FIELD if held[p] == 0]
        return min(free, key=lambda p: _u(p, now.hour)) if free else None

    def send_out(r: dict, step: str, why: str = "Go and have a look, report what you find") -> None:
        """The lead sends a field job out: to a free operator, else (some of them) to the operator queue."""
        nonlocal queued
        who = free_operator()
        if who is None or _u(r["id"], step, "queue") < TO_QUEUE:
            if queued >= QUEUED:
                return
            if t.act(step, exceptions.dispatch, conn, r["id"], LEAD, "role:operator", why):
                queued += 1
            return
        if t.act(step, exceptions.dispatch, conn, r["id"], LEAD, who, why):
            held[who] += 1

    for r in rows:
        u = _u(r["id"], "path")
        if u < LEFT:
            continue  # nobody's yet
        t_ = types.get(r["type"])
        field = bool(t_ and "field" in t_.audience)
        last = _last_field(r)
        since = _since(conn, r["item_id"], r["first_seen"])
        waited = now - since

        if not r["item_id"]:  # raised with no queue: the lead sends it to the field or plans it
            if r["planned"] or waited < _minutes(15, 60, r["id"], "triage"):
                continue
            if _u(r["id"], "plan") < PLANNED:
                start = (now + timedelta(days=1 + int(_u(r["id"], "day") * 3))).replace(hour=7, minute=0, second=0,
                                                                                        microsecond=0)
                t.act("plan", plan.plan_exception, conn, r["id"], LEAD, start, start + timedelta(hours=4),
                      crew=_pick(FIELD, r["id"], "crew"), notes="Planned at triage: not urgent, done with the crew's day")
            else:
                send_out(r, "dispatch")
            continue

        back = (last and last["status"] in ("done", "could_not_do") and r["assignee"] != last["by"]
                and r["role"] != "operator")  # sent round again, it waits in the operator queue below
        if back:  # back from the field: the lead closes what was done, sends a could-not-do round again once
            if r["assignee"] not in (None, LEAD) or waited < _minutes(30, 120, r["id"], "back", _visits(r)):
                continue
            if last["status"] == "done":
                t.act("close", work.close_case, conn, r["item_id"], "done", LEAD,
                      reason=f"Closed on the field's report: {last.get('note') or 'done'}")
            elif _visits(r) < 2:
                send_out(r, "redispatch", f"Try again when you can: {last.get('note') or 'could not do'}")
            continue

        if r["assignee"] in FIELD:  # with an operator: on site after the drive, then done or could not do
            who, visit = r["assignee"], _visits(r)
            if not last or last["status"] in ("done", "could_not_do"):
                if waited >= _minutes(20, 60, r["id"], "travel", visit):
                    _on_site(t, r, who)
            elif last["status"] == "on_site" and waited >= _minutes(90, 300, r["id"], "work", visit):
                if visit == 0 and _u(r["id"], "could") < COULD_NOT:
                    t.act("report", exceptions.report, conn, r["id"], who, "could_not_do",
                          _pick(COULD_NOT_NOTES, r["id"], "why"))
                else:
                    t.act("report", exceptions.report, conn, r["id"], who, "done", _pick(DONE_NOTES, r["id"], "note"))
            continue

        if r["role"] == "operator" and not r["assignee"]:  # in the operator queue: a free operator takes it
            who = free_operator()
            if who and waited >= _minutes(20, 90, r["id"], "take") and t.act("take", work.take, conn, r["item_id"], who):
                held[who] += 1
            continue

        if r["assignee"] and r["assignee"] not in crew:
            continue  # a real person (or the demo account) holds it: theirs

        desk = DESK.get(r["role"] or "")
        if not r["assignee"]:  # in a desk queue: triage, then a desk person takes it when they have room
            if waited < _minutes(15, 75, r["id"], "triage"):
                continue
            if r["role"] != "data_steward" and _u(r["id"], "field") < FIELD_SHARE[field]:
                send_out(r, "dispatch")
            elif desk and wip[desk] < room[desk]:
                if t.act("take", work.take, conn, r["item_id"], desk):
                    wip[desk] += 1
                    hours = _desk_hours(r["id"])
                    if (due := dues.get(r["item_id"])) is not None and due < now + timedelta(hours=hours):
                        # taken late: the desk says when it will be done
                        t.act("triage", work.set_due, conn, r["item_id"], desk, now + timedelta(hours=hours + 2),
                              reason="Taken from the backlog; done by then")
            elif (due := dues.get(r["item_id"])) is None or due < now + timedelta(hours=2):
                due = now + timedelta(hours=24 + 96 * _u(r["id"], "due"))
                t.act("triage", work.set_due, conn, r["item_id"], LEAD, due,
                      reason="Triaged: not urgent, queued behind the work in hand")
            continue

        if r["assignee"] in DESK.values():  # at a desk: worked for hours to days, then closed
            hours = _desk_hours(r["id"])
            if waited >= timedelta(hours=hours):
                if t.act("close", work.close_case, conn, r["item_id"], "done", r["assignee"],
                         reason=_pick(DESK_NOTES.get(r["role"]) or DESK_NOTES[None], r["id"], "desk-note")):
                    wip[r["assignee"]] -= 1


def _desk_hours(exception_id: int) -> float:
    """How long a desk works a case: a few hours for most, days for some (about a day and a half on average)."""
    return 4 + 92 * _u(exception_id, "desk") ** 2


def _on_site(t: _Tick, r: dict, who: str) -> None:
    """On site, where the permit allows; without a valid permit the job comes back could not do."""
    from . import exceptions, readiness

    permit = readiness.permits(t.conn, list(r["subjects"] or []), r["id"])
    if not permit["ready"]:
        t.act("report", exceptions.report, t.conn, r["id"], who, "could_not_do",
              f"No valid permit to work yet: {permit['blockers'][0]}")
        return
    t.act("report", exceptions.report, t.conn, r["id"], who, "on_site", None,
          permit_in_hand=True if permit.get("confirm") else None)


def daily_reports(t: _Tick) -> None:
    """The ops lead publishes each daily report an hour or two after its review comes up (a step nobody else holds)."""
    from ..core import work

    for s in t.conn.execute(
            """
            select t.id, t.item_id, t.element, t.assignee, t.created_at from work_token t join work_item i on i.id = t.item_id
            where i.type = 'daily_report' and t.kind in ('user', 'fix') and t.state = 'waiting'
              and (t.assignee is null or t.assignee = %s) order by t.created_at
            """, (LEAD,)).fetchall():
        if t.now - s["created_at"] < _minutes(45, 180, s["item_id"], s["element"]):
            continue
        data = {"review_decision": "publish"} if s["element"] == "review" else {}
        t.act("report_review", work.complete_step, t.conn, s["id"], LEAD, data)


def walk_rounds(t: _Tick) -> None:
    """This shift's rounds, walked through the shift: most are done by two thirds of the way in, a few are left part
    done or not walked. Readings sit inside their limits, with the odd one outside (which raises a round finding)."""
    from . import rounds

    conn, now = t.conn, t.now
    start, end = rounds.shift(conn, now)
    gone = (now - start) / (end - start)
    for r in rounds.runs(conn, now):
        if r["state"] != "open":
            continue
        u = _u(r["round"], r["shift_start"].isoformat())
        reach = 0.0 if u < ROUND_MISSED else 0.6 if u < ROUND_MISSED + ROUND_PARTLY else 1.0
        offset = 0.1 + 0.25 * _u(r["round"], "start")  # rounds start through the first part of the shift
        share = min(reach, max(0.0, (gone - offset) / 0.45))
        target = int(r["points"] * share + 1e-9)
        if target <= r["taken"]:
            continue
        who = _pick(FIELD, r["round"], r["shift_start"].isoformat())
        run = rounds.run(conn, r["id"])
        for p in [p for p in run["points"] if not p["taken"]][:target - r["taken"]]:
            key = (r["id"], p["id"])
            if p["kind"] == "reading":
                value = _reading(p, *key)
                if value is None:
                    continue
                t.act("round", rounds.take, conn, r["id"], p["id"], who, value=value)
            else:
                ok = _u(*key, "check") >= OUTSIDE
                t.act("round", rounds.take, conn, r["id"], p["id"], who, passed=ok,
                      note=None if ok else "Found it not as it should be; photo on the case")
        t.done["round"] = 0  # a round is walked point by point: the cap is per round, not per tick


def _reading(p: dict, *key) -> float | None:
    """A plausible reading: inside the limits, near the middle, now and then outside them."""
    lo, hi = p["low"], p["high"]
    if lo is None and hi is None:
        return None
    if lo is None:
        lo = hi * 0.5 if hi > 0 else hi * 1.5
    if hi is None:
        hi = lo * 1.5 if lo > 0 else lo * 0.5
    span = hi - lo
    if _u(*key, "outside") < OUTSIDE:
        return round(hi + span * (0.05 + 0.2 * _u(*key, "by")), 2)
    return round(lo + span * (0.3 + 0.4 * _u(*key, "value")), 2)


def run(conn, now: datetime | None = None) -> dict:
    """One tick of the crew, where the project has one. Returns what each part did."""
    if not enabled(conn):
        return {}
    from ..core import exceptions as port

    now = now or conn.execute("select now() as t").fetchone()["t"]
    t = _Tick(conn, now)
    added = staff(conn)
    conn.commit()
    for part in (work_queue, daily_reports, walk_rounds):
        try:
            part(t)
        except Exception as e:  # one part failing leaves the others to run
            conn.rollback()
            t.errors.append(f"{part.__name__}: {e}")
            log.warning("sample crew %s failed: %s", part.__name__, e)
    port.reconcile(conn)  # exceptions whose case the crew closed close now, not at the next tick
    conn.commit()
    return {"staffed": added, "did": t.done, "errors": t.errors[:5]}
