"""Telling a source's owner when it breaks (#225): a failed or held load, failing checks, profile drift and stale
data each raise one `source_issue` exception (fuzer.core.exceptions), whose case goes to the dataset's owner (else
its system's owner, else the `data_steward` role queue). The exception clears, closing its case, when the problem
does. A new one is posted to the alert webhook (alerts.webhook) when the outbox delivers it. Run after each load
and by the scheduled health check.
"""

import json
import logging
import urllib.request

from ..core import detectors, events, settings
from ..core.exceptions import clear_exception, exception_type, raise_exception
from ..core.findings import DETERMINISTIC, Finding, fact

TYPE = "source_issue"
ROLE = "role:data_steward"
exception_type(TYPE, "Source problem", role=ROLE, description="A dataset failed, is held, fails its checks, "
               "changed shape or is stale.", joins=False)  # a data problem, never part of an asset's event
SAY = {"failed": "failed to load", "held": "is held (its schema or a blocking check needs a look)",
       "checks failing": "has failing checks", "profile drift": "changed shape (profile drift)",
       "stale": "is stale (no new delivery within its expected hours)"}


def app_link(link: str) -> str:
    """`link` (such as "#/jobs") on the application's address (notify.app_url); empty when it isn't set."""
    base = (settings.get(None, "notify.app_url") or "").rstrip("/")
    return f"{base}/{link}" if base else ""


def post(text: str) -> bool:
    """Post a message to the alert webhook (Teams / Slack / any JSON webhook) when one is set."""
    url = settings.secret(None, "alerts.webhook")
    if not url:
        return False
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except OSError as e:
        logging.getLogger(__name__).warning("The alert webhook refused a message (%s): %s", e, text[:200])
        return False
    return True


@detectors.register("source_health", title="A dataset that failed, is held, is stale or fails its checks",
                    kind=detectors.RULE, cadence="30min", raises=(TYPE,),
                    eval="test:tests/api/test_catalog_api.py::test_rules_health_alerts_and_lineage", found=lambda out: len(out.get("opened") or []))
def check(conn, names: list[str] | None = None, by: str = "fuzer") -> dict:
    """Raise exceptions for new problems and clear the ones that went away. Returns what was opened and closed."""
    from . import catalog, quality

    rows = quality.health(conn)
    if names is not None:
        rows = [r for r in rows if r["dataset"] in names]
    owners = {n: d["owner"] or d["system_owner"] for n, d in catalog.datasets(conn).items()}
    held = {r["key"] for r in conn.execute("select key from exception_record where type = %s and state = 'open'",
                                           (TYPE,))}
    opened, closed = [], []
    for r in rows:
        now = set(r["problems"]) if r["status"] == "active" else set()
        for problem in sorted(now):
            owner = owners.get(r["dataset"])
            text = f"Source {r['dataset']} ({r['system']}) {SAY.get(problem, problem)}"
            out = raise_exception(conn, TYPE, f"{r['dataset']}:{problem}", text, by, source="sources",
                                  priority=0.5 if problem in ("failed", "held", "stale") else 0.3, assignee=owner,
                                  finding=Finding(
                                      f"sources.{problem}", DETERMINISTIC, method="Source health checks",
                                      items=(fact("Dataset", r["dataset"]), fact("System", r["system"]),
                                             fact("Problem", SAY.get(problem, problem))),
                                      data={"dataset": r["dataset"], "problem": problem, "health": r}))
            if out["raised"]:
                opened.append(f"{r['dataset']}: {problem}")
        for key in held:
            name, _, problem = key.rpartition(":")
            if name == r["dataset"] and problem not in now:
                clear_exception(conn, TYPE, key, by, reason="the problem cleared")
                closed.append(f"{name}: {problem}")
    return {"opened": opened, "closed": closed}


@events.subscriber("exception.raised")
def notify(conn, event: dict) -> None:
    """Post a new source problem to the alert webhook, naming who has it."""
    e = event["payload"]
    if e.get("type") == TYPE:
        owner = e.get("assignee")
        post(e["title"] + (f"; owner {owner}" if owner else ""))
