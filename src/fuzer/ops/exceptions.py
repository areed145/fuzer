"""The built-in exception implementation behind fuzer.core.exceptions: one `exception_record` per open condition,
matched to entities, routed to a case through the Work port, and published on the event outbox.

    raise_(conn, type, key, title, by, refs=..., priority=..., value_usd=..., evidence=..., source=..., assignee=...)
    clear(conn, type, key, by, reason)
    match(conn, refs)        -> (fuzer ids, refs that matched nothing)
    listing(conn, ...)       what the IOC (or the field) sees

States: open (the condition holds), cleared (it went away) and closed (its case finished while it held; the next
raise opens a new exception and a new case, so a closed case never hides a condition that came back).
"""

from psycopg.types.json import Jsonb

from ..core import events, work
from ..core.exceptions import TYPES, ExceptionError, ExceptionType

OPEN = "open"


def _type(name: str) -> ExceptionType:
    if name not in TYPES:
        raise ExceptionError(f"no exception type {name}; declare it with exception_type()")
    return TYPES[name]


def match(conn, refs) -> tuple[list[str], list[str]]:
    """Resolve references to Fuzer IDs: a Fuzer ID itself, `kind:key` through entity_key, `source/key` through
    the crosswalk. Returns (matched ids in first-seen order, refs that matched nothing)."""
    ids, unmatched = [], []
    for ref in dict.fromkeys(r for r in refs or () if r):
        found = [r["fuzer_id"] for r in conn.execute(
            """
            select fuzer_id from entity where fuzer_id = %(r)s and retired_at is null
            union select fuzer_id from entity_key where kind || ':' || key = %(r)s
            union select fuzer_id from crosswalk where source || '/' || source_key = %(r)s
            """, {"r": ref})]
        if found:
            ids += [f for f in sorted(found) if f not in ids]
        else:
            unmatched.append(ref)
    return ids, unmatched


def _payload(row: dict) -> dict:
    keep = ("id", "type", "key", "title", "source", "state", "priority", "value_usd", "subjects", "unmatched",
            "item_id", "raised_count")
    out = {k: row[k] for k in keep}
    out["audience"] = list(_type(row["type"]).audience)
    return out


def _case(conn, t: ExceptionType, row: dict, by: str, assignee: str | None, role: str | None) -> int | None:
    """Open the case that works it: start (or feed) the type's process, else a work item in its queue."""
    if t.process:
        data = {"exception_id": row["id"], "type": t.name, "key": row["key"], **row["evidence"]}
        return work.start_or_correlate(conn, t.process, data, by=by, title=row["title"], subjects=row["subjects"],
                                       priority=row["priority"])["item_id"]
    if not (assignee or role or t.role):
        return None
    return work.open_case(conn, t.case_type or t.name, row["title"], by, subjects=row["subjects"],
                          priority=row["priority"], assignee=assignee, role=None if assignee else (role or t.role),
                          source=row["source"], payload={**row["evidence"], "exception_id": row["id"]})["id"]


def _close_if_case_finished(conn, row: dict | None) -> dict | None:
    if row and row["item_id"]:
        item = conn.execute("select state from work_item where id = %s", (row["item_id"],)).fetchone()
        if item and item["state"] in ("done", "cancelled"):
            conn.execute("update exception_record set state = 'closed', closed_at = now(), updated_at = now() "
                         "where id = %s", (row["id"],))
            return None
    return row


def raise_(conn, type: str, key: str, title: str, by: str, refs=(), priority: float | None = None,
           value_usd: float | None = None, evidence: dict | None = None, source: str | None = None,
           assignee: str | None = None, role: str | None = None) -> dict:
    t = _type(type)
    if not key:
        raise ExceptionError("an exception needs a key: the source's own identity for the condition")
    subjects, unmatched = match(conn, refs)
    current = _close_if_case_finished(conn, conn.execute(
        "select * from exception_record where type = %s and key = %s and state = %s for update",
        (type, key, OPEN)).fetchone())
    if current:
        row = conn.execute(
            """
            update exception_record set title = %s, priority = coalesce(%s, priority),
                value_usd = coalesce(%s, value_usd), evidence = evidence || %s,
                subjects = array(select distinct unnest(subjects || %s::text[])),
                unmatched = array(select distinct unnest(unmatched || %s::text[])),
                raised_count = raised_count + 1, last_seen = now(), updated_at = now()
            where id = %s returning *
            """, (title, priority, value_usd, Jsonb(evidence or {}), subjects, unmatched, current["id"])).fetchone()
        return {**row, "raised": False}
    row = conn.execute(
        """
        insert into exception_record (type, key, title, source, priority, value_usd, subjects, unmatched, evidence)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning *
        """, (type, key, title, source or by, priority, value_usd, subjects, unmatched, Jsonb(evidence or {}))).fetchone()
    item_id = _case(conn, t, row, by, assignee, role)
    if item_id:
        row = conn.execute("update exception_record set item_id = %s where id = %s returning *",
                           (item_id, row["id"])).fetchone()
    case = conn.execute("select assignee, role from work_item where id = %s", (item_id,)).fetchone() if item_id else None
    events.publish(conn, "exception.raised", f"exception:{row['id']}", {**_payload(row), **(case or {})})
    return {**row, "raised": True}


def clear(conn, type: str, key: str, by: str, reason: str | None = None) -> dict | None:
    t = _type(type)
    row = conn.execute("select * from exception_record where type = %s and key = %s and state = %s for update",
                       (type, key, OPEN)).fetchone()
    if row is None:
        return None
    row = conn.execute("update exception_record set state = 'cleared', closed_at = now(), updated_at = now() "
                       "where id = %s returning *", (row["id"],)).fetchone()
    if row["item_id"] and t.close_case_on_clear and not t.process:
        item = conn.execute("select state from work_item where id = %s", (row["item_id"],)).fetchone()
        if item and item["state"] not in ("done", "cancelled"):
            work.close_case(conn, row["item_id"], "done", by, reason=reason or "the condition cleared")
    events.publish(conn, "exception.cleared", f"exception:{row['id']}", {**_payload(row), "reason": reason})
    return row


def listing(conn, state: str | None = OPEN, type: str | None = None, subject: str | None = None,
            audience: str | None = None, limit: int = 200) -> list[dict]:
    """Exceptions, most valuable then newest first, with the case that works them and who has it."""
    types = [n for n, t in TYPES.items() if audience is None or audience in t.audience]
    return conn.execute(
        """
        select e.*, w.state as case_state, w.assignee, w.role from exception_record e
        left join work_item w on w.id = e.item_id
        where (%(state)s::text is null or e.state = %(state)s) and (%(type)s::text is null or e.type = %(type)s)
          and (%(subject)s::text is null or %(subject)s = any(e.subjects))
          and (%(all)s or e.type = any(%(types)s))
        order by e.value_usd desc nulls last, e.priority desc nulls last, e.last_seen desc limit %(limit)s
        """, {"state": state, "type": type, "subject": subject, "all": audience is None, "types": types,
              "limit": limit}).fetchall()
