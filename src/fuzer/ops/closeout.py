"""Close-out goes back to the source (#466): when the field finishes work that came from another system, that system
hears of it, so its record agrees with what the field did and nobody retypes it.

A field report of *done* or *could not do* on an exception another system raised (an SAP notification, or a type a
source pack brought) drafts the update for that system:

    done          complete the record: a completion note with the field's report, and its closed status
    could_not_do  a follow-up record: what could not be done and why, so the work stays on that system's books

How it goes back is the system's `closeout` setting (on Sources): `none` (nothing), `draft` (a case on the
`closeout_role` queue's Inbox to approve or edit first) or `send` (sent at once). SAP drafts to the maintenance
planners unless its system says otherwise. An approved (or sent) update is published on the event outbox as
`source.update` for that system's adapter to deliver.

The demo has no SAP to send to. With the `closeout.apply_to_exports` setting on, a sent update is applied to the
system's export file instead (the status column of the record's row, or a new row for a follow-up), so the next
load and intake see SAP move: a notification done in the field stops being held, and a follow-up is raised.
"""

import csv
import logging
from datetime import date

from psycopg.types.json import Jsonb

from ..core import events, settings, work
from ..core.exceptions import ExceptionError

MODES = ("none", "draft", "send")
DEFAULTS = {"sap": ("draft", "maintenance_planner")}  # a system that sets nothing
CLOSED_STATUS = {"sap": "NOCO"}
OPEN_STATUS = {"sap": "OSNO"}
CASE_TYPE = "source_closeout"
TOPIC = "source.update"
log = logging.getLogger(__name__)


def mode(conn, system: str | None) -> tuple[str, str | None]:
    """How a system's work closes out upstream: (none | draft | send, the queue drafts go to)."""
    if not system:
        return "none", None
    r = conn.execute("select closeout, closeout_role from source_system where name = %s", (system,)).fetchone()
    if r is None:
        return "none", None
    m, role = DEFAULTS.get(system, ("none", None))
    return r["closeout"] or m, (r["closeout_role"] or role or "").removeprefix("role:") or None


def _draft(row: dict, by: str, status: str, note: str | None) -> dict:
    ev = row["evidence"] or {}
    record = str(ev.get("notification") or ev.get("record") or row["key"])
    system = row["source"]
    note = (note or "").strip()
    if status == "done":
        return {"action": "complete", "record": record, "status": CLOSED_STATUS.get(system),
                "text": f"Completed in the field by {by}" + (f": {note}" if note else ".")}
    return {"action": "follow_up", "record": record, "status": OPEN_STATUS.get(system),
            "notif_type": ev.get("notif_type"), "priority": ev.get("priority"),
            "short_text": f"Follow-up to {record}: {note}"[:120],
            "text": f"Could not be done in the field ({by}): {note}. Raised as a follow-up to {record}."}


def on_field_report(conn, row: dict, by: str, status: str, note: str | None) -> dict | None:
    """The field finished work another system raised: draft (or send) its close-out. None when there is nothing to
    send: the system closes out nowhere, or this exception already has one waiting."""
    if status not in ("done", "could_not_do"):
        return None
    m, role = mode(conn, row["source"])
    if m == "none":
        return None
    if conn.execute("select 1 from source_closeout where exception_id = %s and state = 'draft'", (row["id"],)).fetchone():
        return None
    draft = _draft(row, by, status, note)
    out = dict(conn.execute(
        """
        insert into source_closeout (exception_id, system, record, action, draft, created_by)
        values (%s, %s, %s, %s, %s, %s) returning *
        """, (row["id"], row["source"], draft["record"], draft["action"], Jsonb(draft), by)).fetchone())
    if m == "send":
        return _send(conn, out, by)
    what = "completion" if draft["action"] == "complete" else "follow-up"
    item = work.open_case(conn, CASE_TYPE, f"{row['source'].upper()} {what} to approve: {draft['record']}", by,
                          subjects=row["subjects"], priority=row["priority"], role=role, source=row["source"],
                          payload={"closeout_id": out["id"], "exception_id": row["id"], **draft})
    return dict(conn.execute("update source_closeout set item_id = %s where id = %s returning *",
                             (item["id"], out["id"])).fetchone())


def get(conn, closeout_id: int) -> dict:
    r = conn.execute("select * from source_closeout where id = %s", (closeout_id,)).fetchone()
    if r is None:
        raise ExceptionError(f"no close-out {closeout_id}")
    return dict(r)


def listing(conn, state: str | None = "draft", item_id: int | None = None, exception_id: int | None = None) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """
        select * from source_closeout where (%(s)s::text is null or state = %(s)s)
          and (%(i)s::bigint is null or item_id = %(i)s) and (%(e)s::bigint is null or exception_id = %(e)s)
        order by id desc limit 200
        """, {"s": state, "i": item_id, "e": exception_id})]


def _send(conn, c: dict, by: str) -> dict:
    row = dict(conn.execute(
        "update source_closeout set state = 'sent', decided_by = %s, decided_at = now() where id = %s returning *",
        (by, c["id"])).fetchone())
    events.publish(conn, TOPIC, f"{row['system']}/{row['record']}",
                   {"closeout_id": row["id"], "system": row["system"], "exception_id": row["exception_id"],
                    "approved_by": by, **row["draft"]})
    return row


def approve(conn, closeout_id: int, by: str, text: str | None = None, short_text: str | None = None) -> dict:
    """Send a drafted close-out (with the planner's edits to its text), and finish its case."""
    c = get(conn, closeout_id)
    if c["state"] != "draft":
        raise ExceptionError(f"close-out {closeout_id} is {c['state']}, not a draft")
    edits = {k: v.strip() for k, v in (("text", text), ("short_text", short_text)) if v and v.strip()}
    if edits:
        c = dict(conn.execute("update source_closeout set draft = draft || %s where id = %s returning *",
                              (Jsonb(edits), closeout_id)).fetchone())
    row = _send(conn, c, by)
    if c["item_id"]:
        work.close_case(conn, c["item_id"], "done", by, reason=f"sent to {c['system']}")
    return row


def discard(conn, closeout_id: int, by: str, reason: str | None = None) -> dict:
    """Don't send it (the planner updates the system by hand, or nothing needs to change)."""
    c = get(conn, closeout_id)
    if c["state"] != "draft":
        raise ExceptionError(f"close-out {closeout_id} is {c['state']}, not a draft")
    row = dict(conn.execute("update source_closeout set state = 'discarded', decided_by = %s, decided_at = now() "
                            "where id = %s returning *", (by, closeout_id)).fetchone())
    if c["item_id"]:
        work.close_case(conn, c["item_id"], "cancelled", by, reason=reason or "not sent")
    return row


# ---- the demo's stand-in for the source system -------------------------------------------------------------------

@events.subscriber(TOPIC)
def apply_to_exports(conn, event: dict) -> None:
    """Demo only (`closeout.apply_to_exports`): apply a sent update to the system's export file, as the system would."""
    u = event["payload"]

    def skipped(why: str) -> None:  # kept on the close-out, so a sent update that changed nothing says why
        log.warning("close-out %s not applied: %s", u.get("closeout_id"), why)
        conn.execute("update source_closeout set applied_note = %s where id = %s", (why, u.get("closeout_id")))

    if not settings.get(conn, "closeout.apply_to_exports"):
        return skipped("closeout.apply_to_exports is off")
    found = _export_for(conn, u["system"], u["record"])
    if found is None:
        return skipped(f"no CSV export holds {u['system']}/{u['record']} (data folder {projects_data()})")
    path, key_cols = found
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols, rows = list(reader.fieldnames or []), list(reader)
    keyed = lambda r: ">".join(str(r.get(k, "")) for k in key_cols)
    mine = next((r for r in rows if keyed(r) == u["record"]), None)
    if mine is None:
        return skipped(f"{path} has no row keyed {u['record']}")
    if u["action"] == "complete":
        if "status" in cols and u.get("status"):
            mine["status"] = u["status"]
    else:
        new = dict(mine)
        k = key_cols[-1]
        nums = [int(r[k]) for r in rows if str(r.get(k, "")).isdigit()]
        new[k] = str(max(nums) + 1) if nums else f"{mine[k]}-F"
        for col, value in (("status", u.get("status")), ("short_text", u.get("short_text")),
                           ("created", date.today().isoformat()), ("notif_type", u.get("notif_type")),
                           ("priority", u.get("priority"))):
            if col in cols and value:
                new[col] = value
        rows.append(new)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    conn.execute("update source_closeout set applied_at = now(), applied_note = %s where id = %s",
                 (str(path), u["closeout_id"]))


def projects_data() -> str | None:
    from ..core import projects

    return projects.field("data")


def _export_for(conn, system: str, record: str):
    """The CSV export file (and its key columns) holding a system's record."""
    from pathlib import Path

    from ..core import projects
    from ..sources import ingest, mappings

    rt = conn.execute("select record_type from source_record where source = %s and source_key = %s limit 1",
                      (system, record)).fetchone()
    if rt is None:
        return None
    for m in mappings.load(conn).values():
        if m.source != system or m.record_type != rt["record_type"] or not m.dataset or not m.key:
            continue
        c = ingest.dataset(Path(projects.field("data") or "data"), m.dataset, conn)
        path = getattr(c, "path", None)
        if path is not None and Path(path).suffix.lower() == ".csv":
            return Path(path), [m.key] if isinstance(m.key, str) else list(m.key)
    return None
