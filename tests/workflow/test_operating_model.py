"""P8.1 (#810): the operating model as a package. Okan runs the built-in standard with nothing different; a package
exported from it reads back the same; a local change shows as a deviation until the standard is applied again."""

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
