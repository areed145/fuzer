"""P1.5: the source pack contract and the conformance suite every pack must pass, on both bundles."""

import re
from pathlib import Path

import pytest
import yaml

from fuzer.core import bundles, db, detectors, settings
from fuzer.core import pack as packs_
from fuzer.sources import packs, providers

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ROOT / "fakeplant" / "bundles"
MEASUREMENT = SHIPPED / "kestrel" / "packs" / "measurement.yaml"


def _fresh(name: str) -> str:
    from psycopg.conninfo import make_conninfo

    with db.connect(autocommit=True, url=db.database_url()) as a:
        a.execute(f'drop database if exists "{name}" with (force)')
        a.execute(f'create database "{name}"')
    return make_conninfo(db.database_url(), dbname=name)


@pytest.fixture
def project(monkeypatch):
    """A fresh database initialised from a shipped bundle, served as a deployment serves it."""
    def make(bundle: str):
        url = _fresh(f"packs_{bundle}")
        monkeypatch.setenv("FUZER_DATABASE_URL", url)
        monkeypatch.delenv("FUZER_PROJECT", raising=False)
        monkeypatch.delenv("FUZER_DATA_DIR", raising=False)
        packs_.clear_cache()
        settings._cache.clear()
        settings._first.clear()
        conn = db.connect(url=url)
        db.init_schema(conn)
        bundles.init(conn, SHIPPED / bundle, by="test")
        return conn
    yield make
    packs_.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def _shipped_packs() -> list[Path]:
    return sorted(p for b in bundles.shipped() for p in (SHIPPED / b / "packs").glob("*.yaml"))


@pytest.mark.parametrize("bundle", ["okan", "kestrel"])
def test_every_shipped_pack_conforms_on_both_bundles(project, bundle):
    assert MEASUREMENT in _shipped_packs()
    with project(bundle) as conn:
        for path in _shipped_packs():
            report = packs.conformance(conn, path)
            assert report["problems"] == [], (path.name, report)
            if "detection_providers" in yaml.safe_load(path.read_text()):  # a push-only provider (P3.8): its alerts read
                assert report["alerts"] and providers.providers(conn, report["system"])  # both bundles' share a system
                continue
            assert report["loaded"]["records"] > 0 and report["loaded"]["loaded"]
            # one install put its datasets on Sources, its work types on Routing and its detectors on Detection
            assert {"datasets", "exception_types"} <= set(report["installed"])
            assert all(detectors.on(conn, d) for d in yaml.safe_load(path.read_text()).get("detectors") or [])


def test_installing_switches_its_detectors_on(project):
    with project("kestrel") as conn:
        detectors.switch(conn, "source_health", False, by="ana")
        assert not detectors.on(conn, "source_health")
        report = packs.install(conn, MEASUREMENT.read_text(), "test",
                               config={"export": {"path": str(SHIPPED / "kestrel" / "measurement")}})
        assert report["detectors"] == ["source_health"] and detectors.on(conn, "source_health")
        again = packs.install(conn, MEASUREMENT.read_text(), "test",
                              config={"export": {"path": str(SHIPPED / "kestrel" / "measurement")}})
        assert "detectors" not in again and "detector source_health" in again["unchanged"]


def _variant(tmp_path, **changes) -> Path:
    pack = yaml.safe_load(MEASUREMENT.read_text())
    for k, v in changes.items():
        if v is None:
            pack.pop(k, None)
        else:
            pack[k] = v
    path = tmp_path / "packs" / "variant.yaml"
    path.parent.mkdir(exist_ok=True)
    if pack.get("fixture"):
        pack["fixture"] = str(SHIPPED / "kestrel" / "measurement")
    path.write_text(yaml.safe_dump(pack))
    return path


@pytest.mark.parametrize("changes, problem", [
    ({"fixture": None}, "has no fixture"),
    ({"datasets": []}, "brings no datasets"),
    ({"detectors": ["nobody_wrote_this"]}, "detectors nobody registered: nobody_wrote_this"),
    ({"exception_rules": [{"name": "x", "dataset": "elsewhere", "type": "short_gas_day", "when": {}}]},
     "reads dataset 'elsewhere'"),
    ({"connections": [{"name": "export", "type": "folder", "config": {"path": "x"}}]},
     "doesn't declare its class (control, control_adjacent or business) and direction"),  # D4
    ({"connections": [{"name": "export", "type": "folder", "config": {"path": "x"}, "class": "control_adjacent",
                       "direction": "write"}]}, "can only be read"),
])
def test_a_pack_that_breaks_the_contract_is_named_and_writes_nothing(project, tmp_path, changes, problem):
    with project("kestrel") as conn:
        report = packs.conformance(conn, _variant(tmp_path, **changes))
        assert any(problem in p for p in report["problems"]), report["problems"]
        assert conn.execute("select count(*) as n from source_system where name = 'measurement'").fetchone()["n"] == 0


def test_a_connection_says_its_class_and_direction_and_never_writes_toward_control(project):
    """D4 (docs/ot-security.md): a connection's class defaults by its system's kind, its direction to read, and a
    control or control-adjacent connection can't be made to write."""
    from fuzer.sources import catalog
    from fuzer.sources._catalog_common import CatalogError

    with project("kestrel") as conn:
        catalog.add_system(conn, "plant_historian", "test", kind="historian", files=False)
        catalog.add_system(conn, "planner", "test", kind="planning", files=False)
        h = catalog.add_connection(conn, "plant_historian", "web", "folder", "test", {"path": str(SHIPPED)})
        p = catalog.add_connection(conn, "planner", "export", "folder", "test", {"path": str(SHIPPED)},
                                   direction="write")
        assert (h["class"], h["direction"]) == ("control_adjacent", "read")
        assert (p["class"], p["direction"]) == ("business", "write")
        with pytest.raises(CatalogError, match="can only be read"):
            catalog.update_connection(conn, h["id"], "test", direction="write")
        with pytest.raises(CatalogError, match="class is one of"):
            catalog.add_connection(conn, "planner", "odd", "folder", "test", {"path": str(SHIPPED)}, cls="dmz")
        exported = yaml.safe_load(catalog.export_pack(conn, "planner"))
        assert {(c["class"], c["direction"]) for c in exported["connections"]} == {("business", "write")}
        conn.rollback()


# ---- the built-in sources as packs (P1.5b) ------------------------------------------------------


def test_every_built_in_mapping_and_reader_belongs_to_a_pack():
    from fuzer.resources import MAPPINGS
    from fuzer.sources.ingest import READERS

    covered = set()
    for name in packs.builtin_names():
        for d in yaml.safe_load(packs.builtin(name))["datasets"]:
            covered |= {(m["source"], m["record_type"]) for m in d["mappings"]}
            assert d.get("reader") is None or d["name"] in READERS
    every = {(r["source"], r["record_type"]) for r in map(yaml.safe_load, map(Path.read_text, MAPPINGS.glob("*.yaml")))}
    assert every - covered == set()


ROADMAP = ["sap", "pi", "pi_af", "ec", "dcs", "lims", "inspection", "pid", "oplog", "iow", "gis", "maximo", "oem", "edms"]


@pytest.mark.parametrize("bundle", ["okan", "kestrel"])
def test_the_built_in_packs_conform_on_both_bundles(project, okan_data, bundle):
    exports = okan_data if bundle == "okan" else SHIPPED / "kestrel" / "exports"
    results = {}
    with project(bundle) as conn:
        for name in packs.builtin_names():
            results[name] = packs.conformance(conn, name, fixture=exports)
    failing = {n: r["problems"] for n, r in results.items() if r["problems"]}
    assert not {n: p for n, p in failing.items() if n in ROADMAP}, failing
    for name in ROADMAP:
        assert results[name]["loaded"]["records"] > 0, (name, results[name])


# ---- the walkthrough in docs/extend.md (P1.5d): the pack it writes is built from the page and must conform ----

DOC = ROOT / "docs" / "extend.md"


def _files(text: str) -> dict[str, str]:
    """Every fenced block with a title under the walkthrough's heading: its path and its contents."""
    section = text.split("## Write a source pack", 1)[1].split("\n## ", 1)[0]
    return {m[1]: m[2] for m in re.finditer(r'```\w+ title="([^"]+)"\n(.*?)```', section, re.DOTALL)}


def test_the_walkthrough_writes_a_pack_and_its_fixture():
    files = _files(DOC.read_text())
    assert set(files) == {"lab/pack.yaml", "lab/samples/water_cut_samples.csv"}


@pytest.mark.parametrize("bundle", ["kestrel", "okan"])
def test_the_walkthrough_pack_conforms(project, tmp_path, bundle):
    for path, body in _files(DOC.read_text()).items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(body)
    with project(bundle) as conn:
        report = packs.conformance(conn, tmp_path / "lab" / "pack.yaml")
        assert report["problems"] == [], report
        assert report["loaded"]["records"] == 4
        assert report["installed"]["exception_types"] == ["water_cut_jump"]
        from fuzer.sources import workflows

        ran = workflows.evaluate(conn, ["water_cut_samples"])["lab/high_water_cut"]
        assert ran["raised"] + ran["refreshed"] == 1, ran  # LS-003, raised when its fixture loaded
