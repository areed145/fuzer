"""Dagster jobs: real assets, checks and the export sensor, against embedded Postgres."""

import os
import tempfile
from pathlib import Path

import dagster as dg
import pytest

from fuzer import db, fakedata, jobs


@pytest.fixture(scope="module")
def fuzer():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzdata"))
    fakedata.generate(data)
    with db.connect() as c:
        db.reset(c)
    return jobs.Fuzer(data_dir=str(data), database_url=db.database_url())


def test_definitions_load():
    defs = jobs.defs
    assert {j.name for j in defs.resolve_all_job_defs()} >= {"refresh_all", "refresh_logs", "claude_reading"}
    claude = next(s for s in defs.schedules if s.job_name == "claude_reading")
    assert claude.default_status == dg.DefaultScheduleStatus.STOPPED  # costs money: opt in
    assert defs.resolve_sensor_def("new_exports").default_status == dg.DefaultSensorStatus.RUNNING


def test_refresh_all_loads_sources_rebuilds_and_checks(fuzer):
    selection = [*jobs.SOURCES, jobs.register]
    checks = [jobs.review_backlog, jobs.orphan_timeseries, jobs.tags_missing_from_sap]
    result = dg.materialize(selection + checks, resources={"fuzer": fuzer})
    assert result.success

    meta = {e.asset_key.path[-1]: e.materialization.metadata for e in result.get_asset_materialization_events()}
    assert meta["sap_floc"]["records"].value == 84 and meta["oplogs"]["records"].value == 30
    assert meta["register"]["entities"].value > 200

    evals = {e.check_name: e for e in result.get_asset_check_evaluations()}
    assert set(evals) == {"review_backlog", "orphan_timeseries", "tags_missing_from_sap"}
    assert evals["orphan_timeseries"].passed  # every PI point attaches to a tag or well
    assert evals["tags_missing_from_sap"].passed  # every tag in the fake asset has an SAP FLOC
    with db.connect(url=fuzer.database_url) as c:
        assert c.execute("select count(*) as n from edge").fetchone()["n"] > 300


def test_sensor_runs_only_changed_exports(fuzer):
    ctx = dg.build_sensor_context(resources={"fuzer": fuzer})
    first = jobs.new_exports(ctx)
    keys = {k.path[-1] for k in first.run_requests[0].asset_selection}
    assert keys == set(jobs.ingest.export_names()) | {"register"}  # first look: everything is new

    ctx = dg.build_sensor_context(cursor=first.cursor, resources={"fuzer": fuzer})
    assert isinstance(jobs.new_exports(ctx), dg.SkipReason)  # nothing changed

    logs = Path(fuzer.data_dir) / "oplogs.csv"
    logs.write_text(logs.read_text() + "LOG-2025-01-01-99999,2025-01-01T06:00:00,OSB,OIM,A-03 shut in.\n")
    ctx = dg.build_sensor_context(cursor=first.cursor, resources={"fuzer": fuzer})
    third = jobs.new_exports(ctx)
    assert {k.path[-1] for k in third.run_requests[0].asset_selection} == {"oplogs", "register"}


def test_export_signature_is_stable_across_processes(fuzer):
    # Python's hash() is randomised per process; the P&ID signature must not depend on it.
    import subprocess
    import sys
    code = f"from pathlib import Path; from fuzer.jobs import export_signature; print(export_signature(Path({fuzer.data_dir!r}), 'pid_sheets'))"
    runs = {subprocess.check_output([sys.executable, "-c", code], text=True) for _ in range(2)}
    assert len(runs) == 1


def test_check_flags_tag_missing_from_sap(fuzer):
    import json
    af = Path(fuzer.data_dir) / "pi_af.json"
    elements = json.loads(af.read_text())
    elements.append({"path": "OSB\\Area 20\\20-P-1099A", "template": "Centrifugal Pump", "attributes": []})
    af.write_text(json.dumps(elements))
    result = dg.materialize([*jobs.SOURCES, jobs.register, jobs.tags_missing_from_sap], resources={"fuzer": fuzer},
                            selection=["pi_af", "register"])
    check = result.get_asset_check_evaluations()[0]
    assert not check.passed and check.metadata["count"].value == 1
    assert check.metadata["examples"].value[0] == {"label": "20-P-1099A", "seen_in": ["pi_af"]}
