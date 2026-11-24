"""Source links and status mapping (step 8 of docs/work-model.md): one Fuzer object tied to the objects other systems
keep for it, and each system's statuses read into Fuzer's short list of states.

    state_of(conn, system, object_type, status)   the Fuzer state a status means, or None when no row maps it
    link(conn, object, id, system, type, key, ...) tie a Fuzer object to another system's object, with its status
    links(conn, object, id)                       the links on one object
    status_map(conn, system) / set_status(...)    the mapping, as a site edits it
    sync_orders(conn)                             each SAP order on the plan as a job with its source link and a case

A status no row maps is flagged, not guessed: the object holds its last known state and the data steward gets a
`source_issue` for the status. SAP carries system and user statuses in one field ("REL WMAT"); a row matches a system
status alone or with a user status, the most specific row wins, and among system statuses the one furthest along
counts (REL with PCNF is in progress).

Every piece of work has exactly one case. An SAP order raised from a notification is that notification's job, so it
joins the notification's case; an order with no notification Fuzer holds opens a case of its own in the maintenance
planner's queue, which closes when SAP closes the order.
"""

from ..core import events, work
from ..core.exceptions import clear_exception, raise_exception
from ..core.findings import EXTERNAL, Finding, fact
from . import routing

OBJECTS = ("job", "case", "need", "container")
ORDER_CASE = "sap_order"
PLANNER = "role:maintenance_planner"
# Furthest along last: when a status carries several system statuses, the one furthest along counts.
RANK = ("proposed", "scheduled", "in_progress", "done", "cancelled")

# The seed rows: (system, object type, status, user status, Fuzer state, move sent back, note). Examples a source pack
# or a site edits (the table in docs/work-model.md).
SEED = (
    ("sap", "order", "CRTD", "", "proposed", None, "Created: planned, not yet released"),
    ("sap", "order", "REL", "", "scheduled", "the site's dispatched user status", "Released in SAP: can be scheduled"),
    ("sap", "order", "REL", "WMAT", "proposed", None, "Released but waiting on materials: a prerequisite is open"),
    ("sap", "order", "PCNF", "", "in_progress", None, "Partly confirmed"),
    ("sap", "order", "CNF", "", "done", "a confirmation", "Confirmed"),
    ("sap", "order", "TECO", "", "done", "TECO, for the planner to approve", "Technically complete"),
    ("sap", "order", "CLSD", "", "done", None, "Closed"),
    ("sap", "order", "DLFL", "", "cancelled", None, "Flagged for deletion"),
    ("sap", "notification", "OSNO", "", "open", "NOPR when its case opens", "Outstanding"),
    ("sap", "notification", "NOPR", "", "open", None, "In process"),
    ("sap", "notification", "NOCO", "", "closed", "NOCO, with ISO 14224 codes", "Completed"),
    ("p6", "activity", "NOT STARTED", "", "scheduled", None, "Progress is read from P6, not sent back"),
    ("p6", "activity", "IN PROGRESS", "", "in_progress", None, None),
    ("p6", "activity", "COMPLETED", "", "done", None, None),
    ("maximo", "work_order", "WAPPR", "", "proposed", "WAPPR", None),
    ("maximo", "work_order", "APPR", "", "proposed", "APPR", "Approved: can be scheduled"),
    ("maximo", "work_order", "WSCH", "", "scheduled", "WSCH", None),
    ("maximo", "work_order", "INPRG", "", "in_progress", "INPRG", None),
    ("maximo", "work_order", "COMP", "", "done", "COMP", None),
    ("maximo", "work_order", "CLOSE", "", "done", "CLOSE", None),
    ("maximo", "work_order", "CAN", "", "cancelled", "CAN", None),
)


class LinkError(ValueError):
    pass


def seed(conn, by: str = "fuzer") -> int:
    """The seed rows a system has none of yet; a site's own rows are never touched."""
    have = {(r["system"], r["object_type"]) for r in conn.execute(
        "select distinct system, object_type from work_status_map")}
    n = 0
    for system, otype, status, user, state, move, note in SEED:
        if (system, otype) in have:
            continue
        n += conn.execute(
            "insert into work_status_map (system, object_type, status, user_status, state, move, note, created_by, "
            "updated_by) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing",
            (system, otype, status, user, state, move, note, by, by)).rowcount
    return n


def _tokens(status: str | None) -> list[str]:
    return [t for t in str(status or "").upper().replace(",", " ").split() if t]


def state_of(conn, system: str, object_type: str, status: str | None) -> str | None:
    """The Fuzer state `status` means for this system's object type, or None when no row maps it. A status with
    spaces in a row ("NOT STARTED") matches as a whole; otherwise each word is a system or user status."""
    seed(conn)
    rows = conn.execute("select * from work_status_map where system = %s and object_type = %s",
                        (system, object_type)).fetchall()
    text = " ".join(_tokens(status))
    words = set(text.split())
    hits = []
    for r in rows:
        st, user = r["status"].upper(), (r["user_status"] or "").upper()
        if (" " in st and st in text) or st in words:
            if not user or user in words:
                hits.append(r)
    if not hits:
        return None
    rank = {s: i for i, s in enumerate(RANK)}
    best = max(hits, key=lambda r: (bool(r["user_status"]), rank.get(r["state"], -1)))
    # a user status refines its own system status; the system status furthest along still wins over it
    plain = max(hits, key=lambda r: rank.get(r["state"], -1))
    if best["user_status"] and rank.get(plain["state"], -1) > rank.get(best["state"], -1) and \
            plain["status"].upper() != best["status"].upper():
        return plain["state"]
    return best["state"]


def flag_unmapped(conn, system: str, object_type: str, status: str, key: str, by: str = "fuzer") -> dict:
    """A status no row maps: the data steward's question, one per status, naming an object that carries it."""
    return raise_exception(
        conn, "source_issue", f"status:{system}:{object_type}:{status}",
        f"{system.upper()} {object_type.replace('_', ' ')} status {status} has no Fuzer state", by, source="sources",
        priority=0.3, finding=Finding(
            "sources.status_unmapped", EXTERNAL, method="Status mapping: no row matches this status",
            items=(fact("System", system), fact("Object", object_type.replace("_", " ")), fact("Status", status),
                   fact("Example", key)),
            data={"system": system, "object_type": object_type, "status": status, "example": key}))


def clear_mapped(conn, by: str = "fuzer") -> int:
    """The status questions a row now answers clear, closing their cases."""
    n = 0
    for r in conn.execute("select key from exception_record where type = 'source_issue' and state = 'open' "
                          "and key like 'status:%%'").fetchall():
        _, system, otype, status = r["key"].split(":", 3)
        if state_of(conn, system, otype, status) is not None:
            n += clear_exception(conn, "source_issue", r["key"], by, reason="the status mapping has a row for it") \
                is not None
    return n


def link(conn, object: str, object_id: int, system: str, object_type: str, key: str, status: str | None = None,
         owns=(), by: str = "fuzer") -> dict:
    """Tie a Fuzer object to another system's object, with its status there and the Fuzer state that means (None
    while no row maps it). Idempotent on (system, object type, key, object)."""
    if object not in OBJECTS:
        raise LinkError(f"object must be one of {OBJECTS}")
    state = state_of(conn, system, object_type, status) if status else None
    row = conn.execute(
        """
        insert into work_source_link (object, object_id, system, object_type, key, status, state, owns)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (system, object_type, key, object) do update set object_id = excluded.object_id,
            status = excluded.status, state = coalesce(excluded.state, work_source_link.state),
            owns = excluded.owns, seen_at = now()
        returning *, (xmax = 0) as created
        """, (object, object_id, system, object_type, str(key), status, state, list(owns))).fetchone()
    if row["created"]:
        events.publish(conn, "source_link.changed", f"{object}:{object_id}",
                       {"object": object, "id": object_id, "system": system, "type": object_type, "key": str(key),
                        "by": by})
    return row


def links(conn, object: str, object_id: int) -> list[dict]:
    return conn.execute("select * from work_source_link where object = %s and object_id = %s order by system, key",
                        (object, object_id)).fetchall()


def status_map(conn, system: str | None = None) -> list[dict]:
    seed(conn)
    return conn.execute(
        "select * from work_status_map where %s::text is null or system = %s order by system, object_type, status, "
        "user_status", (system, system)).fetchall()


def set_status(conn, system: str, object_type: str, status: str, state: str, by: str, user_status: str = "",
               move: str | None = None, note: str | None = None) -> dict:
    """Add or change one row of a system's mapping."""
    if not all(x and str(x).strip() for x in (system, object_type, status, state)):
        raise LinkError("a mapping row needs a system, an object type, a status and a Fuzer state")
    if object_type in ("notification",) and state not in ("open", "closed"):
        raise LinkError("a notification's state is open or closed")
    if object_type not in ("notification",) and state not in RANK:
        raise LinkError(f"a job's state is one of {', '.join(RANK)}")
    seed(conn, by)
    row = conn.execute(
        """
        insert into work_status_map (system, object_type, status, user_status, state, move, note, created_by, updated_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (system, object_type, status, user_status) do update set state = excluded.state,
            move = excluded.move, note = excluded.note, updated_by = excluded.updated_by, updated_at = now()
        returning *
        """, (system.strip().lower(), object_type.strip().lower(), status.strip().upper(),
              (user_status or "").strip().upper(), state, move, note, by, by)).fetchone()
    clear_mapped(conn, by)
    events.publish(conn, "status_map.changed", f"status_map:{row['id']}",
                   {"system": row["system"], "type": row["object_type"], "status": row["status"],
                    "user_status": row["user_status"], "state": state, "by": by})
    return row


def _notification_cases(conn) -> dict[str, int]:
    """Each SAP order's notification case: the order's `for_notification` link, to the open notification Fuzer
    raised for it."""
    return {r["order_key"]: r["item_id"] for r in conn.execute(
        """
        select ok.key as order_key, e.item_id
        from entity_key ok join edge x on x.src = ok.fuzer_id and x.rel = 'for_notification'
        join entity_key nk on nk.fuzer_id = x.dst and nk.kind = 'notification'
        join exception_record e on e.type = 'sap_notification' and e.key = nk.key
        where ok.kind = 'work_order' and e.item_id is not null and e.joined_to is null
        """)}


def sync_orders(conn, by: str = "sap") -> dict:
    """Each SAP order's job with exactly one case: its notification's case when Fuzer holds one, else a case of its own
    in the planner's queue, which closes when SAP closes the order. Runs after plan.sync_work_orders, which links
    each job to its order."""
    via = _notification_cases(conn)
    opened = joined = closed = 0
    for j in conn.execute("select * from plan_item where source = 'sap' and source_key is not null").fetchall():
        key, open_job = j["source_key"], j["status"] not in ("done", "cancelled")
        case_id = via.get(key) or j["item_id"]
        if case_id is None and open_job:
            case_id = routing.open_task(conn, ORDER_CASE, j["title"], by, f"sap_order:{key}", role=PLANNER,
                                        data={"order": key, "plan_item_id": j["id"]},
                                        subjects=list(j["subjects"] or []), source="sap")["id"]
            opened += 1
        if case_id and j["item_id"] != case_id:
            joined += bool(via.get(key))
            conn.execute("update plan_item set item_id = %s where id = %s", (case_id, j["id"]))
        if case_id:
            status = (conn.execute("select status from work_source_link where object = 'job' and object_id = %s and "
                                   "system = 'sap'", (j["id"],)).fetchone() or {}).get("status")
            link(conn, "case", case_id, "sap", "order", key, status, by=by)
        if not open_job and case_id and case_id != via.get(key):
            c = conn.execute("select state, type from work_item where id = %s", (case_id,)).fetchone()
            if c and c["type"] == ORDER_CASE and c["state"] not in ("done", "cancelled"):
                work.close_case(conn, case_id, j["status"], by, reason=f"SAP {'closed' if j['status'] == 'done' else 'cancelled'} order {key}")
                closed += 1
    return {"opened": opened, "joined": joined, "closed": closed}
