"""P1.6: `fuzer init --bundle` stands up Okan and Kestrel identically from a fresh database. The same three commands
run for each, with no facility, pack, data folder or provider in the environment, and nothing written to it."""

import os
import shutil
from pathlib import Path

import pytest
import yaml

from fuzer.cli import main
from fuzer.core import bundles, db, projects, settings
from fuzer.core import pack as packs
from fuzer.workflow import roles as workflow_roles

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ROOT / "fakeplant" / "bundles"
OLD_VARIABLES = ("FUZER_PROJECT", "FUZER_PACK", "FUZER_DATA_DIR", "FUZER_MAPPINGS", "FUZER_TIMESERIES",
                 "FUZER_TIMEZONE", "FUZER_CACHE_DIR")


def _fresh(name: str) -> str:
    from psycopg.conninfo import make_conninfo

    with db.connect(autocommit=True, url=db.database_url()) as a:
        a.execute(f'drop database if exists "{name}" with (force)')
        a.execute(f'create database "{name}"')
    return make_conninfo(db.database_url(), dbname=name)


def _okan_bundle(tmp_path: Path, exports: Path) -> Path:
    """Okan's shipped bundle with its exports where this test session generated them (CI has no
    fakeplant/data/okan); everything else is the shipped bundle's own files."""
    folder = tmp_path / "okan"
    shutil.copytree(SHIPPED / "okan", folder)
    manifest = yaml.safe_load((folder / "bundle.yaml").read_text())
    manifest["project"]["data"] = str(exports)
    (folder / "bundle.yaml").write_text(yaml.safe_dump(manifest))
    return folder


@pytest.fixture
def clean(monkeypatch):
    for var in OLD_VARIABLES:
        monkeypatch.delenv(var, raising=False)
    before = db.scope()
    yield
    db.set_default(before)
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def test_okan_and_kestrel_stand_up_identically_from_a_fresh_database(okan_data, tmp_path, monkeypatch, clean):
    person_roles = {name for name, spec in workflow_roles.BUILTIN.items() if spec[0] == "person"}
    outcomes = {}
    for name, bundle in (("okan", _okan_bundle(tmp_path, okan_data)), ("kestrel", SHIPPED / "kestrel")):
        url = _fresh(f"standup_{name}")
        monkeypatch.setenv("FUZER_DATABASE_URL", url)
        db.set_default(db.Scope(None))
        packs.clear_cache()
        settings._cache.clear()
        settings._first.clear()
        environment = dict(os.environ)

        main(["init", "--bundle", str(bundle)])
        main(["ingest"])
        main(["rebuild"])

        assert dict(os.environ) == environment, f"{name}: the commands wrote to the environment"
        b = bundles.read(bundle)
        with db.connect(url=url) as conn:
            assert projects.active(conn) == name  # the bundle is the database's first project
            cfg = projects.config(conn, name)
            assert cfg["source"] == "database" and cfg["bundle"]["digest"] == b["config"]["bundle"]["digest"]
            assert projects.field("data") == b["config"]["data"]  # from the project, not the environment
            assert packs.current_pack(conn).facility == b["pack"].facility
            runs = {r["status"] for r in conn.execute("select status from source_run")}
            roots = {r["source_key"] for r in conn.execute(
                "select source_key from source_record where source = 'sap' and record_type = 'floc' "
                "and coalesce(payload->>'parent', '') = ''")}
            outcomes[name] = {
                "records": conn.execute("select count(*) as n from source_record").fetchone()["n"] > 0,
                "entities": conn.execute("select count(*) as n from entity").fetchone()["n"] > 0,
                "register root is the pack's facility": roots == {b["pack"].facility},
                "loads": runs,
                "roster": {r["role"] for r in cfg["bundle"]["roster"]} <= person_roles and bool(cfg["bundle"]["roster"]),
                "operating model": cfg["bundle"]["operating_model"],
                "source packs exist": all(Path(p["path"]).is_file() for p in cfg["bundle"]["source_packs"]),
            }
    assert outcomes["okan"] == outcomes["kestrel"] == {
        "records": True, "entities": True, "register root is the pack's facility": True, "loads": {"done"}, "roster": True,
        "operating model": {"name": "standard", "version": 1}, "source packs exist": True}


def test_activating_a_project_writes_no_environment_and_old_variables_only_override_the_first(
        okan_data, tmp_path, monkeypatch, clean):
    url = _fresh("standup_activate")
    monkeypatch.setenv("FUZER_DATABASE_URL", url)
    with db.connect(url=url) as conn:
        db.init_schema(conn)
        bundles.init(conn, _okan_bundle(tmp_path, okan_data), by="test")
        bundles.init(conn, SHIPPED / "kestrel", by="test")
        environment = dict(os.environ)
        projects.activate(conn, "okan")
        assert dict(os.environ) == environment
        assert projects.field("data") == str(okan_data) and projects.field("timeseries") == "synthetic"
        monkeypatch.setenv("FUZER_DATA_DIR", "/srv/elsewhere")  # deprecated: still overrides the first project
        projects._warned.discard("FUZER_DATA_DIR")
        with pytest.warns(DeprecationWarning, match="FUZER_DATA_DIR is deprecated"):
            overridden = projects.field("data")
        assert overridden == "/srv/elsewhere"
        with projects.use(conn, "kestrel"):  # and never a project added beside it
            assert projects.field("data") == str(SHIPPED / "kestrel" / "exports")
            assert projects.field("timezone") == "America/Denver"


@pytest.mark.parametrize("extra, error", [
    ({"source_packs": "packs/measurement.yaml"}, "source_packs must be a list"),
    ({"source_packs": ["packs/nowhere.yaml"]}, "source pack nowhere.yaml is missing"),
    ({"source_packs": ["pack.yaml"]}, "is not a source pack"),
    ({"roster": "nowhere.yaml"}, "roster template nowhere.yaml is missing"),
    ({"roster": "pack.yaml"}, "must list roles"),
    ({"operating_model": "standard"}, "operating_model must name a model and its version"),
])
def test_a_bundles_source_packs_roster_and_operating_model_are_checked(tmp_path, extra, error):
    folder = tmp_path / "kestrel"
    shutil.copytree(SHIPPED / "kestrel", folder)
    manifest = {**yaml.safe_load((folder / "bundle.yaml").read_text()), **extra}
    (folder / "bundle.yaml").write_text(yaml.safe_dump(manifest))
    with pytest.raises(bundles.BundleError, match=error):
        bundles.read(folder)


def test_schedules_run_in_the_first_projects_timezone(tmp_path, monkeypatch, clean):
    """The scheduler's timezone comes from the database's first project (its bundle), not an environment variable."""
    from fuzer.ops import jobs

    url = _fresh("standup_timezone")
    monkeypatch.setenv("FUZER_DATABASE_URL", url)
    assert jobs.schedule_timezone() == "UTC"  # no project yet
    with db.connect(url=url) as conn:
        db.init_schema(conn)
        bundles.init(conn, SHIPPED / "kestrel", by="test")
    projects._fields.clear()
    assert jobs.schedule_timezone() == "America/Denver"
