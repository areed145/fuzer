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


_load_env()
