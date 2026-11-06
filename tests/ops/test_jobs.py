"""Dagster jobs: real assets, checks and the export sensor, against embedded Postgres."""

import os
import tempfile
from pathlib import Path

import dagster as dg
import pytest

from fakeplant import offshore
from fuzer.core import db
from fuzer.ops import jobs

pytestmark = pytest.mark.sequence  # (#137) the Dagster jobs on one data folder: load, sensor, checks


@pytest.fixture(scope="module")
def fuzer():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzdata"))
    offshore.generate(data)
    with db.connect() as c:
        db.reset(c)
    return jobs.Fuzer(data_dir=str(data), database_url=db.database_url())


def test_definitions_load():
    defs = jobs.defs
    assert {j.name for j in defs.resolve_all_job_defs()} >= {"refresh_all", "refresh_logs", "claude_reading", "work_tick"}
    claude = next(s for s in defs.schedules if s.job_name == "claude_reading")
    assert claude.default_status == dg.DefaultScheduleStatus.STOPPED  # costs money: opt in
    assert defs.resolve_sensor_def("new_exports").default_status == dg.DefaultSensorStatus.RUNNING


def test_refresh_all_loads_sources_rebuilds_and_checks(fuzer):
    selection = [*jobs.SOURCES, jobs.register]
    checks = [jobs.review_backlog, jobs.orphan_timeseries, jobs.tags_missing_from_sap, jobs.downtime_reason_codes,
              jobs.unmapped_site_codes,
              jobs.document_revisions, jobs.flow_vs_piping]
    result = dg.materialize(selection + checks, resources={"fuzer": fuzer})
    assert result.success

    meta = {e.asset_key.path[-1]: e.materialization.metadata for e in result.get_asset_materialization_events()}
    assert meta["sap_floc"]["records"].value == 84 and meta["oplogs"]["records"].value == 30
    assert meta["register"]["entities"].value > 200

    evals = {e.check_name: e for e in result.get_asset_check_evaluations()}
    assert set(evals) == {"review_backlog", "orphan_timeseries", "tags_missing_from_sap", "downtime_reason_codes",
                          "unmapped_site_codes",
                          "document_revisions", "flow_vs_piping"}
    assert evals["orphan_timeseries"].passed  # every PI point attaches to a tag or well
    assert evals["tags_missing_from_sap"].passed  # every tag in the fake asset has an SAP FLOC
    from fuzer.graph import quality
    with db.connect(url=fuzer.database_url) as conn:
        domain_results = {
            "review_backlog": quality.review_backlog(conn),
            "orphan_timeseries": quality.orphan_timeseries(conn),
            "tags_missing_from_sap": quality.tags_missing_from_sap(conn),
            "downtime_reason_codes": quality.downtime_reason_codes(conn),
            "unmapped_site_codes": quality.unmapped_site_codes(conn),
            "document_revisions": quality.document_revisions(conn),
            "flow_vs_piping": quality.flow_vs_piping(conn),
        }
    assert {name: evaluation.passed for name, evaluation in evals.items()} == {
        name: domain["passed"] for name, domain in domain_results.items()}
    with db.connect(url=fuzer.database_url) as c:
        assert c.execute("select count(*) as n from edge").fetchone()["n"] > 300


def test_sensor_runs_only_changed_exports(fuzer):
    ctx = dg.build_sensor_context(resources={"fuzer": fuzer})
    first = jobs.new_exports(ctx)
    keys = {k.path[-1] for k in first.run_requests[0].asset_selection}
    present = {n for n in jobs.ingest.export_names() if jobs.ingest.dataset(Path(fuzer.data_dir), n)}
    assert keys == present | {"register"}  # first look: everything there is new

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
    code = f"from pathlib import Path; from fuzer.ops.jobs import export_signature; print(export_signature(Path({fuzer.data_dir!r}), 'pid_sheets'))"
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


def test_work_tick_job_runs(fuzer):
    result = jobs.work_tick.execute_in_process(resources={"fuzer": fuzer})
    assert result.success


def test_datasets_have_checks_and_their_own_schedules():
    """#225: a source asset and a check per catalog dataset; a dataset's cron is due when a tick falls in the window."""
    from datetime import datetime, timezone

    from fuzer.ops import jobs

    assert jobs.SOURCE_CHECKS and len(jobs.SOURCE_CHECKS) == len(jobs.SOURCES)
    names = {s.name for s in jobs.defs.sensors}
    assert {"dataset_schedules", "new_exports"} <= names and "source_health" in {j.name for j in jobs.defs.jobs}
    t = datetime(2025, 1, 1, 9, 59, tzinfo=timezone.utc).timestamp()
    assert jobs.due("0 * * * *", t, t + 120, "UTC") and not jobs.due("0 * * * *", t + 120, t + 180, "UTC")
