"""P1.5: the source pack contract and the conformance suite every pack must pass, on both bundles."""

from pathlib import Path

import pytest
import yaml

from fuzer.core import bundles, db, detectors, settings
from fuzer.core import pack as packs_
from fuzer.sources import packs

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
])
def test_a_pack_that_breaks_the_contract_is_named_and_writes_nothing(project, tmp_path, changes, problem):
    with project("kestrel") as conn:
        report = packs.conformance(conn, _variant(tmp_path, **changes))
        assert any(problem in p for p in report["problems"]), report["problems"]
        assert conn.execute("select count(*) as n from source_system where name = 'measurement'").fetchone()["n"] == 0


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
