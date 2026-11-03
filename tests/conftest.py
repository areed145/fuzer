"""Shared test databases (#137): each fake plant is generated, ingested and rebuilt once per session into a
template database on the embedded test Postgres; each test module that asks for one (`okan_db`,
`offshore_db`) gets its own copy (CREATE DATABASE ... TEMPLATE, a file copy), so modules can write without
seeing each other's changes and nothing is rebuilt twice.

The plant's generated files are shared too: a module that edits its data folder generates its own."""

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

# Never pick up a developer's .env: it may point at a real database the tests would reset.
os.environ["FUZER_ENV_FILE"] = "none"
# Nor a database from the shell (FUZER_DATABASE_URL, FUZER_PGDATA): the tests reset databases, so they run on
# an embedded Postgres in a temporary folder.
os.environ.pop("FUZER_DATABASE_URL", None)
os.environ["FUZER_PGDATA"] = tempfile.mkdtemp(prefix="fzpg")
# Nor a Claude key: tests run on recorded or fake answers, so one that forgets its fake fails instead of paying.
os.environ.pop("ANTHROPIC_API_KEY", None)

PLANTS = {  # name: (generator module, pack)
    "okan": ("okan", "okan"),
    "offshore": ("offshore", "offshore_demo"),
}


@dataclass
class Built:
    data: Path
    generated: dict  # what the generator returned
    loaded: dict  # what ingest returned
    stats: dict  # what the rebuild returned


@dataclass
class Plant:
    conn: psycopg.Connection
    data: Path
    url: str
    built: Built


def _admin():
    from fuzer.core import db

    return db.connect(autocommit=True, url=db.database_url())


def _url(name: str) -> str:
    from fuzer.core import db

    return make_conninfo(db.database_url(), dbname=name)


@pytest.fixture(scope="session")
def _templates():
    """name -> Built, for each plant built so far (built on first use)."""
    built: dict[str, Built] = {}
    yield built
    with _admin() as a:
        for name in built:
            a.execute(f'drop database if exists "tpl_{name}" with (force)')


def _template(built: dict, name: str) -> Built:
    if name in built:
        return built[name]
    import importlib

    from fuzer.core import db
    from fuzer.core.pack import load_pack
    from fuzer.ops import pipeline
    from fuzer.sources import ingest

    gen, pack = PLANTS[name]
    data = Path(tempfile.mkdtemp(prefix=f"fz{name}"))
    generated = importlib.import_module(f"fakeplant.{gen}").generate(data)
    with _admin() as a:
        a.execute(f'drop database if exists "tpl_{name}" with (force)')
        a.execute(f'create database "tpl_{name}"')
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATA_DIR", str(data))
    try:
        with db.connect(url=_url(f"tpl_{name}")) as c:
            db.init_schema(c)
            loaded = ingest.ingest(c, data)
            stats = pipeline.rebuild(c, load_pack(pack))
    finally:
        mp.undo()
    built[name] = Built(data, generated, loaded, stats)
    return built[name]


def _plant_db(request, built: dict, name: str):
    b = _template(built, name)
    db_name = f"{name}_{re.sub(r'\W', '_', request.module.__name__.rsplit('.', 1)[-1])}"
    with _admin() as a:
        a.execute(f'drop database if exists "{db_name}" with (force)')
        a.execute(f'create database "{db_name}" template "tpl_{name}"')
    url = _url(db_name)
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATABASE_URL", url)  # code that opens its own connection (MCP tools, the API) lands here too
    mp.setenv("FUZER_DATA_DIR", str(b.data))
    from fuzer.core import db, settings

    settings._cache.clear()
    try:
        with db.connect() as c:
            yield Plant(c, b.data, url, b)
    finally:
        mp.undo()
        settings._cache.clear()
        with _admin() as a:
            a.execute(f'drop database if exists "{db_name}" with (force)')


@pytest.fixture(scope="session")
def okan_data(_templates) -> Path:
    """The Okan field's generated files (read-only: a module that edits them generates its own)."""
    return _template(_templates, "okan").data


@pytest.fixture(scope="session")
def offshore_data(_templates) -> Path:
    return _template(_templates, "offshore").data


@pytest.fixture(scope="module")
def okan_db(request, _templates):
    """This module's copy of the Okan field, ingested and rebuilt: Plant(conn, data, url, built)."""
    yield from _plant_db(request, _templates, "okan")


@pytest.fixture(scope="module")
def offshore_db(request, _templates):
    """This module's copy of the offshore demo (Osprey Bravo), ingested and rebuilt."""
    yield from _plant_db(request, _templates, "offshore")


# ---- tiers -----------------------------------------------------------------------------------------

E2E = {"test_replay", "test_ops_eval", "test_eval", "test_up"}  # replays and evals: CI and nightly
PLANT_FIXTURES = {"okan_db", "offshore_db", "okan_data", "offshore_data"}
DB_USE = ("db.connect(", "db.reset(", "TestClient(", "mcp_server.", "ingest.ingest(")
_src: dict[str, str] = {}


def _uses_db(item) -> bool:
    """Whether a test needs a database: a plant fixture; in a module still on its own database fixtures, any
    test; otherwise a test whose own code opens one (directly, or through the API or MCP tools)."""
    import inspect

    if PLANT_FIXTURES & set(item.fixturenames):
        return True
    path = str(item.fspath)
    if path not in _src:
        _src[path] = Path(path).read_text()
    module = _src[path]
    if any(x in module for x in ("db.connect(", "db.reset(")) and not any(f in module for f in PLANT_FIXTURES):
        return True
    try:
        own = inspect.getsource(item.function)
    except (OSError, TypeError):
        return True
    return any(x in own for x in DB_USE)


def pytest_collection_modifyitems(config, items):
    for item in items:
        mod = item.module.__name__.rsplit(".", 1)[-1]
        if any(item.get_closest_marker(t) for t in ("unit", "integration", "e2e")):
            continue  # the test says which tier it is
        if mod in E2E:
            item.add_marker(pytest.mark.e2e)
        elif _uses_db(item):
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)
