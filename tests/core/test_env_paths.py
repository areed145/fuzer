"""Relative paths in .env mean relative to the .env file, from any folder."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def test_relative_paths_resolve_against_the_env_file():
    root = Path(tempfile.mkdtemp(prefix="fzenv"))
    (root / "ui").mkdir()
    (root / ".env").write_text("FUZER_DATA_DIR=fakeplant/data/offshore\nFUZER_MAPPINGS=maps\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("FUZER_")}
    code = "import os, fuzer; print(os.environ['FUZER_DATA_DIR']); print(os.environ['FUZER_PGDATA'])"
    out = subprocess.run([sys.executable, "-c", code], cwd=root / "ui", env=env, capture_output=True, text=True,
                         check=True).stdout.split()
    assert out == [str(root.resolve() / "fakeplant/data/offshore"), str(root.resolve() / ".fuzer/pgdata")]


def test_shell_values_are_left_alone():
    root = Path(tempfile.mkdtemp(prefix="fzenv"))
    (root / ".env").write_text("FUZER_DATA_DIR=from_file\n")
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("FUZER_")}, "FUZER_DATA_DIR": "mine"}
    out = subprocess.run([sys.executable, "-c", "import os, fuzer; print(os.environ['FUZER_DATA_DIR'])"],
                         cwd=root, env=env, capture_output=True, text=True, check=True).stdout.strip()
    assert out == "mine"
