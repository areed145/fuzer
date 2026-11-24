"""Routines (step 7 of docs/work-model.md): a standing rule that raises an obligation need each time it falls due.
One object covers SAP maintenance and inspection plans (SAP keeps the rule; Fuzer reads it) and routine duties kept in
Fuzer (sampling, well tests, chemical checks).

    sync_sap(conn)                 each SAP maintenance plan as a routine, read only
    create(conn, title, by, ...)   a routine kept in Fuzer
    raise_due(conn)                each occurrence within its lead time becomes a need, a case and a job; one done
                                   rolls its routine forward
    listing(conn)                  the routines, each with its next due date, open occurrence and whether it is late

A heavy occurrence (a PM due date) is one case with one job: the need is a `routine_due` exception, an obligation,
routed to the routine's owner role, and its job is the bar on the plan (for SAP, the bar the maintenance plan sync
already made). When the job is done (SAP rolled the plan past it, or the case closed) the occurrence is over. Rounds,
shift handovers and reports keep their own flows for now; they are routines in the model, and move here later.
"""

from datetime import date, datetime, timedelta

from psycopg.types.json import Jsonb

from ..core import clock, events
from ..core.exceptions import TYPES, clear_exception, exception_type, raise_exception

TYPE = "routine_due"
ANCHORS = ("fixed", "floating")
BY = "routines"


class RoutineError(ValueError):
    pass


def _type():
    return TYPES.get(TYPE) or exception_type(
        TYPE, "Routine due", role="role:maintenance_planner", audience=("ioc",), joins=False, need="obligation",
        description="A routine falling due: a maintenance or inspection plan's next date, or a routine duty kept in "
                    "Fuzer. Its job is on the plan; plan it, book it and release it. It closes when the job is done.")


_type()


def _today() -> date:
    return clock.today()


def sync_sap(conn, by: str = "sap") -> int:
    """Each SAP maintenance or inspection plan as a routine (SAP keeps the rule): interval, next due and targets."""
    n = 0
    for r in conn.execute(
        """
        select e.fuzer_id, jsonb_object_agg(g.field, g.value) as f,
               (select array_agg(distinct x.dst) from edge x where x.src = e.fuzer_id and x.rel = 'concerns') as concerns,
               (select min(k.key) from entity_key k where k.fuzer_id = e.fuzer_id and k.kind = 'maintenance_plan') as number,
               (select l.label from entity_label l where l.fuzer_id = e.fuzer_id) as label
        from entity e join golden g on g.fuzer_id = e.fuzer_id
        where e.kind = 'maintenance_plan' and e.retired_at is null
        group by e.fuzer_id
        """).fetchall():
        f = {k: v for k, v in r["f"].items() if v not in (None, "")}
        key = r["number"] or r["label"] or r["fuzer_id"]
        inspection = str(f.get("kind", "")).upper().startswith("INSP")
        try:
            due = date.fromisoformat(str(f.get("next_due"))[:10]) if f.get("next_due") else None
        except ValueError:
            due = None
        conn.execute(
            """
            insert into work_routine (code, title, source, source_key, interval_days, targets, template, next_due,
                                      created_by)
            values (%s, %s, 'sap', %s, %s, %s, %s, %s, %s)
            on conflict (code) do update set title = excluded.title, interval_days = excluded.interval_days,
                targets = excluded.targets, template = excluded.template, next_due = excluded.next_due,
                updated_at = now()
            """, (f"sap:{key}", str(f.get("task") or f.get("description") or f"Plan {key}"), key,
                  f["interval_days"] if isinstance(f.get("interval_days"), (int, float)) else None,
                  r["concerns"] or [r["fuzer_id"]],
                  Jsonb({"kind": "inspection" if inspection else "maintenance", "place": "field",
                         "needs_shutdown": f.get("needs_shutdown") == "X"}), due, by))
        n += 1
    return n


def create(conn, title: str, by: str, interval_days: float, next_due, targets=(), code: str | None = None,
           anchor: str = "fixed", template: dict | None = None, lead_days: float = 14, tolerance_days: float = 0,
           owner_role: str = "maintenance_planner") -> dict:
    """A routine kept in Fuzer: every `interval_days` from `next_due`, counted from the last due date (fixed) or the
    last completion (floating)."""
    if anchor not in ANCHORS:
        raise RoutineError(f"anchor must be one of {ANCHORS}")
    if not interval_days or interval_days <= 0:
        raise RoutineError("a routine needs an interval of more than 0 days")
    if not (title or "").strip():
        raise RoutineError("a routine needs a title")
    due = next_due if isinstance(next_due, date) else date.fromisoformat(str(next_due)[:10])
    row = conn.execute(
        """
        insert into work_routine (code, title, source, interval_days, anchor, targets, template, lead_days,
                                  tolerance_days, owner_role, next_due, created_by)
        values (%s, %s, 'fuzer', %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *
        """, (code or f"fuzer:{title.strip().lower().replace(' ', '-')[:50]}", title.strip(), interval_days, anchor,
              list(targets), Jsonb(template or {}), lead_days, tolerance_days, owner_role, due, by)).fetchone()
    events.publish(conn, "routine.changed", f"routine:{row['id']}", {"id": row["id"], "change": "created", "by": by})
    return row


def _occurrence(conn, routine: dict, due: date, bar: dict | None, by: str) -> dict:
    """Raise the need for one due date (or update it), its case in the owner's queue, and link its job."""
    from . import plan

    key = f"{routine['code']}:{due.isoformat()}"
    e = raise_exception(conn, TYPE, key, f"{routine['title']} (due {due:%d %b})", by, source=routine["source"],
                        refs=routine["targets"], role=f"role:{routine['owner_role']}")
    if bar is None:
        t = routine["template"] or {}
        start = clock.aware(datetime.combine(due, datetime.min.time()))
        bar = plan.propose(conn, t.get("kind") or "maintenance", routine["title"], by, start,
                           start + timedelta(hours=float(t.get("hours") or 8)), refs=routine["targets"],
                           source="routine", source_key=key, crew=t.get("crew"), exception_id=e["id"],
                           needs_shutdown=bool(t.get("needs_shutdown")), notes=t.get("instruction"))
        if t.get("place") == "desk":
            plan.update(conn, bar["id"], by, place="desk")
    elif not bar["exception_id"]:
        conn.execute("update plan_item set exception_id = %s where id = %s", (e["id"], bar["id"]))
    if e.get("item_id"):
        plan.link_case(conn, e["id"], e["item_id"])
    return e


def raise_due(conn, by: str = BY, horizon_days: float | None = None) -> dict:
    """Each occurrence within its routine's lead time becomes a need, a case and a job, once. An occurrence whose job
    is over (SAP rolled the plan past it, or someone closed the bar) clears; a Fuzer routine whose case closed rolls
    forward from its due date (fixed) or from today (floating). `horizon_days` looks further ahead than each routine's
    lead time (the loop eval, on a bundle whose next due dates are months out)."""
    _type()
    today = _today()
    raised = cleared = rolled = 0
    for r in conn.execute("select * from work_routine where active").fetchall():
        lead = timedelta(days=max(r["lead_days"], horizon_days or 0))
        if r["source"] == "sap":
            # SAP's next due date is a bar the maintenance plan sync made: that bar is the occurrence's job
            for bar in conn.execute(
                    "select * from plan_item where source = 'sap_plan' and regexp_replace(source_key, ':[^:]*$', '') = %s "
                    "and status in ('proposed', 'scheduled', 'in_progress') and start_at <= %s",
                    (r["source_key"], clock.aware(datetime.combine(today + lead, datetime.max.time())))):
                e = _occurrence(conn, r, bar["start_at"].date(), bar, by)
                raised += bool(e.get("raised"))
            continue
        if r["next_due"] and r["next_due"] - lead <= today:
            e = _occurrence(conn, r, r["next_due"], conn.execute(
                "select * from plan_item where source = 'routine' and source_key = %s",
                (f"{r['code']}:{r['next_due'].isoformat()}",)).fetchone(), by)
            raised += bool(e.get("raised"))
    # occurrences whose job is over: done there, so done here; a Fuzer routine rolls forward
    for o in conn.execute(
        """
        select e.key, e.state, p.status as bar_status, w.state as case_state
        from exception_record e left join plan_item p on p.exception_id = e.id
        left join work_item w on w.id = e.item_id
        where e.type = %s and (e.state = 'open' or e.closed_at >= now() - interval '1 day')
        """, (TYPE,)).fetchall():
        if o["state"] == "open" and o["bar_status"] in ("done", "cancelled"):
            cleared += clear_exception(conn, TYPE, o["key"], by, reason=f"its job is {o['bar_status']}") is not None
    for r in conn.execute("select * from work_routine where active and source = 'fuzer' and next_due is not null"):
        key = f"{r['code']}:{r['next_due'].isoformat()}"
        done = conn.execute("select 1 from exception_record where type = %s and key = %s and state <> 'open'",
                            (TYPE, key)).fetchone()
        if done:
            base = r["next_due"] if r["anchor"] == "fixed" else today
            nxt = base + timedelta(days=r["interval_days"])
            while r["anchor"] == "fixed" and nxt < today:  # missed ones are not made up: the next one ahead
                nxt += timedelta(days=r["interval_days"])
            conn.execute("update work_routine set last_done = %s, next_due = %s, updated_at = now() where id = %s",
                         (today, nxt, r["id"]))
            rolled += 1
    return {"raised": raised, "cleared": cleared, "rolled": rolled}


def listing(conn) -> list[dict]:
    """Every routine with its next due date, its open occurrence (need and case) and whether it is past tolerance."""
    today = _today()
    out = []
    for r in conn.execute(
        """
        select r.*, (select json_build_object('id', e.id, 'item_id', e.item_id, 'key', e.key) from exception_record e
                     where e.type = %s and e.state = 'open' and e.key like r.code || ':%%' order by e.id desc limit 1)
                    as open_occurrence
        from work_routine r order by r.next_due nulls last, r.id
        """, (TYPE,)).fetchall():
        late = bool(r["next_due"] and r["next_due"] + timedelta(days=r["tolerance_days"]) < today
                    and r["open_occurrence"])
        out.append({**r, "overdue": late})
    return out
