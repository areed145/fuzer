"""What the model providers have been asked, and what it cost: every call, from every feature, in one place.

Every client `llm.client` and `llm.async_client` make is metered: each response's tokens are kept in
`fuzer.llm_usage` with the feature that asked, the project, the model and an estimated cost (core/pricing.py). Batch
results are kept the same way, at the batch price. So an administrator sees the whole spend on the AI usage page
(Settings), not only the parts a feature writes down itself.

- `feature("case brief")` names the feature for the calls inside it. Without one, the calling module is named.
- `month_spend()` is this calendar month's estimated spend, all projects (UTC months, as the provider bills).
- The setting `llm.budget_usd_month` caps automated use: at the cap, `llm.configured()` says no provider is configured,
  so each background feature turns itself off as it does without a key, and a call made anyway is refused
  (`BudgetReached`). A person's own question (Ask, "remember this") runs inside `person()` and is never capped: its
  spend still counts toward the month, so it keeps the automated features off, but it isn't refused.
  At `llm.budget_alert_pct` of the cap the alert webhook is told once a month.
- `summary(conn, days)` is the page: totals, by day, by feature, by model and by project, with the budget.

Metering never breaks a call: a failure to record is logged and the answer still goes back.
"""

import logging
import sys
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

from . import db, settings

log = logging.getLogger(__name__)

_feature: ContextVar[str | None] = ContextVar("fuzer_llm_feature", default=None)
_person: ContextVar[bool] = ContextVar("fuzer_llm_person", default=False)
SPEND_SECONDS = 30  # how long the month's spend is kept before it is read again
_spend: dict[str, tuple[float, float]] = {}
_alerted: set[str] = set()
_lock = threading.Lock()
# Modules that only carry a call: the feature is whoever called them.
_CARRIERS = ("fuzer.core.llm", "fuzer.core.usage", "fuzer.agents")


# What the page calls each feature that asks, by the module that asks; any other is shown by its module name.
NAMES = {
    "agents.brief": "Case briefs", "agents.worker": "Workflow agent steps", "agents.memory": "Assistant memory",
    "agents.skill_drafts": "Skill drafts", "agents.workflow": "Workflow drafting", "agents.assistant": "Assistant",
    "api.assistant": "Assistant questions", "api.knowledge": "Assistant memory", "agents.mcp_server": "MCP assistant",
    "ops.sre_handoff": "SRE diagnoses", "ops.watch": "AI watch", "ops.daily_report": "Daily report drafts",
    "ops.jobs": "Scheduled readers", "graph.llm_links": "Log links reading", "graph.proposals": "Register proposals", "graph.review": "Register review",
    "sources.llm_pids": "P&ID reading", "sources.llm_docs": "Document reading", "sources.llm_curves": "Curve reading",
    "sources.suggest": "Source mapping suggestions", "sources.onboard": "Source onboarding",
    "sources.preview": "Source preview", "evals": "Evaluations",
}


@contextmanager
def person():
    """Calls inside the block answer a person who is waiting (Ask, "remember this"): the monthly cap doesn't refuse them."""
    token = _person.set(True)
    try:
        yield
    finally:
        _person.reset(token)


def asked_by_person() -> bool:
    return _person.get()


class BudgetReached(RuntimeError):
    """The month's model spend has reached the setting llm.budget_usd_month."""


@contextmanager
def feature(name: str):
    """Name the feature for every model call made inside the block (and the tasks it starts)."""
    token = _feature.set(name)
    try:
        yield
    finally:
        _feature.reset(token)


def current_feature() -> str:
    """The named feature, else the first Fuzer module on the stack that isn't only carrying the call."""
    if name := _feature.get():
        return name
    frame = sys._getframe(1)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module.startswith("fuzer.") and module not in _CARRIERS:
            name = module.removeprefix("fuzer.")
            return NAMES.get(name) or NAMES.get(name.split(".")[0]) or name
        frame = frame.f_back
    return "other"


def _month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def budget() -> float:
    try:
        return float(settings.get(None, "llm.budget_usd_month") or 0)
    except Exception:  # no database (a unit test, a CLI before setup): no cap
        return 0.0


def month_spend(fresh: bool = False) -> float:
    """This UTC month's estimated spend, all projects (USD); 0 when there is no database to read."""
    month = _month()
    hit = _spend.get(month)
    if hit and not fresh and time.monotonic() - hit[0] < SPEND_SECONDS:
        return hit[1]
    try:
        with db.connect(autocommit=True) as conn:
            row = conn.execute(
                "select coalesce(sum(cost_usd), 0) as usd from fuzer.llm_usage where at >= date_trunc('month', now() "
                "at time zone 'utc') at time zone 'utc'").fetchone()
        usd = float(row["usd"])
    except Exception:  # no database to read: keep what was known, and don't ask again until it's stale
        usd = hit[1] if hit else 0.0
    _spend[month] = (time.monotonic(), usd)
    return usd


def over_budget() -> bool:
    cap = budget()
    return bool(cap) and month_spend() >= cap


def check() -> None:
    """Refuse an automated call once the month's spend has reached its cap; a person's own question goes through."""
    if _person.get():
        return
    cap = budget()
    if cap and (spent := month_spend()) >= cap:
        raise BudgetReached(f"This month's model spend (${spent:.2f}) has reached its budget of ${cap:.2f} "
                            "(setting llm.budget_usd_month). An administrator can raise it on Settings > AI usage.")


def _tokens(u) -> tuple[int, int, int, int]:
    """(input, output, cache read, cache write) from an Anthropic or OpenAI-compatible usage object."""
    if hasattr(u, "prompt_tokens") and not hasattr(u, "input_tokens"):
        prompt = getattr(u, "prompt_tokens", 0) or 0
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        return max(0, prompt - cached), getattr(u, "completion_tokens", 0) or 0, cached, 0
    return (getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0,
            getattr(u, "cache_read_input_tokens", 0) or 0, getattr(u, "cache_creation_input_tokens", 0) or 0)


def record(response, provider: str, *, batch: bool = False, name: str | None = None) -> dict | None:
    """Keep one response's usage. Returns the row kept, or None when there was nothing to keep or it failed."""
    u = getattr(response, "usage", None)
    if u is None:
        return None
    try:
        from .pricing import cost

        model = getattr(response, "model", None) or "unknown"
        i, o, cr, cw = _tokens(u)
        usd, known = cost(model, i, o, cr, cw, provider)
        if batch:
            usd /= 2  # batch requests are billed at half price
        row = {"project": db.scope().project or settings.project(), "feature": name or current_feature(),
               "provider": provider, "model": model, "input_tokens": i, "output_tokens": o,
               "cache_read_tokens": cr, "cache_write_tokens": cw, "cost_usd": round(usd, 6), "priced": known,
               "batch": batch}
        with db.connect(autocommit=True) as conn:
            conn.execute(
                "insert into fuzer.llm_usage (project, feature, provider, model, input_tokens, output_tokens, "
                "cache_read_tokens, cache_write_tokens, cost_usd, priced, batch) values (%(project)s, %(feature)s, "
                "%(provider)s, %(model)s, %(input_tokens)s, %(output_tokens)s, %(cache_read_tokens)s, "
                "%(cache_write_tokens)s, %(cost_usd)s, %(priced)s, %(batch)s)", row)
    except Exception as e:
        log.warning("model usage not recorded: %s", e)
        return None
    month = _month()
    with _lock:
        if month in _spend:
            at, usd_so_far = _spend[month]
            _spend[month] = (at, usd_so_far + row["cost_usd"])
    _maybe_alert()
    return row


def _maybe_alert() -> None:
    """Tell the alert webhook once a month when the spend passes llm.budget_alert_pct of the cap, and at the cap."""
    cap = budget()
    if not cap:
        return
    spent = month_spend()
    try:
        pct = float(settings.get(None, "llm.budget_alert_pct") or 0)
    except Exception:
        pct = 0.0
    month = _month()
    for level, threshold in (("cap", cap), ("alert", cap * pct / 100 if pct else None)):
        key = f"{month}:{level}"
        if threshold is None or spent < threshold or key in _alerted:
            continue
        _alerted.add(key)
        text = (f"Fuzer model spend this month is ${spent:.2f}, " +
                (f"at its budget of ${cap:.2f}: AI features are off until the month ends or the budget is raised."
                 if level == "cap" else f"{spent / cap:.0%} of its ${cap:.2f} budget."))
        log.warning(text)
        _post(text)
        break


def _post(text: str) -> None:
    """The alert webhook (alerts.webhook), as sources.alerts posts to it; core can't import sources."""
    import json
    import urllib.request

    try:
        url = settings.secret(None, "alerts.webhook")
        if url:
            req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning("budget alert not sent: %s", e)


def summary(conn, days: int = 30) -> dict:
    """The AI usage page: the window's totals, by day, by feature, by model and by project, and the month's budget."""
    days = max(1, min(int(days), 366))
    since = "now() - make_interval(days => %s)"
    total = conn.execute(
        f"select count(*) as calls, coalesce(sum(input_tokens), 0) as input_tokens, coalesce(sum(output_tokens), 0) "
        f"as output_tokens, coalesce(sum(cache_read_tokens), 0) as cache_read_tokens, coalesce(sum(cache_write_tokens),"
        f" 0) as cache_write_tokens, coalesce(sum(cost_usd), 0) as cost_usd, bool_or(not priced) as unpriced, "
        f"max(at) as last_at from fuzer.llm_usage where at >= {since}", (days,)).fetchone()

    def by(column: str, order: str = "cost_usd desc") -> list[dict]:
        rows = conn.execute(
            f"select {column} as key, count(*) as calls, sum(input_tokens + cache_read_tokens + cache_write_tokens) "
            f"as input_tokens, sum(output_tokens) as output_tokens, sum(cost_usd) as cost_usd, max(at) as last_at "
            f"from fuzer.llm_usage where at >= {since} group by 1 order by {order}", (days,)).fetchall()
        return [{**r, "cost_usd": round(float(r["cost_usd"]), 4)} for r in rows]

    daily = conn.execute(
        f"select (at at time zone 'utc')::date as day, feature, count(*) as calls, sum(cost_usd) as cost_usd "
        f"from fuzer.llm_usage where at >= {since} group by 1, 2 order by 1", (days,)).fetchall()
    recent = conn.execute(
        "select at, project, feature, model, input_tokens + cache_read_tokens + cache_write_tokens as input_tokens, "
        "output_tokens, cost_usd, batch from fuzer.llm_usage order by at desc limit 25").fetchall()
    cap = budget()
    spent = month_spend(fresh=True)
    return {
        "days": days,
        "total": {**total, "cost_usd": round(float(total["cost_usd"]), 4), "unpriced": bool(total["unpriced"])},
        "by_feature": by("feature"), "by_model": by("model"), "by_project": by("coalesce(project, '(none)')"),
        "daily": [{"day": str(r["day"]), "feature": r["feature"], "calls": r["calls"],
                   "cost_usd": round(float(r["cost_usd"]), 4)} for r in daily],
        "recent": [{**r, "cost_usd": round(float(r["cost_usd"]), 4)} for r in recent],
        "budget": {"month": _month(), "cap_usd": cap, "spent_usd": round(spent, 4),
                   "alert_pct": settings.get(conn, "llm.budget_alert_pct"),
                   "reached": bool(cap) and spent >= cap},
    }
