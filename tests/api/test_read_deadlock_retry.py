"""A read Postgres cancels as a deadlock's victim runs once more; a write never does (#1332).

On a zero-downtime deploy the new instance's schema update takes exclusive locks while the old instance still answers
reads, and a report panel holding one table while waiting on another was the side Postgres cancelled."""

import psycopg
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from fuzer.api import security


def _app(monkeypatch, calls):
    monkeypatch.setattr(security, "identify", lambda request: None)  # signed-in checks are tested elsewhere
    router = APIRouter(route_class=security.AuthenticatedRoute)

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise psycopg.errors.DeadlockDetected("deadlock detected")
        return {"ok": True}

    router.add_api_route("/read", flaky, methods=["GET"])
    router.add_api_route("/write", flaky, methods=["POST"])
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_a_read_cancelled_by_a_deadlock_runs_again(monkeypatch):
    calls = []
    response = _app(monkeypatch, calls).get("/read")
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert len(calls) == 2


def test_a_write_cancelled_by_a_deadlock_is_not_replayed(monkeypatch):
    calls = []
    response = _app(monkeypatch, calls).post("/write")
    assert response.status_code == 500
    assert len(calls) == 1


def test_a_read_that_deadlocks_twice_fails(monkeypatch):
    monkeypatch.setattr(security, "identify", lambda request: None)
    router = APIRouter(route_class=security.AuthenticatedRoute)
    calls = []

    def stuck():
        calls.append(1)
        raise psycopg.errors.DeadlockDetected("deadlock detected")

    router.add_api_route("/read", stuck, methods=["GET"])
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app, raise_server_exceptions=False).get("/read").status_code == 500
    assert len(calls) == 2
