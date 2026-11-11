"""Work the assistant proposes and a person creates.

Agents read; people decide. So the assistant never writes to a queue: it leaves a draft owned by
whoever asked, and only that person, with operate access, can turn it into a work item. A draft
becomes one item at most, whatever is resent.
"""

import secrets

from . import roles, work_items
from .definitions import NotFound, WorkError, _op

MAX_TITLE, MAX_SUMMARY = 160, 2000
TYPE, SOURCE = "task", "assistant"
TASK_PROCESS = "task"  # the standard process a confirmed draft runs as (definitions/task.bpmn)


@_op
def create(conn, title: str, role: str, summary: str, evidence: list[str], by: str,
           subjects: list[str] | None = None) -> dict:
    title, summary = title.strip(), summary.strip()
    if not title or len(title) > MAX_TITLE:
        raise WorkError(f"a title needs 1 to {MAX_TITLE} characters")
    if not summary or len(summary) > MAX_SUMMARY:
        raise WorkError(f"a summary needs 1 to {MAX_SUMMARY} characters")
    if not evidence:
        raise WorkError("a draft must cite the evidence for it")
    found = roles.role(conn, role)
    if found is None or found["kind"] != "person":
        people = sorted(r["name"] for r in conn.execute("select name from work_role where kind = 'person'"))
        raise WorkError(f"{role!r} is not a person's queue; use one of {', '.join(people)}")
    return conn.execute(
        "insert into work_draft (id, title, role, summary, evidence, subjects, by_user) "
        "values (%s, %s, %s, %s, %s, %s, %s) returning *",
        ("d_" + secrets.token_hex(8), title, role, summary, evidence, subjects or [], by)).fetchone()


def get(conn, draft_id: str) -> dict:
    row = conn.execute(
        """select d.*, i.state as item_state from work_draft d left join work_item i on i.id = d.item_id
           where d.id = %s""", (draft_id,)).fetchone()
    if row is None:
        raise NotFound("unknown draft")
    return row


@_op
def confirm(conn, draft_id: str, by: str) -> dict:
    """The person's decision. The row lock and one transaction make a resend return the same item."""
    draft = conn.execute("select * from work_draft where id = %s for update", (draft_id,)).fetchone()
    if draft is None:
        raise NotFound("unknown draft")
    if draft["item_id"] is not None:
        return {"item_id": draft["item_id"], "created": False}
    found = roles.role(conn, draft["role"])
    if found is None or found["kind"] != "person":
        raise WorkError(f"the {draft['role']} queue no longer exists")
    payload = {"summary": draft["summary"], "evidence": draft["evidence"], "draft": draft_id}
    # The ops' own commits would end this transaction, and the lock, so their unwrapped bodies run here.
    if conn.execute("select 1 from work_definition where process_key = %s and state = 'published' limit 1",
                    (TASK_PROCESS,)).fetchone():  # the standard task process: the queue's SLA and escalation (#509)
        from . import runtime

        item_id = runtime.start_or_correlate.__wrapped__(
            conn, TASK_PROCESS, {**payload, "task_key": f"draft:{draft_id}", "role": draft["role"], "case_type": TYPE,
                                 "source": SOURCE},
            by=by, title=draft["title"], subjects=draft["subjects"])["item_id"]
    else:
        item_id = work_items.create_item.__wrapped__(conn, TYPE, draft["title"], by, draft["subjects"],
                                                     role=draft["role"], source=SOURCE, payload=payload)["id"]
    conn.execute("update work_draft set item_id = %s, confirmed_by = %s, confirmed_at = now() where id = %s",
                 (item_id, by, draft_id))
    return {"item_id": item_id, "created": True}
