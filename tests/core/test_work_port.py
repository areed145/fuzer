"""The Work port (fuzer.core.work): callers raise work without importing the engine, and another implementation
can take its place."""

from types import SimpleNamespace

import pytest

from fuzer.core import work


@pytest.fixture
def fake():
    calls = []
    impl = SimpleNamespace(
        create_item=lambda conn, type, title, by, **kw: calls.append(("create", type, title, by, kw)) or {"id": 7},
        start_or_correlate=lambda conn, key, data, by, **kw: calls.append(("start", key, data, by, kw)) or {"item_id": 8},
        transition=lambda conn, item_id, state, by, reason=None: calls.append(("move", item_id, state, by, reason)) or {},
        agent_work=lambda conn, role, limit: calls.append(("steps", role, limit)) or [],
    )
    work.use(impl)
    yield calls
    work.use(None)


def test_requests_reach_the_chosen_implementation(fake):
    assert work.open_case(None, "source_issue", "Source x is stale", "fuzer", priority=0.5)["id"] == 7
    assert work.start_or_correlate(None, "upset", {"incident_id": "upset:1"}, "agent:sre", title="Upset")["item_id"] == 8
    work.close_case(None, 7, "done", "fuzer", "the problem cleared")
    work.agent_steps(None, "troubleshooter", limit=5)
    assert fake == [
        ("create", "source_issue", "Source x is stale", "fuzer", {"priority": 0.5}),
        ("start", "upset", {"incident_id": "upset:1"}, "agent:sre", {"title": "Upset"}),
        ("move", 7, "done", "fuzer", "the problem cleared"),
        ("steps", "troubleshooter", 5),
    ]


def test_the_built_in_engine_is_the_default_and_its_errors_are_port_errors():
    work.use(None)
    from fuzer.workflow import engine

    assert work._engine() is engine
    assert issubclass(engine.WorkError, work.WorkError)
    assert issubclass(engine.NotFound, work.WorkError)
