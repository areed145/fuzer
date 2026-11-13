"""The agent worker: claims agent steps, runs the agent for each and records its result.

    uv run fuzer work worker            # loops; several can run at once

A step is claimed with `for update skip locked` and a lease, committed before the agent runs, so
two workers never take the same step and a long agent call holds no locks. The result is recorded
in one transaction with moving the process on (engine.complete), and only by the worker still
holding the claim. If a worker dies mid-step its lease runs out and another worker takes the step
over; the agent gets the step's last checkpoint (`call.save(...)`) and `call.key`, a stable key for
anything it does outside Fuzer, so the work it had done isn't done twice. A step that raises is
retried with backoff and then given to a person (engine.agent_failed).

Agents propose; they never complete `requiresHuman` steps, never set the data a person decides (the engine refuses
it, and the step goes to a person with the reason), and have no tools that act on the plant.
"""

import logging
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from ..core import settings
from ..workflow import engine

log = logging.getLogger(__name__)
LEASE = timedelta(seconds=900)  # the default of the setting work.lease_seconds (#131)
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
    instructions: str | None = None

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
          lease: timedelta | None = None, task_id: int | None = None) -> dict | None:
    """Take the next agent step that is due and not held by a live lease (or that one step, if free)."""
    lease = lease or timedelta(seconds=settings.get(conn, "work.lease_seconds"))
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
    lease = lease or timedelta(seconds=settings.get(conn, "work.lease_seconds"))
    proc = conn.execute("select p.data, p.definition_id, t.element, t.name from work_process p "
                        "join work_token t on t.process_id = p.id where t.id = %s", (t["id"],)).fetchone()
    node = engine._definition(conn, proc["definition_id"]).nodes[proc["element"]]
    item = conn.execute("select id, type, title, subjects, priority, payload from work_item where id = %s",
                        (t["item_id"],)).fetchone()
    conn.commit()

    def save(checkpoint: dict):
        conn.execute("update work_token set checkpoint = %s, lease_until = greatest(lease_until, %s) "
                     "where id = %s and claimed_by = %s", (Jsonb(checkpoint), engine.utcnow() + lease, t["id"], worker))
        conn.commit()
    return AgentCall(t["id"], t["role"], proc["element"], proc["name"], item, proc["data"], t["attempts"], t["checkpoint"],
                     instructions=node.attrs.get("instructions"), _save=save)


def run_once(conn, agents: dict[str, Agent], worker: str | None = None, now: datetime | None = None,
             lease: timedelta | None = None, max_attempts: int = MAX_ATTEMPTS) -> dict | None:
    """Claim and run one agent step for the roles in `agents`; None when there is nothing to do."""
    lease = lease or timedelta(seconds=settings.get(conn, "work.lease_seconds"))
    worker = worker or default_worker()
    now = now or engine.utcnow()
    t = claim(conn, worker, sorted(agents), now, lease)
    if t is None:
        return None
    over = _over_cap(conn, t["item_id"])
    if over:
        return engine.agent_failed(conn, t["id"], worker, over, now, max_attempts=0)
    call = _call(conn, t, worker, now, lease)
    try:
        result = agents[t["role"]](call) or {}
    except Exception as e:
        log.warning("agent step %s (%s) failed: %s", t["id"], t["role"], e)
        return engine.agent_failed(conn, t["id"], worker, f"{type(e).__name__}: {e}", now, max_attempts)
    try:
        return engine.complete(conn, t["id"], f"agent:{t['role']}", result, worker=worker, now=now)
    except engine.Conflict as e:  # the lease ran out and another worker has it: its result counts, not ours
        log.warning("dropped result of step %s: %s", t["id"], e)
        return {"task_id": t["id"], "dropped": str(e)}
    except engine.Forbidden as e:  # the result crossed the human-decision boundary (P4.4): a person takes the step
        log.warning("refused result of step %s: %s", t["id"], e)
        return engine.agent_failed(conn, t["id"], worker, f"refused: {e}", now, max_attempts=0)


def _over_cap(conn, item_id: int) -> str | None:
    """Why the case's agent budget stops this step (P4.4), or None while it is under its cap."""
    from .ledger import case_spend

    cap = settings.get(conn, "agents.case_cap_usd")
    spent = case_spend(conn, item_id) if cap else 0.0
    conn.commit()
    if cap and spent >= cap:
        return (f"agent spend on this case reached its cap: ${spent:.2f} of ${cap:.2f} "
                "(setting agents.case_cap_usd); a person takes this step")
    return None


def idle_brief(conn, ask=None) -> dict | None:
    """With no agent step to run, write the oldest pending case brief (P4.3), when `agents.briefs` is on."""
    from . import brief

    if not settings.get(conn, "agents.briefs"):
        return None
    try:
        return brief.next_one(conn, ask)
    except Exception as e:  # a brief never stops the worker; the case still has its person
        conn.rollback()
        log.warning("case brief failed: %s", e)
        return None


def idle_skill_draft(conn, ask=None) -> dict | None:
    """With no agent step or brief to write, draft a change to a skill that fell short (P6.4c), when
    `agents.skill_drafts` is on."""
    from . import skill_drafts

    if not settings.get(conn, "agents.skill_drafts"):
        return None
    try:
        return skill_drafts.next_one(conn, ask)
    except Exception as e:  # a draft never stops the worker
        conn.rollback()
        log.warning("skill draft failed: %s", e)
        return None


def run(connect: Callable, agents: dict[str, Agent], worker: str | None = None, poll: float = 5.0,
        stop: Callable[[], bool] = lambda: False, ask=None) -> None:
    """Keep running agent steps; with none, write a pending case brief, then a skill draft; sleep `poll` seconds when there is neither."""
    worker = worker or default_worker()
    with connect() as conn:
        while not stop():
            if run_once(conn, agents, worker) is None and idle_brief(conn, ask) is None \
                    and idle_skill_draft(conn, ask) is None:
                time.sleep(poll)


def default_worker() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def assistant_agents() -> dict[str, Agent]:
    """Agent steps run by the operations assistant's specialists (#80), read-only, one Claude call each.
    The answer is kept as `<step id>` in the process data, for the person who reviews it next, with its ledger
    (P4.4): the agents, skill and model versions, tools and cost behind it."""

    from .. import agents
    from . import ledger

    def specialist(role: str) -> Agent:
        def run_step(call: AgentCall) -> dict:
            prompt = (f"Workflow step '{call.name or call.element}' on work item {call.item['id']} "
                      f"({call.item['type']}): {call.item['title']}. Subjects: {', '.join(call.item['subjects']) or 'none'}.\n"
                      f"Process data so far: {call.data}\n"
                      + (f"Step instructions / parameters: {call.instructions}\n" if call.instructions else "") +
                      f"Ask the {role} specialist to do this step and propose, not take, any action.")
            answer = agents.ask_sync(prompt)
            return {call.element: {"answer": answer["answer"], "usage": answer["usage"],
                                   "ledger": ledger.record(role, answer)}}
        return run_step
    from ..ops import daily_report

    domain_agents = {role: specialist(role) for role in agents.SPECIALISTS}
    legacy_roles = {
        "troubleshooter": "field-operations-facilities",
        "production": "production-surveillance",
        "maintenance": "maintenance-integrity",
    }
    return {**domain_agents, **{old: specialist(new) for old, new in legacy_roles.items()},
            "reporter": daily_report.reporter_agent}
