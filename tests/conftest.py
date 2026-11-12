"""Shared test databases (#137): the Okan fake plant is generated, ingested and rebuilt once per session into a
template database on the embedded test Postgres; each test module that asks for it (`okan_db`) gets its own
copy (CREATE DATABASE ... TEMPLATE, a file copy), so modules can write without
seeing each other's changes and nothing is rebuilt twice.

The plant's generated files are shared too: a module that edits its data folder generates its own."""

import atexit
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

# Tests live in folders by package (tests/<package>/test_*.py); a few share fakes across folders
# (`from test_llm_links import FakeClaude`), so every test folder is importable.
for _d in sorted(Path(__file__).parent.iterdir()):
    if _d.is_dir() and not _d.name.startswith((".", "_")):
        sys.path.insert(0, str(_d))

# Never pick up a developer's .env: it may point at a real database the tests would reset.
os.environ["FUZER_ENV_FILE"] = "none"
# Nor a database from the shell (FUZER_DATABASE_URL, FUZER_PGDATA): the tests reset databases, so they run on
# an embedded Postgres in a temporary folder.
os.environ.pop("FUZER_DATABASE_URL", None)
os.environ["FUZER_PGDATA"] = tempfile.mkdtemp(prefix="fzpg")
# ...removed when the run ends. Registered before the server starts, so it runs after pgserver's own exit hook has
# stopped it (exit hooks run last registered first); each xdist worker removes its own.
atexit.register(shutil.rmtree, os.environ["FUZER_PGDATA"], ignore_errors=True)
# Nor a Claude key: tests run on recorded or fake answers, so one that forgets its fake fails instead of paying.
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["FUZER_AUTH_MODE"] = "local"  # Legacy API fixtures opt into trusted loopback attribution explicitly.

PLANTS = {"okan": ("okan", "okan")}  # name: (generator module, pack)


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
            from fuzer.core import projects

            projects.save(c, name, {"pack": pack}, "tests")  # the database's first project (no default pack, #376)
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
    settings._first.clear()
    try:
        with db.connect() as c:
            yield Plant(c, b.data, url, b)
    finally:
        mp.undo()
        settings._cache.clear()
        settings._first.clear()
        with _admin() as a:
            a.execute(f'drop database if exists "{db_name}" with (force)')


@pytest.fixture(scope="session")
def okan_data(_templates) -> Path:
    """The Okan field's generated files (read-only: a module that edits them generates its own)."""
    return _template(_templates, "okan").data


@pytest.fixture(scope="module")
def okan_db(request, _templates):
    """This module's copy of the Okan field, ingested and rebuilt: Plant(conn, data, url, built)."""
    yield from _plant_db(request, _templates, "okan")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when == "call":
        item._fuzer_call_failed = report.failed  # read by the shared-connection check below
    return report


@pytest.fixture(autouse=True)
def _shared_connection_left_clean(request):
    """A module's tests share one plant connection, so a test must not leave a transaction on it. Uncommitted
    writes hold row locks that the API and MCP tools (their own connections) then wait on until the statement
    timeout, failing whichever test happens to run next in the random order. A test that wrote without
    committing fails here; one that only read is rolled back, releasing the locks a refresh or schema change
    would need. A test that already failed keeps its own failure: a half-finished transaction is expected."""
    shared = {}  # the plant's connection, and a module's own shared `conn` fixture (often the same one)
    defs = request.node._fixtureinfo.name2fixturedefs
    if "okan_db" in request.fixturenames:
        c = request.getfixturevalue("okan_db").conn
        shared[id(c)] = ("okan_db", c)
    if "conn" in request.fixturenames and defs.get("conn") and defs["conn"][-1].scope != "function":
        if isinstance(c := request.getfixturevalue("conn"), psycopg.Connection):  # a test's own conn isn't shared
            shared.setdefault(id(c), ("conn", c))
    yield
    leaked = []
    for name, conn in shared.values():
        if conn.closed:
            continue
        status = conn.info.transaction_status
        if status == psycopg.pq.TransactionStatus.INERROR:
            conn.rollback()
        elif status == psycopg.pq.TransactionStatus.INTRANS:
            row = conn.execute("select txid_current_if_assigned() is not null as wrote").fetchone()
            conn.rollback()
            if (row["wrote"] if isinstance(row, dict) else row[0]):
                leaked.append(name)
    if leaked and not getattr(request.node, "_fuzer_call_failed", True):
        pytest.fail(f"left uncommitted writes on the shared {' and '.join(leaked)} connection; commit (or roll "
                    "back) before the test ends, or later tests wait on its locks", pytrace=False)


# ---- tiers -----------------------------------------------------------------------------------------

E2E = {"test_replay", "test_ops_eval", "test_eval", "test_up"}  # replays and evals: CI and nightly
PLANT_FIXTURES = {"okan_db", "okan_data"}
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


@pytest.hookimpl(tryfirst=True)  # before -m picks the tiers it was asked for
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


class _Sequence:
    """A module marked `sequence` is one story told in steps (read, then cache, then change): its tests keep the
    file's order, in the slots the shuffle gave them. Everything else runs in any order."""

    @pytest.hookimpl(trylast=True)  # after pytest-randomly's shuffle
    def pytest_collection_modifyitems(self, config, items):
        slots: dict[str, list[int]] = {}
        for i, item in enumerate(items):
            if item.get_closest_marker("sequence"):
                slots.setdefault(str(item.fspath), []).append(i)
        for idx in slots.values():
            steps = sorted((items[i] for i in idx), key=lambda it: it.reportinfo()[1] or 0)
            for i, it in zip(idx, steps):
                items[i] = it


def pytest_configure(config):
    config.pluginmanager.register(_Sequence(), "fuzer-sequence")


def pytest_unconfigure(config):
    """Stop this process's test Postgres and remove its folder (an xdist worker may skip exit hooks)."""
    from fuzer.core import db

    if db._server is not None:
        db._server.cleanup()
        db._server = None
    shutil.rmtree(os.environ["FUZER_PGDATA"], ignore_errors=True)
