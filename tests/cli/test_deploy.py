import signal
import threading

import pytest

from fuzer.cli import deploy


def test_commands_share_runtime_environment(monkeypatch):
    monkeypatch.setenv("FUZER_PACK", "okan")
    monkeypatch.setenv("PORT", "12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    commands = dict(deploy.commands())
    assert commands["API"][-1] == "12345"
    assert commands["Agent worker"] == ["fuzer", "--pack", "okan", "work", "worker"]
    assert commands["Dagster webserver"][-4:] == ["-h", "127.0.0.1", "-p", "3000"]
    assert commands["Dagster daemon"] == ["dagster-daemon", "run", "-m", "fuzer.ops.jobs"]
    added = dict(deploy.commands(["kestrel"]))  # each project added beside Okan runs its own agent steps
    assert added["Agent worker (kestrel)"] == ["fuzer", "--project", "kestrel", "work", "worker"]
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert "Agent worker" not in dict(deploy.commands())


def test_supervisor_propagates_failure_and_stops_siblings(monkeypatch):
    killed = []

    class Proc:
        def __init__(self, command, **kwargs):
            self.pid = 1 if command == ["fail"] else 2
            self.returncode = 7 if self.pid == 1 else None
            assert kwargs["start_new_session"]

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(deploy.subprocess, "Popen", Proc)
    monkeypatch.setattr(deploy.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(RuntimeError, match="exited unexpectedly with code 7"):
        deploy.supervise([("failed task", ["fail"]), ("sibling", ["run"])], threading.Event())
    assert killed == [(2, signal.SIGTERM), (1, signal.SIGTERM)]


def test_shutdown_stops_all_children(monkeypatch):
    stop = threading.Event()
    stop.set()
    killed = []

    class Proc:
        pid = 123

        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(deploy.subprocess, "Popen", Proc)
    monkeypatch.setattr(deploy.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    deploy.supervise([("task", ["run"])], stop)
    assert killed == [(123, signal.SIGTERM)]
