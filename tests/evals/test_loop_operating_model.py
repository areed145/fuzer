"""P8.1 (#810): an operating model exported from one database and applied in another runs the same there. Okan's
model is changed away from the standard (a role's SLA and escalation, a work setting), exported as a package, and
applied to a second copy of Okan that still runs the standard; the loop eval (P1.7) then goes the same way, step for
step, on both."""

import re

import pytest
from psycopg.conninfo import make_conninfo

from fuzer.evals import loop
from fuzer.workflow import engine
from fuzer.workflow import operating_model as om

pytestmark = pytest.mark.sequence

SECOND = "okan_om_second"


def _steps(res: dict) -> dict:
    """Each type's outcome per step, with run-specific numbers (ids, keys, times) taken out."""
    return {r["type"]: (r["failed"], r.get("arrived"), {s: re.sub(r"\d+", "#", str(v)) for s, v in r["steps"].items()})
            for r in res["types"]}


@pytest.fixture
def second(okan_db, monkeypatch):
    """Another copy of Okan, made from the same template as this module's, untouched by it: it runs the standard."""
    from fuzer.core import db, settings

    admin = make_conninfo(db.database_url(), dbname="postgres")
    with db.connect(autocommit=True, url=admin) as a:
        a.execute(f'drop database if exists "{SECOND}" with (force)')
        a.execute(f'create database "{SECOND}" template "tpl_okan"')
    url = make_conninfo(db.database_url(), dbname=SECOND)
    yield url
    monkeypatch.undo()
    settings._cache.clear()
    settings._first.clear()
    with db.connect(autocommit=True, url=admin) as a:
        a.execute(f'drop database if exists "{SECOND}" with (force)')


def test_a_package_moved_between_databases_runs_the_loop_the_same(okan_db, second, tmp_path, monkeypatch):
    from fuzer.core import db, settings

    conn = okan_db.conn
    engine.load_defaults(conn)
    conn.execute("update work_role set default_sla = 'PT2H', escalate_to = 'engineer' where name = 'data_steward'")
    settings.set(conn, "exceptions.join_hours", 24, by="ana", project_name=settings.project())
    conn.commit()
    assert {(d["part"], d["key"]) for d in om.deviations(conn, "standard")} == {
        ("roles", "data_steward"), ("settings", "exceptions.join_hours")}
    out = om.export(conn, tmp_path / "okan-v2", "okan-local", 2, "Okan with a faster data steward")
    here = loop.run(conn)
    assert not here["failed"], "\n".join(here["failed"])

    monkeypatch.setenv("FUZER_DATABASE_URL", second)  # code that opens its own connection lands there too
    settings._cache.clear()
    settings._first.clear()
    with db.connect(url=second) as other:
        engine.load_defaults(other)
        other.commit()
        assert len(om.deviations(other, tmp_path / "okan-v2")) == 2  # it still runs the standard
        done = om.apply(other, tmp_path / "okan-v2", by="test")
        assert done["deviations"] == 0 and done["digest"] == out["digest"]
        assert om.applied(other)["version"] == 2
        assert other.execute("select default_sla from work_role where name = 'data_steward'").fetchone()[
            "default_sla"] == "PT2H"
        there = loop.run(other)

    assert _steps(there) == _steps(here)
    assert here["synthetic"] == there["synthetic"] == []
