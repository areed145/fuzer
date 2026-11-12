"""P8.1 (#810): the operating model as a package. Okan runs the built-in standard with nothing different; a package
exported from it reads back the same; a local change shows as a deviation until the standard is applied again."""

import shutil
from pathlib import Path

import pytest
import yaml

from fuzer.workflow import engine
from fuzer.workflow import operating_model as om

pytestmark = pytest.mark.sequence


@pytest.fixture
def conn(okan_db):
    engine.load_defaults(okan_db.conn)
    okan_db.conn.commit()
    return okan_db.conn


def test_okan_runs_the_standard_with_nothing_different(conn):
    std = om.standard()
    assert std["name"] == "standard" and std["version"] == om.STANDARD_VERSION
    assert {"upset", "queue_item", "task"} <= set(std["processes"]) and "engineer" in std["roles"]
    assert om.deviations(conn, "standard") == []
    assert om.digest(om.snapshot(conn)) == om.digest(std)


def test_an_exported_package_reads_back_the_same(conn, tmp_path):
    out = om.export(conn, tmp_path / "pkg", "okan-local", 3, "Okan as it runs")
    assert out["version"] == 3 and out["counts"]["processes"] >= 3 and out["counts"]["roles"] >= 7
    pkg = om.load(tmp_path / "pkg")
    assert (pkg["name"], pkg["version"]) == ("okan-local", 3)
    assert om.digest(pkg) == out["digest"] == om.digest(om.snapshot(conn))
    assert om.deviations(conn, tmp_path / "pkg") == []


def test_a_local_change_is_a_deviation_until_the_standard_is_applied(conn):
    from fuzer.core import settings

    settings.set(conn, "signals.flat_hours", 5, by="ana", project_name=settings.project())
    conn.execute("update work_role set default_sla = 'PT2H' where name = 'operator'")
    conn.commit()
    rows = {(d["part"], d["key"]): d for d in om.deviations(conn, "standard")}
    assert set(rows) == {("settings", "signals.flat_hours"), ("roles", "operator")}
    assert rows[("settings", "signals.flat_hours")]["change"] == "added"
    assert rows[("roles", "operator")]["change"] == "changed"
    assert rows[("roles", "operator")]["local"]["default_sla"] == "PT2H"

    done = om.apply(conn, "standard", by="ana")
    assert done["changed"]["settings"] == 1 and done["changed"]["roles"] == 1 and done["deviations"] == 0
    assert om.deviations(conn, "standard") == []
    assert om.applied(conn)["name"] == "standard" and om.applied(conn)["digest"] == om.digest(om.standard())


def test_a_package_may_only_hold_operating_settings(tmp_path):
    (tmp_path / "model.yaml").write_text(yaml.safe_dump({"name": "x", "version": 1}))
    (tmp_path / "settings.yaml").write_text(yaml.safe_dump({"llm.model": "m"}))
    with pytest.raises(om.OperatingModelError, match="not an operating setting"):
        om.load(tmp_path)
    (tmp_path / "model.yaml").write_text(yaml.safe_dump({"name": "x"}))
    with pytest.raises(om.OperatingModelError, match="version"):
        om.load(tmp_path)


ROOT = Path(__file__).resolve().parents[2]


def _bundle(name: str, tmp_path: Path, okan_data: Path) -> Path:
    folder = ROOT / "fakeplant" / "bundles" / name
    if name == "okan":  # its exports are generated per test session (CI has no fakeplant/data/okan)
        folder = tmp_path / "okan"
        shutil.copytree(ROOT / "fakeplant" / "bundles" / "okan", folder)
        manifest = yaml.safe_load((folder / "bundle.yaml").read_text())
        manifest["project"]["data"] = str(okan_data)
        (folder / "bundle.yaml").write_text(yaml.safe_dump(manifest))
    return folder


@pytest.fixture
def fresh(monkeypatch):
    """A fresh database per bundle: initialised from it, with the shipped processes loaded and nothing else."""
    from psycopg.conninfo import make_conninfo

    from fuzer.core import bundles, db, settings
    from fuzer.core import pack as packs

    made, admin = [], make_conninfo(db.database_url(), dbname="postgres")

    def make(folder: Path):
        dbname = f"om_{folder.name}"
        with db.connect(autocommit=True, url=admin) as a:
            a.execute(f'drop database if exists "{dbname}" with (force)')
            a.execute(f'create database "{dbname}"')
        made.append(dbname)
        url = make_conninfo(db.database_url(), dbname=dbname)
        monkeypatch.setenv("FUZER_DATABASE_URL", url)
        monkeypatch.delenv("FUZER_PROJECT", raising=False)
        packs.clear_cache()
        settings._cache.clear()
        settings._first.clear()
        conn = db.connect(url=url)
        db.init_schema(conn)
        bundles.init(conn, folder, by="test")
        engine.load_defaults(conn)
        conn.commit()
        return conn

    yield make
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()
    with db.connect(autocommit=True, url=admin) as a:
        for dbname in made:
            a.execute(f'drop database if exists "{dbname}" with (force)')


@pytest.mark.parametrize("bundle, named", [
    ("okan", {}),
    ("kestrel", {("settings", "handover.shift_times"): "ops_lead"}),
])
def test_each_bundle_adopts_the_standard_with_its_deviations_named(bundle, named, fresh, tmp_path, okan_data):
    from fuzer.core import settings

    conn = fresh(_bundle(bundle, tmp_path, okan_data))
    before = om.conformance(conn)
    assert (before["model"], before["version"], before["applied"]) == ("standard", 1, False)

    done = om.adopt(conn, by="test")
    assert done["applied"] and done["unnamed"] == [] and done["stale"] == []
    assert done["deviations"] == done["named"] == len(named)
    rows = om.deviations(conn, om.conformance_package(conn))
    assert {(d["part"], d["key"]): d["owner"] for d in rows} == named
    assert all(d["reason"] for d in rows)
    if bundle == "kestrel":
        assert settings.get(conn, "handover.shift_times") == "07:00,19:00"

    # A local change nobody named shows as unnamed, until it is named; a name taken back makes it unnamed again.
    conn.execute("update work_role set default_sla = 'PT2H' where name = 'operator'")
    conn.commit()
    assert om.conformance(conn)["unnamed"] == [{"part": "roles", "key": "operator", "change": "changed"}]
    om.name(conn, "roles", "operator", "Two-hour response agreed with the control room", "ops_lead", by="ana")
    now = om.conformance(conn)
    assert now["unnamed"] == [] and now["named"] == len(named) + 1
    assert om.unname(conn, "roles", "operator")
    assert len(om.conformance(conn)["unnamed"]) == 1

    # Adopting again puts the standard back where nothing named it, and keeps the bundle's own.
    again = om.adopt(conn, by="test")
    assert again["deviations"] == len(named) and again["unnamed"] == []
    conn.close()


def test_a_name_needs_a_part_a_reason_and_an_owner(conn):
    with pytest.raises(om.OperatingModelError, match="no part"):
        om.name(conn, "budgets", "x", "r", "o", by="ana")
    with pytest.raises(om.OperatingModelError, match="a reason and an owner"):
        om.name(conn, "settings", "signals.flat_hours", " ", "ops_lead", by="ana")
