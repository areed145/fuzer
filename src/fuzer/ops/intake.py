"""Intake: records other systems already hold that are work the IOC should see, raised as exceptions.

An SAP notification is someone saying "this asset needs attention". While it is open and no work order has been
raised for it, nobody is working it: it becomes a `sap_notification` exception, matched to the equipment it
concerns, waiting on the Exceptions page for the IOC to dispatch it to the field, plan it, or leave it for SAP's
planners. When an order is raised for it, or SAP completes it, the exception clears. One the field reports done
closes with its case and is held (not raised again) until SAP's status for it changes. The field's report drafts the
SAP update on the maintenance planner's Inbox (ops/closeout.py, #466); once it is sent and SAP applies it, the next
intake sees the new status and the hold ends.

    sync_notifications(conn)   run on each register refresh, after the SAP records are rebuilt

It is on by default (the IOC is where work from every source arrives); a deployment whose SAP planners work their
notifications elsewhere turns the `intake.sap_notifications` setting off. Only notifications created in the
last `intake.sap_notifications.max_age_days` (counted back from the latest data, so a historical asset reads as
current) are raised: an old backlog is a planning question, not a queue of things to send someone to.
"""

from datetime import date, timedelta

from ..core import settings
from ..core.exceptions import clear_exception, exception_type, raise_exception
from ..core.findings import EXTERNAL, Finding, at, fact
from ..graph.operations import as_of

SAP_NOTIFICATION = exception_type(
    "sap_notification", "SAP notification", audience=("ioc", "field"), due_hours="intake.sap_notifications.due_hours",
    description="An open SAP maintenance notification with no work order raised for it yet.").name

OPEN = ("OSNO", "NOPR")  # SAP: outstanding, in process; NOCO (completed) and the rest are closed
BY = "sap"
# SAP's codes as the words they stand for; the code follows in brackets, since the planner knows it by that.
SAP_WORDS = {"status": {"OSNO": "outstanding", "NOPR": "in process", "NOCO": "completed", "ORAS": "order assigned"},
             "notif_type": {"M1": "maintenance request", "M2": "malfunction report", "M3": "activity report"}}


def _worded(k: str, text) -> str | None:
    """A code a field operator can't read as the words it stands for, with the code after it: `VIB` reads
    "Vibration (VIB)", SAP status `OSNO` reads "outstanding (OSNO)". Unknown codes stay as they are."""
    from ..core.taxonomy import load_standard

    if text in (None, ""):
        return None
    text = str(text)
    std = load_standard("iso14224")
    iso = {"failure_mode": std.get("failure_modes", {}), "failure_mechanism": std.get("failure_mechanisms", {})}
    words = SAP_WORDS.get(k, {}).get(text.upper()) or iso.get(k, {}).get(text)
    return f"{words} ({text})" if words else text


def finding(key: str, f: dict) -> Finding:
    """An SAP notification as a finding: its type, status, priority and failure coding in words. The site's damage
    and cause codes are left out where the ISO 14224 failure mode and mechanism they map to are there to read."""
    data = {k: f[k] for k in ("notif_type", "status", "created", "priority", "failure_mode", "failure_mechanism",
                              "damage_code", "cause_code") if k in f}
    created = str(f.get("created") or "")
    return Finding("sap.notification", EXTERNAL, method="An open notification with no work order",
                   start=created or None, data={"notification": key, **data}, items=(
                       fact("Notification", key),
                       fact("Notification type", _worded("notif_type", f.get("notif_type"))),
                       fact("Status", _worded("status", f.get("status"))),
                       (at if "T" in created else fact)("Created in SAP", created),
                       fact("SAP priority", f.get("priority")),
                       fact("Failure mode", _worded("failure_mode", f.get("failure_mode"))),
                       fact("Failure mechanism", _worded("failure_mechanism", f.get("failure_mechanism"))),
                       None if f.get("failure_mode") else fact("Damage code", f.get("damage_code")),
                       None if f.get("failure_mechanism") else fact("Cause code", f.get("cause_code"))))


def _status(f: dict) -> str:
    return str(f.get("status") or "").split()[0].upper() if f.get("status") else ""


def _priority(f: dict) -> float | None:
    """SAP priority 1 (very high) to 4 (low) as a rank where higher comes first."""
    try:
        return float(5 - int(str(f.get("priority")).strip()[0]))
    except (TypeError, ValueError, IndexError):
        return None


def sync_notifications(conn, max_age_days: int | None = None, enabled: bool | None = None) -> dict:
    """Raise an exception for each recent open notification without a work order; clear the ones that got an
    order or closed. Returns counts. Nothing is raised or cleared when the setting is off."""
    if not (settings.get(conn, "intake.sap_notifications") if enabled is None else enabled):
        return {"raised": 0, "cleared": 0, "enabled": False}
    if max_age_days is None:
        max_age_days = settings.get(conn, "intake.sap_notifications.max_age_days")
    since = (date.fromisoformat(as_of(conn)) - timedelta(days=max_age_days)).isoformat()
    rows = conn.execute(
        """
        select e.fuzer_id, jsonb_object_agg(g.field, g.value) as f,
               (select min(k.key) from entity_key k where k.fuzer_id = e.fuzer_id and k.kind = 'notification') as number,
               (select array_agg(distinct x.dst) from edge x where x.src = e.fuzer_id and x.rel = 'concerns') as concerns,
               exists (select 1 from edge o where o.dst = e.fuzer_id and o.rel = 'for_notification') as has_order
        from entity e join golden g on g.fuzer_id = e.fuzer_id
        where e.kind = 'notification' and e.retired_at is null
        group by e.fuzer_id
        """).fetchall()
    already = {r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (SAP_NOTIFICATION,))}
    # Notifications the field said were done: SAP doesn't hear of it, so the same open notification would come
    # straight back. Each is held while SAP still shows the status it had when the field closed it; a new status
    # (or an order, which clears it above) means SAP moved, and a status that is still open raises it again.
    done_in_field = {r["key"]: r["status"] for r in conn.execute(
        """
        select distinct on (key) key, evidence->>'status' as status, evidence->'field'->-1->>'status' as said
        from exception_record where type = %s and state = 'closed' order by key, closed_at desc
        """, (SAP_NOTIFICATION,)) if r["said"] == "done"}
    raised = cleared = held = 0
    for r in rows:
        f = {k: v for k, v in r["f"].items() if v not in (None, "")}
        key = r["number"] or r["fuzer_id"]
        status = _status(f)
        wanted = status in OPEN and not r["has_order"] and "ORAS" not in str(f.get("status", "")).upper()
        if not wanted:
            if key in already:
                why = "a work order was raised for it" if r["has_order"] else f"SAP status {status or 'closed'}"
                cleared += clear_exception(conn, SAP_NOTIFICATION, key, BY, reason=why) is not None
            continue
        if key in already or str(f.get("created", ""))[:10] < since:
            continue  # raised already (a repeat would only bump its count), or too old to send anyone to
        if key in done_in_field and done_in_field[key] == f.get("status"):
            held += 1  # done in the field; waiting for SAP's planners to close or change it
            continue
        text = f.get("short_text") or f.get("description") or "SAP notification"
        raise_exception(conn, SAP_NOTIFICATION, key, f"{text} (notification {key})", BY, source="sap",
                        refs=r["concerns"] or [r["fuzer_id"]], priority=_priority(f), finding=finding(key, f))
        raised += 1
    return {"raised": raised, "cleared": cleared, "held": held}
