"""The source catalog (#225): seeding from the built-in files without overwriting what people change, loads
driven by each dataset's row (status, load mode, drift policy) with run history, mapping versions with
approval and rollback, retiring, export / import between databases, connections and the audit log."""

import csv
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from fuzer.core import db, settings
from fuzer.sources import catalog, connections, ingest, mappings


@pytest.fixture
def data(okan_data):
    d = Path(tempfile.mkdtemp(prefix="fzcat"))
    shutil.copytree(okan_data, d, dirs_exist_ok=True)
    return d


@pytest.fixture
def conn():
    with db.connect() as c:
        db.reset(c)
        settings._cache.clear()
        try:
            yield c
        finally:
            c.rollback()
            db.reset(c)
            settings._cache.clear()


def _drop_row(path: Path, column: str, value: str) -> None:
    rows = list(csv.DictReader(path.open()))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(r for r in rows if r[column] != value)


def test_seed_imports_every_builtin_mapping_once(conn):
    first = catalog.seed(conn)
    files = catalog.seed_files()
    assert len(first["mappings"]) == len(files)
    assert {"pi_af", "ec_network", "pid_sheets", "documents"} <= set(first["datasets"])
    assert catalog.seed(conn) == {}  # idempotent
    m = mappings.load(conn)
    assert set(m) == {(raw["source"], raw["record_type"]) for raw, _ in files}
    assert all(x.version for x in m.values())  # each from a mapping_version row
    eq = catalog.dataset(conn, "sap_equipment")
    assert eq["source"] == "sap" and eq["key"] == ["equnr"] and eq["connection_type"] == "folder"
    assert catalog.dataset(conn, "oplogs")["load_mode"] == "append"  # watermarked
    assert all(r["by"] == "seed" for r in catalog.audit(conn, limit=500))


def test_dataset_mapping_versions_match_api_and_cli(conn):
    catalog.seed(conn)

    from fuzer.api.catalog import dataset_detail
    from fuzer.cli.sources import show

    api_versions = dataset_detail("documents", conn)["mappings"]
    cli_versions = show(conn, "documents")["mappings"]

    assert [v["id"] for v in api_versions] == [v["id"] for v in cli_versions]
    assert [v["id"] for v in api_versions] == [
        v["id"] for v in catalog.versions_for_dataset(conn, catalog.dataset(conn, "documents"))
    ]


def test_seed_leaves_a_persons_changes_alone(conn, monkeypatch):
    catalog.seed(conn)
    text = next(t for r, t in catalog.seed_files() if r["record_type"] == "equipment" and r["source"] == "sap")
    edited = catalog.propose(conn, text.replace("fields: [description,", "fields: [description, weight,")
                             if "fields: [description," in text else text + "\n# edited\n", "ana", status="approved")
    catalog.update_dataset(conn, "sap_equipment", "ana", "their key", key=["equnr", "serial"])

    changed = text + "\n# the built-in moved on\n"
    real = catalog.seed_files
    monkeypatch.setattr(catalog, "seed_files", lambda: [(r, changed if r["source"] == "sap" and r["record_type"] ==
                                                         "equipment" else t) for r, t in real()])
    out = catalog.seed(conn)
    assert out["ahead"] == ["sap/equipment"] and out["drafts"] == ["sap/equipment"]
    assert catalog.current_mappings(conn) and mappings.load(conn)[("sap", "equipment")].version == edited["id"]
    draft = catalog.versions(conn, "sap", "equipment")[0]
    assert draft["status"] == "draft" and draft["author"] == "seed" and draft["parent_id"] == edited["id"]
    assert catalog.dataset(conn, "sap_equipment")["key"] == ["equnr", "serial"]  # their key stays
    assert catalog.seed(conn) == {"ahead": ["sap/equipment"]}  # said again, no second draft

    monkeypatch.setattr(catalog, "seed_files", lambda: [(r, t + "\n# new\n" if r["source"] == "sap" and r["record_type"]
                                                         == "floc" else t) for r, t in real()])
    assert catalog.seed(conn)["updated"] == ["sap/floc"]  # nobody edited it: it follows the built-in


def test_loads_follow_the_dataset_rows(conn, data):
    first = ingest.ingest(conn, data)
    run = catalog.runs(conn, "sap_equipment")[0]
    assert run["status"] == "done" and run["inserted"] == run["rows_read"] > 0 and run["mappings"]["sap/equipment"]
    assert catalog.dataset(conn, "sap_equipment")["last_run_id"] == run["id"]

    catalog.pause(conn, "sap_equipment", "ana")
    again = ingest.ingest(conn, data, full=True)
    assert "sap_equipment" in again["paused"] and "sap_equipment" not in again["loaded"]
    catalog.resume(conn, "sap_equipment", "ana")

    # incremental: a row gone from the export stays
    serial = next(csv.DictReader((data / "sap_equipment.csv").open()))["equnr"]
    _drop_row(data / "sap_equipment.csv", "equnr", serial)
    catalog.update_dataset(conn, "sap_equipment", "ana", load_mode="incremental")
    assert ingest.ingest(conn, data, ["sap_equipment"])["removed"] == 0
    catalog.update_dataset(conn, "sap_equipment", "ana", load_mode="snapshot")
    stats = ingest.ingest(conn, data, ["sap_equipment"], full=True)
    assert stats["removed"] == 1 and catalog.runs(conn, "sap_equipment")[0]["removed"] == 1
    assert len(catalog.runs(conn, "sap_equipment")) == 3 and first["records"]

    # drift policy: an added column loads under hold_breaking, holds under hold_any
    rows = list(csv.DictReader((data / "sap_floc.csv").open()))
    with (data / "sap_floc.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[*rows[0].keys(), "extra"])
        w.writeheader()
        w.writerows({**r, "extra": "x"} for r in rows)
    catalog.update_dataset(conn, "sap_floc", "ana", drift_policy="hold_any")
    held = ingest.ingest(conn, data, ["sap_floc"])
    assert held["held"] == ["sap_floc"] and catalog.runs(conn, "sap_floc")[0]["status"] == "held"
    catalog.update_dataset(conn, "sap_floc", "ana", drift_policy="hold_breaking")
    assert ingest.ingest(conn, data, ["sap_floc"])["loaded"] == ["sap_floc"]


def test_mapping_versions_approve_and_roll_back(conn, data):
    ingest.ingest(conn, data, ["sap_equipment"])
    first = mappings.load(conn)[("sap", "equipment")].version
    text = catalog.versions(conn, "sap", "equipment")[0]["yaml"]
    draft = catalog.propose(conn, text.replace("- serial\n", "- serial\n- weight\n") if "- serial\n" in text
                            else text + "\n", "ana", note="add weight")
    assert draft["status"] == "draft" and mappings.load(conn)[("sap", "equipment")].version == first
    catalog.approve(conn, draft["id"], "ben")
    assert mappings.load(conn)[("sap", "equipment")].version == draft["id"]
    back = catalog.rollback(conn, first, "ben", "weight was wrong")
    assert back["parent_id"] == first and mappings.load(conn)[("sap", "equipment")].version == back["id"]
    acts = [a["action"] for a in catalog.audit(conn, "mapping", limit=5)]
    assert acts[:3] == ["rollback", "approve", "approve"]
    with pytest.raises(catalog.CatalogError, match="not valid"):
        catalog.propose(conn, "source: sap\n", "ana")
    spare = catalog.propose(conn, text + "\n# spare\n", "ana")
    catalog.discard(conn, spare["id"], "ana")
    assert catalog.versions(conn, "sap", "equipment")[0]["status"] == "retired"
    with pytest.raises(catalog.CatalogError, match="isn't a draft"):
        catalog.discard(conn, spare["id"], "ana")


def test_retire_keeps_or_retracts(conn, data):
    ingest.ingest(conn, data)
    count = "select count(*) as n from source_record where source = 'maximo' and record_type = 'asset'"
    n = conn.execute(count).fetchone()["n"]
    assert catalog.retire_preview(conn, "maximo_assets")["records"] == n > 0
    catalog.retire(conn, "maximo_assets", "ana", retract=False)
    assert conn.execute(count).fetchone()["n"] == n  # kept as history
    assert "maximo_assets" not in ingest.export_names(conn)
    out = catalog.retire(conn, "maximo_assets", "ana", retract=True)
    assert out["retracted"] and conn.execute(count).fetchone()["n"] == 0
    assert ("maximo", "asset") not in mappings.load(conn)


def test_export_and_import_move_config_between_databases(conn, data, tmp_path):
    catalog.seed(conn)
    catalog.update_dataset(conn, "sap_floc", "ana", schedule="0 * * * *", owner="ana", expected_hours=24)
    catalog.update_system(conn, "sap", "ana", owner="SAP team", timezone="Europe/Oslo")
    files = catalog.export(conn)
    assert "# " in files["sap_equipment.yaml"]  # unchanged: written as the built-in was, comments and all
    floc = yaml.safe_load(files["sap_floc.yaml"])
    assert floc["catalog"]["schedule"] == "0 * * * *" and floc["catalog"]["owner"] == "ana"

    db.reset(conn)
    catalog.seed(conn)
    catalog.import_(conn, files["systems.yaml"], "ben")
    assert catalog.system(conn, "sap")["owner"] == "SAP team"
    v = catalog.import_(conn, files["sap_floc.yaml"], "ben")
    assert catalog.dataset(conn, "sap_floc")["schedule"] is None  # a draft changes nothing yet
    catalog.approve(conn, v["draft"], "ben")
    d = catalog.dataset(conn, "sap_floc")
    assert d["schedule"] == "0 * * * *" and d["expected_hours"] == 24


def test_connections_check_test_and_discover(conn, data, monkeypatch):
    catalog.seed(conn)
    with pytest.raises(catalog.CatalogError, match="reference"):
        catalog.add_connection(conn, "pi", "prod", "pi_web_api", "ana", {"url": "https://pi"}, {"password": "hunter2"})
    with pytest.raises(catalog.CatalogError, match="needs"):
        catalog.add_connection(conn, "pi", "prod", "pi_web_api", "ana", {}, {})
    c = catalog.add_connection(conn, "pi", "prod", "pi_web_api", "ana", {"url": "https://pi"}, {"password": "env:PI_PW"})
    monkeypatch.delenv("PI_PW", raising=False)
    assert connections.resolves(c["secrets"]) == {"password": False}
    monkeypatch.setenv("PI_PW", "x")
    assert connections.resolves(c["secrets"]) == {"password": True}

    files = next(x for x in catalog.connections(conn, "sap") if x["type"] == "folder")
    t = connections.test(conn, files["id"], data)
    assert t["ok"] and [s["step"] for s in t["steps"]] == ["reach", "list", "read"]
    assert catalog.connection(conn, files["id"])["last_test"]["ok"]
    (data / "vendor_pumps.csv").write_text("pump_id,make\nP1,Acme\n")
    assert "vendor_pumps" in connections.discover(conn, files["id"], data)["uncovered"]
    bad = connections.test(conn, files["id"], data / "nowhere")
    assert not bad["ok"] and bad["steps"][0]["step"] == "reach" and len(bad["steps"]) == 1


@pytest.mark.integration
def test_old_pi_settings_become_a_connection(conn, monkeypatch):
    catalog.seed(conn)
    monkeypatch.setenv("FUZER_PI_WEB_API_URL", "http://pi.example/piwebapi")
    from fuzer.core import settings

    settings._cache.clear()
    catalog.ensure(conn)
    pi = [c for c in catalog.connections(conn, "pi") if c["type"] == "pi_web_api"]
    assert len(pi) == 1 and pi[0]["config"]["url"] == "http://pi.example/piwebapi"
    assert catalog.dataset(conn, "pi_points")["connection_type"] == "pi_web_api"
    assert catalog.dataset(conn, "pi_af")["connection_type"] == "pi_web_api"
    catalog.ensure(conn)
    assert len([c for c in catalog.connections(conn, "pi") if c["type"] == "pi_web_api"]) == 1  # once
    settings._cache.clear()
