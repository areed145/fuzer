"""A second facility from its own seed bundle (#376): Kestrel Ridge boots in a fresh database with nothing
from Okan, and the pack follows the database a process connects to, not an import-time global."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.core import bundles, db, projects, settings
from fuzer.core import pack as packs
from fuzer.core.findings import external
from fuzer.core.tags import TagGrammar
from fuzer.sources.relative_dates import plant_today

ROOT = Path(__file__).resolve().parents[2]
KESTREL = ROOT / "fakeplant" / "bundles" / "kestrel"
KESTREL_DATASETS = {
    "sap_floc", "sap_equipment", "pi_points", "maximo_assets", "pi_af",
    "sap_notifications", "sap_work_orders", "sap_maintenance_plans", "sap_bom", "sap_materials", "sap_purchase_orders", "sap_work_centers", "ptw_permits", "ptw_isolations", "rounds_points", "teams_threads",
    "safeguard_register", "safeguard_bypasses", "safeguard_demands", "moc_changes",
    "oplogs", "ec_downtime", "dcs_alarms", "dcs_alarm_journal", "iow_register", "inspection_readings", "lims_results",
    "ec_well_tests", "ec_network", "oem_curves", "pid_sheets", "documents",
    "gis_facilities", "gis_pipelines", "gis_wells"}
KESTREL_ENTITIES = 416  # with 14 closed permits (P6.5), Operator rounds points, 4 Teams threads (P5.8),
# 5 safeguards and 3 bypasses (P3.9a), 3 change records (P3.9b) and 4 safeguard demands (P4.2e)
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


def test_every_facility_is_a_shipped_bundle_and_fuzer_packages_none():
    """P1.6a: Okan's pack lives in its bundle like Kestrel's; src/fuzer holds no packs and no project files."""
    from fuzer.resources import PACKAGE

    assert not (PACKAGE / "packs").exists() and not (ROOT / "projects").exists()
    assert {"okan", "kestrel"} <= set(bundles.shipped()) == set(projects.repo_names())
    for name, facility in (("okan", "OKAN"), ("kestrel", "KRGP")):
        assert packs.load_pack(name).facility == facility
        cfg = projects.config(None, name)  # no database row: the shipped bundle's project
        assert cfg["source"] == "repository" and cfg["pack_config"]["facility"] == facility
        assert packs.for_project(cfg).facility == facility
        assert Path(cfg["data"]).is_absolute()
    assert projects.config(None, "okan")["data"] == str(ROOT / "fakeplant" / "data" / "okan")
    with pytest.raises(LookupError, match="no seed bundle"):
        packs.load_pack("nowhere")
    with pytest.raises(projects.ProjectError):
        projects.config(None, "nowhere")


def test_nothing_facility_specific_remains_under_src_fuzer():
    """P1.6b: no shipped bundle's name or facility code appears in Fuzer's own code, docs or data; what differs
    between facilities comes from their bundles."""
    from fuzer.resources import PACKAGE

    words = set()
    for name in bundles.shipped():
        b = bundles.read(bundles.find(name))
        words |= {name.lower(), b["pack"].facility.lower()}
    assert {"okan", "kestrel", "krgp"} <= words
    pattern = re.compile(r"\b(" + "|".join(sorted(map(re.escape, words))) + r")\b", re.IGNORECASE)
    hits = []
    for path in sorted(PACKAGE.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".yaml", ".yml", ".md", ".sql", ".json", ".txt"}:
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(PACKAGE)}:{n}: {line.strip()}")
    assert not hits, "facility names under src/fuzer:\n" + "\n".join(hits)


def test_a_missing_bundle_fails(tmp_path):
    with pytest.raises(bundles.BundleError, match="no bundle.yaml"):
        bundles.read(tmp_path)


def test_kestrels_sources_name_only_tags_its_register_holds():
    """Every tag a Kestrel drawing, document, work record, IOW, lab or inspection reading names has a SAP functional
    location, so the sources agree on one plant and nothing the IOC is sent to is missing from the register."""
    import csv
    import json

    x = KESTREL / "exports"
    pack = bundles.read(KESTREL)["pack"]
    flocs = {pack.tags.parse(r["floc"]) for r in csv.DictReader((x / "sap_floc.csv").open())} - {None}
    from fuzer.sources import ingest

    sheets = [s for _, _, _, s in ingest._pid_sheets(x / "pid_sheets.json")]  # a DEXPI sheet read from its file
    named = set()
    for s in sheets:
        named |= set(s["tags"]) | {c[e] for c in s["connections"] for e in ("from", "to")}
    named |= {t for d in json.loads((x / "documents.json").read_text()) for t in d.get("tags", [])}
    for name, col in [("sap_notifications", "floc"), ("sap_work_orders", "floc"), ("sap_maintenance_plans", "floc"),
                      ("sap_equipment", "floc"), ("iow_register", "tag"), ("lims_results", "tag"),
                      ("inspection_readings", "tag")]:
        named |= {r[col] for r in csv.DictReader((x / f"{name}.csv").open()) if r[col]}
    assert {pack.tags.parse(t) for t in named} <= flocs
    on_drawings = {pack.tags.parse(t) for s in sheets for t in s["tags"]}
    assert flocs <= on_drawings  # and every tag in the register is on a P&ID


def test_kestrels_drawings_match_its_p_and_id_sheets(tmp_path):
    """The committed drawings are what `fakeplant drawings` draws from pid_sheets.json today: one per sheet, with
    every tag placed. Edit a sheet without redrawing and this fails."""
    import json
    import shutil

    from fakeplant import drawings
    from fuzer.sources import ingest

    committed = json.loads((KESTREL / "exports" / "pid_sheets.json").read_text())
    for s in ingest._pid_sheets(KESTREL / "exports" / "pid_sheets.json"):  # a DEXPI sheet's positions are in its file
        assert (KESTREL / "exports" / s[3]["image"]).is_file()
        assert set(s[3]["positions"]) == set(s[3]["tags"])
    copy = tmp_path / "kestrel"
    shutil.copytree(KESTREL, copy, ignore=shutil.ignore_patterns("*.png"))
    drawings.render(copy)
    assert json.loads((copy / "exports" / "pid_sheets.json").read_text()) == committed
    for s in committed:
        if s.get("dexpi"):
            assert (copy / "exports" / s["dexpi"]).read_text() == (KESTREL / "exports" / s["dexpi"]).read_text()


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
        assert set(loaded["loaded"]) == KESTREL_DATASETS and not loaded["held"]  # each through a built-in mapping
        built = pipeline.rebuild(conn, pack)
        assert built["entities"] > 0 and built["incidents"] >= 1

        kinds = {r["kind"]: r["n"] for r in conn.execute(
            "select kind, count(*) as n from entity where merged_into is null and retired_at is null group by kind")}
        assert kinds["facility"] == 1 and kinds["area"] == 8 and kinds["tag"] == 82 and kinds["well"] == 2
        # Maximo's eight assets merge into SAP's by serial, despite its formats. Of the five planted without one
        # (P2.2), one merges on maker and description; the amine reboiler would too, but it is A-criticality, so the
        # human gate (P5.10) asks a person; the glycol pump's driver would merge wrongly into the pump at the pack's
        # 0.90, and the gate holds it short of 99%; the stabilizer reboiler waits for a person and the pump's motor
        # stays its own item.
        assert kinds["equipment"] == 55
        # Its own work, drawings, map and documents: what the IOC triages and the field operator is sent with.
        assert kinds["notification"] == 12 and kinds["work_order"] == 21 and kinds["log_entry"] == 24
        assert kinds["pipeline"] == 2 and kinds["document"] >= 17 and kinds["well_test"] == 24
        keys = {r["kind"]: set() for r in conn.execute("select distinct kind from entity")}
        for r in conn.execute("select e.kind, k.key from entity e join entity_key k using (fuzer_id) "
                              "where e.merged_into is null"):
            keys[r["kind"]].add(r["key"])
        assert keys["facility"] == {"KRGP"}
        assert keys["area"] == {f"KRGP-{a}1" for a in "ABCDEFGH"}
        assert {k for k in keys["tag"] if re.fullmatch(r"[A-H]\d-[A-Z]+-\d{4}[A-Z]?", k)} >= {
            "A1-V-1001", "B1-K-2001", "C1-P-3002A", "C1-FT-3005", "C1-H-3008", "D1-V-4001", "E1-K-5004",
            "F1-V-6002", "G1-G-7006", "H1-FT-8010"}
        assert keys["well"] == {"KR 12-03H", "KR 12-04H"}
        # Every P&ID shows as a drawing with each of its tags marked and linked (smart sheets: no model call).
        from fuzer.graph.queries.documents import drawing

        sheets = [r["fuzer_id"] for r in conn.execute(
            "select fuzer_id from crosswalk where source = 'pid' and record_type = 'sheet'")]
        assert len(sheets) == 10
        for sheet in sheets:
            g = drawing(conn, sheet)
            assert g and g["read_by"] == "smart file" and g["marks"]
            assert all(m.get("id") for m in g["marks"]), [m["key"] for m in g["marks"] if not m.get("id")]
        assert not any("OK" in k for ks in keys.values() for k in ks)
        wells_with_series = conn.execute(
            "select count(distinct src) as n from edge where rel = 'has_timeseries' and src in "
            "(select fuzer_id from entity where kind = 'well')").fetchone()["n"]
        assert wells_with_series == 2
        # Its records are written relative to today, so the compressor trip is yesterday's, and the notification
        # it raised reaches the IOC's queue (#456).
        yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()  # a date alone: the server's day
        trip = conn.execute("select g.value #>> '{}' as v from golden g join entity_key k using (fuzer_id) "
                            "where k.kind = 'notification' and k.key = '10004001' and g.field = 'created'").fetchone()
        assert trip["v"] == yesterday
        assert pipeline.follow_register(conn, pack)["notifications"]["raised"] == 4
        assert conn.execute("select count(*) as n from exception_record where type = 'sap_notification' "
                            "and key = '10004001' and state = 'open'").fetchone()["n"] == 1

        # The trip reaches the IOC three ways, and is one case (#464): the IOW check on the compressor's suction
        # pressure opens a case that the waiting notification joins, and the SRE's upset joins it too. The other
        # three notifications (the glycol pump, the glycol contactor, the amine contactor's foaming) are other events.
        from fuzer.core.exceptions import raise_exception
        from fuzer.ops import exceptions as ex
        from fuzer.ops import sre_handoff  # noqa: F401  (declares the upset type)
        from fuzer.workflow import engine

        engine.load_defaults(conn)
        ids = {r["key"]: r["fuzer_id"] for r in conn.execute(
            "select key, fuzer_id from entity_key where kind = 'tag' and key in ('B1-K-2001', 'B1-PT-2001')")}
        point = conn.execute("select dst from edge where rel = 'has_timeseries' and src = %s",
                             (ids["B1-PT-2001"],)).fetchone()["dst"]
        iow = raise_exception(conn, "iow_breach", "iow:B1-PT-2001", "Suction pressure IOW breach", "agent:sre",
                              source="limits", refs=[point, ids["B1-PT-2001"]], value_usd=25000,
                              finding=external("test", {"iow": "IOW-B1-01", "point": "KRGP:B1.PT2001.PV", "level": "critical",
                                        "side": "lo", "limit": 18.0, "breaches": 1, "hours_outside": 2.5}))
        upset = raise_exception(conn, "upset", "upset:test", "Step change on B1-PT-2001 across 6 assets", "agent:sre",
                                source="sre", refs=[ids["B1-K-2001"], point])
        conn.commit()
        rows = ex.listing(conn)
        [trip] = [r for r in rows if r["id"] == iow["id"]]
        assert sorted((j["type"], j["key"]) for j in trip["joined"]) == [("sap_notification", "10004001"),
                                                                          ("upset", "upset:test")]
        assert upset["item_id"] == iow["item_id"]
        others = {r["key"] for r in rows if r["type"] == "sap_notification"}
        assert others == {"10004002", "10004007", "10004008"}  # still waiting on their own rows


    # The API resolves the pack from the database it serves, at request time.
    app = FastAPI()
    app.include_router(home.router)
    app.include_router(investigation.router)
    client = TestClient(app)
    stats = client.get("/stats").json()
    assert (stats["pack"], stats["facility"]) == ("kestrel", "KRGP")
    assert stats["entities"]["tag"] == 82
    assert client.get("/demo/okan/investigation").status_code >= 400  # the Okan journey isn't Kestrel's
    assert client.get("/demo/investigation").status_code == 404  # and Kestrel's bundle defines none


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

    deps.rebuilder.wait()  # an earlier test's rebuild on the default queue finishes first, so it can't land here
    okan_before = deps.rebuilder.status()["last"]
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
                    assert pipeline.rebuild(k, pack)["entities"] == KESTREL_ENTITIES
                    assert k.execute("select display_name from access_account").fetchone()["display_name"] == "Ana"
                # Kestrel's own settings, whatever the process's environment says (one deployment, two projects).
                from fuzer.sources import timeseries

                assert projects.field("data") == added["data"] and projects.field("timezone") == "America/Denver"
                # Its own historian: Kestrel's process model, from its own data folder, not Okan's.
                provider = timeseries.provider()
                assert provider.name == "synthetic" and provider.model and provider.model.covers("KRGP:B1.PT2001.PV")
                assert provider.model.climate["mean"] == 10.0  # inland Colorado, not Okan's offshore tropics
                # Its historian has values, and they move together: yesterday's compressor trip (placed relative to
                # today, #456) drops the compressor's discharge pressure from about 66 barg to its settle-out pressure.
                from fuzer.analysis import values

                with db.connect(url=url) as k:
                    k2001 = k.execute("select fuzer_id from entity_key where key = 'B1-K-2001'").fetchone()["fuzer_id"]
                    got = values.around(k, k2001, f"{plant_today() - timedelta(days=1)}T04:30", 12, 12, pack=pack)
                pd = {s["point"]: s for s in got["series"]}["KRGP:B1.PT2001.PV"]
                assert pd.get("modelled") and pd["max"] > 60 and pd["min"] < 35
                assert all(s["values"] for s in got["series"])
                # The API's rebuild queue runs Kestrel's rebuild on its own thread, in Kestrel's schema.
                deps.rebuilder.request()
                deps.rebuilder.wait()
                assert deps.rebuilder.last["stats"]["entities"] == KESTREL_ENTITIES, deps.rebuilder.last
            assert deps.rebuilder.status()["last"] == okan_before  # Okan's queue didn't run
            assert projects.field("data") == "/srv/okan" and projects.field("timeseries") == "synthetic"

            # Back in the first project: none of Kestrel's records or entities, and Okan's pack.
            assert conn.execute("select count(*) as n from source_record").fetchone()["n"] == 0
            assert conn.execute("select count(*) as n from entity").fetchone()["n"] == 0
            assert packs.current_pack(conn).facility == "OKAN" and deps.pack().facility == "OKAN"
            n = conn.execute("select count(*) as n from proj_kestrel.entity").fetchone()["n"]
            assert n >= KESTREL_ENTITIES  # Kestrel's register is where it was built
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
        from datetime import datetime

        at = datetime(2026, 1, 1, 2, tzinfo=UTC)
        with dg.instance_for_test() as instance:
            with dg.build_schedule_context(instance=instance, resources={"fuzer": fuzer},
                                           scheduled_execution_time=at) as ctx:
                runs = schedule.evaluate_tick(ctx).run_requests
            assert sorted(r.tags[jobs.PROJECT_TAG] for r in runs) == ["kestrel", "okan"]
            # A project whose previous run is still going skips the tick rather than queue another behind it.
            instance.add_run(dg.DagsterRun(job_name="refresh_all", run_id="still-running",
                                           status=dg.DagsterRunStatus.STARTED, tags=jobs.project_tags("kestrel")))
            with dg.build_schedule_context(instance=instance, resources={"fuzer": fuzer},
                                           scheduled_execution_time=at) as ctx:
                runs = schedule.evaluate_tick(ctx).run_requests
            assert [r.tags[jobs.PROJECT_TAG] for r in runs] == ["okan"]

        sensed = jobs.new_exports(dg.build_sensor_context(resources={"fuzer": fuzer}))
        assert [r.tags[jobs.PROJECT_TAG] for r in sensed.run_requests] == ["kestrel"]  # only Kestrel has exports
        again = jobs.new_exports(dg.build_sensor_context(cursor=sensed.cursor, resources={"fuzer": fuzer}))
        assert isinstance(again, dg.SkipReason)

        result = dg.materialize([*jobs.SOURCES, jobs.register], resources={"fuzer": fuzer},
                                tags=jobs.project_tags("kestrel"))
        assert result.success
        meta = {e.asset_key.path[-1]: e.materialization.metadata for e in result.get_asset_materialization_events()}
        assert meta["register"]["entities"].value == KESTREL_ENTITIES
        with db.connect(url=url, schema="public") as conn:
            assert conn.execute("select count(*) as n from entity").fetchone()["n"] == 0  # Okan's is untouched
            assert conn.execute("select count(*) as n from proj_kestrel.entity").fetchone()["n"] >= KESTREL_ENTITIES
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
