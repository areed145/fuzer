"""A case's checklist: what the case asks for, ticked off as it is done.

A step is the engine's: one task in the case's workflow, held by one person or queue. A multi-part ask (the assistant's
weekly look-ahead: confirm a WO, raise three orders, check the standby pumps) runs as one step on the standard task
workflow, so its parts are kept here, each with the entities it is about. Whoever holds the case ticks each item done,
or marks it not needed with why; every change goes in the case's history. Closing the case keeps the list as the
record of what was done.

An item that belongs to another queue (a permit, a spares decision) is sent as its own case: a child case on the same
standard task workflow, linked both ways (`work_check.child_item_id`, `work_item.parent_id`). The item reads as done
when that case is done; until then it is open, and the parent case says so when it is finished.
"""

import secrets

from .definitions import NotFound, WorkError, _event, _op

STATES = ("open", "done", "skipped")
MAX_ITEMS, MAX_TEXT, MAX_TITLE = 30, 300, 160
TASK_PROCESS = "task"

# An item is ticked off when it was marked done or not needed, or the case it was sent as is done.
_TICKED = "(c.state <> 'open' or ch.state = 'done')"
# The list's progress on a case row (`i` is work_item): null when it has no checklist.
PROGRESS_SQL = f"""(select jsonb_build_object('done', count(*) filter (where {_TICKED}), 'total', count(*))
                   from work_check c left join work_item ch on ch.id = c.child_item_id
                   where c.item_id = i.id having count(*) > 0) as checklist"""
# Each item as people read it: a sent item takes its state from its case.
_ITEMS = """
    select c.id, c.position, c.text, c.subjects,
           case when c.state = 'open' and ch.state = 'done' then 'done' else c.state end as state,
           c.note, c.done_by, coalesce(c.done_at, case when ch.state = 'done' then ch.closed_at end) as done_at,
           case when ch.id is null then null else jsonb_build_object(
               'id', ch.id, 'title', ch.title, 'state', ch.state, 'closed_at', ch.closed_at,
               'assignee', ch.assignee, 'role', ch.role) end as child
    from work_check c left join work_item ch on ch.id = c.child_item_id
    where c.item_id = %(item)s and (%(id)s::bigint is null or c.id = %(id)s)
    order by c.position, c.id
"""


def items(conn, item_id: int) -> list[dict]:
    return conn.execute(_ITEMS, {"item": item_id, "id": None}).fetchall()


def _one(conn, item_id: int, check_id: int) -> dict:
    return conn.execute(_ITEMS, {"item": item_id, "id": check_id}).fetchone()


def item_text(text: str) -> str:
    text = " ".join((text or "").split())
    if not text or len(text) > MAX_TEXT:
        raise WorkError(f"a checklist item needs 1 to {MAX_TEXT} characters")
    return text


def _open_case(conn, item_id: int) -> dict:
    row = conn.execute("select id, title, subjects, closed_at from work_item where id = %s for update",
                       (item_id,)).fetchone()
    if row is None:
        raise NotFound("unknown case")
    if row["closed_at"] is not None:
        raise WorkError("the case is closed: its checklist is its record")
    return row


def _check(conn, item_id: int, check_id: int) -> dict:
    row = conn.execute("select * from work_check where id = %s and item_id = %s", (check_id, item_id)).fetchone()
    if row is None:
        raise NotFound("unknown checklist item")
    return row


def insert(conn, item_id: int, by: str, entries: list[dict]) -> list[dict]:
    """Add items at the end, in the caller's transaction (a confirmed draft adds its steps as it opens the case).
    Each entry is {text, subjects?}."""
    _open_case(conn, item_id)
    have = conn.execute("select count(*) as n, coalesce(max(position), 0) as last from work_check where item_id = %s",
                        (item_id,)).fetchone()
    if have["n"] + len(entries) > MAX_ITEMS:
        raise WorkError(f"a checklist holds at most {MAX_ITEMS} items; send the rest as their own case")
    ids = [conn.execute(
        "insert into work_check (item_id, position, text, subjects, created_by) values (%s, %s, %s, %s, %s) "
        "returning id", (item_id, have["last"] + n, item_text(e.get("text", "")), list(e.get("subjects") or []), by),
    ).fetchone()["id"] for n, e in enumerate(entries, start=1)]
    out = [_one(conn, item_id, i) for i in ids]
    if out:
        _event(conn, "checklist_added", by, item_id=item_id, data={"items": [r["text"] for r in out]})
    return out


@_op
def add(conn, item_id: int, by: str, entries: list[dict]) -> list[dict]:
    return insert(conn, item_id, by, entries)


@_op
def mark(conn, item_id: int, check_id: int, by: str, state: str, note: str | None = None) -> dict:
    """Tick an item done, mark it not needed (`skipped`, which needs why) or open it again."""
    if state not in STATES:
        raise WorkError(f"a checklist item is {', '.join(STATES)}")
    note = (note or "").strip() or None
    if state == "skipped" and not note:
        raise WorkError("say why it is not needed")
    _open_case(conn, item_id)
    row = _check(conn, item_id, check_id)
    conn.execute(
        "update work_check set state = %(state)s, note = %(note)s, "
        "done_by = case when %(state)s = 'open' then null else %(by)s end, "
        "done_at = case when %(state)s = 'open' then null else now() end where id = %(id)s",
        {"state": state, "note": note, "by": by, "id": check_id})
    _event(conn, "checked", by, item_id=item_id, data={"item": row["text"], "state": state,
                                                       **({"note": note} if note else {})})
    return _one(conn, item_id, check_id)


@_op
def edit(conn, item_id: int, check_id: int, by: str, text: str) -> dict:
    _open_case(conn, item_id)
    before = _check(conn, item_id, check_id)
    after = item_text(text)
    conn.execute("update work_check set text = %s where id = %s", (after, check_id))
    if after != before["text"]:
        _event(conn, "checklist_edited", by, item_id=item_id, data={"from": before["text"], "to": after})
    return _one(conn, item_id, check_id)


@_op
def remove(conn, item_id: int, check_id: int, by: str) -> dict:
    _open_case(conn, item_id)
    row = _check(conn, item_id, check_id)
    if row["child_item_id"] and conn.execute("select closed_at from work_item where id = %s",
                                             (row["child_item_id"],)).fetchone()["closed_at"] is None:
        raise WorkError(f"it was sent as case {row['child_item_id']}, which is still open: cancel that case first")
    conn.execute("delete from work_check where id = %s", (check_id,))
    _event(conn, "checklist_removed", by, item_id=item_id, data={"item": row["text"]})
    return {"id": check_id, "removed": True}


@_op
def send(conn, item_id: int, check_id: int, by: str, to: str, note: str | None = None) -> dict:
    """Send an item as its own case to `to`: a role's queue (role:<name>) on the standard task workflow, with its SLA
    and escalation, or a named person. The new case is part of this one, about the item's entities (else the case's),
    and the item is done when it is done. An item whose case was cancelled can be sent again."""
    from . import roles, runtime, work_items

    parent = _open_case(conn, item_id)
    row = _check(conn, item_id, check_id)
    if row["state"] != "open":
        raise WorkError("only an open item is sent as a case")
    if row["child_item_id"]:
        child = conn.execute("select state, closed_at from work_item where id = %s", (row["child_item_id"],)).fetchone()
        if child["closed_at"] is None or child["state"] == "done":
            raise WorkError(f"it is already case {row['child_item_id']}")
    title = row["text"] if len(row["text"]) <= MAX_TITLE else row["text"][:MAX_TITLE - 1].rstrip() + "…"
    note = (note or "").strip() or None
    data = {"summary": note or row["text"], "parent_case": item_id, "source": "checklist"}
    subjects = list(row["subjects"] or parent["subjects"] or [])
    lane = to.removeprefix("role:") if to.startswith("role:") else None
    if lane:
        found = roles.role(conn, lane)
        if found is None or found["kind"] != "person":
            raise WorkError(f"{lane!r} is not a person's queue")
    published = conn.execute("select 1 from work_definition where process_key = %s and state = 'published' limit 1",
                             (TASK_PROCESS,)).fetchone()
    # the ops' own commits would end this transaction, so their unwrapped bodies run here; a fresh key, so an item
    # sent again after its case was cancelled starts a new case
    if lane and published:
        child_id = runtime.start_or_correlate.__wrapped__(
            conn, TASK_PROCESS, {**data, "task_key": f"check:{check_id}:{secrets.token_hex(4)}", "role": lane,
                                 "case_type": "task"}, by=by, title=title, subjects=subjects)["item_id"]
    else:
        child_id = work_items.create_item.__wrapped__(conn, "task", title, by, subjects, role=lane,
                                                      assignee=None if lane else to, source="checklist",
                                                      payload=data)["id"]
    conn.execute("update work_item set parent_id = %s where id = %s", (item_id, child_id))
    conn.execute("update work_check set child_item_id = %s where id = %s", (child_id, check_id))
    _event(conn, "checklist_sent", by, item_id=item_id, data={"item": row["text"], "case": child_id, "to": to,
                                                              **({"note": note} if note else {})})
    return _one(conn, item_id, check_id)
