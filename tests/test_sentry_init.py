"""Sentry reporting turns on only with SENTRY_DSN, and never sends personal data."""

import fuzer


def test_no_dsn_no_sentry(monkeypatch):
    calls = []
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: calls.append(kw))
    fuzer._init_sentry()
    assert calls == []


def test_dsn_starts_sentry_tagged_with_the_program(monkeypatch):
    calls, tags = [], {}
    monkeypatch.setenv("SENTRY_DSN", "https://key@o0.ingest.sentry.io/0")
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    monkeypatch.setattr("sys.argv", ["/app/.venv/bin/dagster-daemon", "run"])
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: calls.append(kw))
    monkeypatch.setattr("sentry_sdk.set_tag", lambda k, v: tags.__setitem__(k, v))
    fuzer._init_sentry()
    [kw] = calls
    assert kw["environment"] == "render" and kw["release"] == "abc123"
    assert kw["send_default_pii"] is False and kw["traces_sample_rate"] == 0
    assert tags == {"program": "dagster-daemon"}
