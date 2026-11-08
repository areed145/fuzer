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
    assert b["config"]["data"] == str(KESTREL / "data")
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
    with db.connect(autocommit=True, url=db.database_url()) as a:
        a.execute(f'drop database if exists "{name}" with (force)')


@pytest.fixture(scope="module")
def kestrel_url():
    url = _fresh("kestrel_bundle")
    yield url
    _drop("kestrel_bundle")


@pytest.fixture
def serving(monkeypatch):
    """Point this process at a database, as a deployment does, with no pack or project in the environment."""
    def to(url: str):
        monkeypatch.setenv("FUZER_DATABASE_URL", url)
        monkeypatch.delenv("FUZER_PACK", raising=False)
        monkeypatch.delenv("FUZER_PROJECT", raising=False)
        packs.clear_cache()
        settings._cache.clear()
    yield to
    packs.clear_cache()
    settings._cache.clear()


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

        # Another facility's bundle is refused: a database holds one facility.
        with pytest.raises(bundles.BundleError, match="holds project 'kestrel'"):
            bundles.init(conn, OKAN, by="test")

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
