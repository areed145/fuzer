"""How the work is running (P7.2a, #660): five weeks of measures (this week and the last four) for every queue and
every work type, from the work history, so an ops lead can see whether a queue keeps up and open the cases behind any
number.

    arrivals      cases opened in the week
    closed        cases closed in the week
    backlog       cases still open at the week's end (now, for this week)
    triage_h      median hours from opening to the first thing a person did on it, for the week's arrivals
    dispatch_h    median hours from opening to its first hand-off, for the week's arrivals that were handed off
    close_h       median hours from opening to closing, for the week's closed cases
    sla_misses    person steps due in the week that finished late or are still open past due
    escalations   escalations in the week
    hand_backs    hand-offs back to whoever sent it, in the week
    repeats       cases reopened as a repeat in the week (P6.1b)

A case's queue is its role, else the role of its first person step, else "unrouted". A count carries the case ids
behind it (up to 200). Schedule compliance is the maintenance plan's on-time share (#373).
"""

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median

from ..graph import operations

WEEKS = 5
COUNTS = ("arrivals", "closed", "backlog", "sla_misses", "escalations", "hand_backs", "repeats")
TIMES = ("triage_h", "dispatch_h", "close_h")
IDS = 200
SYSTEM = {"fuzer", "system", "scheduler"}
NOT_TRIAGE = {"created", "started", "step_started", "timer_fired", "auto_assigned", "adopted", "migrated", "message",
              "gated", "agent_failed", "failed"}


def _human(actor: str | None) -> bool:
    return bool(actor) and actor not in SYSTEM and not actor.startswith("agent:")


def _queue(role: str | None) -> str:
    return (role or "").removeprefix("role:") or "unrouted"


def weeks(now: datetime) -> list[tuple[datetime, datetime]]:
    """The last WEEKS weeks, Monday to Monday, oldest first; this week ends now."""
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    out = [(start - timedelta(weeks=k), start - timedelta(weeks=k - 1)) for k in range(WEEKS - 1, 0, -1)]
    return [*out, (start, now)]


def _cases(conn, since: datetime) -> list[dict]:
    return conn.execute(
        """
        select i.id, i.type, i.created_at, i.closed_at, i.created_by,
               coalesce(i.role, (select t.role from work_token t where t.item_id = i.id and t.kind in ('user', 'fix')
                                 and t.role is not null order by t.id limit 1)) as role,
               (select min(e.at) from work_event e where e.item_id = i.id and e.kind = 'handed_off') as dispatched_at,
               (select array_agg(e.at order by e.id) from work_event e where e.item_id = i.id
                and not (e.kind = any(%(skip)s))
                and e.actor is not null and e.actor <> all(%(system)s) and e.actor not like 'agent:%%') as touched
        from work_item i where i.closed_at is null or i.closed_at >= %(since)s
        """, {"since": since, "skip": list(NOT_TRIAGE), "system": list(SYSTEM)}).fetchall()


def _events(conn, since: datetime) -> list[dict]:
    """Escalations, hand-backs, SLA misses and repeats since `since`, each with its case and when."""
    rows = [dict(r) for r in conn.execute(
        """
        select 'escalations' as what, e.item_id, e.at from work_event e where e.kind = 'escalated' and e.at >= %(s)s
        union all
        select 'hand_backs', e.item_id, e.at from work_event e where e.kind = 'handed_off' and e.at >= %(s)s
          and exists (select 1 from work_event b where b.item_id = e.item_id and b.kind = 'handed_off' and b.id < e.id
                      and b.actor = e.data->>'to' and b.data->>'to' = e.actor)
        union all
        select 'sla_misses', t.item_id, t.due_at from work_token t where t.kind in ('user', 'fix') and t.due_at >= %(s)s
          and t.due_at < now() and (t.finished_at > t.due_at or (t.finished_at is null and t.state in ('ready', 'waiting')))
        """, {"s": since})]
    if conn.execute("select to_regclass('case_repeat') is not null as ok").fetchone()["ok"]:
        rows += [{"what": "repeats", "item_id": r["first"], "at": r["at"]} for r in conn.execute(
            "select first, at from case_repeat where at >= %s", (since,))]
    return rows


def _empty() -> dict:
    return {**{k: {"n": 0, "ids": []} for k in COUNTS}, **{k: None for k in TIMES}}


def _add(cell: dict, what: str, item_id: int) -> None:
    cell[what]["n"] += 1
    if len(cell[what]["ids"]) < IDS and item_id not in cell[what]["ids"]:
        cell[what]["ids"].append(item_id)


def _hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600


def measures(conn, now: datetime | None = None) -> dict:
    """Five weeks of measures per queue and per work type, with a keeping-up verdict per queue."""
    now = now or conn.execute("select now() as t").fetchone()["t"]
    span = weeks(now)
    since = span[0][0]
    cases = _cases(conn, since)
    by_id = {c["id"]: c for c in cases}
    out = {"queue": defaultdict(lambda: [_empty() for _ in span]), "type": defaultdict(lambda: [_empty() for _ in span])}
    times = {"queue": defaultdict(lambda: [defaultdict(list) for _ in span]),
             "type": defaultdict(lambda: [defaultdict(list) for _ in span])}

    def week_of(at: datetime) -> int | None:
        return next((k for k, (a, b) in enumerate(span) if a <= at < b or (k == len(span) - 1 and a <= at <= b)), None)

    for c in cases:
        keys = {"queue": _queue(c["role"]), "type": c["type"]}
        for k, (a, b) in enumerate(span):
            if c["created_at"] < b and (c["closed_at"] is None or c["closed_at"] >= b):
                for dim, key in keys.items():
                    _add(out[dim][key][k], "backlog", c["id"])
        if (k := week_of(c["created_at"])) is not None:
            first = next((t for t in (c["touched"] or []) if t >= c["created_at"]), None)
            for dim, key in keys.items():
                _add(out[dim][key][k], "arrivals", c["id"])
                if first:
                    times[dim][key][k]["triage_h"].append(_hours(c["created_at"], first))
                if c["dispatched_at"]:
                    times[dim][key][k]["dispatch_h"].append(_hours(c["created_at"], c["dispatched_at"]))
        if c["closed_at"] and (k := week_of(c["closed_at"])) is not None:
            for dim, key in keys.items():
                _add(out[dim][key][k], "closed", c["id"])
                times[dim][key][k]["close_h"].append(_hours(c["created_at"], c["closed_at"]))
    for e in _events(conn, since):
        c = by_id.get(e["item_id"])
        if c is None or (k := week_of(e["at"])) is None:
            continue
        for dim, key in (("queue", _queue(c["role"])), ("type", c["type"])):
            _add(out[dim][key][k], e["what"], c["id"])
    for dim, rows in out.items():
        for key, cells in rows.items():
            for k, cell in enumerate(cells):
                for t, xs in times[dim][key][k].items():
                    cell[t] = round(median(xs), 1) if xs else None
    return {"weeks": [{"start": a.isoformat(), "end": b.isoformat()} for a, b in span],
            "queues": [{"queue": q, "weeks": cells, **verdict(cells)} for q, cells in sorted(out["queue"].items())],
            "types": [{"type": t, "weeks": cells, **verdict(cells)} for t, cells in sorted(out["type"].items())],
            "compliance": _compliance(conn)}


def verdict(cells: list[dict]) -> dict:
    """Is it keeping up this week against the last four? Keeping up: it closed at least what arrived, and its backlog
    is no more than a tenth above the four-week average. Falling behind: neither. Otherwise holding."""
    now, before = cells[-1], cells[:-1]
    avg = sum(c["backlog"]["n"] for c in before) / len(before) if before else 0
    closing = now["closed"]["n"] >= now["arrivals"]["n"]
    backlog_ok = now["backlog"]["n"] <= avg * 1.1 or now["backlog"]["n"] == 0
    state = "keeping_up" if closing and backlog_ok else "falling_behind" if not closing and not backlog_ok else "holding"
    return {"keeping_up": state, "backlog_avg4": round(avg, 1)}


def _compliance(conn) -> dict | None:
    out = operations.maintenance_compliance(conn, limit=0)
    return {"as_of": out["as_of"], "summary": out["summary"]} if out["summary"] else None
