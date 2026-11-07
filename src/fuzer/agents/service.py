"""The operations assistant behind the API: one team per server process, and each question run as a
background job the web page polls for progress (the tools each agent is calling) and then the answer.
Conversations are kept in Postgres (history.PostgresHistory, #93): each question gets a fresh session on
its conversation id and the history is loaded from the database, so a follow-up works after a restart.
Answers are saved in assistant_answer so engineers can rate them.
"""

import asyncio
import hashlib
import json
import time
import uuid

from ..core import db, llm, settings
from .capabilities import ASKER

_team = None
_purged_at = 0.0
_jobs: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()
MAX_JOBS = 200


class Unavailable(RuntimeError):
    pass


class Busy(RuntimeError):
    pass


def team():
    global _team
    if _team is None:
        from . import team as make_team
        from .history import PostgresHistory

        if not llm.configured("agents"):
            raise Unavailable("Configure the agents provider API key to use the operations assistant.")
        _team = make_team(history=PostgresHistory())
    return _team


def start(question: str, conversation_id: str | None = None, by: str = "anonymous",
          images: list[tuple[str, bytes]] | None = None) -> dict:
    """Start answering in the background; returns the job id and conversation id at once. `images`
    (media type, bytes) go to the configured provider with the question."""
    from . import ask, history

    if sum(j["status"] == "running" for j in _jobs.values()) >= settings.get(None, "assistant.concurrent_questions"):
        raise Busy("The assistant is answering another question. Wait for it to finish and try again.")
    agent = team()
    with db.connect() as conn:
        _purge(conn)
        if not history.exists(conn, conversation_id):
            conversation_id = uuid.uuid4().hex
            history.ensure(conn, conversation_id, by, question)
            conn.commit()
    session = agent.create_session(session_id=conversation_id)
    job_id = uuid.uuid4().hex[:16]
    job = {"id": job_id, "conversation_id": conversation_id, "status": "running", "by_user": by,
           "question": question + (f" [{len(images)} image(s) attached]" if images else ""),
           "images": [hashlib.sha256(data).hexdigest() for _, data in images or []],
           "trace": [], "answer": None, "error": None}
    _jobs[job_id] = job
    while len(_jobs) > MAX_JOBS:
        finished = next((key for key, value in _jobs.items() if value["status"] != "running"), None)
        if finished is None:
            break
        _jobs.pop(finished)

    async def run():
        ASKER.set(by)  # this task's own context: what the assistant drafts belongs to who asked
        try:
            async with asyncio.timeout(settings.get(None, "assistant.timeout_seconds")):
                out = await ask(question, agent, session, job["trace"], images)
            job.update(status="done", answer=out["answer"], usage=out["usage"], seconds=out["seconds"],
                       specialists=out["specialists"], tools=out["tools"], recalled=out["recalled"])
        except TimeoutError:
            job.update(status="error", error="This question exceeded the time limit. Try a narrower question or time window.")
        except Exception as e:  # shown on the page; the conversation can carry on
            job.update(status="error", error=f"{type(e).__name__}: {e}")
        _save(job, by)

    task = asyncio.get_running_loop().create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job_id, "conversation_id": conversation_id}


def _purge(conn) -> None:
    """Drop conversations past assistant.retention_days, at most once an hour."""
    global _purged_at
    if time.time() - _purged_at > 3600:
        _purged_at = time.time()
        from .history import purge

        purge(conn, settings.get(conn, "assistant.retention_days"))


def _save(job: dict, by: str) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            insert into assistant_answer (id, conversation_id, question, answer, error, specialists, tools, usage,
                                          seconds, by_user, images, recalled)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (id) do nothing
            """,
            (job["id"], job["conversation_id"], job["question"], job.get("answer"), job.get("error"),
             job.get("specialists") or [], json.dumps(job.get("tools") or []), json.dumps(job.get("usage") or {}),
             job.get("seconds"), by, job["images"], job.get("recalled") or []),
        )
        conn.commit()


def status(job_id: str) -> dict | None:
    """Where a job is: the steps so far (which agent called which tool), then the answer."""
    job = _jobs.get(job_id)
    if job is None:
        return _saved(job_id)
    steps = [{"agent": t["agent"], "tool": t["tool"], "args": t["args"]} for t in job["trace"] if t["kind"] == "tool"]
    calls = sum(1 for t in job["trace"] if t["kind"] == "model")
    return {k: job.get(k) for k in ("id", "conversation_id", "question", "status", "answer", "error", "usage",
                                    "seconds", "specialists", "recalled")} | {"steps": steps, "model_calls": calls}


def owner(job_id: str) -> dict | None:
    job = _jobs.get(job_id)
    return {"by_user": job["by_user"]} if job else None


def _saved(job_id: str) -> dict | None:
    """A finished job from assistant_answer (after a restart the in-memory job is gone)."""
    with db.connect() as conn:
        a = conn.execute("select * from assistant_answer where id = %s", (job_id,)).fetchone()
    if a is None:
        return None
    steps = [{"agent": t["agent"], "tool": t["tool"], "args": t.get("args") or {}} for t in a["tools"] or []]
    return {"id": a["id"], "conversation_id": a["conversation_id"], "question": a["question"],
            "status": "error" if a["error"] else "done", "answer": a["answer"], "error": a["error"],
            "usage": a["usage"], "seconds": float(a["seconds"]) if a["seconds"] is not None else None,
            "specialists": a["specialists"], "recalled": a["recalled"], "steps": steps, "model_calls": (a["usage"] or {}).get("model_calls", 0)}
