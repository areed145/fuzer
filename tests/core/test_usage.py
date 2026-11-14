"""Model usage: every call a metered client makes is kept with its feature and cost, the page sums it, and the monthly
budget turns the AI features off and refuses calls once it is spent."""

import asyncio
from types import FunctionType, SimpleNamespace

import pytest

from fuzer.core import llm, settings, usage


def _message(model="claude-opus-5-5", i=1000, o=200, cr=0, cw=0):
    return SimpleNamespace(model=model, stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")],
                           usage=SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=cr,
                                                 cache_creation_input_tokens=cw))


class FakeAnthropic:
    """Stands in for the SDK client: `messages` and `beta.messages` each with a create."""

    def __init__(self, asynchronous=False):
        self.calls = 0

        def create(**kwargs):
            self.calls += 1
            return _message(kwargs.get("model", "claude-opus-5-5"))

        async def acreate(**kwargs):
            return create(**kwargs)

        make = acreate if asynchronous else create
        self.messages = SimpleNamespace(create=make)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=make))


@pytest.fixture
def meter(okan_db):
    conn = okan_db.conn
    conn.execute("delete from fuzer.llm_usage")
    conn.commit()
    usage._spend.clear()
    usage._alerted.clear()
    yield conn
    usage._spend.clear()


def _ask():
    return usage.current_feature()


def _asked_from(module: str) -> str:
    """What `current_feature` finds when code in `module` asks: `_ask` run with that module's globals."""
    return FunctionType(_ask.__code__, {"__name__": module, "usage": usage})()


def test_feature_is_named_or_found_on_the_stack():
    with usage.feature("case brief"):
        assert usage.current_feature() == "case brief"
    assert usage.current_feature() == "other"  # no Fuzer module asked
    assert _asked_from("fuzer.agents.brief") == "Case briefs"  # code in the brief module, asking through a carrier
    assert _asked_from("fuzer.ops.new_thing") == "ops.new_thing"  # a module the page has no name for shows as itself


def test_every_metered_call_is_kept_with_its_feature_and_cost(meter):
    fake = FakeAnthropic()
    client = llm._meter(fake, "anthropic", is_async=False)
    with usage.feature("case brief"):
        llm.call(client, {"model": "claude-opus-5-5", "max_tokens": 10, "messages": []})
    client.messages.create(model="claude-sonnet-5-5", max_tokens=10, messages=[])
    asyncio.run(llm._meter(FakeAnthropic(asynchronous=True), "anthropic", is_async=True)
                .beta.messages.create(model="claude-haiku-5-5", max_tokens=10, messages=[]))

    rows = meter.execute("select feature, model, input_tokens, output_tokens, cost_usd from fuzer.llm_usage "
                         "order by id").fetchall()
    assert [(r["feature"], r["model"]) for r in rows] == [
        ("case brief", "claude-opus-5-5"), ("other", "claude-sonnet-5-5"),
        ("other", "claude-haiku-5-5")]
    assert float(rows[0]["cost_usd"]) == pytest.approx((1000 * 4 + 200 * 20) / 1e6)  # Opus 5.5: $4 in, $20 out
    assert fake.calls == 2

    out = usage.summary(meter, 30)
    assert out["total"]["calls"] == 3
    assert {f["key"] for f in out["by_feature"]} == {"case brief", "other"}
    assert {m["key"] for m in out["by_model"]} == {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}
    assert out["daily"] and out["recent"][0]["model"] == "claude-haiku-5-5"
    assert out["budget"]["spent_usd"] == pytest.approx(out["total"]["cost_usd"], abs=1e-4)


def test_a_batch_result_is_kept_at_half_price(meter):
    row = usage.record(_message(), "anthropic", batch=True, name="Log links reading")
    assert row["batch"] and row["cost_usd"] == pytest.approx((1000 * 4 + 200 * 20) / 1e6 / 2)


def test_a_spent_budget_turns_the_features_off_and_refuses_calls(meter, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = llm._meter(FakeAnthropic(), "anthropic", is_async=False)
    with settings.Overrides({"llm.budget_usd_month": 0.01, "llm.budget_alert_pct": 50.0}):
        assert llm.configured()
        posted = []
        monkeypatch.setattr(usage, "_post", posted.append)
        for _ in range(2):  # $0.008 each: the first passes half the budget, the second reaches it
            client.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[])
        assert len(posted) == 2 and "at its budget" in posted[1]
        assert usage.over_budget() and not llm.configured()
        with pytest.raises(usage.BudgetReached, match="llm.budget_usd_month"):
            client.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[])
        assert usage.summary(meter)["budget"]["reached"]
    with settings.Overrides({"llm.budget_usd_month": 0.0}):
        assert llm.configured()  # no budget, no cap
