"""Ops IQ, Evals: how well each skill answers its questions and how sound what the agents wrote is, with the questions
people keep for each skill. Reading calls no model; running a skill's eval and grading a run do (AI access)."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from .deps import get_conn
from .security import AuthenticatedRoute, current

router = APIRouter(route_class=AuthenticatedRoute)


def _call(fn, *args):
    try:
        return fn(*args)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


@router.get("/evals")
def evals_overview(conn=Depends(get_conn)):
    """Every eval subject with its latest run, its trend and its questions."""
    from ..agents import eval_runs

    return eval_runs.overview(conn)


@router.get("/evals/subject")
def evals_subject(subject: str, conn=Depends(get_conn)):
    """One subject's runs (recorded ones too) and, for a skill, its questions."""
    from ..agents import eval_runs

    return _call(eval_runs.history, conn, subject)


@router.get("/evals/runs/{run_id}")
def evals_run(run_id: str, conn=Depends(get_conn)):
    from ..agents import eval_runs

    if (r := eval_runs.get(conn, run_id)) is None:
        raise HTTPException(404, "no such eval run")
    return r


class SkillRun(BaseModel):
    skill: str
    by: str = "anonymous"


@router.post("/evals/skills/run")
def evals_skill_run(r: SkillRun, background: BackgroundTasks, conn=Depends(get_conn)):
    """Ask a skill's eval questions of its live version, after the response; 503 when no model may be called."""
    from ..agents import eval_runs

    try:
        run = _call(eval_runs.start_skill, conn, r.skill, r.by)
    except eval_runs.Unavailable as e:
        raise HTTPException(503, str(e)) from None
    background.add_task(eval_runs.ask_skill, run["id"])
    return run


class OutputsRun(BaseModel):
    subject: str
    by: str = "anonymous"
    n: int = Field(20, ge=1, le=100)


@router.post("/evals/outputs/run")
def evals_outputs_run(r: OutputsRun, background: BackgroundTasks, conn=Depends(get_conn)):
    """Check the latest kept summaries, notes or daily reports now (no model); then grade them after the response,
    when the person may spend on AI and a model may be called."""
    from ..agents import eval_runs

    run = _call(eval_runs.run_outputs, conn, r.subject, r.by, r.n)
    person = current.get()
    if run["scores"].get("items"):
        if person is None or person.allows("ai"):
            background.add_task(eval_runs.grade_run, run["id"])
        else:
            conn.execute("update ai_eval_run set grade_why = %s where id = %s",
                         ("Grading asks a model: someone with AI access grades it.", run["id"]))
            conn.commit()
    return eval_runs.get(conn, str(run["id"]))


@router.post("/evals/runs/{run_id}/grade")
def evals_grade(run_id: int, conn=Depends(get_conn)):
    """Grade a run now; without a model it stays waiting and says why."""
    from ..agents import eval_runs

    if eval_runs.get(conn, str(run_id)) is None:
        raise HTTPException(404, "no such eval run")
    return eval_runs.grade_run(run_id, conn=conn)


class QuestionEdit(BaseModel):
    skill: str
    key: str | None = None
    question: str
    must_mention: list[str | list[str]] = Field(default_factory=list)
    specialists: list[str] = Field(default_factory=list)
    note: str | None = None
    by: str = "anonymous"


class QuestionAct(BaseModel):
    skill: str
    key: str
    by: str = "anonymous"


@router.put("/evals/questions")
def evals_question_save(q: QuestionEdit, conn=Depends(get_conn)):
    """Add a question to a skill's eval (no key), edit a kept one, or override a shipped one."""
    from ..agents import eval_questions

    return _call(eval_questions.save, conn, q.skill, q.key, q.question, q.must_mention, q.specialists, q.by, q.note)


@router.post("/evals/questions/retire")
def evals_question_retire(a: QuestionAct, conn=Depends(get_conn)):
    from ..agents import eval_questions

    return _call(eval_questions.retire, conn, a.skill, a.key, a.by)


@router.post("/evals/questions/restore")
def evals_question_restore(a: QuestionAct, conn=Depends(get_conn)):
    from ..agents import eval_questions

    return _call(eval_questions.restore, conn, a.skill, a.key, a.by)
