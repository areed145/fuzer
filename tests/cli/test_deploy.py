import signal
import threading
from contextlib import nullcontext

import pytest

from fuzer.cli import deploy
from fuzer.core import db


def test_commands_share_runtime_environment(monkeypatch):
    monkeypatch.setenv("PORT", "12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    commands = dict(deploy.commands())
    assert commands["API"][-1] == "12345"
    assert commands["Agent worker"] == ["fuzer", "work", "worker"]
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


@pytest.mark.parametrize("fail_first", [False, True])
def test_startup_reads_each_project_in_its_own_scope(monkeypatch, capsys, fail_first):
    seen = []
    monkeypatch.setattr(deploy.db, "connect", lambda: nullcontext(object()))
    monkeypatch.setattr(deploy.projects, "current", lambda conn: "okan")
    monkeypatch.setattr(deploy.projects, "use", lambda conn, name: db.use(
        db.Scope(name, "public" if name == "okan" else f"proj_{name}")))
    monkeypatch.setattr(deploy, "current_pack", lambda conn: db.scope().project)

    def read(conn, pack, report):
        seen.append((pack, db.scope().schema))
        if fail_first and pack == "okan":
            raise RuntimeError("interrupted")
        report("finished")

    monkeypatch.setattr(deploy.pipeline, "read_pending", read)
    deploy.read_projects(["kestrel"])
    assert seen == [("okan", "public"), ("kestrel", "proj_kestrel")]
    assert db.scope().project is None
    out = capsys.readouterr().out
    assert "LLM readings (kestrel): finished" in out
    assert ("Inspect recorded readings before a deliberate retry" in out) == fail_first


def test_main_starts_readers_in_background_before_supervising(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("FUZER_DATABASE_URL", "postgresql://unused/test")
    monkeypatch.setenv("FUZER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DAGSTER_HOME", str(tmp_path))
    monkeypatch.setattr(deploy.db, "connect", lambda: nullcontext(object()))
    monkeypatch.setattr(deploy, "seed", lambda conn, bundle: calls.append("seed") or {"name": "first"})
    monkeypatch.setattr(deploy, "register_projects", lambda conn, first: first == "first" and ["kestrel"])
    monkeypatch.setattr(deploy.engine, "load_defaults", lambda conn: None)
    monkeypatch.setattr(deploy.projects, "use", lambda conn, name: nullcontext())
    monkeypatch.setattr(deploy.signal, "signal", lambda *args: None)
    monkeypatch.setattr(deploy, "commands", lambda added: [])

    class Thread:
        def __init__(self, *, target, args, name, daemon):
            assert target is deploy.read_projects and args == (["kestrel"],)
            assert name == "startup-readings" and daemon

        def start(self):
            calls.append("background readers")

    monkeypatch.setattr(deploy.threading, "Thread", Thread)
    monkeypatch.setattr(deploy, "supervise", lambda tasks, stop: calls.append("serve"))
    deploy.main(["--bundle", str(tmp_path)])
    assert calls == ["seed", "background readers", "serve"]
