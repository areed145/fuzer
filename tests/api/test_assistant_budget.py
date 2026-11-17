"""Ask tells a spent monthly budget apart from a missing key (the key was blamed when the budget ran out)."""

from fuzer.api import assistant
from fuzer.core import usage


def test_spent_budget_is_not_reported_as_a_missing_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(usage, "budget", lambda: 20.0)
    monkeypatch.setattr(usage, "month_spend", lambda fresh=False: 20.02)
    out = assistant.assistant_agents()
    assert out["provider_configured"] is True
    assert out["budget_reached"] is True
    assert out["budget_usd_month"] == 20.0 and out["month_spend_usd"] == 20.02


def test_missing_key_is_still_reported(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(usage, "budget", lambda: 20.0)
    monkeypatch.setattr(usage, "month_spend", lambda fresh=False: 0.0)
    out = assistant.assistant_agents()
    assert out["provider_configured"] is False and out["budget_reached"] is False
