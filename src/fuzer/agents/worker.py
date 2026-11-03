"""The agent worker: claims agent steps, runs the agent for each and records its result.

    uv run fuzer work worker            # loops; several can run at once

A step is claimed with `for update skip locked` and a lease, committed before the agent runs, so
two workers never take the same step and a long agent call holds no locks. The result is recorded
in one transaction with moving the process on (engine.complete), and only by the worker still
holding the claim. If a worker dies mid-step its lease runs out and another worker takes the step
over; the agent gets the step's last checkpoint (`call.save(...)`) and `call.key`, a stable key for
anything it does outside Fuzer, so the work it had done isn't done twice. A step that raises is
retried with backoff and then given to a person (engine.agent_failed).

Agents propose; they never complete `requiresHuman` steps and have no tools that act on the plant.
"""

import logging
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from ..workflow import engine

log = logging.getLogger(__name__)
LEASE = timedelta(seconds=int(os.environ.get("FUZER_WORK_LEASE_SECONDS", "900")))
MAX_ATTEMPTS = 3


@dataclass
class AgentCall:
    """What an agent step gets: the step, its work item, the process data and the last checkpoint."""
    task_id: int
    role: str
    element: str
    name: str | None
    item: dict
    data: dict
    attempt: int
    checkpoint: dict | None
    _save: Callable[[dict], None] = field(repr=False, default=lambda c: None)

    @property
    def key(self) -> str:
        """Idempotency key for outside effects: the same on every retry of this step."""
        return f"fuzer-work-{self.task_id}"

    def save(self, checkpoint: dict) -> None:
        """Keep progress so a retry after a crash carries on from here (also renews the lease)."""
        self._save(checkpoint)
        self.checkpoint = checkpoint


Agent = Callable[[AgentCall], dict | None]


def claim(conn, worker: str, roles: list[str] | None = None, now: datetime | None = None,
          lease: timedelta = LEASE, task_id: int | None = None) -> dict | None:
    """Take the next agent step that is due and not held by a live lease (or that one step, if free)."""
    now = now or engine.utcnow()
    row = conn.execute(
        """
        update work_token set claimed_by = %(w)s, lease_until = %(until)s, attempts = attempts + 1
        where id = (
            select id from work_token
            where state = 'waiting' and kind = 'agent' and run_after <= %(now)s
              and (lease_until is null or lease_until < %(now)s) and (%(roles)s::text[] is null or role = any(%(roles)s))
              and (%(task)s::bigint is null or id = %(task)s)
            order by run_after, id limit 1 for update skip locked)
        returning *
        """, {"w": worker, "until": now + lease, "now": now, "roles": roles, "task": task_id}).fetchone()
    if row:
        engine._event(conn, "claimed", f"agent:{row['role']}", row["item_id"], row["process_id"], row["id"],
                      element=row["element"], data={"worker": worker, "attempt": row["attempts"]}, at=now)
    conn.commit()
    return row


def _call(conn, t: dict, worker: str, now: datetime, lease: timedelta) -> AgentCall:
    proc = conn.execute("select data from work_process where id = %s", (t["process_id"],)).fetchone()
    item = conn.execute("select id, type, title, subjects, priority, payload from work_item where id = %s",
                        (t["item_id"],)).fetchone()
    conn.commit()

    def save(checkpoint: dict):
        conn.execute("update work_token set checkpoint = %s, lease_until = greatest(lease_until, %s) "
                     "where id = %s and claimed_by = %s", (Jsonb(checkpoint), engine.utcnow() + lease, t["id"], worker))
        conn.commit()
    return AgentCall(t["id"], t["role"], t["element"], t["name"], item, proc["data"], t["attempts"], t["checkpoint"], save)


def run_once(conn, agents: dict[str, Agent], worker: str | None = None, now: datetime | None = None,
             lease: timedelta = LEASE, max_attempts: int = MAX_ATTEMPTS) -> dict | None:
    """Claim and run one agent step for the roles in `agents`; None when there is nothing to do."""
    worker = worker or default_worker()
    now = now or engine.utcnow()
    t = claim(conn, worker, sorted(agents), now, lease)
    if t is None:
        return None
    call = _call(conn, t, worker, now, lease)
    try:
        result = agents[t["role"]](call) or {}
    except Exception as e:  # noqa: BLE001 - any agent failure is retried, then goes to a person
        log.warning("agent step %s (%s) failed: %s", t["id"], t["role"], e)
        return engine.agent_failed(conn, t["id"], worker, f"{type(e).__name__}: {e}", now, max_attempts)
    try:
        return engine.complete(conn, t["id"], f"agent:{t['role']}", result, worker=worker, now=now)
    except engine.Conflict as e:  # the lease ran out and another worker has it: its result counts, not ours
        log.warning("dropped result of step %s: %s", t["id"], e)
        return {"task_id": t["id"], "dropped": str(e)}


def run(connect: Callable, agents: dict[str, Agent], worker: str | None = None, poll: float = 5.0,
        stop: Callable[[], bool] = lambda: False) -> None:
    """Keep running agent steps; sleep `poll` seconds when there are none."""
    worker = worker or default_worker()
    with connect() as conn:
        while not stop():
            if run_once(conn, agents, worker) is None:
                time.sleep(poll)


def default_worker() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def assistant_agents() -> dict[str, Agent]:
    """Agent steps run by the operations assistant's specialists (#80), read-only, one Claude call each.
    The answer is kept as `<step id>` in the process data, for the person who reviews it next."""

    from .. import agents

    def specialist(role: str) -> Agent:
        def run_step(call: AgentCall) -> dict:
            prompt = (f"Workflow step '{call.name or call.element}' on work item {call.item['id']} "
                      f"({call.item['type']}): {call.item['title']}. Subjects: {', '.join(call.item['subjects']) or 'none'}.\n"
                      f"Process data so far: {call.data}\n"
                      f"Ask the {role} specialist to do this step and propose, not take, any action.")
            answer = agents.ask_sync(prompt)
            return {call.element: {"answer": answer["answer"], "usage": answer["usage"]}}
        return run_step
    from ..ops import daily_report

    return {**{role: specialist(role) for role in agents.SPECIALISTS}, "reporter": daily_report.reporter_agent}
