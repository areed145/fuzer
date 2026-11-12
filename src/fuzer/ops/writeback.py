"""The approved write-back queue (P5.7, #634): every change Fuzer proposes to a source system, and the only way one
is ever written.

    propose(conn, system, kind, payload, by, ...)    drafted, then waiting for approval in the approver role's queue
    approve(conn, id, by) / reject(conn, id, by)     a named person in one of the system's approver roles decides
    run(conn, now)                                   send what is approved and due by the system's cadence
    confirm(conn, id, by) / fail(conn, id, ...)      the source said it took it (releases the case), or why not

A write-back moves through fixed states:

    drafted -> awaiting_approval -> approved -> sent -> confirmed
                       |                  \\-> failed -> (approved again, or rejected)
                       \\-> rejected

Nothing is sent without a recorded approver: `run` sends approved rows only, each adapter checks the row itself
before it writes, and approving is the write_back fixed gate (core/gates.py), so no agent, webhook or the system
can approve. A send that fails goes back to the approver's queue with the source's reason and is never retried on
its own. Sends are idempotent: each row has a key, an adapter given a key it already wrote writes nothing again.

Each system declares what it accepts, who may approve, its cadence and its adapter (the `writeback` column of
source_system, from its source pack), over the defaults agreed for the operating model: safety notifications at
once, maintenance records in batches every 15 minutes, master data daily, with a limit on writes per run. A system
that accepts no writes through an API gets the `export` adapter: the queue writes an export file for its owner.
"""

import json
import logging
import re
from datetime import datetime, time, timedelta
from pathlib import Path

from psycopg.types.json import Jsonb

from ..core import events, gates, work
from ..core.exceptions import ExceptionError

log = logging.getLogger(__name__)

CASE_TYPE = "writeback"
STATES = ("drafted", "awaiting_approval", "approved", "sent", "confirmed", "failed", "rejected")

# What each kind is, who approves it and when it goes, unless the system's pack says otherwise.
KINDS: dict[str, dict] = {
    "notification": {"label": "Notification", "approvers": ["maintenance_planner", "ops_lead"], "cadence": "15min"},
    "safety_notification": {"label": "Safety notification", "approvers": ["ops_lead", "maintenance_planner"],
                            "cadence": "now"},
    "order": {"label": "Work order", "approvers": ["maintenance_planner"], "cadence": "15min"},
    "master_data": {"label": "Master data change", "approvers": ["data_steward"], "cadence": "daily 02:00"},
    "annotation": {"label": "Historian annotation", "approvers": ["ops_lead", "engineer"], "cadence": "15min"},
    "drawing_correction": {"label": "Drawing correction", "approvers": ["data_steward", "engineer"],
                           "cadence": "daily 02:00"},
    "log_entry": {"label": "Operator log entry", "approvers": ["ops_lead"], "cadence": "now"},
    "detection_feedback": {"label": "Detection feedback", "approvers": ["engineer", "ops_lead"], "cadence": "15min"},
}
LIMIT = 50  # writes per system per run, unless its pack says otherwise


class WritebackError(ExceptionError):
    """A write-back the queue refused: an unknown kind, a system that doesn't take it, a state that doesn't allow it."""


# ---- the system's policy -------------------------------------------------------------------------------------------

def policy(conn, system: str) -> dict:
    """What `system` accepts, who approves each kind, its cadence, adapter and limit per run."""
    row = conn.execute("select writeback from source_system where name = %s", (system,)).fetchone()
    declared = (row or {}).get("writeback") or {}
    accepts = declared.get("accepts") or list(KINDS)
    kinds = {}
    for k in accepts:
        if k not in KINDS:
            continue
        own = (declared.get("kinds") or {}).get(k) or {}
        kinds[k] = {"label": KINDS[k]["label"], "approvers": own.get("approvers") or declared.get("approvers")
                    or KINDS[k]["approvers"], "cadence": own.get("cadence") or KINDS[k]["cadence"]}
    from ..core import settings

    demo = settings.get(conn, "closeout.apply_to_exports")  # the demo stands in for SAP by editing its export
    return {"system": system, "kinds": kinds, "adapter": declared.get("adapter") or ("demo_csv" if demo else "export"),
            "limit": int(declared.get("limit") or LIMIT)}


def validate_policy(value) -> dict | None:
    """A pack's `writeback` block, checked: accepts (kinds), approvers, kinds.<k>.{approvers, cadence}, adapter,
    limit."""
    if value in (None, {}):
        return None
    if not isinstance(value, dict):
        raise WritebackError("writeback is a mapping: accepts, approvers, kinds, adapter, limit")
    bad = [k for k in value.get("accepts") or [] if k not in KINDS]
    if bad:
        raise WritebackError(f"writeback accepts unknown kinds {bad}; known: {', '.join(KINDS)}")
    for k, own in (value.get("kinds") or {}).items():
        if k not in KINDS:
            raise WritebackError(f"writeback.kinds.{k} isn't a kind")
        if own.get("cadence"):
            parse_cadence(own["cadence"])
    if value.get("adapter") not in (None, *ADAPTERS):
        raise WritebackError(f"writeback adapter is one of {', '.join(ADAPTERS)}")
    return value


def parse_cadence(text: str) -> tuple:
    """now | Nmin | hourly | daily HH:MM | window HH:MM-HH:MM."""
    t = (text or "").strip().lower()
    if t == "now":
        return ("now",)
    if m := re.fullmatch(r"(\d+)\s*min", t):
        return ("every", int(m[1]))
    if t == "hourly":
        return ("every", 60)
    if m := re.fullmatch(r"daily\s+(\d{1,2}):(\d{2})", t):
        return ("daily", time(int(m[1]), int(m[2])))
    if m := re.fullmatch(r"window\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", t):
        return ("window", time(int(m[1]), int(m[2])), time(int(m[3]), int(m[4])))
    raise WritebackError(f"cadence '{text}' isn't now, Nmin, hourly, daily HH:MM or window HH:MM-HH:MM")


def _tz():
    from zoneinfo import ZoneInfo

    from ..core import projects

    try:
        return ZoneInfo(projects.field("timezone") or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def next_send(cadence: str, after: datetime) -> datetime:
    """When an item approved at `after` goes, by its cadence (in the project's timezone)."""
    c = parse_cadence(cadence)
    if c[0] == "now":
        return after
    if c[0] == "every":
        step = c[1] * 60
        return datetime.fromtimestamp((int(after.timestamp()) // step + 1) * step, after.tzinfo)
    tz = _tz()
    local = after.astimezone(tz)
    if c[0] == "daily":
        at = datetime.combine(local.date(), c[1], tz)
        return (at if at > local else at + timedelta(days=1)).astimezone(after.tzinfo)
    start, end = c[1], c[2]
    t = local.time().replace(tzinfo=None)
    inside = start <= t < end if start < end else (t >= start or t < end)
    if inside:
        return after
    at = datetime.combine(local.date(), start, tz)
    return (at if at > local else at + timedelta(days=1)).astimezone(after.tzinfo)


def describe_cadence(cadence: str) -> str:
    c = parse_cadence(cadence)
    if c[0] == "now":
        return "at once"
    if c[0] == "every":
        return f"in the next {c[1]}-minute batch"
    if c[0] == "daily":
        return f"in the daily batch at {c[1]:%H:%M}"
    return f"inside the {c[1]:%H:%M}-{c[2]:%H:%M} window"


# ---- the queue -----------------------------------------------------------------------------------------------------

def get(conn, wid: int) -> dict:
    r = conn.execute("select * from writeback where id = %s", (wid,)).fetchone()
    if r is None:
        raise WritebackError(f"no write-back {wid}")
    return dict(r)


def _log(conn, wid: int, state: str, by: str, note: str | None = None) -> None:
    conn.execute("insert into writeback_event (writeback_id, state, by, note) values (%s, %s, %s, %s)",
                 (wid, state, by, note))


def propose(conn, system: str, kind: str, payload: dict, by: str, *, record: str | None = None,
            action: str = "update", key: str | None = None, item_id: int | None = None, title: str | None = None,
            subjects: list[str] | None = None, source_ref: str | None = None, approver_role: str | None = None) -> dict:
    """Draft a change to `system` and put it in its approver's queue. An agent or a person may draft; only a
    person approves. `key` makes it idempotent: proposing the same key again returns the one already there."""
    p = policy(conn, system)
    if kind not in p["kinds"]:
        raise WritebackError(f"{system} doesn't take {kind} write-backs (it takes {', '.join(p['kinds']) or 'none'})")
    key = key or f"{system}:{kind}:{record or ''}:{json.dumps(payload, sort_keys=True, default=str)}"
    have = conn.execute("select * from writeback where idem_key = %s", (key,)).fetchone()
    if have:
        return dict(have)
    role = approver_role or p["kinds"][kind]["approvers"][0]
    row = dict(conn.execute(
        "insert into writeback (system, kind, record, action, payload, idem_key, drafted_by, approver_role, item_id, "
        "source_ref, state) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'drafted') returning *",
        (system, kind, record, action, Jsonb(payload), key, by, role, item_id, source_ref)).fetchone())
    _log(conn, row["id"], "drafted", by)
    label = p["kinds"][kind]["label"].lower()
    title = title or f"{system.upper()} {label} to approve" + (f": {record}" if record else "")
    if row["item_id"] is None:  # its approver's case: the standard task process, with the role's SLA (#509)
        from . import routing

        case = routing.open_task(conn, CASE_TYPE, title, by, f"writeback:{row['id']}", role=f"role:{role}",
                                 data={"writeback_id": row["id"], "system": system, "kind": kind},
                                 subjects=subjects or [], source=system)
        row["item_id"] = case["id"]
    row = dict(conn.execute("update writeback set state = 'awaiting_approval', item_id = %s where id = %s returning *",
                            (row["item_id"], row["id"])).fetchone())
    _log(conn, row["id"], "awaiting_approval", by, f"in the {role} queue")
    return row


def _approver(conn, row: dict, by: str) -> None:
    gates.require_person(conn, "write_back", by, ref=f"writeback:{row['id']}", item_id=row["item_id"])
    allowed = [*(policy(conn, row["system"])["kinds"].get(row["kind"], {}).get("approvers") or []), row["approver_role"]]
    held = set(work.roles_of(conn, by))
    if not held & {a for a in allowed if a}:
        raise WritebackError(f"{by} can't approve a {row['system']} {row['kind']} write-back: it needs one of "
                             f"{', '.join(allowed)}")


def approve(conn, wid: int, by: str, payload: dict | None = None, now: datetime | None = None) -> dict:
    """A named approver approves it (with any edits): it goes by the system's cadence. A failed one approved again
    is sent again, with the same key."""
    row = get(conn, wid)
    if row["state"] not in ("awaiting_approval", "failed"):
        raise WritebackError(f"write-back {wid} is {row['state']}, not waiting for approval")
    _approver(conn, row, by)
    now = now or datetime.now().astimezone()
    cadence = policy(conn, row["system"])["kinds"][row["kind"]]["cadence"]
    merged = {**row["payload"], **(payload or {})}
    row = dict(conn.execute(
        "update writeback set state = 'approved', approved_by = %s, approved_at = %s, payload = %s, due_at = %s, "
        "error = null where id = %s returning *", (by, now, Jsonb(merged), next_send(cadence, now), wid)).fetchone())
    _log(conn, wid, "approved", by, f"goes {describe_cadence(cadence)}")
    if row["item_id"]:
        work.comment(conn, row["item_id"], by, f"Approved. It goes to {row['system'].upper()} "
                     f"{describe_cadence(cadence)}.")
    return row


def reject(conn, wid: int, by: str, reason: str | None = None) -> dict:
    row = get(conn, wid)
    if row["state"] not in ("drafted", "awaiting_approval", "failed"):
        raise WritebackError(f"write-back {wid} is {row['state']}; only one not yet sent can be rejected")
    if gates.machine(by):
        raise gates.GateRefused(f"rejecting a write-back needs a named person; {by} can't")
    row = dict(conn.execute("update writeback set state = 'rejected', reason = %s where id = %s returning *",
                            (reason, wid)).fetchone())
    _log(conn, wid, "rejected", by, reason)
    if row["item_id"]:
        _close(conn, row["item_id"], "cancelled", by, reason or "not sent")
    return row


def _close(conn, item_id: int, state: str, by: str, reason: str) -> None:
    item = conn.execute("select state from work_item where id = %s", (item_id,)).fetchone()
    if item and item["state"] not in ("done", "cancelled"):
        work.close_case(conn, item_id, state, by, reason=reason)


def confirm(conn, wid: int, by: str, note: str | None = None) -> dict:
    """The source took it (its adapter said so, or its owner did for an export): the case is released."""
    row = get(conn, wid)
    if row["state"] != "sent":
        raise WritebackError(f"write-back {wid} is {row['state']}, not sent")
    row = dict(conn.execute("update writeback set state = 'confirmed', confirmed_at = now() where id = %s returning *",
                            (wid,)).fetchone())
    _log(conn, wid, "confirmed", by, note)
    if row["item_id"]:
        _close(conn, row["item_id"], "done", by, f"confirmed by {row['system'].upper()}" + (f": {note}" if note else ""))
    events.publish(conn, "writeback.confirmed", f"writeback:{wid}", {"id": wid, "system": row["system"],
                                                                     "kind": row["kind"], "record": row["record"]})
    return row


def fail(conn, wid: int, error: str, by: str = "system") -> dict:
    """The source refused it: back to the approver's queue with its reason, never retried on its own."""
    row = dict(conn.execute("update writeback set state = 'failed', error = %s, failed_at = now() where id = %s "
                            "returning *", (error[:2000], wid)).fetchone())
    _log(conn, wid, "failed", by, error[:2000])
    if row["item_id"]:
        item = conn.execute("select state from work_item where id = %s", (row["item_id"],)).fetchone()
        text = (f"{row['system'].upper()} refused it: {error}. Approve it again to resend, or reject it.")
        if item and item["state"] in ("done", "cancelled"):
            from . import routing

            case = routing.open_task(conn, CASE_TYPE, f"{row['system'].upper()} write-back failed: {row['record'] or wid}",
                                     by, f"writeback:{wid}:failed:{row['attempts']}", role=f"role:{row['approver_role']}",
                                     data={"writeback_id": wid, "system": row["system"], "kind": row["kind"]},
                                     source=row["system"])
            conn.execute("update writeback set item_id = %s where id = %s", (case["id"], wid))
            row["item_id"] = case["id"]
        work.comment(conn, row["item_id"], by, text)
    return row


def listing(conn, system: str | None = None, state: str | None = None, item_id: int | None = None,
            limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "select * from writeback where (%(s)s::text is null or system = %(s)s) and (%(st)s::text is null or state = "
        "%(st)s) and (%(i)s::bigint is null or item_id = %(i)s) order by id desc limit %(n)s",
        {"s": system, "st": state, "i": item_id, "n": limit}).fetchall()
    return [dict(r) for r in rows]


def history(conn, wid: int) -> list[dict]:
    return conn.execute("select * from writeback_event where writeback_id = %s order by id", (wid,)).fetchall()


def detail(conn, wid: int) -> dict:
    row = get(conn, wid)
    p = policy(conn, row["system"])
    cadence = p["kinds"].get(row["kind"], {}).get("cadence")
    return {**row, "label": KINDS[row["kind"]]["label"], "approvers": p["kinds"].get(row["kind"], {}).get("approvers"),
            "cadence": describe_cadence(cadence) if cadence else None, "adapter": p["adapter"],
            "history": history(conn, wid)}


# ---- sending -------------------------------------------------------------------------------------------------------

def _check_approved(row: dict) -> None:
    """Every adapter calls this before it writes: an approved row with a named approver, or nothing is written."""
    if row.get("state") != "approved" or not row.get("approved_by") or gates.machine(row.get("approved_by")):
        raise WritebackError(f"write-back {row.get('id')} has no recorded approver: nothing is written")


def _export(conn, row: dict) -> str | None:
    """A system with no write API: the change goes into an export file for its owner (JSON lines, one per write,
    keyed so a resend adds nothing). The owner confirms once it is applied."""
    _check_approved(row)
    from ..core import projects

    folder = Path(projects.field("data") or "data") / "writeback"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{row['system']}.jsonl"
    if path.exists() and any(json.loads(line).get("key") == row["idem_key"]
                             for line in path.read_text(encoding="utf-8").splitlines() if line.strip()):
        return None  # already written: idempotent
    line = {"key": row["idem_key"], "id": row["id"], "system": row["system"], "kind": row["kind"],
            "record": row["record"], "action": row["action"], "payload": row["payload"],
            "approved_by": row["approved_by"], "approved_at": row["approved_at"].isoformat()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=str) + "\n")
    return None  # sent; the owner confirms


def _demo_csv(conn, row: dict) -> str | None:
    """The demo's stand-in for a source system: apply a close-out to the system's own export CSV, as the system
    would, and confirm (the export now holds it). Off unless closeout.apply_to_exports is on."""
    _check_approved(row)
    if row["action"] not in ("complete", "follow_up"):  # not a close-out: the owner gets it in the export file
        return _export(conn, row)
    from . import closeout

    why = closeout.apply_update(conn, {**row["payload"], "system": row["system"], "record": row["record"],
                                       "action": row["action"]})
    if why is not None:
        raise RuntimeError(why)  # back to the approver with the reason
    return "confirmed"


ADAPTERS = {"export": _export, "demo_csv": _demo_csv}


def send(conn, wid: int, by: str = "system") -> dict:
    """Send one approved write-back now through its system's adapter."""
    row = get(conn, wid)
    _check_approved(row)
    adapter = ADAPTERS[policy(conn, row["system"])["adapter"]]
    conn.execute("savepoint writeback_send")
    try:
        out = adapter(conn, row)
    except Exception as e:  # the source's refusal goes back to the approver
        conn.execute("rollback to savepoint writeback_send")
        return fail(conn, wid, str(e), by)
    conn.execute("release savepoint writeback_send")
    row = dict(conn.execute("update writeback set state = 'sent', sent_at = now(), attempts = attempts + 1 "
                            "where id = %s returning *", (wid,)).fetchone())
    _log(conn, wid, "sent", by, f"through {adapter.__name__.strip('_')}")
    events.publish(conn, "writeback.sent", f"writeback:{wid}", {"id": wid, "system": row["system"], "kind": row["kind"],
                                                                "record": row["record"], "approved_by": row["approved_by"]})
    if out == "confirmed":
        return confirm(conn, wid, "system", "applied to its export")
    return row


def run(conn, now: datetime | None = None) -> dict:
    """Send what is approved and due by each system's cadence, up to its limit per run; commits."""
    now = now or datetime.now().astimezone()
    due = conn.execute("select id, system from writeback where state = 'approved' and due_at <= %s order by due_at, id "
                       "for update skip locked", (now,)).fetchall()
    sent: dict[str, int] = {}
    out = {"sent": 0, "failed": 0, "held": 0}
    for r in due:
        limit = policy(conn, r["system"])["limit"]
        if sent.get(r["system"], 0) >= limit:
            out["held"] += 1
            continue
        row = send(conn, r["id"])
        sent[r["system"]] = sent.get(r["system"], 0) + 1
        out["failed" if row["state"] == "failed" else "sent"] += 1
    conn.commit()
    return out
