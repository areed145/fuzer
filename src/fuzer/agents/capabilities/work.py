"""The work tools (#97): agents take, finish and hand off workflow steps as themselves. MCP-only. Declared with
`capability` (#133); mcp_server serves them."""

import json
from typing import Any

from ...core import db
from ...workflow import engine as work
from ..mcp_server import AgentRole, _plain, _work, capability


@capability(kind="work")
def claim_work(task_id: int, agent: AgentRole) -> dict[str, Any]:
    """Take a step before working on it, so no worker or other agent takes it too. An agent step is
    held with a lease (renewed by finishing in time); a person step that doesn't need a person is
    assigned to you. Returns the step, its form and the evidence so far."""
    from .. import worker
    with db.connect() as conn:
        t = conn.execute("select kind, role from work_token where id = %s", (task_id,)).fetchone()
        conn.commit()
        if t is None:
            return {"error": "unknown task"}
        if t["kind"] == "agent":
            if t["role"] != agent:
                return {"error": f"step {task_id} is for agent:{t['role']}"}
            if worker.claim(conn, f"mcp:{agent}", [agent], task_id=task_id) is None:
                return {"error": f"step {task_id} isn't free: a worker holds it, it is waiting to retry, or it is done"}
        else:
            try:
                work.claim(conn, task_id, f"agent:{agent}")
            except work.WorkError as e:
                return {"error": str(e)}
        return _plain(work.task(conn, task_id))


@capability(kind="work")
def complete_work(task_id: int, agent: AgentRole, result: dict[str, Any]) -> dict[str, Any]:
    """Finish a step with your result (merged into the process data; a proposal, findings, a draft).
    Gateways read it, so use the keys the step asks for. Steps that need a person refuse agents."""
    with db.connect() as conn:
        t = conn.execute("select claimed_by from work_token where id = %s", (task_id,)).fetchone()
        conn.commit()
    mine = f"mcp:{agent}"
    return _work(work.complete, task_id, f"agent:{agent}", result,
                 worker=mine if t and t["claimed_by"] == mine else None)


@capability(kind="work")
def hand_off(task_id: int, agent: AgentRole, to: str, reason: str) -> dict[str, Any]:
    """Give a step to someone better placed: a person by name, a role's queue (`role:engineer`) or
    another agent (`agent:maintenance`). Say why; it is kept in the item's history."""
    return _work(work.hand_off, task_id, f"agent:{agent}", to, reason)


@capability(kind="work")
def send_evidence(agent: AgentRole, evidence: dict[str, Any], correlation_key: str | None = None,
                  item_id: int | None = None, process_key: str | None = None,
                  name: str = "evidence") -> dict[str, Any]:
    """Add evidence (new findings, a figure id, a SAP notification raised) to an open process: by its
    correlation key (an upset's incident id), or by work item id. A process waiting for this message
    moves on; otherwise the evidence is kept on it for the next person or agent."""
    with db.connect() as conn:
        if correlation_key is None and item_id is not None:
            row = conn.execute("select correlation_key, process_key from work_process where item_id = %s and "
                               "state = 'running' and parent_token is null and correlation_key is not null "
                               "order by id limit 1", (item_id,)).fetchone()
            conn.commit()
            if row is None:
                return _work(work.comment, item_id, f"agent:{agent}", f"{name}: {json.dumps(evidence, default=str)}",
                             [{"kind": name, "data": evidence}])
            correlation_key, process_key = row["correlation_key"], process_key or row["process_key"]
    if correlation_key is None:
        return {"error": "give the correlation_key (e.g. the incident id) or the item_id"}
    return _work(work.send_message, name, correlation_key, evidence, f"agent:{agent}", process_key)
