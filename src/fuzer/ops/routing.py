"""The front door for work (#509): one routing table and one `route()` that opens the case for anything that arrives.

    routes(conn)                     the routing table: one row per type of work, wherever the type is declared
    route_for(conn, type)            one row of it
    route(conn, intake, by, ...)     open (or feed) the case that works an intake; returns its work item id or None
    open_task(conn, case_type, ...)  open the case for work no exception raised (an opportunity, a posted task)
    adopt_open_cases(conn)           move cases opened plain before every kind of work ran as a process onto one
    due(conn, intake)                when the case is due, by the type's due policy
    explain(conn, type, refs, ...)   dry-run an intake: what it matches, whether it joins, where it goes, who holds it

Phase 1 of #509 wraps what Fuzer already does, unchanged: a type is declared in code (`exception_type()`), as data
(`exception_kind`) or by a source pack (data with its `source_system`), and its row here reads the same fields. A
type either starts (or feeds) a published BPMN process, opens a case in a role's queue, or waits for the IOC to
dispatch it. Since phase 2 a queue type runs as the standard `queue_item` process; phase 3 put the shift handover and
the source close-out on their own processes; phase 4 made the due policy a field of every type (`due_hours`, a
priority-to-hours map, or for SAP notifications the `intake.sap_notifications.due_hours` setting, #494), applied when
the IOC dispatches one, and sends opportunities, assistant tasks and `POST /work/items` through the `task` process.

This sits in `ops` because the layer contract keeps operations from importing the work engine: it reaches the engine
only through the Work port (`fuzer.core.work`), and it is the one module in `ops` that opens a case for an
exception (`fuzer.ops.exceptions` hands it every intake).
"""

from datetime import datetime, timedelta

from ..core import settings, work
from ..core.exceptions import TYPES
from ..core.findings import KEY as FINDING


def _hours_map(text) -> dict[str, float]:
    out = {}
    for part in str(text or "").split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                continue
    return out


def _due_source(t) -> tuple[str | None, str | None]:
    """Where a type's due policy is kept: (setting, None) for a code type that names a setting, (None, map) for a
    map written on the type, (None, None) for no policy."""
    if not t or not t.due_hours:
        return None, None
    return (None, t.due_hours) if ":" in t.due_hours else (t.due_hours, None)


def _due_hours(conn, t) -> dict[str, float] | None:
    """A type's priority -> hours map (#509: one policy for every type; SAP's is its type's row, read from the
    `intake.sap_notifications.due_hours` setting), or None when the type has no due policy."""
    setting, text = _due_source(t)
    if setting:
        text = settings.get(conn, setting)
    return _hours_map(text) if (setting or text) else None


def _due_policy(conn, t) -> dict | None:
    setting, _ = _due_source(t)
    hours = _due_hours(conn, t)
    if hours is None:
        return None
    return {"by": "priority", "hours": hours, "setting": setting, "from": "raised", "applies": "when dispatched"}


def _starts(t) -> str:
    """What arriving work of a type does: starts (or feeds) a process, opens a case in a queue, or waits on the board."""
    return "process" if t.process else "queue" if t.role else "dispatch"


def routes(conn) -> list[dict]:
    """The routing table: every type of work, where it is declared, how it joins one event's case, what it starts and
    when it is due. Types declared in code win over data of the same name, as they always have."""
    from . import exceptions

    brought = {r["name"]: r["source_system"] for r in conn.execute("select name, source_system from exception_kind")}
    join_hours = settings.get(conn, "exceptions.join_hours")
    standard = queue_process(conn)
    out = []
    for name, t in sorted(exceptions.types(conn).items()):
        declared = "code" if name in TYPES else ("source pack" if brought.get(name) else "data")
        out.append({
            "type": name, "label": t.label, "description": t.description,
            "declared_in": declared, "source_system": None if name in TYPES else brought.get(name),
            "joins": t.joins, "join_hours": join_hours if t.joins else None,
            "starts": _starts(t), "process": t.process or (QUEUE_PROCESS if t.role and standard else None), "role": t.role.removeprefix("role:") if t.role else None,
            "case_type": t.case_type or name, "audience": list(t.audience),
            "close_case_on_clear": t.close_case_on_clear, "due": _due_policy(conn, t),
        })
    return out


def route_for(conn, type: str) -> dict:
    """One type's row of the routing table; ExceptionError for a type nobody declared."""
    from . import exceptions

    exceptions._type(conn, type)
    return next(r for r in routes(conn) if r["type"] == type)


def due(conn, intake: dict) -> datetime | None:
    """When an intake's case is due by its type's policy: the hours its priority maps to, from when Fuzer raised it,
    on today's clock (#494, #509). None for a type with no policy, an unmapped priority or the policy turned off."""
    from . import exceptions

    hours = _due_hours(conn, exceptions.types(conn).get(intake.get("type")))
    if not hours:
        return None
    p = str(_data(intake).get("priority") or "").strip()
    p = p if p in hours else p[:1]  # SAP's "1 - very high" reads as 1
    if not (hours and p and intake.get("first_seen")):
        return None
    return intake["first_seen"] + timedelta(hours=hours[p]) if p in hours else None


def _data(intake: dict) -> dict:
    """The variables an intake's process routes on: its evidence without the finding record, which is for people
    (and read from the exception, core.findings.shown), not for gateways."""
    return {k: v for k, v in (intake.get("evidence") or {}).items() if k != FINDING}


QUEUE_PROCESS = "queue_item"


def published(conn, process_key: str) -> bool:
    """Whether a process is published here (loading the starting processes publishes Fuzer's own)."""
    return bool(conn.execute("select 1 from work_definition where process_key = %s and state = 'published' limit 1",
                             (process_key,)).fetchone())


def queue_process(conn) -> bool:
    """Whether the standard queue_item process is published here."""
    return published(conn, QUEUE_PROCESS)


def open_process_case(conn, process_key: str, data: dict, by: str, title: str, plain: dict,
                      subjects: list[str] | None = None, priority: float | None = None, lane: str | None = None) -> int:
    """The case for work Fuzer itself raises (a shift handover, a close-out to approve, #509): its process when
    published, so it has its lane's SLA, escalation and a place on the monitor; else a plain case with the `plain`
    fields (role, source, payload), as before the starting processes are loaded, or when its `lane` (the role a
    `=role` lane takes from the data) is no person role."""
    if published(conn, process_key) and (lane is None or lane in work.person_roles(conn)):
        return work.start_or_correlate(conn, process_key, data, by=by, title=title, subjects=subjects,
                                       priority=priority)["item_id"]
    return work.open_case(conn, process_key, title, by, subjects=subjects, priority=priority, **plain)["id"]


TASK_PROCESS = "task"


def open_task(conn, case_type: str, title: str, by: str, key: str, role: str | None = None,
              assignee: str | None = None, data: dict | None = None, subjects: list[str] | None = None,
              priority: float | None = None, due: datetime | None = None, source: str | None = None) -> dict:
    """Work asked of a queue that no exception raised (#509): an opportunity the scan finds, a task someone posts.
    In a person role's queue it runs as the standard `task` process, so it has the role's SLA, escalation and a place
    on the monitor; `key` names it (one open task per key). For a named person, a queue no person role backs, or
    before the starting processes are loaded, it opens a plain case as before. Returns the case's work item."""
    lane = role.removeprefix("role:") if role else None
    if lane and not assignee and published(conn, TASK_PROCESS) and lane in work.person_roles(conn):
        start = {**(data or {}), "task_key": key, "role": lane, "case_type": case_type, "source": source or by}
        item_id = work.start_or_correlate(conn, TASK_PROCESS, start, by=by, title=title, subjects=subjects,
                                          priority=priority)["item_id"]
        if due:
            work.set_due(conn, item_id, by, due, reason="due as asked")
        return conn.execute("select * from work_item where id = %s", (item_id,)).fetchone()
    return work.open_case(conn, case_type, title, by, subjects=subjects, priority=priority, assignee=assignee,
                          role=lane, due=due, source=source, payload=data or {})


def queue_data(t, intake: dict, queue: str) -> dict:
    """What queue_item starts with: its lane's role (`=role`) and its case's type (`=case_type`) come from the type."""
    return {"exception_id": intake.get("id"), "type": t.name, "key": intake.get("key"),
            "role": queue.removeprefix("role:"), "case_type": t.case_type or t.name, "source": intake.get("source")}


def queue_case(conn, item_id: int | None) -> bool:
    """A case worked by the standard queue_item process: it closes and reports back as a plain queue case does."""
    return bool(item_id) and bool(conn.execute(
        "select 1 from work_process where item_id = %s and process_key = %s limit 1", (item_id, QUEUE_PROCESS)).fetchone())


def route(conn, intake: dict, by: str, assignee: str | None = None, role: str | None = None,
          to: str | None = None, reason: str | None = None) -> int | None:
    """Open the case that works an intake and return its work item id, or None when it waits for the IOC.

    The intake is what arrived: `id` (its exception), `type`, `key`, `title`, `source`, `subjects`, `priority`,
    `evidence` and `first_seen`. With `to` (a person or `role:<name>`) the IOC is dispatching it: the case opens for
    them, due by the type's policy, with the reason on its history. Otherwise the type decides: its process is started
    or fed the evidence, or the standard queue_item process starts in its queue (or `role`'s when the caller names
    one), so the case has the queue's SLA and escalation. A case for a named `assignee`, and every case where
    queue_item isn't published yet, opens as a plain work item."""
    from . import exceptions

    t = exceptions.types(conn).get(intake["type"]) or exceptions.ExceptionType(intake["type"], intake["type"])
    payload = {**_data(intake), "exception_id": intake["id"]}
    common = {"subjects": intake["subjects"], "priority": intake["priority"], "source": intake["source"],
              "payload": payload}
    if to and (item_id := _dispatched(conn, t, intake, by, to, reason)):
        return item_id
    if to:
        queue = to.startswith("role:")
        return work.open_case(conn, t.case_type or t.name, intake["title"], by, due=due(conn, intake),
                              assignee=None if queue else to, role=to.removeprefix("role:") if queue else None,
                              reason=reason, **common)["id"]
    if t.process:
        data = {"exception_id": intake["id"], "type": t.name, "key": intake["key"], **_data(intake)}
        return work.start_or_correlate(conn, t.process, data, by=by, title=intake["title"],
                                       subjects=intake["subjects"], priority=intake["priority"])["item_id"]
    if not (assignee or role or t.role):
        return None
    queue = None if assignee else (role or t.role)
    if queue and queue_process(conn) and queue.removeprefix("role:") in work.person_roles(conn):  # the standard process: the queue's SLA, escalation and monitor (#509)
        data = {**_data(intake), **queue_data(t, intake, queue)}
        return work.start_or_correlate(conn, QUEUE_PROCESS, data, by=by, title=intake["title"],
                                       subjects=intake["subjects"], priority=intake["priority"])["item_id"]
    return work.open_case(conn, t.case_type or t.name, intake["title"], by, assignee=assignee,
                          role=queue.removeprefix("role:") if queue else None, **common)["id"]


def _dispatch_lane(conn, t, to: str) -> str | None:
    """The queue a dispatched case's step sits in: the role it was sent to; for a person, the type's queue when they
    hold it, else the first role they hold. None when no person role fits (the case then opens plain)."""
    known = work.person_roles(conn)
    if to.startswith("role:"):
        lane = to.removeprefix("role:")
        return lane if lane in known else None
    theirs = [r for r in work.roles_of(conn, to) if r in known]
    own = t.role.removeprefix("role:") if t.role else None
    return own if own in theirs else (theirs[0] if theirs else None)


def _dispatched(conn, t, intake: dict, by: str, to: str, reason: str | None) -> int | None:
    """A dispatch from All work as the queue_item process (#509): the step opens in the lane `to` belongs to and is
    handed to them by the dispatcher, with the reason, so a hand-back returns it to the dispatcher; due by the type's
    policy when it has one, else by the role's SLA. None when queue_item isn't published or no person role fits."""
    lane = _dispatch_lane(conn, t, to) if queue_process(conn) else None
    if not lane:
        return None
    data = {**_data(intake), **queue_data(t, intake, lane)}
    item_id = work.start_or_correlate(conn, QUEUE_PROCESS, data, by=by, title=intake["title"],
                                      subjects=intake["subjects"], priority=intake["priority"])["item_id"]
    if not to.startswith("role:") and not by.startswith("agent:") and by != to:
        work.dispatch(conn, item_id, by, by, f"to send it to {to}")  # the IOC holds it, then sends it
    work.dispatch(conn, item_id, by, to, reason)
    if when := due(conn, intake):
        work.set_due(conn, item_id, by, when, reason="due by its priority")
    return item_id


def _case_summary(conn, row: dict) -> dict:
    case = conn.execute("select id, title, state, assignee, role from work_item where id = %s",
                        (row["item_id"],)).fetchone() if row.get("item_id") else None
    return {"exception_id": row["id"], "type": row["type"], "title": row["title"], "source": row["source"],
            "case": dict(case) if case else None}


def _holders(conn, role: str | None) -> list[str]:
    return work.role_holders(conn, role) if role else []


def explain(conn, type: str, refs=(), key: str | None = None, evidence: dict | None = None,
            to: str | None = None) -> dict:
    """Dry-run an intake without writing anything: the entities its refs match, the open exception it would update
    or the event's case it would join, the process (each step's lane and who holds it) or queue it would go to, and
    when it would be due. `to` explains a dispatch to that person or `role:<name>` instead."""
    from . import exceptions

    with conn.transaction(force_rollback=True):  # reading roles may seed them; an explanation leaves nothing behind
        r = route_for(conn, type)
        t = exceptions.types(conn)[type]
        subjects, unmatched = exceptions.match(conn, refs)
        matched = [{"id": e["fuzer_id"], "label": e["label"] or e["fuzer_id"], "kind": e["kind"]}
                   for e in exceptions._assets(conn, subjects)]
        out = {"type": type, "route": r, "matched": matched, "unmatched": unmatched,
               "unmatched_note": exceptions._unmatched_note(conn, unmatched), "updates": None, "joins": None,
               "process": None, "queue": None, "due": None}
        current = conn.execute("select * from exception_record where type = %s and key = %s and state = 'open'",
                               (type, key)).fetchone() if key else None
        if current:
            out["updates"] = _case_summary(conn, current)
            out["outcome"] = "updates"
            out["summary"] = (f"{r['label']} {key} is already open (exception {current['id']}); this adds to it"
                              + (", and its process gets the new evidence." if t.process and not current["joined_to"]
                                 else "."))
            return out
        host = None if to else exceptions._host(conn, t, type, key or "", subjects)
        if host:
            out["joins"] = _case_summary(conn, host)
            out["outcome"] = "joins"
            out["summary"] = (f"Joins case {host['item_id']} ({host['title']}): {exceptions._label(conn, host['type'])} "
                              f"from {host['source']} is about the same asset, so it is one event.")
            return out
        intake = {"type": type, "evidence": evidence or {}, "first_seen": datetime.now().astimezone()}
        if to:
            queue = to.removeprefix("role:") if to.startswith("role:") else None
            out["queue"] = {"role": queue, "assignee": None if queue else to, "holders": _holders(conn, queue)}
            when = due(conn, intake)
            out["due"] = when.isoformat() if when else None
            out["outcome"] = "dispatched"
            lane = _dispatch_lane(conn, t, to) if queue_process(conn) else None
            if lane:
                steps = work.process_routing(conn, QUEUE_PROCESS, queue_data(t, {"key": key}, lane)) or []
                out["process"] = {"key": QUEUE_PROCESS, "published": True, "lane": lane, "steps": steps}
                sla = next((step["sla"] for step in steps if step["sla"]), None)
                when_text = f", due {when:%Y-%m-%d %H:%M}" if when else (f", due within its SLA ({sla})" if sla else "")
                out["summary"] = (f"Opens a {r['case_type'].replace('_', ' ')} case in the {lane} lane and hands it "
                                  f"to {to}{when_text}.")
            else:
                out["summary"] = f"Opens a {r['case_type'].replace('_', ' ')} case for {to}" + (
                    f", due {when:%Y-%m-%d %H:%M}." if when else ".")
            return out
        if t.process:
            steps = work.process_routing(conn, t.process, {"type": type, "key": key, **(evidence or {})})
            for s in steps or []:
                s["holders"] = _holders(conn, s["role"]) if s["kind"] == "person" else []
            out["process"] = {"key": t.process, "published": steps is not None, "steps": steps or []}
            first = next((s for s in steps or [] if s["kind"] == "person"), None)
            out["outcome"] = "process"
            if steps is None:
                out["summary"] = f"Would start the {t.process} process, but no version of it is published."
            elif first:
                who = f" ({', '.join(first['holders'])})" if first["holders"] else ", which nobody holds"
                out["summary"] = (f"Starts the {t.process} process; its first person step, {first['step']}, waits in "
                                  f"the {first['role']} queue{who}.")
            else:
                out["summary"] = f"Starts the {t.process} process."
            return out
        if t.role:
            role = t.role.removeprefix("role:")
            holders = _holders(conn, role)
            out["queue"] = {"role": role, "assignee": None, "holders": holders}
            if queue_process(conn):
                steps = work.process_routing(conn, QUEUE_PROCESS, queue_data(t, {"key": key}, role)) or []
                for step in steps:
                    step["holders"] = _holders(conn, step["role"]) if step["kind"] == "person" else []
                out["process"] = {"key": QUEUE_PROCESS, "published": True, "steps": steps}
            out["outcome"] = "queue"
            sla = next((step["sla"] for step in (out["process"] or {}).get("steps", []) if step["sla"]), None)
            out["summary"] = (f"Opens a {r['case_type'].replace('_', ' ')} case in the {role} queue"
                              + (f" ({', '.join(holders)})" if holders else ", which nobody holds")
                              + (f", due within its SLA ({sla})." if sla else "."))
            return out
        out["outcome"] = "dispatch"
        out["summary"] = "Waits under All work for the IOC to dispatch it: nothing routes this type on its own."
        return out


def _adoption(conn, item: dict, known: list[str], types: dict) -> tuple[str, dict] | str:
    """The process an open plain case moves onto and its start data, or why it stays plain."""
    pay = {k: v for k, v in (item["payload"] or {}).items() if k != "priority"}  # its priority stays the case's
    role = item["role"]
    if item["type"] == "shift_handover" and pay.get("handover_id") is not None:
        return ("shift_handover", {**pay, "source": "handover"}) if "ops_lead" in known else "no ops_lead role"
    if item["type"] == "source_closeout" and pay.get("closeout_id") is not None:
        return ("source_closeout", {**pay, "role": role, "source": item["source"]}) if role in known \
            else f"its role {role!r} is no person role"
    if pay.get("exception_id") is not None:
        ex = conn.execute("select id, type, key, source from exception_record where id = %s",
                          (pay["exception_id"],)).fetchone()
        t = types.get(ex["type"]) if ex else None
        if t is None:
            return "its exception or type is gone"
        if t.process:
            return f"its type starts {t.process}, which would begin again from the start"
        lane = role if role in known else (_dispatch_lane(conn, t, item["assignee"]) if item["assignee"] else None)
        if lane not in known:
            return "no person role fits it"
        return QUEUE_PROCESS, {**pay, **queue_data(t, dict(ex), lane)}
    if not role:
        return "it is for a named person"  # as a task posted for one is (open_task)
    if role not in known:
        return f"its role {role!r} is no person role"
    key = (f"opportunity:{pay['opportunity']}" if item["type"] == "opportunity" and pay.get("opportunity")
           else f"draft:{pay['draft']}" if pay.get("draft") else f"item:{item['id']}")
    return TASK_PROCESS, {**pay, "task_key": key, "role": role, "case_type": item["type"],
                          "source": item["source"] or item["created_by"]}


def adopt_open_cases(conn, by: str = "system") -> dict:
    """Move the cases opened as plain work items before routing ran every kind of work as a process (#509) onto the
    process route() now starts for them: a queue case onto queue_item, a handover and a close-out onto theirs, an
    opportunity, an assistant's task or a task asked of a role onto task. Whoever held each still holds it and its
    due date stays. Cases for a named person and ones whose type has its own process stay as they are. Idempotent:
    only open cases with no process are looked at. Returns how many moved onto each process, and why the rest
    stayed."""
    from . import exceptions

    out: dict = {"moved": {}, "stayed": {}, "failed": []}
    rows = conn.execute("select i.* from work_item i where i.state not in ('done', 'cancelled') and not exists "
                        "(select 1 from work_process p where p.item_id = i.id) order by i.id").fetchall()
    conn.commit()
    if not rows:
        return out
    known, types = work.person_roles(conn), exceptions.types(conn)
    for item in rows:
        how = _adoption(conn, item, known, types)
        if isinstance(how, str):
            out["stayed"][how] = out["stayed"].get(how, 0) + 1
            continue
        process_key, data = how
        if not published(conn, process_key):
            out["stayed"][f"{process_key} isn't published"] = out["stayed"].get(f"{process_key} isn't published", 0) + 1
            continue
        try:
            if work.attach_process(conn, item["id"], process_key, data, by):
                out["moved"][process_key] = out["moved"].get(process_key, 0) + 1
        except Exception as e:  # one case that can't move must not hold up the rest
            conn.rollback()
            out["failed"].append({"item_id": item["id"], "process": process_key, "error": str(e)})
    return out
