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


def test_budget_caps_automated_use_but_not_a_person(monkeypatch):
    """The monthly budget turns automated features off; a person's own question still goes through (Adam, #1168)."""
    import pytest

    from fuzer.core import llm

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(usage, "budget", lambda: 20.0)
    monkeypatch.setattr(usage, "month_spend", lambda fresh=False: 20.02)
    assert llm.configured("agents") is False
    with pytest.raises(usage.BudgetReached):
        usage.check()
    assert llm.configured("agents", person=True) is True
    with usage.person():
        assert llm.configured("agents") is True
        usage.check()  # not refused
    assert llm.configured("agents") is False  # and the mark ends with the block
