"""A second facility from its own seed bundle (#376): Kestrel Ridge boots in a fresh database with nothing
from Okan, and the pack follows the database a process connects to, not an import-time global."""

import re
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.core import bundles, db, projects, settings
from fuzer.core import pack as packs
from fuzer.core.tags import TagGrammar

ROOT = Path(__file__).resolve().parents[2]
KESTREL = ROOT / "fakeplant" / "bundles" / "kestrel"
OKAN = ROOT / "fakeplant" / "bundles" / "okan"


# ---- pack-defined tag grammar ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def kestrel_pack():
    return bundles.read(KESTREL)["pack"]


@pytest.mark.parametrize("text,area,expected", [
    ("KRGP-A1-PT1001", None, "A1-PT-1001"),        # SAP floc
    ("KRGP:B1.VT2003.PV", None, "B1-VT-2003"),     # PI point
    ("C1-P-3002A", None, "C1-P-3002A"),
    ("P3002A (C1 DEHY)", "C1", "C1-P-3002A"),      # Maximo location, area from the text
    ("10-PT-3003", None, None),                    # an Okan tag: two-digit area, not a Kestrel one
    ("A1-PT-101", None, None),                     # Kestrel loops have four digits
])
def test_kestrel_tag_grammar(kestrel_pack, text, area, expected):
    assert kestrel_pack.tags.parse(text, default_area=area) == expected


def test_area_is_read_in_the_packs_format(kestrel_pack):
    okan = packs.load_pack("okan")
    assert kestrel_pack.tags.area_in("P3002A (C1 DEHY)") == "C1"
    assert kestrel_pack.tags.area_in("Train 20 inlet") is None
    assert okan.tags.area_in("Train 20 inlet") == "20"
    assert okan.tags.area_in("P3002A (C1 DEHY)") is None
    assert kestrel_pack.tags.is_area("B1") and not kestrel_pack.tags.is_area("10")


def test_a_pack_can_replace_the_whole_tag_pattern():
    # Function first, then loop, then the area after a slash: PT-0101/U2.
    g = TagGrammar(["PT", "TT"], area=r"U\d", pattern=r"(?P<func>{funcs})-(?P<loop>{loop})/(?P<area>{area})",
                   loop_digits=(4, 4))
    assert g.parse("see PT-0101/U2 and TT-0102/U3") == "U2-PT-0101"
    assert [t.key() for t in g.find_all("PT-0101/U2 and TT-0102/U3")] == ["U2-PT-0101", "U3-TT-0102"]


def test_a_tag_pattern_must_name_function_and_loop():
    with pytest.raises(ValueError, match="func"):
        TagGrammar(["PT"], pattern=r"(?P<loop>\d{4})")


# ---- bundle validation -------------------------------------------------------------------------------

def _bundle(tmp_path, manifest: dict, pack: dict | None = None) -> Path:
    (tmp_path / "bundle.yaml").write_text(yaml.safe_dump(manifest))
    if pack is not None:
        (tmp_path / "pack.yaml").write_text(yaml.safe_dump(pack))
    return tmp_path


def test_bundle_paths_are_relative_to_the_bundle_and_the_pack_is_carried():
    b = bundles.read(KESTREL)
    assert b["name"] == "kestrel"
    assert b["config"]["data"] == str(KESTREL / "exports")
    assert b["config"]["pack_config"]["facility"] == "KRGP"
    assert b["config"]["bundle"]["digest"] == bundles.read(KESTREL / "bundle.yaml")["config"]["bundle"]["digest"]


@pytest.mark.parametrize("manifest,pack,error", [
    ({"bundle": 2, "name": "x"}, {}, "unsupported bundle version"),
    ({"bundle": 1, "name": "Bad Name"}, {}, "name must be"),
    ({"bundle": 1, "name": "x"}, None, "pack file"),
    ({"bundle": 1, "name": "x"}, {"name": "x"}, "not a valid asset pack"),
    ({"bundle": 1, "name": "x", "project": {"pack": "okan"}}, None, "project.pack is set by the bundle"),
])
def test_malformed_bundles_fail_explicitly(tmp_path, manifest, pack, error):
    if pack is None and "project" in manifest:
        pack = yaml.safe_load((KESTREL / "pack.yaml").read_text())
    with pytest.raises(bundles.BundleError, match=error):
        bundles.read(_bundle(tmp_path, manifest, pack))


def test_a_missing_bundle_fails(tmp_path):
    with pytest.raises(bundles.BundleError, match="no bundle.yaml"):
        bundles.read(tmp_path)


# ---- acceptance: Kestrel boots from its bundle in a fresh database ----------------------------------

def _fresh(name: str) -> str:
    from psycopg.conninfo import make_conninfo

    with db.connect(autocommit=True, url=db.database_url()) as a:
        a.execute(f'drop database if exists "{name}" with (force)')
        a.execute(f'create database "{name}"')
    return make_conninfo(db.database_url(), dbname=name)


def _drop(name: str) -> None:
    from psycopg.conninfo import make_conninfo

    with db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres")) as a:
        a.execute(f'drop database if exists "{name}" with (force)')


@pytest.fixture
def kestrel_url():
    """A fresh database per test, so the tests don't depend on each other's order."""
    url = _fresh("kestrel_bundle")
    yield url
    _drop("kestrel_bundle")


@pytest.fixture
def serving(monkeypatch):
    """Point this process at a database, as a deployment does, with no pack or project in the environment."""
    def to(url: str):
        monkeypatch.setenv("FUZER_DATABASE_URL", url)
        monkeypatch.delenv("FUZER_PROJECT", raising=False)
        packs.clear_cache()
        settings._cache.clear()
        settings._first.clear()
    yield to
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def test_kestrel_boots_from_its_bundle_with_nothing_from_okan(kestrel_url, serving):
    from fuzer.api import home, investigation
    from fuzer.ops import pipeline
    from fuzer.sources import ingest

    serving(kestrel_url)
    with db.connect(url=kestrel_url) as conn:
        db.init_schema(conn)
        assert projects.active(conn) is None
        first = bundles.init(conn, KESTREL, by="test")
        assert first["status"] == "initialised" and first["facility"] == "KRGP"
        # Initialising is configuration only: nothing is loaded or built.
        assert conn.execute("select count(*) as n from source_record").fetchone()["n"] == 0
        assert conn.execute("select count(*) as n from entity").fetchone()["n"] == 0
        assert bundles.init(conn, KESTREL, by="test")["status"] == "unchanged"
        assert projects.active(conn) == "kestrel"

        pack = packs.current_pack(conn)
        assert (pack.name, pack.facility) == ("kestrel", "KRGP")
        loaded = ingest.ingest(conn, Path(first["data"]))
        assert set(loaded["loaded"]) == {"sap_floc", "sap_equipment", "pi_points", "maximo_assets"}
        built = pipeline.rebuild(conn, pack)
        assert built["entities"] > 0

        kinds = {r["kind"]: r["n"] for r in conn.execute(
            "select kind, count(*) as n from entity where merged_into is null and retired_at is null group by kind")}
        assert kinds["facility"] == 1 and kinds["area"] == 3 and kinds["tag"] == 13 and kinds["well"] == 2
        assert kinds["equipment"] == 6  # Maximo's four assets merged into SAP's by serial, despite its formats
        keys = {r["kind"]: set() for r in conn.execute("select distinct kind from entity")}
        for r in conn.execute("select e.kind, k.key from entity e join entity_key k using (fuzer_id) "
                              "where e.merged_into is null"):
            keys[r["kind"]].add(r["key"])
        assert keys["facility"] == {"KRGP"}
        assert keys["area"] == {"KRGP-A1", "KRGP-B1", "KRGP-C1"}
        assert {k for k in keys["tag"] if re.fullmatch(r"[A-C]\d-[A-Z]+-\d{4}[A-Z]?", k)} >= {
            "A1-V-1001", "B1-K-2001", "C1-P-3002A", "C1-FT-3005"}
        assert keys["well"] == {"KR 12-03H", "KR 12-04H"}
        assert not any("OK" in k for ks in keys.values() for k in ks)
        wells_with_series = conn.execute(
            "select count(distinct src) as n from edge where rel = 'has_timeseries' and src in "
            "(select fuzer_id from entity where kind = 'well')").fetchone()["n"]
        assert wells_with_series == 2


    # The API resolves the pack from the database it serves, at request time.
    app = FastAPI()
    app.include_router(home.router)
    app.include_router(investigation.router)
    client = TestClient(app)
    stats = client.get("/stats").json()
    assert (stats["pack"], stats["facility"]) == ("kestrel", "KRGP")
    assert stats["entities"]["tag"] == 13
    assert client.get("/demo/okan/investigation").status_code >= 400  # the Okan journey isn't Kestrel's


def test_each_database_serves_its_own_pack(kestrel_url, serving):
    """Two databases, two facilities, one process: switching the database switches the pack, with no restart and
    no environment variable naming either."""
    from fuzer.agents import mcp_server
    from fuzer.api import deps

    with db.connect(url=kestrel_url) as conn:
        db.init_schema(conn)
        assert bundles.init(conn, KESTREL, by="test")["status"] == "initialised"
    okan_url = _fresh("okan_bundle")
    try:
        with db.connect(url=okan_url) as conn:
            db.init_schema(conn)
            assert bundles.init(conn, OKAN, by="test")["status"] == "initialised"  # config only; no exports needed
        serving(okan_url)
        assert deps.pack().facility == "OKAN" and mcp_server.PACK.facility == "OKAN"
        serving(kestrel_url)
        assert deps.pack().facility == "KRGP" and mcp_server.PACK.facility == "KRGP"
        assert "facility KRGP" in mcp_server.instructions(deps.pack())
    finally:
        _drop("okan_bundle")


def test_a_database_without_a_project_serves_no_default_pack(serving, capsys):
    """Phase 5: there is no Okan fallback. A database that holds no project names that, and says how to fix it;
    the first project is the one a bundle stored with the `public` register, not a setting."""
    from fuzer.cli import main

    url = _fresh("no_project_yet")
    serving(url)
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            with pytest.raises(projects.ProjectError, match="fuzer init --bundle"):
                packs.resolve(conn)
            with pytest.raises(projects.ProjectError, match="holds none"):
                packs.current_pack()
            assert settings.project() is None
        with pytest.raises(SystemExit, match="No project to serve"):
            main(["rebuild"])
        main(["init", "--bundle", str(OKAN)])  # works on an empty database
        assert '"status": "initialised"' in capsys.readouterr().out
        packs.clear_cache()
        settings._first.clear()
        with db.connect(url=url) as conn:
            assert projects.active(conn) == "okan" and packs.current_pack(conn).facility == "OKAN"
            assert conn.execute("select count(*) as n from setting where key = 'project.active'").fetchone()["n"] == 0
        assert settings.project() == "okan"  # settings are scoped to the first project without naming it anywhere
    finally:
        _drop("no_project_yet")


def test_init_refuses_a_database_with_data_loaded_without_a_project(serving):
    url = _fresh("unowned_bundle")
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            conn.execute("insert into source_record (source, record_type, source_key, payload) "
                         "values ('sap', 'floc', 'X', '{}')")
            conn.commit()
            with pytest.raises(bundles.BundleError, match="without a project"):
                bundles.init(conn, KESTREL, by="test")
            assert projects.active(conn) is None
    finally:
        _drop("unowned_bundle")


def test_a_bundle_missing_its_own_exports_folder_fails(tmp_path):
    pack = yaml.safe_load((KESTREL / "pack.yaml").read_text())
    with pytest.raises(bundles.BundleError, match="exports folder exports is missing"):
        bundles.read(_bundle(tmp_path, {"bundle": 1, "name": "x", "project": {"data": "exports"}}, pack))


# ---- several projects in one database (multi-project phase 2) ---------------------------------------

def test_two_projects_share_one_database_without_seeing_each_other(serving, monkeypatch):
    """Okan is the database's first project (public); Kestrel is added beside it in its own schema, ingested and
    rebuilt there. Each project's code sees only its own register, pack and settings; accounts stay shared."""
    from fuzer.api import deps
    from fuzer.ops import pipeline
    from fuzer.sources import ingest

    url = _fresh("two_projects")
    serving(url)
    monkeypatch.setenv("FUZER_TIMESERIES", "synthetic")  # as on Render: process-wide, meant for the first project
    monkeypatch.setenv("FUZER_DATA_DIR", "/srv/okan")
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            assert bundles.init(conn, OKAN, by="test")["status"] == "initialised"
            added = bundles.init(conn, KESTREL, by="test")
            assert (added["status"], added["schema"]) == ("added", "proj_kestrel")
            assert bundles.init(conn, KESTREL, by="test")["status"] == "unchanged"
            assert projects.active(conn) == "okan"  # the first project stays the database's default
            conn.execute("insert into access_account (id, tenant_id, object_id, display_name, enabled, owner) "
                         "values ('a1', 't', 'o', 'Ana', true, true)")
            conn.commit()

            with projects.use(conn, "kestrel"):
                with db.connect(url=url) as k:
                    assert k.execute("select current_schema() as s").fetchone()["s"] == "proj_kestrel"
                    pack = packs.current_pack(k)
                    assert pack.facility == "KRGP" and deps.pack().facility == "KRGP"
                    assert settings.project() == "kestrel"
                    ingest.ingest(k, Path(added["data"]))
                    assert pipeline.rebuild(k, pack)["entities"] == 34
                    assert k.execute("select display_name from access_account").fetchone()["display_name"] == "Ana"
                # Kestrel's own settings, whatever the process's environment says (one deployment, two projects).
                from fuzer.sources import timeseries

                assert projects.field("data") == added["data"] and projects.field("timezone") == "America/Denver"
                assert timeseries.provider().name == "none"  # no historian yet: empty series, not Okan's synthetic
                # The API's rebuild queue runs Kestrel's rebuild on its own thread, in Kestrel's schema.
                deps.rebuilder.request()
                deps.rebuilder.wait()
                assert deps.rebuilder.last["stats"]["entities"] == 34, deps.rebuilder.last
            assert deps.rebuilder.status()["last"] is None  # Okan's queue never ran
            assert projects.field("data") == "/srv/okan" and projects.field("timeseries") == "synthetic"

            # Back in the first project: none of Kestrel's records or entities, and Okan's pack.
            assert conn.execute("select count(*) as n from source_record").fetchone()["n"] == 0
            assert conn.execute("select count(*) as n from entity").fetchone()["n"] == 0
            assert packs.current_pack(conn).facility == "OKAN" and deps.pack().facility == "OKAN"
            n = conn.execute("select count(*) as n from proj_kestrel.entity").fetchone()["n"]
            assert n >= 34  # Kestrel's register is where it was built
            with projects.use(conn, "okan"), db.connect(url=url) as o:
                assert o.execute("select count(*) as n from entity").fetchone()["n"] == 0
    finally:
        _drop("two_projects")


def test_scheduled_jobs_run_once_per_project_in_its_own_schema(serving, tmp_path):
    """Dagster (multi-project phase 4): schedules and the export sensor start a run per project, tagged with it,
    and a tagged run loads that project's own exports into its own schema with its own pack."""
    import dagster as dg

    from fuzer.ops import jobs

    url = _fresh("dagster_projects")
    serving(url)
    before = db._default
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            bundles.init(conn, OKAN, by="test")
            added = bundles.init(conn, KESTREL, by="test")
        fuzer = jobs.Fuzer(data_dir=str(tmp_path), database_url=url)  # Okan's folder: empty here
        assert fuzer.targets() == [("kestrel", added["data"]), ("okan", str(tmp_path))]

        schedule = next(s for s in jobs.schedules if s.job_name == "refresh_all")
        from datetime import datetime, timezone

        at = datetime(2026, 1, 1, 2, tzinfo=timezone.utc)
        with dg.build_schedule_context(resources={"fuzer": fuzer}, scheduled_execution_time=at) as ctx:
            runs = schedule.evaluate_tick(ctx).run_requests
        assert sorted(r.tags[jobs.PROJECT_TAG] for r in runs) == ["kestrel", "okan"]

        sensed = jobs.new_exports(dg.build_sensor_context(resources={"fuzer": fuzer}))
        assert [r.tags[jobs.PROJECT_TAG] for r in sensed.run_requests] == ["kestrel"]  # only Kestrel has exports
        again = jobs.new_exports(dg.build_sensor_context(cursor=sensed.cursor, resources={"fuzer": fuzer}))
        assert isinstance(again, dg.SkipReason)

        result = dg.materialize([*jobs.SOURCES, jobs.register], resources={"fuzer": fuzer},
                                tags=jobs.project_tags("kestrel"))
        assert result.success
        meta = {e.asset_key.path[-1]: e.materialization.metadata for e in result.get_asset_materialization_events()}
        assert meta["register"]["entities"].value == 34
        with db.connect(url=url, schema="public") as conn:
            assert conn.execute("select count(*) as n from entity").fetchone()["n"] == 0  # Okan's is untouched
            assert conn.execute("select count(*) as n from proj_kestrel.entity").fetchone()["n"] >= 34
    finally:
        db.set_default(before)
        _drop("dagster_projects")


def test_an_unknown_project_scope_fails(serving):
    url = _fresh("no_such_project")
    serving(url)
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            with pytest.raises(projects.ProjectError):
                projects.use(conn, "nowhere")
        with pytest.raises(ValueError):
            db.search_path("bad; drop schema public")
    finally:
        _drop("no_such_project")
