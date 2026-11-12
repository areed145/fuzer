"""Fuzer: entity resolution, linking and ontology for industrial assets."""

import os
from pathlib import Path

from dotenv import dotenv_values, find_dotenv, load_dotenv

# Settings that are paths. Relative ones in a .env file mean relative to that file, so commands run
# from any subfolder (ui/, docs/) find the same data and the same built-in database.
PATH_SETTINGS = ("FUZER_DATA_DIR", "FUZER_PGDATA", "FUZER_MAPPINGS", "DAGSTER_HOME")


def _load_env() -> None:
    """Settings from a .env file: $FUZER_ENV_FILE, else the nearest .env from the current folder up.
    Variables already set in the environment win. FUZER_ENV_FILE=none turns it off (tests do)."""
    path = os.environ.get("FUZER_ENV_FILE")
    if path == "none":
        return
    path = path or find_dotenv(usecwd=True)
    if not path:
        return
    base = Path(path).resolve().parent
    from_file = {k for k in dotenv_values(path) if k not in os.environ}
    load_dotenv(path, override=False)
    for key in PATH_SETTINGS:
        if key in from_file and os.environ.get(key):
            os.environ[key] = os.pathsep.join(
                str(p if Path(p).is_absolute() else base / p) for p in os.environ[key].split(os.pathsep) if p)
    # The built-in database lives next to the .env, not in whatever folder a command runs from.
    os.environ.setdefault("FUZER_PGDATA", str(base / ".fuzer" / "pgdata"))


def _init_sentry() -> None:
    """Report crashes and errors to Sentry when SENTRY_DSN is set (the deployment sets it; local runs and tests don't).
    Every Fuzer process imports this package first: the deploy supervisor, the API, the agent workers and Dagster's
    webserver, daemon and run processes. Each is tagged with its program, so Sentry shows which one failed.
    Uncaught exceptions and ERROR log records become events; request bodies and user details are not sent."""
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    import sys

    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT") or ("render" if os.environ.get("RENDER") else "local"),
        release=os.environ.get("RENDER_GIT_COMMIT") or None,
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE") or 0),
        send_default_pii=False,
    )
    program = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "python"
    sentry_sdk.set_tag("program", "deploy" if program == "deploy.py" else program)


_load_env()
_init_sentry()
