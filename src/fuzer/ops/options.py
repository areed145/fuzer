"""Options with consequences on a case (P5.20, #1251): the realistic choices for one piece of field work, side by side
on one scale, one of them recommended with the factor that decided it, carried out in one tap and recorded.

    for_exception(conn, id)    the options for an event: dispatch now, ride along with the next visit to its equipment,
                               shelve, escalate; each with when it would be done, the production deferred until then
                               (bbl and $), the risk of waiting, readiness and clashes
    for_case(conn, item_id)    the same, for the event a case works
    choose(conn, id, by, option, reason, ...)  carry out a choice: dispatch it, put it on the plan, shelve it or send
                               it to the engineer, with the reason on the case's history and the field job
    acceptance(conn, days)     how often the recommendation was taken, per work type

Read from what Fuzer already has, never from equipment type:
    what it defers now      the producing wells it reaches at their latest test (sre_handoff.rate_at_risk): all of it
                            while a machine is down (a trip), else the pack's share for a process upset still going
                            (value config `upsets.rate_at_risk_share`), the same assumption an upset's value uses.
                            A machine that is down takes the wells behind it on the flow network (at_risk.trip), its
                            share where parallel trains carry the rest. Anything else (a report of a smell or a drip,
                            a notification) has no measured link to production: what it defers is not known (#1365)
    what it puts at risk    for a condition not yet costing production: what is behind it if it trips, for the
                            risk line; never added to the deferred barrels, since the chance it trips is not known
    the risk of waiting     from the record only: its assets' ABC criticality (with its priority), or a hazard it
                            reports (gas, H2S, fire) or its type carries (safety, HSE), which never reads low. Neither
                            on record: no risk word, and the criticality is named as not known (#1365)
    when it is done         dispatch now: the operator queue's time to respond (its SLA); the next visit: a field
                            operator already sent to or on site at the same asset (#1365), else a plan item on its
                            equipment (its assets or what they are part of, short of the area) under way or starting
                            within `options.window_days`; shelve: until the next
                            turnaround or shutdown of the area, at most `exceptions.max_shelve_days`, never for a limit
                            or a leak with production behind it (#1381); escalate: the engineer queue's SLA (the ops
                            lead's for a gas test or permit), then a dispatch, never to a role the reader acts as
    what the field is asked the IOC's words, else the message the case came from, else ASK (#1381)
    carrying one out        dispatch: to whom it is sent; ride along: a field step for the visit's operator (the
                            person on it, else the operator queue) on the visit's day, asking the same (#1390);
                            shelve: off every queue until its date; escalate: to that queue
    readiness and clashes   readiness.check (the field's last report, crew, permit, spares, simultaneous operations,
                            MOC) for dispatch now, less the visit it can ride along with: someone already at the
                            asset is the ride along, not a clash; the visit's own clashes on the plan for riding along
    price                   the pack's value config (oil per bbl, gas per Mscf)

The recommendation is a rule, the same on every case, so two people reading one case get one answer: riding along
when it is ready and does not leave a high risk waiting, where a separate trip (`options.trip_usd`) has to save more
than it costs, else dispatch now, else escalate. Shelve is offered but never recommended (#1390). It names the factor
that decided it (`deciding`, one of FACTORS). Every open option carries the same fields (YARDSTICK), so options read
on one scale whoever reads them: the shared skill
`shared/options-and-tradeoffs` (P4.8) tells every specialist to answer from them. Anything not known is named, not
guessed.
"""

import re
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from ..core import events, settings, work
from ..core.exceptions import ExceptionError

KEYS = ("dispatch", "window", "shelve", "escalate")
DOWN_TYPES = ("machine_trip",)  # the thing is stopped: everything it reaches is deferred, not a share
SHARE_TYPES = ("upset",)  # a process upset still going: the pack's share of what it reaches, as an upset's value says
# A hazard to people in what was reported: gas escaping, H2S, fire or smoke. Never a low risk, whatever the asset.
HAZARD = re.compile(r"(?i)\b(h2s|hydrogen sul[pf]hide|smoke|flames?|explosion|lel)\b"
                    r"|\bfire\b(?!\s*(water|pump|main|hydrant))|gas (smell|leak|release|escap\w*|detector|alarm)"
                    r"|smell(s|ing|ed)? (of )?gas|(leaking|escaping) gas")
# Past or heading past a limit, or leaking: on kit with production behind it, never one to shelve (#1381).
LIMIT_OR_LEAK = re.compile(r"(?i)\blimits?\b|\b(hi ?hi|lo ?lo|high|low)[- ](alarm|limit)\b"
                           r"|\b(leak\w*|weep\w*|seep\w*|drip\w*)\b|\b(dropping|falling|rising|climbing|crept up|down to)\b")
# What the field's report says it waits on that the ops lead arranges, not an engineer: the permit holder's work.
PERMIT_WORK = ("a gas test", "a permit", "an isolation", "depressurising")
OPEN_STAGES = ("dispatch", "desk", "back", "planned")
JOB_HOURS = 4.0  # the job itself, once someone is there: added after a wait for a visit or a shelf
LEVELS = ("Low", "Medium", "High")
# The fields every open option carries, on one scale: when it is done, what it defers until then, the risk of waiting,
# readiness, clashes, what is not known about it, and the basis for the time. A closed option says why it is closed.
YARDSTICK = ("hours", "by", "deferred", "risk", "ready", "blockers", "clashes", "unknown", "basis")
# What decides a recommendation: what waiting defers against a separate trip, the risk of waiting, or whether it can go
# now (readiness: crew, permit, parts, clashes; or someone already at the asset).
FACTORS = ("deferred", "risk", "readiness")


class OptionsError(ExceptionError):
    pass


# ---- the event and what it costs ------------------------------------------------------------------------------

def _event(conn, exception_id: int) -> dict:
    from . import exceptions

    row = conn.execute(
        """
        select e.*, w.state as case_state, w.assignee, w.role, w.step as case_step, w.priority as case_priority,
               """ + exceptions.BACK_FROM_FIELD + """,
               (select json_build_object('id', p.id, 'start_at', p.start_at, 'end_at', p.end_at, 'crew', p.crew)
                from plan_item p where p.exception_id = e.id and p.status in ('proposed', 'scheduled', 'in_progress')
                order by p.start_at limit 1) as planned
        from exception_record e left join work_item w on w.id = e.item_id where e.id = %s
        """, (exception_id,)).fetchone()
    if row is None:
        raise OptionsError(f"no exception {exception_id}")
    if row["joined_to"]:
        return _event(conn, row["joined_to"])
    row = dict(row)
    row["stage"] = exceptions.stage(row, exceptions.operators(conn))
    return row


def _hours(conn, role: str, fallback: float) -> float:
    """A queue's time to respond, from its SLA (work_role.default_sla, an ISO 8601 duration Postgres reads)."""
    import psycopg

    try:
        with conn.transaction():  # a savepoint: an SLA Postgres can't read rolls back only this read
            r = conn.execute("select extract(epoch from default_sla::interval) / 3600 as h from work_role "
                             "where name = %s and default_sla is not null", (role,)).fetchone()
    except psycopg.DataError:  # an SLA written in a form Postgres doesn't read: the fallback, not a failed page
        return fallback
    return float(r["h"]) if r and r["h"] else fallback


def _parallel(conn, unit: str) -> int:
    """How many units carry what `unit` carries: those fed by the same nodes and feeding the same ones on the flow
    network (trains in parallel). One when it has no twin."""
    rows = conn.execute(
        """
        with mine as (select array_agg(distinct dst order by dst) as out,
                             (select array_agg(distinct src order by src) from edge where rel = 'flows_to'
                              and dst = %(u)s and valid_to is null) as feeds
                      from edge where rel = 'flows_to' and src = %(u)s and valid_to is null)
        select count(*) as n from (
            select o.src, array_agg(distinct o.dst order by o.dst) as out,
                   (select array_agg(distinct i.src order by i.src) from edge i where i.rel = 'flows_to'
                    and i.dst = o.src and i.valid_to is null) as feeds
            from edge o where o.rel = 'flows_to' and o.valid_to is null group by o.src) t, mine
        where t.out = mine.out and t.feeds is not distinct from mine.feeds
        """, {"u": unit}).fetchone()
    return max(1, int(rows["n"] or 1))


def _prices(conn) -> dict:
    from ..analysis import value
    from ..core import pack as packs

    try:
        cfg = value.config(packs.current_pack(conn))
    except Exception:  # no pack chosen yet: the built-in prices, said as such
        cfg = value.config(type("NoPack", (), {"name": "none", "value": None})())
    return {**cfg["prices"], "source": cfg["source"], "share": cfg["upsets"]["rate_at_risk_share"]}


def _usd(oil: float, gas: float, prices: dict) -> float:
    return oil * prices["oil_bbl"] + gas * prices["gas_mscf"]


def loss(conn, row: dict, prices: dict) -> tuple[dict | None, dict | None, list[str]]:
    """What the event defers now (per day) and what is behind it if it trips, each with its basis, and what is not
    known. Deferred now: a down machine's share of the wells behind it, or the wells the condition reaches; at risk:
    what is behind a machine whose condition has not stopped it yet."""
    from . import at_risk, sre_handoff

    subjects = list(row["subjects"] or [])
    unknown: list[str] = []
    down = row["type"] in DOWN_TYPES
    rate = sre_handoff.rate_at_risk(conn, subjects) if subjects else None
    now = behind = None
    if rate and not down and row["type"] not in SHARE_TYPES:
        # a report of a smell or a drip on a well defers nothing anyone measured: what is behind it, not a loss
        behind = {"oil_bbld": round(rate["oil_bbld"], 1), "gas_mscfd": round(rate["gas_mscfd"], 1),
                  "usd_per_day": round(_usd(rate["oil_bbld"], rate["gas_mscfd"], prices)), "wells": rate["wells"],
                  "basis": f"the latest test rate of the {rate['wells']} producing well(s) it is on, if they stop"}
        unknown.append("what it defers: nothing measured links it to a loss of production")
    elif rate:
        share = 1.0 if down else prices["share"]
        oil, gas = rate["oil_bbld"] * share, rate["gas_mscfd"] * share
        now = {"oil_bbld": round(oil, 1), "gas_mscfd": round(gas, 1), "usd_per_day": round(_usd(oil, gas, prices)),
               "wells": rate["wells"],
               "basis": (f"the latest test rate of the {rate['wells']} producing well(s) it is on, stopped"
                         if down else f"{share:.0%} of the latest test rate of the {rate['wells']} producing well(s) "
                         "it is on, while it lasts (the pack's share for an upset still going)")}
    elif subjects:
        tripped = at_risk.trip(conn, subjects[0], hours=24)
        oil = next((a["value"] for a in tripped["at_risk"] if a["quantity"] == "oil_rate"), 0.0)
        gas = next((a["value"] for a in tripped["at_risk"] if a["quantity"] == "gas_rate"), 0.0)
        if tripped["on_network"] and (oil or gas):
            # one train of several in parallel carries its share; a machine that takes down a set of units (a driver
            # sharing its train's parent) is one train of however many its widest unit has twins
            n = max(_parallel(conn, u["id"]) for u in tripped["units"])
            oil, gas = oil / n, gas / n
            wells = len(tripped["wells"])
            said = (f"the {wells} well(s) behind {tripped['item']['label']} on the flow network at their latest test"
                    + (f", its share of {n} trains in parallel" if n > 1 else ""))
            figure = {"oil_bbld": round(oil, 1), "gas_mscfd": round(gas, 1),
                      "usd_per_day": round(_usd(oil, gas, prices)), "wells": wells, "basis": said}
            if down:
                now = {**figure, "basis": said + ", stopped"}
            else:
                behind = {**figure, "basis": said + ", if it trips"}
                unknown.append("what it defers: nothing measured links it to a loss of production")
        elif not tripped["on_network"]:
            unknown.append("what it defers: it reaches no tested producing well and is not on the flow network")
    else:
        unknown.append("what it defers: it is matched to no asset")
    return now, behind, unknown


def _criticality(conn, subjects: list[str]) -> str | None:
    """The highest ABC criticality among its assets and the tags they are installed at."""
    if not subjects:
        return None
    rows = conn.execute(
        """
        select value #>> '{}' as v from golden where field = 'criticality' and fuzer_id in (
            select unnest(%(s)s::text[]) union select dst from edge where rel = 'installed_at' and valid_to is null
            and src = any(%(s)s))
        """, {"s": subjects}).fetchall()
    found = sorted({(r["v"] or "").strip().upper()[:1] for r in rows} & {"A", "B", "C"})
    return found[0] if found else None


def _hazard(conn, row: dict) -> str | None:
    """What makes it a hazard to people, from the record: its type is a safety case type or HSE's work, or what was
    reported (its title, the field's notes) names gas escaping, H2S, fire or smoke. None when nothing does."""
    from . import exceptions

    if row["type"] in _safety_types(conn):
        return "a safety case type"
    t = exceptions.types(conn).get(row["type"])
    if t is not None and t.role == "role:hse_specialist":
        return "HSE work"
    for text in _said(conn, row):
        if m := HAZARD.search(text):
            return f"{m.group(0).lower()} reported"
    return None


def _said(conn, row: dict) -> list[str]:
    """What the record says of the event in words: its title, the message it came from, the field's notes."""
    from . import messages

    reports = (row.get("evidence") or {}).get("field") if isinstance(row.get("evidence"), dict) else None
    text = messages.text_of(conn, row["id"])
    return [row.get("title") or "", *([text] if text else []), *[(f or {}).get("note") or "" for f in reports or []]]


def _no_shelf(conn, row: dict, now: dict | None, behind: dict | None) -> str | None:
    """Why a shelf is not offered: past or heading past a limit, or a leak, on kit with production behind it (#1381).
    None when it can wait on a shelf."""
    flow = now or behind
    if not flow or not (flow["oil_bbld"] or flow["gas_mscfd"]):
        return None
    m = next((m for t in _said(conn, row) if (m := LIMIT_OR_LEAK.search(t))), None)
    if m is None:
        return None
    said = (f"about {_n(about(flow['oil_bbld']))} bbl/d" if flow["oil_bbld"]
            else f"about {_n(about(flow['gas_mscfd']))} Mscf/d")
    return f"“{m.group(0)}” with {said} behind it can't be shelved"


def _base(priority: float | None, crit: str | None, hazard: str | None = None) -> dict | None:
    """The risk of the condition itself, 0 low to 2 high, and the least it can be: its priority (a 0-1 score, or SAP
    1-4 stored as 5 minus it) and its criticality, whichever is higher; a hazard to people is high and never reads
    low however short the wait. None when nothing on record backs a risk word: no criticality and no hazard (#1365)."""
    if not crit and not hazard:
        return None
    p = 0
    if priority is not None and priority > 0:
        if priority <= 1:
            p = 2 if priority >= 0.8 else 1 if priority >= 0.5 else 0
        elif priority <= 4:
            sap = 5 - round(priority)
            p = 2 if sap <= 2 else 1 if sap == 3 else 0
        else:  # a value in USD
            p = 2 if priority >= 10000 else 1 if priority >= 1000 else 0
    c = {"A": 2, "B": 1, "C": 0}.get(crit or "", 0)
    return {"level": 2 if hazard else max(p, c), "floor": 1 if hazard else 0, "hazard": hazard}


def _risk(base: dict | None, hours: float, crit: str | None, behind: dict | None) -> dict | None:
    """The risk of leaving it this long: a short wait takes the condition's own risk down a level or two, a long one
    leaves it as it is; a hazard stays at least medium. None when nothing on record backs one."""
    if base is None:
        return None
    level = base["level"] - 2 if hours <= 8 else base["level"] - 1 if hours <= 72 else base["level"]
    level = max(base["floor"], min(2, level))
    why = [f"criticality {crit}" if crit else "criticality not assessed"]
    if base["hazard"]:
        why.append(base["hazard"])
    if behind:
        why.append(f"{_n(behind['oil_bbld'])} bbl/d behind it if it trips")
    return {"level": LEVELS[level], "why": ", ".join(why)}


def _n(x: float) -> str:
    return f"{x:,.0f}"


def _deferred(now: dict | None, hours: float, prices: dict) -> dict | None:
    if now is None:
        return None
    days = hours / 24
    oil, gas = now["oil_bbld"] * days, now["gas_mscfd"] * days
    return {"oil_bbl": round(oil), "gas_mscf": round(gas), "usd": round(_usd(oil, gas, prices))}


# ---- the windows -------------------------------------------------------------------------------------------------

def _alongside(conn, row: dict, at: datetime) -> dict | None:
    """A field operator already sent to or on site at one of the event's own assets, on another open job: the visit
    to ride along with, now (#1365). Work in the operator queue nobody has taken is not someone there."""
    from . import exceptions

    field = exceptions.operators(conn)
    if not field:
        return None
    for r in conn.execute(
        """
        select e.id, e.title, e.evidence, w.assignee from exception_record e join work_item w on w.id = e.item_id
        where e.state = 'open' and e.joined_to is null and e.id <> %s and e.subjects && %s::text[]
          and w.state not in ('done', 'cancelled') and w.assignee = any(%s)
        order by e.updated_at desc, e.id
        """, (row["id"], list(row["subjects"] or []), sorted(field))).fetchall():
        reports = ((r["evidence"] or {}).get("field") or []) if isinstance(r["evidence"], dict) else []
        last = next((f for f in reversed(reports) if (f or {}).get("status") != "note"), None) or {}
        if last.get("status") in ("done", "could_not_do"):
            continue  # back from the field (a note after it changes nothing): nobody there now
        there = "on site" if last.get("status") == "on_site" and last.get("by") == r["assignee"] else "sent there"
        return {"id": None, "exception": r["id"], "person": r["assignee"], "title": f"{r['assignee']}, {there} for "
                f"“{r['title']}”", "kind": "field_job", "start_at": at, "end_at": at + timedelta(hours=JOB_HOURS),
                "crew": r["assignee"], "basis": f"{r['assignee']} is {there} on its asset now, then {JOB_HOURS:g} h "
                "for the job"}
    return None


def _visit(conn, row: dict, at: datetime, days: int) -> dict | None:
    """The next visit to the event's own equipment that it could ride along with: a field operator at the same asset
    now (`_alongside`), else a plan item scheduled or under way, ending after now and starting within `days`, not this
    event's own, on one of its assets or on what they are part of short of an area (an instrument's exchanger, a
    well's tags), never a field job that came back done or could not do (#1381). An area can be half a plant (one
    demo field's compression area holds half its tags), so a job elsewhere in it is not a visit to this one (#1358)."""
    from . import exceptions
    from .plan import ACTIVE

    subjects = list(row["subjects"] or [])
    if not subjects:
        return None
    if along := _alongside(conn, row, at):
        return along
    r = conn.execute(
        """
        with recursive up (fuzer_id, depth) as (
            select s, 0 from unnest(%(s)s::text[]) s
            union
            select e.dst, up.depth + 1 from up join edge e on e.src = up.fuzer_id and e.rel = 'part_of'
            join entity k on k.fuzer_id = e.dst and k.kind not in ('area', 'facility') where up.depth < 4),
        mine as (select array_agg(fuzer_id) as ids from up)
        select p.id, p.title, p.kind, p.start_at, p.end_at, p.crew,
               (select w.assignee from exception_record v join work_item w on w.id = v.item_id
                where v.id = p.exception_id and w.closed_at is null) as held_by
        from plan_item p, mine
        where p.status = any(%(active)s) and p.status <> 'proposed' and p.end_at > %(at)s
          and p.start_at <= %(until)s and p.exception_id is distinct from %(e)s
          and not exists (select 1 from exception_record v  -- a field job that came back (a note after changes nothing)
                          where v.id = p.exception_id and jsonb_typeof(v.evidence->'field') = 'array'
                          and (select f.r->>'status' from jsonb_array_elements(v.evidence->'field')
                               with ordinality f(r, n) where f.r->>'status' <> 'note' order by f.n desc limit 1)
                              in ('done', 'could_not_do'))
          and (p.subjects && mine.ids or exists (select 1 from edge x where x.src = any(p.subjects)
                                                 and x.rel = 'part_of' and x.dst = any(mine.ids)))
        order by p.start_at, p.id limit 1
        """, {"active": list(ACTIVE), "at": at, "until": at + timedelta(days=days), "e": row["id"],
              "s": subjects}).fetchone()
    if not r:
        return None
    # who goes on the visit: the person it is booked to, else whoever holds the field job it is (a dispatch's bar)
    crew = r["crew"] if r["crew"] and not r["crew"].startswith("role:") else None
    held = r.pop("held_by")
    return {**r, "start_at": max(r["start_at"], at),  # under way: it is there now
            "operator": crew or (held if held in exceptions.operators(conn) else None)}


def _beside(ready: dict, visit: dict | None) -> dict:
    """Readiness for dispatch now, less the same-asset clash with the visit it can ride along with: someone already at
    the asset is the ride along, not a clash (#1365)."""
    if not visit:
        return ready
    ref = {"exception": visit["exception"]} if visit.get("exception") else {"plan_item": visit["id"]}
    gone = {x["say"] for c in ready["checks"] if c["check"] == "simops" for x in c.get("conflicts") or []
            if x["kind"] == "same_asset" and x["with"] == ref}
    if not gone:
        return ready
    checks = [{**c, "blockers": [b for b in c["blockers"] if b not in gone]} if c["check"] == "simops" else c
              for c in ready["checks"]]
    blockers = [b for b in ready["blockers"] if b.split(": ", 1)[-1] not in gone]
    return {**ready, "checks": checks, "blockers": blockers, "ready": not blockers}


def _turnaround(conn, row: dict, at: datetime) -> dict | None:
    """The next turnaround or shutdown of the event's area, within a year."""
    from .plan import ACTIVE, SHUTDOWNS, _area

    area = _area(conn, list(row["subjects"] or []))
    if not area:
        return None
    r = conn.execute(
        """
        select id, title, kind, start_at, end_at from plan_item
        where status = any(%(active)s) and kind = any(%(sd)s) and area_id = %(area)s and start_at > %(at)s
          and start_at <= %(at)s + interval '365 days'
        order by start_at limit 1
        """, {"active": list(ACTIVE), "sd": list(SHUTDOWNS), "area": area, "at": at}).fetchone()
    return dict(r) if r else None


def _plan_clashes(conn, item_id: int, start: datetime, end: datetime) -> list[str]:
    from . import plan

    for i in plan.items(conn, start - timedelta(hours=1), end + timedelta(hours=1)):
        if i["id"] == item_id:
            return [c["say"] for c in i["clashes"] if c["kind"] != "bundle"]
    return []


def _above(conn, role: str) -> str:
    """Where a queue escalates (work_role.escalate_to); the ops lead when it names nowhere."""
    r = conn.execute("select escalate_to from work_role where name = %s", (role,)).fetchone()
    return (r and r["escalate_to"]) or "ops_lead"


def _escalate_to(conn, row: dict, waits_on: list[str], viewer: list[str] | None = None) -> str | None:
    """Who an escalation goes to: the ops lead for what the field says waits on a gas test, a permit, an isolation or
    depressurising (the permit holder's work, never the engineer's); else the engineer, unless the engineer already has
    it, then where the engineer queue escalates. Never a role the viewer acts as (#1381): past it, or None when there
    is nowhere further. A viewer holding every person role (an owner) narrows nothing."""
    if any(w in PERMIT_WORK for w in waits_on):
        to = "ops_lead"
    elif row.get("role") == "engineer" or (row.get("assignee") and "engineer" in work.roles_of(conn, row["assignee"])):
        to = _above(conn, "engineer")
    else:
        to = "engineer"
    if not viewer or {r["name"] for r in conn.execute("select name from work_role where kind = 'person'")} <= set(viewer):
        return to
    seen = set()
    while to in viewer:
        seen.add(to)
        to = _above(conn, to)
        if to in seen:
            return None
    return to


# ---- the options -------------------------------------------------------------------------------------------------

def _applies(conn, row: dict) -> str | None:
    """Why options don't apply to this event, or None when they do: work the field does, still to be decided. A
    message that only says what happened and that it is over is no field work to dispatch (#1381)."""
    from . import exceptions, messages

    t = exceptions.types(conn).get(row["type"])
    if t is not None and "field" not in t.audience:
        return "not field work: it is worked at a desk"
    if row["state"] != "open":
        return f"it is {row['state']}"
    if row["stage"] not in OPEN_STAGES:
        return "the field has it" if row["stage"] == "field" else f"it is {row['stage']}"
    if row["stage"] == "back" and _last_report(row) == "done":
        return "the field reported it done: close it out"
    if row.get("shelved_until") and row["shelved_until"] > datetime.now(row["shelved_until"].tzinfo):
        return f"it is shelved until {row['shelved_until']:%a %d %b}"
    if (text := messages.text_of(conn, row["id"])) and (why := messages.info_only(text)):
        return f"{why}: close it out"
    return None


def _last_report(row: dict) -> str | None:
    """The status of the field's last report on an event (done, could_not_do, on_site, note), or None."""
    reports = (row.get("evidence") or {}).get("field") if isinstance(row.get("evidence"), dict) else None
    return (reports[-1] or {}).get("status") if reports else None


def for_case(conn, item_id: int, viewer: list[str] | None = None) -> dict:
    r = conn.execute("select id from exception_record where item_id = %s and joined_to is null order by id limit 1",
                     (item_id,)).fetchone()
    if r is None:
        return {"applies": False, "why_not": "no event behind this case", "exception_id": None, "options": []}
    return for_exception(conn, r["id"], viewer=viewer)


def for_exception(conn, exception_id: int, at: datetime | None = None, viewer: list[str] | None = None) -> dict:
    """The options for an event; `viewer` is the roles of whoever reads them, so none escalates to themselves."""
    from . import readiness

    row = _event(conn, exception_id)
    # the database's clock, as shelve checks its expiry against: a shelf to the last day offered is not refused
    at = at or conn.execute("select now() as t").fetchone()["t"]
    head = {"exception_id": row["id"], "item_id": row["item_id"], "type": row["type"], "stage": row["stage"],
            "decided": decisions(conn, row["id"])[:1] or None}
    why_not = _applies(conn, row)
    if why_not:
        return {**head, "applies": False, "why_not": why_not, "options": []}
    prices = _prices(conn)
    now, behind, unknown = loss(conn, row, prices)
    crit = _criticality(conn, list(row["subjects"] or []))
    if not crit:
        unknown.append("criticality: not assessed on its assets")
    base = _base(row["case_priority"] or row["priority"], crit, _hazard(conn, row))
    options = []
    days = int(settings.get(conn, "options.window_days"))
    visit = _visit(conn, row, at, days)

    # dispatch now
    respond = _hours(conn, "operator", 4.0)
    ready = _beside(readiness.check(conn, list(row["subjects"] or []), row["id"]), visit)
    clashes = next((c["blockers"] for c in ready["checks"] if c["check"] == "simops"), [])
    notes = [c["note"] for c in ready["checks"] if not c.get("checked") and c.get("note")]
    options.append({
        "key": "dispatch", "label": "Dispatch now", "detail": "to a field operator",
        "hours": respond, "by": (at + timedelta(hours=respond)).isoformat(),
        "deferred": _deferred(now, respond, prices), "risk": _risk(base, respond, crit, behind),
        "ready": ready["ready"], "blockers": [b for b in ready["blockers"] if b not in clashes],
        "warnings": ready["warnings"], "unchecked": notes, "unknown": ready["unknown"], "clashes": clashes, "available": True,
        "basis": f"the operator queue answers within {respond:g} h (its SLA)"})

    # ride along with the next visit
    field = next((c for c in ready["checks"] if c["check"] == "field"), {"blockers": [], "waits_on": []})
    if visit:
        hours = (visit["start_at"] - at).total_seconds() / 3600 + JOB_HOURS
        parts = readiness.spares(conn, list(row["subjects"] or []))
        # what the field said it waits on holds a ride along as it holds a dispatch (#1381)
        short = ([f"Field: {b}" for b in field["blockers"]]
                 + [b for b in parts["blockers"] if not _arrives_by(b, visit["start_at"])])
        options.append({
            "key": "window", "label": "Ride along with the next visit",
            "detail": f"{visit['title']}, {visit['start_at']:%a %d %b}", "plan_item": visit["id"],
            "start": visit["start_at"].isoformat(), "crew": visit["crew"],
            "hours": round(hours, 1), "by": (at + timedelta(hours=hours)).isoformat(),
            "deferred": _deferred(now, hours, prices), "risk": _risk(base, hours, crit, behind),
            "ready": not short, "blockers": short, "warnings": [], "unknown": [parts["note"]] if parts.get("unknown") else [],
            "clashes": _plan_clashes(conn, visit["id"], visit["start_at"], visit["end_at"]), "available": True,
            "person": visit.get("person"), "with_exception": visit.get("exception"),
            "operator": visit.get("person") or visit.get("operator"),
            "basis": visit.get("basis") or f"{visit['kind'].replace('_', ' ')} on the plan on its equipment, then "
                                           f"{JOB_HOURS:g} h for the job"})
    else:
        options.append({"key": "window", "label": "Ride along with the next visit", "available": False,
                        "unavailable": f"nothing scheduled on its equipment in the next {days} days"})

    # shelve
    longest = int(settings.get(conn, "exceptions.max_shelve_days"))
    ta = _turnaround(conn, row, at)
    until = min(ta["start_at"], at + timedelta(days=longest)) if ta else at + timedelta(days=longest)
    safety = row["type"] in _safety_types(conn)
    held = None if safety else _no_shelf(conn, row, now, behind)
    hours = (until - at).total_seconds() / 3600 + JOB_HOURS
    to_ta = bool(ta) and until == ta["start_at"]
    shelve = {"key": "shelve", "label": "Shelve until the turnaround" if to_ta else f"Shelve for {(until - at).days} days",
              "detail": f"{until:%a %d %b}" + (f", {ta['title']}" if to_ta else ""),
              "until": until.isoformat(), "hours": round(hours, 1), "by": (at + timedelta(hours=hours)).isoformat(),
              "deferred": _deferred(now, hours, prices), "risk": _risk(base, hours, crit, behind),
              "ready": True, "blockers": [], "warnings": [], "clashes": [], "unknown": [],
              "available": not safety and not held,
              "basis": (f"the next {ta['kind']} of its area starts {ta['start_at']:%d %b}" if ta else
                        "no turnaround of its area on the plan") +
                       (f"; shelving is for at most {longest} days" if not to_ta else "")}
    if safety or held:
        shelve["unavailable"] = held or "a safety case type can't be shelved"
    options.append(shelve)

    # escalate
    to = _escalate_to(conn, row, field.get("waits_on") or [], viewer)
    if to is None:
        options.append({"key": "escalate", "label": "Escalate", "available": False,
                        "unavailable": "it would come back to you"})
    else:
        hours = _hours(conn, to, 24.0) + respond
        options.append({
            "key": "escalate", "label": f"Escalate to the {to.replace('_', ' ')}", "detail": "decides what happens next",
            "to": f"role:{to}", "hours": round(hours, 1), "by": (at + timedelta(hours=hours)).isoformat(),
            "deferred": _deferred(now, hours, prices), "risk": _risk(base, hours, crit, behind),
            "ready": True, "blockers": [], "warnings": [], "clashes": [], "unknown": [], "available": True,
            "basis": f"the {to.replace('_', ' ')} queue answers within {_hours(conn, to, 24.0):g} h, then a dispatch"})

    key, reason, deciding = recommend(options, now, float(settings.get(conn, "options.trip_usd")))
    return {**head, "applies": True, "why_not": None, "at": at.isoformat(), "loss": now, "if_it_trips": behind,
            "criticality": crit, "prices": {"oil_bbl": prices["oil_bbl"], "gas_mscf": prices["gas_mscf"],
                                            "source": prices["source"]},
            "options": options, "recommended": key, "reason": reason, "deciding": deciding, "unknown": unknown,
            "undetermined": undetermined_of(unknown, ready)}


def _arrives_by(blocker: str, start: datetime) -> bool:
    """A spares blocker whose purchase order is due before the visit starts."""
    import re

    m = re.search(r"due (\d{4}-\d{2}-\d{2})", blocker)
    return bool(m) and m.group(1) <= start.date().isoformat()


def _safety_types(conn) -> set[str]:
    from ..core import gates

    return set(gates.safety_case_types(conn))


def recommend(options: list[dict], now: dict | None, trip_usd: float) -> tuple[str | None, str, str]:
    """One option, by one rule, the reason and the factor that decided it (one of FACTORS). Riding along with the next
    visit is preferred while it leaves no high risk and defers less than a separate trip costs; dispatch now when
    waiting costs more or leaves the risk high; escalate when it should go now and can't. Shelve is never recommended
    (#1390): a person may choose it, but a shelf leaves the work undone, and no rule on the record can say a defect is
    safe to leave. When it can't go now and an escalation would come back to whoever reads it, none: the blocker is
    theirs to clear."""
    from ..core.wording import money

    by = {o["key"]: o for o in options if o.get("available")}
    d = by["dispatch"]
    usd = lambda o: (o.get("deferred") or {}).get("usd") or 0
    level = lambda o: (o.get("risk") or {}).get("level")
    waiting = [o for o in (by.get("window"), by.get("shelve")) if o]
    # riding along with a visit already going there is not a wait anyone needs a criticality to judge
    w = by.get("window")
    if w and w["ready"] and level(w) != "High" and (saved := usd(w) - usd(d)) <= trip_usd:
        risk = (f"waiting leaves the risk {level(w).lower()}" if level(w)
                else "the risk of waiting is not known (no criticality on record)")
        cost = (f"{_money(saved)} more deferred than going now, less than a separate trip "
                f"({money(trip_usd)})" if now and saved > 0 else "nothing deferred while it waits")
        if w.get("person"):
            return "window", f"{w['person']} is already there: riding along does it with {cost}; {risk}.", "readiness"
        return "window", f"The visit on {w['detail'].rsplit(', ', 1)[-1]} does it with {cost}; {risk}.", "deferred"
    alt = min(waiting, key=lambda o: (usd(o), o["hours"])) if waiting else None
    if d["ready"]:
        why, deciding = [], None
        if alt and now:
            deciding = "deferred"
            name = (f"the visit on {alt['detail'].rsplit(', ', 1)[-1]}" if alt["key"] == "window"
                    else f"{round(alt['hours'] / 24)} days")
            wait = f"for {name}" if alt["key"] == "window" else name
            why.append(f"Waiting {wait} defers {_bbl(alt)} ({_money(usd(alt))}) against {_bbl(d)} "
                       f"({_money(usd(d))}) if it goes now")
        if any(level(o) == "High" for o in waiting):
            why.append("waiting leaves the risk high" if why else "Waiting leaves the risk high")
            deciding = deciding or "risk"
        if not deciding and d.get("risk") is None:  # a risk word shows beside it only when the record backs one
            why.append("The risk of waiting is not known (no criticality on record)")
            deciding = "risk"
        why.append("nothing blocks it")
        return "dispatch", "; ".join(why) + ".", deciding or "readiness"
    blocked = (d["blockers"] or d["clashes"] or ["not ready"])[0]
    if "window" in by and by["window"]["ready"] and level(by["window"]) != "High":
        return "window", f"It can't go now ({blocked}); the next visit can take it.", "readiness"
    high = " and waiting leaves the risk high" if any(level(o) == "High" for o in waiting) else ""
    if "escalate" not in by:
        return None, f"It can't go now ({blocked}){high}: clear that, then dispatch it.", "readiness"
    to = by["escalate"]["label"].removeprefix("Escalate to ")
    return "escalate", f"It can't go now ({blocked}){high}: {to} decides the way round.", "readiness"


def about(v: float) -> float:
    """An estimate to two significant figures: a test rate times hours claims no more (as Plan shows at-risk)."""
    if not v:
        return 0
    import math

    p = 10 ** max(0, math.floor(math.log10(abs(v))) - 1)
    return round(v / p) * p


def _money(v: float) -> str:
    from ..core.wording import money

    return money(about(v))


def _bbl(o: dict) -> str:
    d = o.get("deferred") or {}
    return f"about {_n(about(d.get('oil_bbl') or 0))} bbl"


# ---- carrying out a choice ---------------------------------------------------------------------------------------

ASK = "Go and have a look, report what you find"  # what a dispatch asks when the IOC writes nothing (as the sample crew)


def _ask(conn, exception_id: int) -> str:
    """What the field is asked when the IOC writes nothing: the message the case came from, word for word, so the
    field reads what was reported (#1381); else ASK."""
    from . import messages

    return messages.text_of(conn, exception_id) or ASK


def choose(conn, exception_id: int, by: str, option: str, reason: str | None = None, *, to: str | None = None,
           ask: str | None = None, due=None, priority: float | None = None, override: str | None = None) -> dict:
    """Carry out one of an event's options and record it: dispatch it (to a person or the operator queue, with what to
    do), put it on the plan with the next visit, shelve it, or send it to the engineer. Taking another
    option than the one recommended, or sending one that is not ready, asks for nothing more: a why is optional (#1347),
    and a dispatch that is not ready still names its blockers on the case. The choice, the recommendation and any reason
    are kept, on the case's history too, and published as exception.option_chosen."""
    from . import exceptions, plan, readiness

    if option not in KEYS:
        raise OptionsError(f"option must be one of {KEYS}")
    offered = for_exception(conn, exception_id)
    if not offered["applies"]:
        raise OptionsError(f"no options to choose from: {offered['why_not']}")
    o = next(x for x in offered["options"] if x["key"] == option)
    if not o.get("available"):
        raise OptionsError(f"{o['label']} is not open: {o.get('unavailable')}")
    reason = (reason or "").strip()
    rec = offered["recommended"]
    # the recommendation's reason stands for a choice that follows it; another choice without one says none was given
    why = reason or (offered["reason"] if option == rec else "none given")
    eid = offered["exception_id"]
    if option == "dispatch":
        ready = readiness.for_exception(conn, eid)
        override = (override or "").strip() or why
        # The field reads only what to check or do (#1344). Why it goes now, the IOC's or the recommendation's, is kept
        # on the case's history below, not sent as the instruction.
        told = (ask or "").strip() or _ask(conn, eid)
        row = exceptions.dispatch(conn, eid, by, to or "role:operator", told,
                                  override=override if not ready["ready"] else None, blockers=ready["blockers"])
        if row["item_id"] and due:
            work.set_due(conn, row["item_id"], by, due, "wanted back by then when dispatched")
        if row["item_id"] and priority is not None:
            work.set_priority(conn, row["item_id"], by, priority, "set when dispatched")
    elif option == "window":
        # A field step for whoever goes on the visit, asking what was reported (#1390): someone already at the asset
        # takes it with them now; a visit on the plan takes it on its day, booked to its operator, else the operator
        # queue. Never only a bar on the plan with the case left at the desk.
        said = _ask(conn, eid)
        told = (ask or "").strip() or f"While you are there: {said[0].lower()}{said[1:]}"
        to = o.get("operator") or f"role:{exceptions.OPERATOR_ROLE}"
        if o.get("person"):
            row = exceptions.dispatch(conn, eid, by, to, told)
        else:
            from ..core.plan import PlanError

            start = datetime.fromisoformat(o["start"])
            try:
                plan.plan_exception(conn, eid, by, start, start + timedelta(hours=JOB_HOURS), crew=to,
                                    notes=f"Rides along with {o['detail']}. Why: {why}")
            except PlanError as e:
                raise OptionsError(str(e)) from e
            row = exceptions.dispatch(conn, eid, by, to, told, planned=True)
            if row["item_id"]:
                work.set_due(conn, row["item_id"], by, start + timedelta(hours=JOB_HOURS),
                             f"rides along with {o['detail']}")
    elif option == "shelve":
        row = exceptions.shelve(conn, eid, by, why, o["until"])
    else:
        row = exceptions.dispatch(conn, eid, by, o["to"], f"Escalated: {why}")
    row = _event(conn, eid)
    accepted = option == rec
    rec_label = next((x["label"] for x in offered["options"] if x["key"] == rec), None)
    kept = {k: offered[k] for k in ("loss", "if_it_trips", "criticality", "recommended", "reason", "deciding",
                                    "unknown")}
    kept["options"] = [{k: x.get(k) for k in ("key", "label", "detail", "hours", "deferred", "risk", "ready")}
                       for x in offered["options"] if x.get("available")]
    d = conn.execute(
        """
        insert into option_choice (exception_id, item_id, type, chosen, recommended, accepted, reason, by, offered)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning *
        """, (eid, row["item_id"], row["type"], option, rec, accepted, reason or None, by, Jsonb(kept))).fetchone()
    if row["item_id"] and offered["undetermined"]:  # chosen without them: kept for what would have answered them
        from . import undetermined

        undetermined.keep(conn, offered["undetermined"], "system", item_id=row["item_id"],
                            value_usd=undetermined.at_stake(conn, row["item_id"]) or (o.get("deferred") or {}).get("usd"))
    if row["item_id"]:
        cost = (o.get("deferred") or {}).get("usd")
        text = (f"Chose {o['label'].lower()}" + (f" ({o['detail']})" if o.get("detail") else "")
                + (f", deferring about {_money(cost)}" if cost else "")
                + (", as recommended" if accepted
                   else f" instead of the recommended {rec_label.lower()}" if rec_label else "")
                + f". Why: {why}")
        work.comment(conn, row["item_id"], by, text)
    events.publish(conn, "exception.option_chosen", f"exception:{eid}",
                   {"id": eid, "item_id": row["item_id"], "chosen": option, "recommended": rec, "accepted": accepted,
                    "reason": reason or None, "by": by})
    return {"choice": d, "event": {k: row[k] for k in ("id", "item_id", "stage", "state")}}


def decisions(conn, exception_id: int) -> list[dict]:
    """The choices made on an event, newest first."""
    return conn.execute("select id, chosen, recommended, accepted, reason, by, at from option_choice "
                        "where exception_id = %s order by id desc", (exception_id,)).fetchall()


def acceptance(conn, days: int = 90) -> dict:
    """How often the recommended option was taken over the last `days`, per work type and overall: the measure of
    whether the recommendations are any good."""
    from . import exceptions

    rows = conn.execute(
        """
        with c as (select type, chosen, count(*) as n, count(*) filter (where accepted) as a from option_choice
                   where at > now() - make_interval(days => %s) group by type, chosen)
        select type, sum(n)::int as decided, sum(a)::int as accepted, jsonb_object_agg(chosen, n) as chosen
        from c group by type order by sum(n) desc, type
        """, (days,)).fetchall()
    labels = {n: t.label for n, t in exceptions.types(conn).items()}
    out = [{"type": r["type"], "label": labels.get(r["type"], r["type"].replace("_", " ")), "decided": r["decided"],
            "accepted": r["accepted"], "rate": round(r["accepted"] / r["decided"], 2) if r["decided"] else None,
            "chosen": r["chosen"] or {}} for r in rows]
    decided = sum(r["decided"] for r in out)
    taken = sum(r["accepted"] for r in out)
    return {"days": days, "decided": decided, "accepted": taken,
            "rate": round(taken / decided, 2) if decided else None, "types": out}


def undetermined_of(unknown: list[str], ready: dict) -> list[dict]:
    """What the options couldn't settle (P6.6), typed: readiness's own (parts with no stock source), and each line of
    `unknown` with what would have answered it."""
    from . import undetermined

    out = list(ready.get("undetermined") or [])
    for line in unknown:
        if line.startswith("criticality"):
            out.append(undetermined.note("How critical are its assets?", "no_data", "source", "criticality assessment",
                                         line, "options"))
        else:
            out.append(undetermined.note("What production does it defer?", "no_data", "source",
                                         "well tests and flow network", line, "options"))
    return out
