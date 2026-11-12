"""Single-instance Render runtime: API, Dagster and workflow worker on shared storage."""

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from ..core import db, llm, projects
from ..core.pack import current_pack
from ..ops import pipeline
from ..workflow import engine
from .demo_seed import register_projects, seed


def commands(added: list[str] | tuple[str, ...] = ()) -> list[tuple[str, list[str]]]:
    """The processes this instance runs. Dagster serves every project (a run per project); each project added
    beside the first gets its own agent worker, which runs that project's workflow agent steps."""
    port = int(os.environ.get("PORT", "10000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    tasks = [
        ("Dagster webserver", ["dagster-webserver", "-m", "fuzer.ops.jobs",
                               "-h", "127.0.0.1", "-p", "3000"]),
        ("Dagster daemon", ["dagster-daemon", "run", "-m", "fuzer.ops.jobs"]),
    ]
    if llm.configured("agents"):
        tasks.append(("Agent worker", ["fuzer", "work", "worker"]))  # the database's first project
        tasks += [(f"Agent worker ({name})", ["fuzer", "--project", name, "work", "worker"]) for name in added]
    else:
        print("The agents provider is not configured: Ask and workflow agent steps are unavailable. "
              "Configure its key and redeploy to start the worker.", flush=True)
    tasks.append(("API", [sys.executable, "-m", "uvicorn", "fuzer.api:app",
                          "--host", "0.0.0.0", "--port", str(port)]))
    return tasks


def supervise(tasks: list[tuple[str, list[str]]], stop: threading.Event) -> None:
    children: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, command in tasks:
            print(f"Starting {name}", flush=True)
            children.append((name, subprocess.Popen(command, start_new_session=True)))
        while not stop.wait(1):
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


def read_projects(added: list[str]) -> None:
    """Enrich each project's loaded register without holding up API startup or replaying failures."""
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


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="Run this instance: seed its first bundle, add the rest, serve.")
    p.add_argument("--bundle", required=True,
                   help="the seed bundle this database's first project comes from (its folder, with bundle.yaml)")
    args = p.parse_args(argv)
    if not os.environ.get("FUZER_DATABASE_URL"):
        raise SystemExit("Render runtime requires FUZER_DATABASE_URL; embedded Postgres is not used")
    home = Path(os.environ.get("DAGSTER_HOME", "/var/lib/fuzer/dagster"))
    home.mkdir(parents=True, exist_ok=True)
    (home / "dagster.yaml").touch(exist_ok=True)
    os.environ["DAGSTER_HOME"] = str(home.resolve())
    with db.connect() as conn:
        first = seed(conn, Path(args.bundle))["name"]
        engine.load_defaults(conn)
        added = register_projects(conn, first)
    for name in added:
        with db.connect() as conn, projects.use(conn, name), db.connect() as scoped:
            engine.load_defaults(scoped)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    threading.Thread(target=read_projects, args=(added,), name="startup-readings", daemon=True).start()
    supervise(commands(added), stop)


if __name__ == "__main__":
    main()
