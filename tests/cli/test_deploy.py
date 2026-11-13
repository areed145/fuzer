import os
import signal
import threading
from contextlib import nullcontext
from types import SimpleNamespace

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


def test_the_daemon_waits_for_the_startup_readings(monkeypatch):
    started, stop, ready = [], threading.Event(), threading.Event()

    class Proc:
        pid = 5

        def __init__(self, command, **kwargs):
            started.append(command[0])

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    waits = iter([False, False, True])

    def wait_once(timeout):
        if started == ["api"]:  # held while the readings run; they finish now
            ready.set()
        return next(waits)

    monkeypatch.setattr(stop, "wait", wait_once)
    monkeypatch.setattr(deploy.subprocess, "Popen", Proc)
    monkeypatch.setattr(deploy.os, "killpg", lambda pid, sig: None)
    deploy.supervise([("Dagster daemon", ["daemon"]), ("API", ["api"])], stop, ready)
    assert started == ["api", "daemon"]


def test_the_daemon_starts_anyway_after_the_wait(monkeypatch):
    started, stop = [], threading.Event()

    class Proc:
        pid = 5

        def __init__(self, command, **kwargs):
            started.append(command[0])

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    waits = iter([False, True])
    monkeypatch.setattr(stop, "wait", lambda timeout: next(waits))
    monkeypatch.setattr(deploy.subprocess, "Popen", Proc)
    monkeypatch.setattr(deploy.os, "killpg", lambda pid, sig: None)
    deploy.supervise([("Dagster daemon", ["daemon"]), ("API", ["api"])], stop, threading.Event(), wait=0)
    assert started == ["api", "daemon"]


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
    done = threading.Event()
    deploy.read_projects(["kestrel"], done)
    assert done.is_set()
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
    monkeypatch.setattr(deploy.db, "connect", lambda **kw: nullcontext(SimpleNamespace(execute=lambda *a: calls.append("lock") if kw else None)))
    monkeypatch.setattr(deploy, "configure_dagster", lambda home, url: calls.append("dagster"))
    monkeypatch.setattr(deploy, "end_orphaned_runs", lambda: calls.append("end orphans"))
    monkeypatch.setattr(deploy, "seed", lambda conn, bundle: calls.append("seed") or {"name": "first"})
    monkeypatch.setattr(deploy, "register_projects", lambda conn, first: first == "first" and ["kestrel"])
    monkeypatch.setattr(deploy.engine, "load_defaults", lambda conn: None)
    monkeypatch.setattr(deploy.projects, "use", lambda conn, name: nullcontext())
    monkeypatch.setattr(deploy.signal, "signal", lambda *args: None)
    monkeypatch.setattr(deploy, "commands", lambda added, role: [role])

    class Thread:
        def __init__(self, *, target, args, name, daemon):
            assert target is deploy.read_projects and args[0] == ["kestrel"]
            assert isinstance(args[1], threading.Event)
            assert name == "startup-readings" and daemon

        def start(self):
            calls.append("background readers")

    monkeypatch.setattr(deploy.threading, "Thread", Thread)
    monkeypatch.setattr(deploy, "supervise", lambda tasks, stop, ready=None: calls.append(f"serve {tasks[0]}"))
    monkeypatch.delenv(deploy.ROLE_ENV, raising=False)
    deploy.main(["--bundle", str(tmp_path)])
    assert calls == ["dagster", "end orphans", "lock", "seed", "background readers", "serve all"]
    # Split: the worker ends the runs its old machine left and does the readings; the web service does neither.
    calls.clear()
    deploy.main(["--bundle", str(tmp_path), "--role", "worker"])
    assert calls == ["dagster", "end orphans", "lock", "seed", "background readers", "serve worker"]
    calls.clear()
    monkeypatch.setenv(deploy.ROLE_ENV, "web")
    deploy.main(["--bundle", str(tmp_path)])
    assert calls == ["dagster", "lock", "seed", "serve web"]


def test_a_split_deployment_runs_each_process_on_one_service(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    web = [name for name, _ in deploy.commands(["kestrel"], "web")]
    monkeypatch.delenv("PORT", raising=False)  # a background worker is given no port
    worker = [name for name, _ in deploy.commands(["kestrel"], "worker")]
    assert web == ["Dagster webserver", "API"]
    assert worker == ["Dagster daemon", "Agent worker", "Agent worker (kestrel)"]
    assert sorted(web + worker) == sorted(name for name, _ in deploy.commands(["kestrel"]))
    with pytest.raises(ValueError):
        deploy.commands(role="api")


def test_dagster_url_searches_only_the_dagster_schema():
    assert deploy.dagster_url("postgres://u:p@host:5432/fuzer") == \
        "postgresql+psycopg2://u:p@host:5432/fuzer?options=-csearch_path%3Ddagster"
    url = deploy.dagster_url("postgresql+psycopg://u@/fuzer?host=/tmp/pg&options=-cstatement_timeout%3D0")
    assert url.startswith("postgresql+psycopg2://u@/fuzer?host=%2Ftmp%2Fpg&options=")
    assert "-csearch_path%3Ddagster+-cstatement_timeout%3D0" in url


def test_clear_disk_storage_drops_sqlite_and_old_run_output(tmp_path):
    """The SQLite databases (and a killed process's journals) go; recent run output stays."""
    now = 1_000_000_000.0
    old = now - (deploy.COMPUTE_LOG_DAYS + 1) * 86400
    files = {name: tmp_path / name for name in (
        "history/runs.db", "history/runs.db-journal", "schedules/schedules.db", "storage/index.db",
        "storage/run1.db", "storage/run1.db-wal", "storage/run1/compute_logs/a.out",
        "storage/run2/compute_logs/b.out", "dagster.yaml")}
    for f in files.values():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    os.utime(files["storage/run1/compute_logs/a.out"], (old, old))
    deploy.clear_disk_storage(tmp_path, now=now)
    assert sorted(str(f.relative_to(tmp_path)) for f in tmp_path.rglob("*") if f.is_file()) == \
        ["dagster.yaml", "storage/run2/compute_logs/b.out"]
    assert not (tmp_path / "storage" / "run1").exists()
    deploy.clear_disk_storage(tmp_path / "absent")  # a first deploy has nothing to clear


def test_configure_dagster_keeps_dagster_storage_in_postgres(monkeypatch, tmp_path):
    from dagster import DagsterInstance

    monkeypatch.delenv(deploy.DAGSTER_URL_ENV, raising=False)
    url = db.database_url()
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "runs.db").write_text("corrupt")  # what the old disk storage left behind
    try:
        deploy.configure_dagster(tmp_path, url)
        assert not (tmp_path / "history" / "runs.db").exists()
        with DagsterInstance.from_config(str(tmp_path)) as instance:
            assert type(instance.run_storage).__name__ == "PostgresRunStorage"
            assert type(instance.run_coordinator).__name__ == "QueuedRunCoordinator"
            assert instance.run_coordinator.get_run_queue_config().max_concurrent_runs == deploy.MAX_CONCURRENT_RUNS
            assert instance.run_monitoring_enabled
            assert instance.run_monitoring_max_runtime_seconds == deploy.MAX_RUNTIME_SECONDS
            assert {"AssetDaemon", "FreshnessDaemon"}.isdisjoint(instance.get_required_daemon_types())
            assert instance.get_runs() == []
            instance.event_log_storage.store_event(_event())  # NOTIFY on write: fails under psycopg 3
        with db.connect(url=url) as conn:
            assert conn.execute("select to_regclass('dagster.runs') is not null as made").fetchone()["made"]
            assert conn.execute("select to_regclass('public.runs') is null as clean").fetchone()["clean"]
    finally:
        with db.connect(autocommit=True, url=url) as conn:
            conn.execute(f"drop schema if exists {deploy.DAGSTER_SCHEMA} cascade")


def test_startup_ends_the_previous_instances_runs(monkeypatch, tmp_path):
    """Runs left starting or running, and scheduled runs left queued, are ended; a run queued by hand stays."""
    from dagster import DagsterInstance
    from dagster import DagsterRunStatus as S

    monkeypatch.delenv(deploy.DAGSTER_URL_ENV, raising=False)
    monkeypatch.setenv("DAGSTER_HOME", str(tmp_path))
    url = db.database_url()
    try:
        deploy.configure_dagster(tmp_path, url)
        with DagsterInstance.get() as instance:
            def run(status, **tags):
                return instance.add_run(_run(status, tags)).run_id
            ids = {"started": run(S.STARTED), "starting": run(S.STARTING),
                   "scheduled": run(S.QUEUED, **{"dagster/schedule_name": "work_tick_schedule"}),
                   "manual": run(S.QUEUED), "done": run(S.SUCCESS)}
        deploy._end_orphaned_runs()
        with DagsterInstance.get() as instance:
            status = {name: instance.get_run_by_id(i).status for name, i in ids.items()}
        assert status == {"started": S.CANCELED, "starting": S.CANCELED, "scheduled": S.CANCELED,
                          "manual": S.QUEUED, "done": S.SUCCESS}
    finally:
        with db.connect(autocommit=True, url=url) as conn:
            conn.execute(f"drop schema if exists {deploy.DAGSTER_SCHEMA} cascade")


def _run(status, tags):
    import uuid

    from dagster import DagsterRun
    from dagster._core.remote_origin import RegisteredCodeLocationOrigin, RemoteJobOrigin, RemoteRepositoryOrigin

    origin = RemoteJobOrigin(RemoteRepositoryOrigin(RegisteredCodeLocationOrigin("fuzer.ops.jobs"), "__repository__"),
                             "work_tick")  # a queued run needs one
    return DagsterRun(job_name="work_tick", run_id=str(uuid.uuid4()), status=status, tags=tags,
                      remote_job_origin=origin)


def _event():
    import time as clock

    from dagster import DagsterEvent, DagsterEventType, EventLogEntry
    from dagster._core.events import EngineEventData

    return EventLogEntry(error_info=None, level="INFO", user_message="", run_id="r1", timestamp=clock.time(),
                         dagster_event=DagsterEvent(DagsterEventType.ENGINE_EVENT.value, "job",
                                                               event_specific_data=EngineEventData()))
