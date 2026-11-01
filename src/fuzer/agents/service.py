"""The operations assistant behind the API: one team per server process, conversations kept in memory,
and each question run as a background job the web page polls for progress (the tools each agent is
calling) and then the answer. Answers are saved in assistant_answer so engineers can rate them.
"""

import asyncio
import json
import os
import uuid

from .. import db

_team = None
_sessions: dict[str, object] = {}
_jobs: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()
MAX_JOBS = 200


class Unavailable(RuntimeError):
    pass


def team():
    global _team
    if _team is None:
        from . import team as make_team

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise Unavailable("Set ANTHROPIC_API_KEY (in .env) to use the operations assistant.")
        _team = make_team()
    return _team


def start(question: str, conversation_id: str | None = None, by: str = "anonymous") -> dict:
    """Start answering in the background; returns the job id and conversation id at once."""
    from . import ask

    agent = team()
    conversation_id = conversation_id if conversation_id in _sessions else uuid.uuid4().hex
    session = _sessions.setdefault(conversation_id, agent.create_session())
    job_id = uuid.uuid4().hex[:16]
    job = {"id": job_id, "conversation_id": conversation_id, "question": question, "status": "running",
           "trace": [], "answer": None, "error": None}
    _jobs[job_id] = job
    while len(_jobs) > MAX_JOBS:
        _jobs.pop(next(iter(_jobs)))

    async def run():
        try:
            out = await ask(question, agent, session, job["trace"])
            job.update(status="done", answer=out["answer"], usage=out["usage"], seconds=out["seconds"],
                       specialists=out["specialists"], tools=out["tools"])
        except Exception as e:  # shown on the page; the conversation can carry on
            job.update(status="error", error=f"{type(e).__name__}: {e}")
        _save(job, by)

    task = asyncio.get_running_loop().create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job_id, "conversation_id": conversation_id}


def _save(job: dict, by: str) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            insert into assistant_answer (id, conversation_id, question, answer, error, specialists, tools, usage,
                                          seconds, by_user)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (id) do nothing
            """,
            (job["id"], job["conversation_id"], job["question"], job.get("answer"), job.get("error"),
             job.get("specialists") or [], json.dumps(job.get("tools") or []), json.dumps(job.get("usage") or {}),
             job.get("seconds"), by),
        )
        conn.commit()


def status(job_id: str) -> dict | None:
    """Where a job is: the steps so far (which agent called which tool), then the answer."""
    job = _jobs.get(job_id)
    if job is None:
        return None
    steps = [{"agent": t["agent"], "tool": t["tool"], "args": t["args"]} for t in job["trace"] if t["kind"] == "tool"]
    calls = sum(1 for t in job["trace"] if t["kind"] == "model")
    return {k: job.get(k) for k in ("id", "conversation_id", "question", "status", "answer", "error", "usage",
                                    "seconds", "specialists")} | {"steps": steps, "model_calls": calls}
