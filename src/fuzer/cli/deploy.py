"""Render runtime: API, Dagster and workflow worker on shared storage, on one instance (role `all`) or split into a
web service that only serves requests (role `web`) and a background worker that runs the jobs (role `worker`)."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..core import db, llm, projects
from ..core.pack import current_pack
from ..ops import pipeline, stops
from ..workflow import engine
from .demo_seed import register_projects, seed

ROLES = ("all", "web", "worker")
ROLE_ENV = "FUZER_ROLE"
SEED_LOCK = 7_709_001  # advisory lock: a web service and a worker starting together seed one after the other


def commands(added: list[str] | tuple[str, ...] = (), role: str = "all") -> list[tuple[str, list[str]]]:
    """The processes this instance runs. Dagster serves every project; each project added beside the first gets its
    own agent worker, which runs that project's workflow agent steps. Role `web` keeps only what answers a browser
    (the API, and the Dagster webserver behind "Open in Dagster"); role `worker` keeps what does scheduled work (the
    Dagster daemon, which starts every run on its own machine, and the agent workers)."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    tasks: list[tuple[str, list[str]]] = []
    if role != "worker":
        tasks.append(("Dagster webserver", ["dagster-webserver", "-m", "fuzer.ops.jobs",
                                            "-h", "127.0.0.1", "-p", "3000"]))
    if role != "web":
        tasks.append(("Dagster daemon", ["dagster-daemon", "run", "-m", "fuzer.ops.jobs"]))
        # The worker starts on a key alone: while the month's AI budget is spent it waits (agents/worker.run), so
        # raising the budget brings agent steps back without a redeploy.
        if llm.has_credentials("agents"):
            tasks.append(("Agent worker", ["fuzer", "work", "worker"]))  # the database's first project
            tasks += [(f"Agent worker ({name})", ["fuzer", "--project", name, "work", "worker"]) for name in added]
            if not llm.configured("agents"):
                print("The month's AI budget (Settings > AI usage) is spent: the agent worker waits until it is "
                      "raised or the month ends.", flush=True)
        else:
            print("The agents provider is not configured: Ask and workflow agent steps are unavailable. "
                  "Configure its key and redeploy to start the worker.", flush=True)
    if role != "worker":
        port = int(os.environ.get("PORT", "10000"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        tasks.append(("API", [sys.executable, "-m", "uvicorn", "fuzer.api:app",
                              "--host", "0.0.0.0", "--port", str(port)]))
    return tasks


DAGSTER_SCHEMA = "dagster"
DAGSTER_URL_ENV = "FUZER_DAGSTER_PG_URL"
# Run output is no longer written to the disk (compute_logs below): the 1 GB disk filled with it, and every run then
# failed to set up its log capture. What a run prints still reaches the service log, and its events are in Postgres.
# Output left from before is cleared at the next start.
COMPUTE_LOG_DAYS = 0
MAX_CONCURRENT_RUNS = 2  # each run is a ~250 MB Python process; four with the rest filled the 2 GB instance
MAX_RUNTIME_SECONDS = 4 * 3600  # the nightly rebuild is the longest run
DAGSTER_YAML = f"""\
# Written by fuzer.cli.deploy on every start: Dagster's run, event and schedule storage lives in the
# deployment's Postgres (schema {DAGSTER_SCHEMA}), not in SQLite on a disk, which the API, webserver,
# daemon and every run process would otherwise share. Run output (compute logs) is not kept: it goes to the service log.
compute_logs:
  module: dagster._core.storage.noop_compute_log_manager
  class: NoOpComputeLogManager
storage:
  postgres:
    postgres_url:
      env: {DAGSTER_URL_ENV}
# One instance with one CPU: a few runs at a time (each is its own Python process), and a run whose process died
# (killed with an old instance, or stuck) is ended rather than holding a run slot forever.
run_coordinator:
  module: dagster._core.run_coordinator
  class: QueuedRunCoordinator
  config:
    max_concurrent_runs: {MAX_CONCURRENT_RUNS}
    dequeue_interval_seconds: 10
run_monitoring:
  enabled: true
  start_timeout_seconds: 300
  cancel_timeout_seconds: 300
  max_runtime_seconds: {MAX_RUNTIME_SECONDS}
  max_resume_run_attempts: 0
  poll_interval_seconds: 60
# Fuzer defines no automation conditions or freshness policies: their daemons would only poll the database.
auto_materialize:
  enabled: false
  use_sensors: false
freshness:
  enabled: false
retention:
  schedule:
    purge_after_days: 14
  sensor:
    purge_after_days:
      skipped: 3
      failure: 14
      success: 14
"""


def dagster_url(url: str) -> str:
    """FUZER_DATABASE_URL as Dagster's SQLAlchemy URL, searching only the Dagster schema. The driver is pinned to
    psycopg2 (which dagster-postgres is written for): SQLAlchemy 2.1 picks psycopg 3 for a bare postgresql://, and
    Dagster's NOTIFY statements fail under psycopg 3's server-side parameters."""
    parts = urlsplit(url)
    scheme = "postgresql+psycopg2" if parts.scheme in ("postgres", "postgresql") or parts.scheme.startswith(
        "postgresql+") else parts.scheme
    query = dict(parse_qsl(parts.query))
    query["options"] = " ".join(filter(None, [f"-csearch_path={DAGSTER_SCHEMA}", query.get("options")]))
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def clear_disk_storage(home: Path, now: float | None = None) -> None:
    """Free the disk of what Dagster kept there before its storage moved to Postgres: the SQLite run,
    schedule and event databases (and any journals a killed process left), then run output older than
    COMPUTE_LOG_DAYS. The run history they held is not carried over."""
    cutoff = (now or time.time()) - COMPUTE_LOG_DAYS * 86400
    for old in [home / "history", home / "schedules"]:
        for f in old.glob("*.db*") if old.is_dir() else ():
            f.unlink(missing_ok=True)
    storage = home / "storage"
    if not storage.is_dir():
        return
    for f in storage.glob("*.db*"):  # SQLite event logs: index.db and one <run_id>.db per run
        f.unlink(missing_ok=True)
    for f in storage.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
    for d in sorted((d for d in storage.rglob("*") if d.is_dir()), key=lambda d: len(d.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()


def configure_dagster(home: Path, url: str) -> None:
    """Point every Dagster process (and the Jobs page) at Postgres storage in its own schema."""
    clear_disk_storage(home)
    with db.connect(autocommit=True, url=url) as conn:
        conn.execute(f'create schema if not exists "{DAGSTER_SCHEMA}"')
    os.environ[DAGSTER_URL_ENV] = dagster_url(url)
    (home / "dagster.yaml").write_text(DAGSTER_YAML)


def end_orphaned_runs() -> None:
    """End the runs the previous instance left behind, before this instance's daemon starts and only once the previous
    instance has stopped (take_over waits for its lock), so no run process of its survives: a run still
    marked starting or running is an orphan that would hold a run slot until it timed out, and a scheduled run still
    queued is a backlog its schedule will replace within minutes. Runs queued by hand stay queued. Runs in a separate
    process, so this supervisor does not keep Dagster loaded."""
    code = "from fuzer.cli.deploy import _end_orphaned_runs; _end_orphaned_runs()"
    if subprocess.run([sys.executable, "-c", code], check=False).returncode:
        print("Dagster: could not end the previous instance's runs; the run monitor ends them on its timeouts",
              flush=True)


def _end_orphaned_runs() -> None:
    import dagster as dg

    S = dg.DagsterRunStatus
    with dg.DagsterInstance.get() as instance:
        orphaned = instance.get_runs(dg.RunsFilter(statuses=[S.STARTING, S.STARTED, S.CANCELING]))
        for run in orphaned:
            instance.report_run_canceled(run, "Ended at startup: the instance running it was replaced.")
        queued = [run for run in instance.get_runs(dg.RunsFilter(statuses=[S.QUEUED, S.NOT_STARTED]))
                  if "dagster/schedule_name" in run.tags]
        for run in queued:
            instance.report_run_canceled(run, "Dropped at startup: its schedule starts a fresh run.")
    print(f"Dagster: ended {len(orphaned)} runs the previous instance left running and dropped {len(queued)} "
          "queued scheduled runs", flush=True)


READ_WAIT_SECONDS = 15 * 60  # the daemon's catch-up runs and the startup register rebuild together OOM'd 2 GB
OWNER_LOCK = 7_709_002  # advisory lock held by the instance that runs the scheduled work, for as long as it lives
OWNER_POLL_SECONDS = 5


def supervise(tasks: list[tuple[str, list[str]]], stop: threading.Event,
              gates: dict[str, threading.Event] | None = None) -> None:
    """Run every task, restarting none: one exiting stops the deployment. A task named in `gates` starts only once its
    event is set, so this instance can answer requests before it takes over the scheduled work."""
    children: list[tuple[str, subprocess.Popen]] = []
    gates = gates or {}
    later = [t for t in tasks if t[0] in gates]

    def start(name: str, command: list[str]) -> None:
        print(f"Starting {name}", flush=True)
        children.append((name, subprocess.Popen(command, start_new_session=True)))

    try:
        for name, command in tasks:
            if (name, command) not in later:
                start(name, command)
        if later:
            print(f"Holding {', '.join(n for n, _ in later)} until this instance takes over the scheduled work",
                  flush=True)
        while not stop.wait(1):
            for name, command in [t for t in later if gates[t[0]].is_set()]:
                start(name, command)
                later.remove((name, command))
            for name, proc in children:
                if proc.poll() is not None:
                    raise RuntimeError(f"{name} exited unexpectedly with code {proc.returncode}; "
                                       "stopping the deployment so Render can restart it")
    finally:
        for _, proc in reversed(children):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # the group has already exited
        for _, proc in reversed(children):
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()


def wait_for_ownership(stop: threading.Event, poll: float = OWNER_POLL_SECONDS):
    """Wait until no other instance runs the scheduled work, then hold the lock that says this one does, for as long
    as this process lives (the returned connection must stay open). On a zero-downtime deploy Render starts this
    instance while the previous one still serves and runs jobs; two Dagster daemons would start every schedule twice,
    and ending "orphaned" runs would cancel the old instance's live ones. Render stops the old instance once this one
    answers its health check, and its lock goes with it. None if told to stop first."""
    conn = db.connect(autocommit=True)
    said = False
    while not stop.is_set():
        if conn.execute("select pg_try_advisory_lock(%s) as got", (OWNER_LOCK,)).fetchone()["got"]:
            return conn
        if not said:
            print("Waiting for the previous instance to stop before taking over the scheduled work", flush=True)
            said = True
        stop.wait(poll)
    conn.close()
    return None


def take_over(added: list[str], stop: threading.Event, owned: threading.Event, read: threading.Event,
              wait: float = READ_WAIT_SECONDS, holder: list | None = None) -> None:
    """Become the instance that runs the scheduled work: wait for the previous one to stop, end the runs it left,
    then set `owned` (the agent workers start), run the startup readings and set `read` when they finish or `wait`
    seconds pass (the Dagster daemon starts), so startup work doesn't all land at once."""
    try:
        conn = wait_for_ownership(stop)
    except Exception as e:
        print(f"Could not take the scheduled-work lock ({e}); stopping so Render restarts this instance", flush=True)
        stop.set()
        return
    if conn is None:
        return
    if holder is not None:
        holder.append(conn)  # kept open: closing it releases the lock
    end_orphaned_runs()
    owned.set()
    done = threading.Event()
    threading.Thread(target=read_projects, args=(added, done), name="startup-readings", daemon=True).start()
    done.wait(wait)
    read.set()


def read_projects(added: list[str], done: threading.Event | None = None) -> None:
    """Enrich each project's loaded register without holding up API startup or replaying failures; set `done`
    when finished, failed or not."""
    try:
        _read_projects(added)
    finally:
        if done is not None:
            done.set()


def _read_projects(added: list[str]) -> None:
    for name in [None, *added]:
        try:
            with db.connect() as conn:
                project = name or projects.current(conn)
                with projects.use(conn, project), db.connect() as scoped:
                    pipeline.read_pending(scoped, current_pack(scoped),
                                          lambda msg, project=project: print(f"LLM readings ({project}): {msg}", flush=True))
        except Exception as e:
            print(f"LLM readings ({name or 'primary project'}) stopped: {e}. "
                  "Inspect recorded readings before a deliberate retry.", flush=True)


def keep_assets() -> None:
    """Keep this build's files beside earlier builds' in Postgres, so a page opened before this deploy can still load
    the files it names (P5.18a). The API serves from there when the build lacks a name."""
    from ..core import kept_assets
    from ..resources import UI_DIST

    if not (UI_DIST / "assets").is_dir():
        return
    try:
        with db.connect() as conn:
            print(f"Built files kept for open pages: {kept_assets.keep(conn, UI_DIST / 'assets')}", flush=True)
        os.environ[kept_assets.ENV] = "1"
    except Exception as e:  # pages opened before this deploy may need a reload, nothing worse
        os.environ.pop(kept_assets.ENV, None)
        print(f"Built files not kept for open pages ({e}); a page opened before this deploy may need a reload", flush=True)


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="Run this instance: seed its first bundle, add the rest, serve.")
    p.add_argument("--bundle", required=True,
                   help="the seed bundle this database's first project comes from (its folder, with bundle.yaml)")
    p.add_argument("--role", choices=ROLES, default=os.environ.get(ROLE_ENV, "all"),
                   help="all: everything on this instance; web: only the API and Dagster webserver; "
                        "worker: only the Dagster daemon, its runs and the agent workers")
    args = p.parse_args(argv)
    if not (url := os.environ.get("FUZER_DATABASE_URL")):
        raise SystemExit("Render runtime requires FUZER_DATABASE_URL; embedded Postgres is not used")
    home = Path(os.environ.get("DAGSTER_HOME", "/var/lib/fuzer/dagster"))  # instance settings only, rewritten here
    home.mkdir(parents=True, exist_ok=True)
    os.environ["DAGSTER_HOME"] = str(home.resolve())
    configure_dagster(home, url)
    with db.connect(autocommit=True) as lock:
        lock.execute("select pg_advisory_lock(%s)", (SEED_LOCK,))  # released when this connection closes
        with db.connect() as conn:
            first = seed(conn, Path(args.bundle))["name"]
            engine.load_defaults(conn)
            added = register_projects(conn, first)
        for name in added:
            with db.connect() as conn, projects.use(conn, name), db.connect() as scoped:
                engine.load_defaults(scoped)
    keep_assets()
    stop = threading.Event()

    def stopping(*_):  # recorded first, so the next instance knows the runs this stop interrupts (ops/stops.py)
        stops.mark_stopped()
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stopping)
    if args.role == "web":  # nothing here waits on the readings: the worker does them and holds its own daemon
        print("Role web: the Dagster daemon, its runs and the agent workers run on the worker service", flush=True)
        supervise(commands(added, "web"), stop)
        return
    # The API answers at once (Render's health check, then traffic); the scheduled work waits for the previous
    # instance to stop (take_over), since on a zero-downtime deploy both run for a while.
    owned, read, holder = threading.Event(), threading.Event(), []
    threading.Thread(target=take_over, args=(added, stop, owned, read), kwargs={"holder": holder},
                     name="take-over", daemon=True).start()
    tasks = commands(added, args.role)
    gates = {name: (read if name == "Dagster daemon" else owned) for name, _ in tasks
             if name == "Dagster daemon" or name.startswith("Agent worker")}
    supervise(tasks, stop, gates)


if __name__ == "__main__":
    main()
