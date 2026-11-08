"""Exercise the repository's import-linter contracts without importing application modules."""

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "pyproject.toml"


@pytest.fixture
def package(tmp_path):
    config = tomllib.loads(CONFIG.read_text())
    contract = next(c for c in config["tool"]["importlinter"]["contracts"] if c["type"] == "layers")
    pkg = tmp_path / "src" / "fuzer"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    for layer in contract["layers"]:
        for name in re.split(r"\s*[|:]\s*", layer):
            name = name.removeprefix("fuzer.")
            directory = pkg / name
            directory.mkdir()
            (directory / "__init__.py").write_text("")
    for name in ("components", "reference", "resources"):
        (pkg / f"{name}.py").write_text("")
    (pkg / "ops" / "sre_handoff.py").write_text("from fuzer import agents\n")
    (pkg / "workflow" / "engine.py").write_text("")
    (pkg / "ops" / "jobs.py").write_text("from fuzer.workflow import engine\n")
    (tmp_path / "pyproject.toml").write_text(CONFIG.read_text())
    return pkg


def lint(package):
    executable = shutil.which("lint-imports")
    assert executable is not None, "Run with uv run pytest so import-linter is available"
    return subprocess.run(
        [executable, "--no-cache", "--no-logo", "--config", "pyproject.toml"],
        cwd=package.parents[1],
        env={**os.environ, "PYTHONPATH": str(package.parent)},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("as_package", [True, False], ids=["package", "module"])
def test_new_top_level_code_requires_layer_assignment(package, as_package):
    if as_package:
        service = package / "unassigned_service"
        service.mkdir()
        module = service / "__init__.py"
    else:
        module = package / "unassigned_service.py"
    module.write_text("from fuzer.api import routes\n")
    (package / "api" / "routes.py").write_text("")

    result = lint(package)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "fuzer.unassigned_service" in result.stdout
    assert "not listed as layers" in result.stdout


@pytest.mark.parametrize(
    ("importer", "source", "imported"),
    [
        ("core/invalid.py", "import fuzer.api\n", "fuzer.api"),
        ("sources/invalid.py", "from ..api import routes\n", "fuzer.api"),
        ("graph/invalid.py", "def query():\n    from fuzer import analysis\n", "fuzer.analysis"),
        ("api/invalid.py", "from fuzer import evals\n", "fuzer.evals"),
        ("evals/invalid.py", "from fuzer import api\n", "fuzer.api"),
        ("ops/other.py", "from fuzer import agents\n", "fuzer.agents"),
    ],
)
def test_upward_and_independent_sibling_imports_fail(package, importer, source, imported):
    (package / "api" / "routes.py").write_text("")
    (package / importer).write_text(source)

    result = lint(package)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"is not allowed to import {imported}" in result.stdout


def test_shared_domain_operations_and_existing_exception_are_allowed(package):
    imports = {
        "api/routes.py": "from fuzer.sources import quality\n",
        "cli/commands.py": "from fuzer.sources import quality\n",
        "agents/tools.py": "from fuzer.sources import quality\n",
        "sources/quality.py": "from fuzer.core import db\n",
        "core/db.py": "from fuzer.resources import SCHEMA\n",
        "ops/jobs.py": "from fuzer.plots import general\nfrom fuzer.workflow import engine\n",
        "plots/general.py": "from fuzer.ops import board\n",
        "ops/board.py": "",
    }
    for name, source in imports.items():
        (package / name).write_text(source)

    result = lint(package)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Layers (#135)" in result.stdout and result.stdout.count("(1 ignored import)") == 2


def test_stale_exception_fails(package):
    (package / "ops" / "sre_handoff.py").write_text("")

    result = lint(package)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "fuzer.ops.sre_handoff -> fuzer.agents" in result.stdout


@pytest.mark.parametrize("importer", ["sources/alerts.py", "analysis/limits.py", "ops/sre_handoff.py", "graph/x.py"])
def test_raising_work_goes_through_the_port(package, importer):
    (package / "core" / "work.py").write_text("")
    path = package / importer
    path.write_text((path.read_text() if path.exists() else "") + "from fuzer.workflow import engine\n")

    result = lint(package)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "fuzer.workflow" in result.stdout and "Work port" in result.stdout


def test_the_port_itself_is_allowed(package):
    (package / "core" / "work.py").write_text("")
    (package / "sources" / "alerts.py").write_text("from fuzer.core import work\n")
    (package / "ops" / "board.py").write_text("from fuzer.core import work\n")

    result = lint(package)

    assert result.returncode == 0, result.stdout + result.stderr
