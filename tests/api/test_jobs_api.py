"""The Jobs page's API (#208): runs, schedules, sensors and asset checks read from a Dagster instance.

No daemon and no database: a few small stand-in jobs run in process on a temporary Dagster instance (SQLite, as `fuzer jobs` keeps it),
which the routes read in place of $DAGSTER_HOME; the heartbeat is faked to test the scheduler states."""

import time
from types import SimpleNamespace

import dagster as dg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.api import jobs

MODEL = "claude-sonnet-5-5"


@dg.asset(name="register")
def register():
    return dg.MaterializeResult(metadata={"entities": 42, "edges": 7})


@dg.asset_check(asset=register, description="Open review questions stay a small share of the register.")
def review_backlog():
    return dg.AssetCheckResult(passed=True, metadata={"open_reviews": 3})


@dg.asset_check(asset=register, description="PI points are attached to a tag or well.")
def orphan_timeseries():
    return dg.AssetCheckResult(passed=False, severity=dg.AssetCheckSeverity.WARN,
                               metadata={"orphans": 2, "examples": dg.MetadataValue.json(["10-PT-1", "10-PT-2"])})


@dg.asset(group_name="claude", deps=[register])
def claude_log_reads():
    return dg.MaterializeResult(metadata={"model": MODEL, "input_tokens": 1_000_000, "output_tokens": 100_000})


@dg.op
def boom():
    raise ValueError("the export folder is empty")


@dg.job(description="Work engine timers and SLAs.")
def work_tick():
    boom()


refresh_all = dg.define_asset_job("refresh_all", selection=dg.AssetSelection.assets(register),
                                  description="Load every export and rebuild.")
claude_reading = dg.define_asset_job("claude_reading", selection=dg.AssetSelection.groups("claude"),
                                     description="Claude reads new text.")


@dg.sensor(job=refresh_all, default_status=dg.DefaultSensorStatus.RUNNING, description="Changed exports.")
def new_exports(context):
    return dg.SkipReason("no changed exports")


class Claude(dg.ConfigurableResource):
    batch: bool = True
    max_sre_usd: float = 5.0


DEFS = dg.Definitions(
    assets=[register, claude_log_reads],
    asset_checks=[review_backlog, orphan_timeseries],
    jobs=[refresh_all, claude_reading, work_tick],
    schedules=[
        dg.ScheduleDefinition(job=refresh_all, cron_schedule="0 2 * * *", default_status=dg.DefaultScheduleStatus.RUNNING),
        dg.ScheduleDefinition(job=claude_reading, cron_schedule="0 3 * * *", default_status=dg.DefaultScheduleStatus.STOPPED),
        dg.ScheduleDefinition(job=work_tick, cron_schedule="*/5 * * * *", default_status=dg.DefaultScheduleStatus.RUNNING),
    ],
    sensors=[new_exports],
    resources={"claude": Claude()},
)


class WithHeartbeat:
    """The ephemeral instance, with a scheduler daemon that has just checked in."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def get_daemon_heartbeats(self):
        return {"SCHEDULER": SimpleNamespace(timestamp=time.time())}


@pytest.fixture(scope="module")
def instance(tmp_path_factory):
    inst = dg.DagsterInstance.local_temp(str(tmp_path_factory.mktemp("dagster_home")))
    DEFS.resolve_job_def("refresh_all").execute_in_process(instance=inst)
    DEFS.resolve_job_def("claude_reading").execute_in_process(instance=inst)
    work_tick.execute_in_process(instance=inst, raise_on_error=False)
    return inst


def client_for(instance) -> TestClient:
    app = FastAPI()
    app.include_router(jobs.router)
    app.dependency_overrides[jobs.get_instance] = lambda: instance
    app.dependency_overrides[jobs.get_definitions] = lambda: DEFS
    app.dependency_overrides[jobs.get_dagster_link] = lambda: {"url": "http://127.0.0.1:3000", "reachable": False}
    return TestClient(app)


@pytest.fixture(autouse=True)
def fresh_readings(monkeypatch):
    """Each test reads the scheduler's storage itself; the shared reading has its own test."""
    monkeypatch.setattr(jobs, "SHARED_SECONDS", 0)


def test_dagster_link_keeps_local_default_for_development(monkeypatch):
    monkeypatch.delenv("FUZER_PUBLIC_URL", raising=False)
    monkeypatch.delenv("FUZER_DAGSTER_URL", raising=False)
    monkeypatch.setattr(jobs, "_reachable", lambda url: True)

    assert jobs.get_dagster_link() == {"url": "http://127.0.0.1:3000", "reachable": True}


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:3000", "http://127.2.3.4:3000", "http://[::1]:3000",
    "http://0.0.0.0:3000", "http://localhost:3000",
])
def test_dagster_link_hides_loopback_url_in_public_deployment(monkeypatch, url):
    monkeypatch.setenv("FUZER_PUBLIC_URL", "https://fuzer.example")
    monkeypatch.setenv("FUZER_DAGSTER_URL", url)
    monkeypatch.setattr(jobs, "_reachable", lambda _: pytest.fail("loopback URL must not be probed"))

    assert jobs.get_dagster_link() == {"url": "", "reachable": False}


def test_dagster_link_uses_public_url_when_configured(monkeypatch):
    monkeypatch.setenv("FUZER_PUBLIC_URL", "https://fuzer.example")
    monkeypatch.setenv("FUZER_DAGSTER_URL", "https://dagster.example")
    monkeypatch.setattr(jobs, "_reachable", lambda url: url == "https://dagster.example")

    assert jobs.get_dagster_link() == {"url": "https://dagster.example", "reachable": True}


def test_overview_without_the_daemon(instance):
    body = client_for(instance).get("/jobs").json()
    assert body["available"] and body["scheduler"]["running"] is False
    by = {j["name"]: j for j in body["jobs"]}
    assert set(by) == {"refresh_all", "claude_reading", "work_tick"}
    assert by["refresh_all"]["last_run"]["status"] == "SUCCESS"
    assert by["work_tick"]["last_run"]["status"] == "FAILURE"
    assert by["work_tick"]["last_run"]["seconds"] is not None

    triggers = {t["name"]: t for t in by["refresh_all"]["triggers"]}
    assert triggers["refresh_all_schedule"]["status"] == "on" and triggers["refresh_all_schedule"]["cron"] == "0 2 * * *"
    assert triggers["refresh_all_schedule"]["next_tick"] is None  # nothing will fire while the daemon is down
    assert triggers["new_exports"]["kind"] == "sensor" and triggers["new_exports"]["status"] == "on"
    assert by["claude_reading"]["triggers"][0]["status"] == "off"  # stopped by default (costs money)

    checks = {c["name"]: c for c in body["checks"]}
    assert checks["review_backlog"]["level"] == "pass" and checks["review_backlog"]["metadata"] == {"open_reviews": 3}
    assert checks["orphan_timeseries"]["level"] == "warn"
    assert checks["orphan_timeseries"]["metadata"]["examples"] == ["10-PT-1", "10-PT-2"]

    h = body["health"]
    assert (h["level"], h["failed_runs_24h"], h["failing_checks"], h["scheduler_running"]) == ("red", 1, 1, False)
    assert "scheduler not running" in h["summary"]


def test_scheduler_running_gives_next_ticks(instance):
    body = client_for(WithHeartbeat(instance)).get("/jobs").json()
    assert body["scheduler"]["running"] is True
    by = {j["name"]: j for j in body["jobs"]}
    assert by["refresh_all"]["triggers"][0]["next_tick"]
    assert by["claude_reading"]["triggers"][0]["next_tick"] is None  # off
    assert "scheduler not running" not in body["health"]["summary"]


def test_failed_run_shows_its_error(instance):
    c = client_for(instance)
    run_id = {j["name"]: j for j in c.get("/jobs").json()["jobs"]}["work_tick"]["last_run"]["run_id"]
    run = c.get(f"/jobs/runs/{run_id}").json()
    assert run["status"] == "FAILURE" and run["trigger"] == "manual"
    step = next(e for e in run["errors"] if e["step"] == "boom")
    assert step["error_class"] == "ValueError" and "the export folder is empty" in step["message"]
    assert run["log_tail"] and all({"time", "level", "message"} <= set(x) for x in run["log_tail"])


def test_run_detail_lists_materializations(instance):
    c = client_for(instance)
    run_id = {j["name"]: j for j in c.get("/jobs").json()["jobs"]}["refresh_all"]["last_run"]["run_id"]
    run = c.get(f"/jobs/runs/{run_id}").json()
    assert run["errors"] == []
    assert [a["asset"] for a in run["assets"]] == ["register"]
    assert run["assets"][0]["metadata"] == {"entities": 42, "edges": 7}
    assert c.get("/jobs/runs/not-a-run").status_code == 404


def test_claude_spend(instance):
    claude = client_for(instance).get("/jobs").json()["claude"]
    assert claude["schedule"] == "off" and claude["limits"] == {"batch": True, "max_sre_usd": 5.0}
    (run,) = claude["runs"]
    assert (run["model"], run["input_tokens"], run["output_tokens"]) == (MODEL, 1_000_000, 100_000)
    assert run["usd"] == 1.5  # (1M x $2 + 0.1M x $10) / 1M, half price in batch
    assert claude["usd_30d"] == 1.5


def test_no_dagster_yet():
    c = client_for(None)
    body = c.get("/jobs").json()
    assert body["available"] is False and body["jobs"] == []
    assert c.get("/jobs/health").json()["level"] == "unknown"
    assert c.get("/jobs/runs/x").status_code == 404


def test_health_route(instance):
    h = client_for(WithHeartbeat(instance)).get("/jobs/health").json()
    assert h["level"] == "red" and h["summary"].startswith("1 failed run in 24 h")


def test_health_names_a_failing_sensor(instance, monkeypatch):
    failed = SimpleNamespace(status=SimpleNamespace(value="FAILURE"))
    monkeypatch.setattr(jobs, "_states", lambda inst: {"new_exports": (None, failed), "idle": (None, None)})
    monkeypatch.setattr(jobs, "_runs", lambda *a, **k: [])
    h = jobs.health(WithHeartbeat(instance), DEFS, check_rows=[])
    assert h["level"] == "red" and h["failing_triggers"] == ["new_exports"]
    assert h["summary"] == "new_exports failing, so the jobs it starts are not running"


def test_people_who_ask_together_share_one_reading(monkeypatch):
    monkeypatch.setattr(jobs, "SHARED_SECONDS", 15)
    instance, other, reads = object(), object(), []

    def read():
        reads.append(1)
        return {"n": len(reads)}

    assert jobs.shared("health", instance, read) == {"n": 1}
    assert jobs.shared("health", instance, read) == {"n": 1}  # within the window: the same reading
    assert jobs.shared("health", other, read) == {"n": 2}  # another scheduler's storage is read again
    assert jobs.shared("health", None, read) == {"n": 3}  # no scheduler: nothing to keep
    monkeypatch.setattr(jobs.time, "monotonic", lambda: 1e12)
    assert jobs.shared("health", other, read) == {"n": 4}  # the window passed
