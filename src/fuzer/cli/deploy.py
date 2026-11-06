"""Single-instance Render runtime: API, Dagster and workflow worker on shared storage."""

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from ..core import db
from ..workflow import engine
from .demo_seed import seed


def commands() -> list[tuple[str, list[str]]]:
    port = int(os.environ.get("PORT", "10000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    tasks = [
        ("Dagster webserver", ["dagster-webserver", "-m", "fuzer.ops.jobs",
                               "-h", "127.0.0.1", "-p", "3000"]),
        ("Dagster daemon", ["dagster-daemon", "run", "-m", "fuzer.ops.jobs"]),
    ]
    if os.environ.get("ANTHROPIC_API_KEY"):
        tasks.append(("Agent worker", ["fuzer", "--pack", os.environ["FUZER_PACK"], "work", "worker"]))
    else:
        print("ANTHROPIC_API_KEY is not set: Ask and workflow agent steps are unavailable. "
              "Set it in Render and redeploy to start the worker.", flush=True)
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


def main() -> None:
    if not os.environ.get("FUZER_DATABASE_URL"):
        raise SystemExit("Render runtime requires FUZER_DATABASE_URL; embedded Postgres is not used")
    if os.environ.get("FUZER_PACK") != "okan":
        raise SystemExit("Render test runtime requires FUZER_PACK=okan")
    home = Path(os.environ.get("DAGSTER_HOME", "/var/lib/fuzer/dagster"))
    home.mkdir(parents=True, exist_ok=True)
    (home / "dagster.yaml").touch(exist_ok=True)
    os.environ["DAGSTER_HOME"] = str(home.resolve())
    with db.connect() as conn:
        seed(conn, Path(os.environ["FUZER_DATA_DIR"]))
        engine.load_defaults(conn)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    supervise(commands(), stop)


if __name__ == "__main__":
    main()
