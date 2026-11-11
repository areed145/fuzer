"""The front door for work (#509): one routing table and one `route()` that opens the case for anything that arrives.

    routes(conn)                     the routing table: one row per type of work, wherever the type is declared
    route_for(conn, type)            one row of it
    route(conn, intake, by, ...)     open (or feed) the case that works an intake; returns its work item id or None
    due(conn, intake)                when the case is due, by the type's due policy
    explain(conn, type, refs, ...)   dry-run an intake: what it matches, whether it joins, where it goes, who holds it

Phase 1 of #509 wraps what Fuzer already does, unchanged: a type is declared in code (`exception_type()`), as data
(`exception_kind`) or by a source pack (data with its `source_system`), and its row here reads the same fields. A
type either starts (or feeds) a published BPMN process, opens a case in a role's queue, or waits for the IOC to
dispatch it; the one due policy is SAP notifications' priority-to-hours (`intake.sap_notifications.due_hours`, #494),
applied when the IOC dispatches one. Later phases make every type start a process and the due policy generic.

This sits in `ops` because the layer contract keeps operations from importing the work engine: it reaches the engine
only through the Work port (`fuzer.core.work`), and it is the one module in `ops` that opens a case for an
exception (`fuzer.ops.exceptions` hands it every intake).
"""

from datetime import datetime, timedelta

from ..core import settings, work
from ..core.exceptions import TYPES

DUE_SETTING = "intake.sap_notifications.due_hours"
DUE_TYPES = ("sap_notification",)  # the one due policy today (#494): SAP priority -> hours, from that setting


def _due_hours(conn, type: str) -> dict[str, float] | None:
    """A type's priority -> hours map, or None when the type has no due policy."""
    if type not in DUE_TYPES:
        return None
    out = {}
    for part in str(settings.get(conn, "intake.sap_notifications.due_hours") or "").split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                continue
    return out


def _due_policy(conn, name: str) -> dict | None:
    hours = _due_hours(conn, name)
    if hours is None:
        return None
    return {"by": "priority", "hours": hours, "setting": DUE_SETTING, "from": "raised", "applies": "when dispatched"}


def _starts(t) -> str:
    """What arriving work of a type does: starts (or feeds) a process, opens a case in a queue, or waits on the board."""
    return "process" if t.process else "queue" if t.role else "dispatch"


def routes(conn) -> list[dict]:
    """The routing table: every type of work, where it is declared, how it joins one event's case, what it starts and
    when it is due. Types declared in code win over data of the same name, as they always have."""
    from . import exceptions

    brought = {r["name"]: r["source_system"] for r in conn.execute("select name, source_system from exception_kind")}
    join_hours = settings.get(conn, "exceptions.join_hours")
    out = []
    for name, t in sorted(exceptions.types(conn).items()):
        declared = "code" if name in TYPES else ("source pack" if brought.get(name) else "data")
        out.append({
            "type": name, "label": t.label, "description": t.description,
            "declared_in": declared, "source_system": None if name in TYPES else brought.get(name),
            "joins": t.joins, "join_hours": join_hours if t.joins else None,
            "starts": _starts(t), "process": t.process, "role": t.role.removeprefix("role:") if t.role else None,
            "case_type": t.case_type or name, "audience": list(t.audience),
            "close_case_on_clear": t.close_case_on_clear, "due": _due_policy(conn, name),
        })
    return out


def route_for(conn, type: str) -> dict:
    """One type's row of the routing table; ExceptionError for a type nobody declared."""
    from . import exceptions

    exceptions._type(conn, type)
    return next(r for r in routes(conn) if r["type"] == type)


def due(conn, intake: dict) -> datetime | None:
    """When an intake's case is due by its type's policy: SAP priority hours from when Fuzer raised it, on today's
    clock (#494). None for a type with no policy, an unmapped priority or the setting turned off."""
    hours = _due_hours(conn, intake.get("type"))
    p = str((intake.get("evidence") or {}).get("priority") or "").strip()[:1]
    if not (hours and p and intake.get("first_seen")):
        return None
    return intake["first_seen"] + timedelta(hours=hours[p]) if p in hours else None


def route(conn, intake: dict, by: str, assignee: str | None = None, role: str | None = None,
          to: str | None = None, reason: str | None = None) -> int | None:
    """Open the case that works an intake and return its work item id, or None when it waits for the IOC.

    The intake is what arrived: `id` (its exception), `type`, `key`, `title`, `source`, `subjects`, `priority`,
    `evidence` and `first_seen`. With `to` (a person or `role:<name>`) the IOC is dispatching it: the case opens for
    them, due by the type's policy, with the reason on its history. Otherwise the type decides: its process is started
    or fed the evidence, or a case opens in its queue (or for `assignee` / `role` when the caller names one)."""
    from . import exceptions

    t = exceptions.types(conn).get(intake["type"]) or exceptions.ExceptionType(intake["type"], intake["type"])
    payload = {**(intake.get("evidence") or {}), "exception_id": intake["id"]}
    common = {"subjects": intake["subjects"], "priority": intake["priority"], "source": intake["source"],
              "payload": payload}
    if to:
        queue = to.startswith("role:")
        return work.open_case(conn, t.case_type or t.name, intake["title"], by, due=due(conn, intake),
                              assignee=None if queue else to, role=to.removeprefix("role:") if queue else None,
                              reason=reason, **common)["id"]
    if t.process:
        data = {"exception_id": intake["id"], "type": t.name, "key": intake["key"], **(intake.get("evidence") or {})}
        return work.start_or_correlate(conn, t.process, data, by=by, title=intake["title"],
                                       subjects=intake["subjects"], priority=intake["priority"])["item_id"]
    if not (assignee or role or t.role):
        return None
    queue = None if assignee else (role or t.role)
    return work.open_case(conn, t.case_type or t.name, intake["title"], by, assignee=assignee,
                          role=queue.removeprefix("role:") if queue else None, **common)["id"]


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
            out["summary"] = f"Opens a {r['case_type'].replace('_', ' ')} case for {to}" + (
                f", due {when:%Y-%m-%d %H:%M}." if when else ".")
            return out
        if t.process:
            steps = work.process_routing(conn, t.process)
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
            out["outcome"] = "queue"
            out["summary"] = (f"Opens a {r['case_type'].replace('_', ' ')} case in the {role} queue"
                              + (f" ({', '.join(holders)})." if holders else ", which nobody holds."))
            return out
        out["outcome"] = "dispatch"
        out["summary"] = "Waits under All work for the IOC to dispatch it: nothing routes this type on its own."
        return out
