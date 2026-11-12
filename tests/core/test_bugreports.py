"""Unexpected errors reported to GitHub as issues (fuzer.core.bugreports): one issue per kind of error, a comment
for a repeat (at most one an hour, across processes), nothing without a token, and no secrets in what's sent."""

from datetime import UTC, datetime, timedelta

import pytest

from fuzer.core import bugreports


class FakeGitHub:
    """The issues endpoints, in memory, as Reporter._call sees them."""

    def __init__(self):
        self.issues: dict[int, dict] = {}
        self.comments: list[tuple[int, str]] = []

    def call(self, method, path, body=None):
        if method == "POST" and path == "/issues":
            n = len(self.issues) + 1
            self.issues[n] = {"number": n, "state": "open", "updated_at": "2000-01-01T00:00:00Z", **body}
            return self.issues[n]
        if method == "POST" and path.endswith("/comments"):
            self.comments.append((int(path.split("/")[2]), body["body"]))
            return {}
        if method == "GET" and path.startswith("/issues?"):
            return [i for i in self.issues.values() if i["state"] == "open"]
        if method == "GET":
            return self.issues[int(path.split("/")[2])]
        raise AssertionError(f"unexpected {method} {path}")


def boom(message="it broke"):
    raise ValueError(message)


def caught(fn, *a):
    try:
        fn(*a)
    except Exception as e:
        return e


@pytest.fixture
def gh(monkeypatch):
    monkeypatch.setenv(bugreports.TOKEN_ENV, "ghp_secret_token_value")
    monkeypatch.setenv(bugreports.REPO_ENV, "owner/repo")
    monkeypatch.setenv("FUZER_DATABASE_URL", "postgresql://fuzer:pg-password-123@db.internal/fuzer")
    fake = FakeGitHub()
    return fake


@pytest.fixture
def reporter(gh, monkeypatch):
    r = bugreports.Reporter()
    monkeypatch.setattr(r, "_call", gh.call)
    monkeypatch.setattr(r, "_start", lambda: None)  # sent by send_all, in the test's thread
    return r


def send_all(r):
    while not r._q.empty():
        r._send(*r._q.get())


def test_one_issue_per_kind_and_a_comment_for_a_repeat(reporter, gh, monkeypatch):
    reporter.report(caught(boom), "GET /api/x")
    send_all(reporter)
    [issue] = gh.issues.values()
    assert issue["title"] == "ValueError: it broke" and issue["labels"] == ["auto-report"]
    assert "GET /api/x" in issue["body"] and "<!-- fingerprint:" in issue["body"]

    reporter.report(caught(boom), "GET /api/x")  # within the hour in this process: counted, not sent
    send_all(reporter)
    assert len(gh.issues) == 1 and gh.comments == []

    fresh = bugreports.Reporter()  # another process (or a restart) finds the open issue by its fingerprint
    monkeypatch.setattr(fresh, "_call", gh.call)
    monkeypatch.setattr(fresh, "_start", lambda: None)
    fresh.report(caught(boom, "another message"), "worker")
    send_all(fresh)
    assert len(gh.issues) == 1 and [n for n, _ in gh.comments] == [1]

    gh.issues[1]["updated_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()  # just commented on
    again = bugreports.Reporter()
    monkeypatch.setattr(again, "_call", gh.call)
    again.report(caught(boom), "crash loop", wait=True)
    assert len(gh.comments) == 1  # a crash loop adds one comment an hour, not one per restart

    gh.issues[1]["state"] = "closed"  # closed: the next occurrence opens a new issue
    newer = bugreports.Reporter()
    monkeypatch.setattr(newer, "_call", gh.call)
    newer.report(caught(boom), "later", wait=True)
    assert len(gh.issues) == 2


def test_different_errors_get_different_issues(reporter, gh):
    reporter.report(caught(boom), "a")
    reporter.report(caught(lambda: {}["missing"]), "b")
    send_all(reporter)
    assert sorted(i["title"].split(":")[0] for i in gh.issues.values()) == ["KeyError", "ValueError"]


def test_a_failed_run_is_fingerprinted_by_its_job(reporter, gh):
    for run in ("abc", "def"):
        failure = bugreports.Reported(f"run {run} failed: step x", "dagster-run:refresh_all", kind="Dagster job failed")
        bugreports.Reporter.report(reporter, failure, "alert_on_failure", wait=True)
        reporter._last.clear()
    [issue] = gh.issues.values()
    assert issue["title"] == "Dagster job failed: run abc failed: step x"


def test_nothing_is_sent_without_a_token(monkeypatch):
    monkeypatch.delenv(bugreports.TOKEN_ENV, raising=False)
    r = bugreports.Reporter()
    monkeypatch.setattr(r, "_call", lambda *a, **k: pytest.fail("called GitHub"))
    r.report(caught(boom), "x", wait=True)
    assert r._q.empty() and not r.enabled()


def test_secrets_are_scrubbed(reporter, gh):
    reporter.report(caught(boom, "connect postgresql://fuzer:pg-password-123@db.internal/fuzer token=abcdef123456 "
                                 "with ghp_secret_token_value"), "x")
    send_all(reporter)
    [issue] = gh.issues.values()
    text = issue["title"] + issue["body"]
    for secret in ("pg-password-123", "abcdef123456", "ghp_secret_token_value"):
        assert secret not in text


def test_reporting_never_raises(reporter, gh, monkeypatch, capsys):
    monkeypatch.setattr(reporter, "_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("GitHub down")))
    reporter.report(caught(boom), "x", wait=True)
    assert "Error report not sent to GitHub: RuntimeError: GitHub down" in capsys.readouterr().err


def test_a_server_error_is_reported(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from fuzer.api.security import server_error

    sent = []
    monkeypatch.setattr(bugreports.reporter, "report", lambda exc, where, **kw: sent.append((type(exc), where)))
    app = FastAPI()
    app.add_exception_handler(Exception, server_error)

    @app.get("/broken")
    def broken():
        boom()

    response = TestClient(app, raise_server_exceptions=False).get("/broken")
    assert response.status_code == 500
    assert sent == [(ValueError, "GET /broken")]
