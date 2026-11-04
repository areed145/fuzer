"""Telling a source's owner when it breaks (#225): a failed or held load, failing checks, profile drift and stale
data each open one work item (type `source_issue`) for the dataset's owner (else its system's owner, else the
`data_steward` role queue), and post to the alert webhook (alerts.webhook). An item closes itself when the
problem clears. Run after each load and by the scheduled health check.
"""

import json
import urllib.request

from ..core import settings

TYPE = "source_issue"
ROLE = "role:data_steward"
SAY = {"failed": "failed to load", "held": "is held (its schema or a blocking check needs a look)",
       "checks failing": "has failing checks", "profile drift": "changed shape (profile drift)",
       "stale": "is stale (no new delivery within its expected hours)"}


def post(text: str) -> bool:
    """Post a message to the alert webhook (Teams / Slack / any JSON webhook) when one is set."""
    url = settings.secret(None, "alerts.webhook")
    if not url:
        return False
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except OSError:
        return False
    return True


def check(conn, names: list[str] | None = None, by: str = "fuzer") -> dict:
    """Open work items for new problems and close the ones that cleared. Returns what was opened and closed."""
    from ..workflow import engine
    from . import catalog, quality

    rows = quality.health(conn)
    if names is not None:
        rows = [r for r in rows if r["dataset"] in names]
    owners = {n: d["owner"] or d["system_owner"] for n, d in catalog.datasets(conn).items()}
    open_items = {(r["payload"]["dataset"], r["payload"]["problem"]): r["id"] for r in conn.execute(
        "select id, payload from work_item where type = %s and state not in ('done', 'cancelled')", (TYPE,))}
    opened, closed = [], []
    for r in rows:
        now = set(r["problems"]) if r["status"] == "active" else set()
        for problem in now:
            if (r["dataset"], problem) in open_items:
                continue
            owner = owners.get(r["dataset"])
            text = f"Source {r['dataset']} ({r['system']}) {SAY.get(problem, problem)}"
            engine.create_item(conn, TYPE, text, by, priority=0.5 if problem in ("failed", "held", "stale") else 0.3,
                               assignee=owner, role=None if owner else ROLE, source="sources",
                               payload={"dataset": r["dataset"], "problem": problem, "health": r})
            post(text + (f"; owner {owner}" if owner else ""))
            opened.append(f"{r['dataset']}: {problem}")
        for (name, problem), item in open_items.items():
            if name == r["dataset"] and problem not in now:
                engine.transition(conn, item, "done", by, reason="the problem cleared")
                closed.append(f"{name}: {problem}")
    return {"opened": opened, "closed": closed}
