"""A case's checklist: what the case asks for, ticked off as it is done.

A step is the engine's: one task in the case's workflow, held by one person or queue. A multi-part ask (the assistant's
weekly look-ahead: confirm a WO, raise three orders, check the standby pumps) runs as one step on the standard task
workflow, so its parts are kept here, each with the entities it is about. Whoever holds the case ticks each item done,
or marks it not needed with why; every change goes in the case's history. Closing the case keeps the list as the
record of what was done.
"""

from .definitions import NotFound, WorkError, _event, _op

STATES = ("open", "done", "skipped")
MAX_ITEMS, MAX_TEXT = 30, 300

# The list's progress on a case row (`i` is work_item): null when it has no checklist.
PROGRESS_SQL = """(select jsonb_build_object('done', count(*) filter (where c.state <> 'open'), 'total', count(*))
                   from work_check c where c.item_id = i.id having count(*) > 0) as checklist"""


def items(conn, item_id: int) -> list[dict]:
    return conn.execute("select id, position, text, subjects, state, note, done_by, done_at from work_check "
                        "where item_id = %s order by position, id", (item_id,)).fetchall()


def open_count(conn, item_id: int) -> int:
    return conn.execute("select count(*) as n from work_check where item_id = %s and state = 'open'",
                        (item_id,)).fetchone()["n"]


def item_text(text: str) -> str:
    text = " ".join((text or "").split())
    if not text or len(text) > MAX_TEXT:
        raise WorkError(f"a checklist item needs 1 to {MAX_TEXT} characters")
    return text


def _open_case(conn, item_id: int) -> dict:
    row = conn.execute("select id, closed_at from work_item where id = %s for update", (item_id,)).fetchone()
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
    out = []
    for n, e in enumerate(entries, start=1):
        out.append(conn.execute(
            "insert into work_check (item_id, position, text, subjects, created_by) values (%s, %s, %s, %s, %s) "
            "returning id, position, text, subjects, state, note, done_by, done_at",
            (item_id, have["last"] + n, item_text(e.get("text", "")), list(e.get("subjects") or []), by)).fetchone())
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
    _check(conn, item_id, check_id)
    row = conn.execute(
        "update work_check set state = %(state)s, note = %(note)s, "
        "done_by = case when %(state)s = 'open' then null else %(by)s end, "
        "done_at = case when %(state)s = 'open' then null else now() end "
        "where id = %(id)s returning id, position, text, subjects, state, note, done_by, done_at",
        {"state": state, "note": note, "by": by, "id": check_id}).fetchone()
    _event(conn, "checked", by, item_id=item_id, data={"item": row["text"], "state": state,
                                                       **({"note": note} if note else {})})
    return row


@_op
def edit(conn, item_id: int, check_id: int, by: str, text: str) -> dict:
    _open_case(conn, item_id)
    before = _check(conn, item_id, check_id)
    row = conn.execute("update work_check set text = %s where id = %s "
                       "returning id, position, text, subjects, state, note, done_by, done_at",
                       (item_text(text), check_id)).fetchone()
    if row["text"] != before["text"]:
        _event(conn, "checklist_edited", by, item_id=item_id, data={"from": before["text"], "to": row["text"]})
    return row


@_op
def remove(conn, item_id: int, check_id: int, by: str) -> dict:
    _open_case(conn, item_id)
    row = _check(conn, item_id, check_id)
    conn.execute("delete from work_check where id = %s", (check_id,))
    _event(conn, "checklist_removed", by, item_id=item_id, data={"item": row["text"]})
    return {"id": check_id, "removed": True}
