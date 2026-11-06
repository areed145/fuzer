import asyncio

import pytest

from fuzer.agents import service
from fuzer.core import settings


def test_web_assistant_admits_bounded_questions_and_times_out_with_recovery(okan_db, monkeypatch):
    class Team:
        def create_session(self, **kwargs):
            return object()

    import fuzer.agents as agents

    saved = []
    monkeypatch.setattr(service, "_jobs", {})
    monkeypatch.setattr(service, "_tasks", set())
    monkeypatch.setattr(service, "team", lambda: Team())
    monkeypatch.setattr(service, "_purge", lambda conn: None)
    monkeypatch.setattr(service, "_save", lambda job, by: saved.append((dict(job), by)))

    async def slow(*args):
        await asyncio.sleep(10)

    monkeypatch.setattr(agents, "ask", slow)
    original_get = settings.get
    monkeypatch.setattr(settings, "get", lambda conn, key, *args, **kwargs:
                        .01 if key == "assistant.timeout_seconds" else original_get(conn, key, *args, **kwargs))

    async def exercise():
        with settings.Overrides({"assistant.concurrent_questions": 1}):
            started = service.start("A narrow synthetic question", by="user:test")
            with pytest.raises(service.Busy, match="another question"):
                service.start("Must not create a second model workload", by="user:other")
            await asyncio.gather(*service._tasks)
            result = service.status(started["job_id"])
            assert result["status"] == "error" and "narrower question" in result["error"]
            assert service.owner(started["job_id"]) == {"by_user": "user:test"}
            assert len(saved) == 1 and saved[0][1] == "user:test"
            assert not service._tasks

    asyncio.run(exercise())
