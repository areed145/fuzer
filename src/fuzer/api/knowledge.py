"""Lessons learned, troubleshooting briefs and feedback."""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from ..graph import knowledge, lessons
from . import deps
from .deps import get_conn

from .security import AuthenticatedRoute, current, require

router = APIRouter(route_class=AuthenticatedRoute)


@router.get("/lessons")
def lesson_list(status: str | None = "suggested", limit: int = 100, conn=Depends(get_conn)):
    return lessons.listing(conn, status if status != "all" else None, min(limit, 1000))


class LessonDecision(BaseModel):
    decision: Literal["approve", "reject", "edit"]
    text: str | None = None
    by: str = "anonymous"


@router.post("/lessons/{lesson_id}")
def lesson_decide(lesson_id: int, d: LessonDecision, conn=Depends(get_conn)):
    if (row := lessons.decide(conn, lesson_id, d.decision, d.by, d.text)) is None:
        raise HTTPException(404, "unknown lesson")
    knowledge.build(conn)  # approved lessons appear in search straight away
    conn.commit()
    return row


@router.get("/entities/{fuzer_id}/brief")
def troubleshooting_brief(fuzer_id: str, conn=Depends(get_conn)):
    """Everything that helps troubleshoot a unit or incident, with sources."""
    from ..agents import assistant
    return assistant.brief(conn, deps.PACK, fuzer_id)


class Feedback(BaseModel):
    target: str
    useful: bool
    comment: str | None = None
    by: str = "anonymous"


@router.post("/feedback")
def feedback(f: Feedback, background: BackgroundTasks, conn=Depends(get_conn)):
    """A rating. An assistant answer rated useful is learned from (memory.learn, after the response); one rated
    not useful has the facts learned from it dropped (#93)."""
    from ..agents import memory

    person = current.get()
    if person and f.target.startswith("answer:"):
        answer_id = f.target.split(":", 1)[1]
        answer = conn.execute("select by_user from assistant_answer where id = %s", (answer_id,)).fetchone()
        if not answer or answer["by_user"] != person.actor:
            raise HTTPException(404, "Answer not found in your account")
        if f.useful:
            require(person, "ai")
    row = conn.execute("insert into feedback (target, useful, comment, by_user) values (%s, %s, %s, %s) returning id",
                       (f.target, f.useful, f.comment, f.by)).fetchone()
    conn.commit()
    if f.target.startswith("answer:"):
        answer_id = f.target.split(":", 1)[1]
        if f.useful:
            background.add_task(memory.learn_later, answer_id, None, f.by)
        else:
            memory.forget_episode(conn, answer_id)
    return row
