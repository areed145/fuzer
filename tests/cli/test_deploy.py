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


def test_worker_starts_when_the_ai_budget_is_spent(monkeypatch, capsys):
    """A spent month's budget no longer keeps the worker from starting (it waits in agents/worker.run), and the log
    says the budget is spent rather than that no provider is configured."""
    from fuzer.core import usage

    monkeypatch.setenv("PORT", "12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(usage, "over_budget", lambda: True)
    assert "Agent worker" in dict(deploy.commands())
    out = capsys.readouterr().out
    assert "budget" in out and "not configured" not in out


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


def test_held_tasks_start_when_their_gate_opens(monkeypatch):
    started, stop, owned, read = [], threading.Event(), threading.Event(), threading.Event()

    class Proc:
        pid = 5

        def __init__(self, command, **kwargs):
            started.append(command[0])

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    waits = iter([False, False, False, True])

    def wait_once(timeout):
        if started == ["api"]:  # the previous instance stopped: the agent worker may start
            owned.set()
        elif started == ["api", "agent"]:  # the readings finished: the daemon may start
            read.set()
        return next(waits)

    monkeypatch.setattr(stop, "wait", wait_once)
    monkeypatch.setattr(deploy.subprocess, "Popen", Proc)
    monkeypatch.setattr(deploy.os, "killpg", lambda pid, sig: None)
    deploy.supervise([("Dagster daemon", ["daemon"]), ("Agent worker", ["agent"]), ("API", ["api"])], stop,
                     {"Dagster daemon": read, "Agent worker": owned})
    assert started == ["api", "agent", "daemon"]


def test_a_new_instance_waits_for_the_old_one_before_taking_over_the_scheduled_work(monkeypatch):
    """Zero-downtime deploys run the old and new instance side by side for a while: only one runs the scheduled work,
    and the new one ends the old one's runs only once it has stopped."""
    monkeypatch.setattr(deploy, "OWNER_POLL_SECONDS", 0.05)
    old = deploy.wait_for_ownership(threading.Event())
    assert old is not None
    calls, stop, owned, read, holder = [], threading.Event(), threading.Event(), threading.Event(), []
    monkeypatch.setattr(deploy, "end_orphaned_runs", lambda: calls.append("end orphans"))
    monkeypatch.setattr(deploy, "read_projects", lambda added, done: calls.append("read") or done.set())
    t = threading.Thread(target=deploy.take_over, args=([], stop, owned, read),
                         kwargs={"holder": holder, "wait": 5}, daemon=True)
    t.start()
    assert not owned.wait(0.3) and calls == [], "the old instance still runs the scheduled work"
    old.close()  # the old instance stops
    assert owned.wait(5) and read.wait(5)
    t.join(5)
    assert calls == ["end orphans", "read"]
    stopped = threading.Event()
    stopped.set()
    assert deploy.wait_for_ownership(stopped) is None  # told to stop while waiting: it gives up
    holder[0].close()


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


def test_main_serves_at_once_and_takes_over_the_scheduled_work_in_the_background(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("FUZER_DATABASE_URL", "postgresql://unused/test")
    monkeypatch.setenv("FUZER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DAGSTER_HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(deploy.db, "connect", lambda **kw: nullcontext(SimpleNamespace(execute=lambda *a: calls.append("lock") if "advisory" in a[0] else SimpleNamespace(fetchone=lambda: None, fetchall=list))))
    monkeypatch.setattr(deploy, "configure_dagster", lambda home, url: calls.append("dagster"))
    monkeypatch.setattr(deploy, "keep_assets", lambda: calls.append("assets"))
    monkeypatch.setattr(deploy, "seed", lambda conn, bundle: calls.append("seed") or {"name": "first"})
    monkeypatch.setattr(deploy, "register_projects", lambda conn, first: first == "first" and ["kestrel"])
    monkeypatch.setattr(deploy.engine, "load_defaults", lambda conn: None)
    monkeypatch.setattr(deploy.projects, "use", lambda conn, name: nullcontext())
    monkeypatch.setattr(deploy.signal, "signal", lambda *args: None)

    class Thread:
        def __init__(self, *, target, args, kwargs, name, daemon):
            assert target is deploy.take_over and args[0] == ["kestrel"]
            assert name == "take-over" and daemon

        def start(self):
            calls.append("take over")

    def supervise(tasks, stop, gates=None):
        gates = gates or {}
        calls.append(f"serve {[n for n, _ in tasks if n not in gates]} holding {sorted(gates)}")

    monkeypatch.setattr(deploy.threading, "Thread", Thread)
    monkeypatch.setattr(deploy, "supervise", supervise)
    monkeypatch.delenv(deploy.ROLE_ENV, raising=False)
    deploy.main(["--bundle", str(tmp_path)])
    assert calls == ["dagster", "lock", "seed", "assets", "take over",
                     "serve ['Dagster webserver', 'API'] holding ['Agent worker', 'Agent worker (kestrel)', "
                     "'Dagster daemon']"]
    calls.clear()
    monkeypatch.delenv("PORT", raising=False)
    deploy.main(["--bundle", str(tmp_path), "--role", "worker"])
    assert calls == ["dagster", "lock", "seed", "assets", "take over",
                     "serve [] holding ['Agent worker', 'Agent worker (kestrel)', 'Dagster daemon']"]
    calls.clear()
    monkeypatch.setenv(deploy.ROLE_ENV, "web")
    deploy.main(["--bundle", str(tmp_path)])
    assert calls == ["dagster", "lock", "seed", "assets", "serve ['Dagster webserver', 'API'] holding []"]


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
            assert type(instance.compute_log_manager).__name__ == "NoOpComputeLogManager"  # nothing fills the disk
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
