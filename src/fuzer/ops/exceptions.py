"""The built-in exception implementation behind fuzer.core.exceptions: one `exception_record` per open condition,
matched to entities, routed to a case through the Work port, and published on the event outbox.

    raise_(conn, type, key, title, by, refs=..., priority=..., value_usd=..., evidence=..., source=..., assignee=...)
    clear(conn, type, key, by, reason)
    reconcile(conn)          close the ones whose case finished (the work tick runs it)
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
    t = TYPES.get(row["type"])  # its declaring module may not be loaded in this process (the work tick)
    out["audience"] = list(t.audience) if t else []
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
        if t.process:  # new evidence for the process already working it
            _case(conn, t, {**row, "evidence": evidence or {}}, by, assignee, role)
        return {**row, "raised": False}
    row = conn.execute(
        """
        insert into exception_record (type, key, title, source, priority, value_usd, subjects, unmatched, evidence)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning *
        """, (type, key, title, source or by, priority, value_usd, subjects, unmatched, Jsonb(evidence or {}))).fetchone()
    # The work engine runs each operation as its own transaction: a case that can't open rolls this insert back
    # with it, and one that opens commits it.
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


def reconcile(conn) -> dict:
    """Close open exceptions whose case finished (a person closed it, its process ended). If the condition
    still holds, its next raise opens a new exception and case."""
    rows = conn.execute(
        """
        update exception_record e set state = 'closed', closed_at = now(), updated_at = now()
        from work_item w where w.id = e.item_id and e.state = 'open' and w.state in ('done', 'cancelled')
        returning e.*
        """).fetchall()
    for row in rows:
        events.publish(conn, "exception.closed", f"exception:{row['id']}", _payload(row))
    return {"closed": len(rows)}


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


# ---- the IOC dispatching and the field reporting back -------------------------------------------------------------

FIELD_STATUSES = ("on_site", "done", "could_not_do", "note")
SEEN_SKIP = {"excursions", "priority", "health", "pre_diagnosis", "field", "exception_id", "type", "key"}


def _row(conn, exception_id: int, lock: bool = False) -> dict:
    row = conn.execute("select * from exception_record where id = %s" + (" for update" if lock else ""),
                       (exception_id,)).fetchone()
    if row is None:
        raise ExceptionError(f"no exception {exception_id}")
    return row


def dispatch(conn, exception_id: int, by: str, to: str, reason: str | None = None) -> dict:
    """Send an exception to someone (a person, `role:<role>`): open its case for them if it has none, else hand
    its case (or the case's open person step) to them. Publishes exception.dispatched."""
    row = _row(conn, exception_id, lock=True)
    if row["state"] != OPEN:
        raise ExceptionError(f"exception {exception_id} is {row['state']}")
    if row["item_id"]:
        work.dispatch(conn, row["item_id"], by, to, reason)
    else:
        t = TYPES.get(row["type"]) or ExceptionType(row["type"], row["type"])
        item_id = work.open_case(conn, t.case_type or t.name, row["title"], by, subjects=row["subjects"],
                                 priority=row["priority"], assignee=None if to.startswith("role:") else to,
                                 role=to if to.startswith("role:") else None, source=row["source"],
                                 payload={**row["evidence"], "exception_id": row["id"]})["id"]
        conn.execute("update exception_record set item_id = %s, updated_at = now() where id = %s", (item_id, row["id"]))
    row = _row(conn, exception_id)
    events.publish(conn, "exception.dispatched", f"exception:{row['id']}", {**_payload(row), "to": to, "by": by,
                                                                            "reason": reason})
    return row


def report(conn, exception_id: int, by: str, status: str, note: str | None = None) -> dict:
    """What a field operator says back: on site, done, could not do (with why) or a note. Kept on the exception's
    evidence (`field`) and its case's history, and published as exception.reported for the IOC. `done` on a case
    without a process closes it; a process's step is completed with its own form."""
    if status not in FIELD_STATUSES:
        raise ExceptionError(f"status must be one of {FIELD_STATUSES}")
    if status in ("could_not_do", "note") and not (note or "").strip():
        raise ExceptionError("say why (or what) in the note")
    row = _row(conn, exception_id, lock=True)
    entry = {"at": conn.execute("select now() as t").fetchone()["t"].isoformat(), "by": by, "status": status,
             "note": note}
    conn.execute("update exception_record set evidence = jsonb_set(evidence, '{field}', "
                 "coalesce(evidence->'field', '[]') || %s), updated_at = now() where id = %s",
                 (Jsonb([entry]), exception_id))
    if row["item_id"]:
        said = {"on_site": "On site", "done": "Done", "could_not_do": "Could not do", "note": "Note"}[status]
        work.comment(conn, row["item_id"], by, f"Field: {said}" + (f": {note}" if note else ""))
        has_process = conn.execute("select 1 from work_process where item_id = %s", (row["item_id"],)).fetchone()
        state = conn.execute("select state from work_item where id = %s", (row["item_id"],)).fetchone()["state"]
        if status == "done" and not has_process and state not in ("done", "cancelled"):
            work.close_case(conn, row["item_id"], "done", by, reason=note or "done in the field")
    row = _row(conn, exception_id)
    events.publish(conn, "exception.reported", f"exception:{row['id']}", {**_payload(row), **entry})
    return row


def _seen(evidence: dict) -> list[tuple[str, str]]:
    """The evidence a person reads at a glance: short scalar values, not the nested detail."""
    out = []
    for k, v in evidence.items():
        if k in SEEN_SKIP or isinstance(v, (dict, list)) or v is None or v == "":
            continue
        out.append((k, f"{v:g}" if isinstance(v, float) else str(v)[:120]))
    return out[:12]


def _assets(conn, ids: list[str]) -> list[dict]:
    """Each subject with its label, kind and a location: its own, else the nearest located thing it is part of."""
    if not ids:
        return []
    rows = conn.execute(
        """
        with recursive up (start, fuzer_id, depth) as (
            select s, s, 0 from unnest(%s::text[]) s
            union all
            select up.start, e.dst, up.depth + 1 from up join edge e on e.src = up.fuzer_id and e.rel = 'part_of'
            where up.depth < 3)
        select distinct on (s.id) s.id as fuzer_id, l.label, en.kind, g.cy as lat, g.cx as lon
        from unnest(%s::text[]) with ordinality s (id, n)
        left join entity_label l on l.fuzer_id = s.id
        left join entity en on en.fuzer_id = s.id
        left join up on up.start = s.id
        left join geo_index g on g.fuzer_id = up.fuzer_id
        order by s.id, g.fuzer_id is null, up.depth
        """, (ids, ids)).fetchall()
    order = {f: i for i, f in enumerate(ids)}
    return sorted(rows, key=lambda r: order[r["fuzer_id"]])


def field_jobs(conn, person: str) -> list[dict]:
    """The open exceptions whose case is with this person (the case, or its open person step), most urgent first,
    each with where to go, what was seen, what happened at the asset lately and the field log."""
    rows = conn.execute(
        """
        select e.*, w.state as case_state, w.assignee, w.role, coalesce(t.due_at, w.due) as due,
               t.id as task_id, t.name as task_name
        from exception_record e join work_item w on w.id = e.item_id
        left join lateral (select id, name, due_at, assignee from work_token
                           where item_id = w.id and kind = 'user' and state in ('ready', 'waiting')
                           order by due_at nulls last, id limit 1) t on true
        where e.state = 'open' and w.state not in ('done', 'cancelled')
          and (t.assignee = %(p)s or (t.id is null and w.assignee = %(p)s))
        order by coalesce(t.due_at, w.due) nulls last, e.value_usd desc nulls last, e.priority desc nulls last
        """, {"p": person}).fetchall()
    out = []
    for r in rows:
        history = conn.execute(
            "select id, title, state, first_seen from exception_record where id <> %s and subjects && %s "
            "and first_seen > now() - interval '90 days' order by first_seen desc limit 5",
            (r["id"], r["subjects"])).fetchall() if r["subjects"] else []
        t = TYPES.get(r["type"])
        out.append({**r, "label": t.label if t else r["type"].replace("_", " "), "assets": _assets(conn, r["subjects"]),
                    "seen": _seen(r["evidence"]), "history": history, "field": r["evidence"].get("field", [])})
    return out
