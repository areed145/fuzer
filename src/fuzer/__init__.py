"""Fuzer: entity resolution, linking and ontology for industrial assets."""

import os

from dotenv import find_dotenv, load_dotenv


def _load_env() -> None:
    """Settings from a .env file: $FUZER_ENV_FILE, else the nearest .env from the current folder up.
    Variables already set in the environment win. FUZER_ENV_FILE=none turns it off (tests do)."""
    path = os.environ.get("FUZER_ENV_FILE")
    if path == "none":
        return
    load_dotenv(path or find_dotenv(usecwd=True), override=False)


_load_env()
