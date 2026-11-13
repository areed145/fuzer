"""A failed scheduled run, and a sensor or schedule that fails to evaluate, reaches the alert webhook once (R.4b).

The webhook is a real HTTP server on loopback that records what it is sent; the run is a real failing Dagster job on
a temporary instance."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import dagster as dg
import pytest

from fuzer.core import bugreports
from fuzer.ops import jobs
from fuzer.sources import alerts


@pytest.fixture
def webhook(monkeypatch):
    got: list[dict] = []

    class Hook(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/hook"
    monkeypatch.setattr(alerts.settings, "secret", lambda conn, key: url if key == "alerts.webhook" else None)
    monkeypatch.setattr(alerts.settings, "get",
                        lambda conn, key, *a, **k: "https://fuzer.example/" if key == "notify.app_url" else None)
    monkeypatch.setattr(bugreports.reporter, "report", lambda *a, **k: None)
    yield got
    server.shutdown()


@dg.op
def empty_folder():
    raise ValueError("the export folder is empty")


@dg.job
def refresh_logs():
    empty_folder()


def test_a_failed_run_reaches_the_webhook_with_its_project_and_a_link_to_the_run(webhook):
    instance = dg.DagsterInstance.ephemeral()
    result = refresh_logs.execute_in_process(instance=instance, raise_on_error=False,
                                             tags={jobs.PROJECT_TAG: "okan"})
    run = result.dagster_run
    context = dg.build_run_status_sensor_context(
        sensor_name="alert_on_failure", dagster_instance=instance, dagster_run=run,
        dagster_event=result.get_run_failure_event()).for_run_failure()

    jobs.alert_on_failure(context)

    assert len(webhook) == 1
    text = webhook[0]["text"]
    assert text.startswith(f"Fuzer (okan): job 'refresh_logs' failed (run {run.run_id[:8]})")
    assert text.endswith(f"Jobs: https://fuzer.example/#/jobs/run/{run.run_id}")


def tick(ts: float, status: str, error: str = "") -> SimpleNamespace:
    return SimpleNamespace(timestamp=ts, status=SimpleNamespace(value=status),
                           error=SimpleNamespace(message=error) if error else None)


class Instance:
    """Instigator states and their latest ticks, as a Dagster instance returns them."""

    def __init__(self, latest: dict[str, tuple[str, SimpleNamespace]]):
        self.latest = latest

    def all_instigator_state(self):
        return [SimpleNamespace(instigator_name=n, instigator_origin_id=n, selector_id=n,
                                instigator_type=SimpleNamespace(value=kind)) for n, (kind, _) in self.latest.items()]

    def get_ticks(self, origin, selector, limit=1):
        return [self.latest[origin][1]]


def test_a_failing_sensor_is_reported_once_and_again_after_it_recovers_and_fails_again():
    inst = Instance({"new_exports": ("SENSOR", tick(100, "FAILURE", "permission denied: /data/okan")),
                     "work_tick_schedule": ("SCHEDULE", tick(100, "SUCCESS")),
                     "dataset_schedules": ("SENSOR", tick(90, "SKIPPED"))})
    new, seen = jobs.failed_ticks(inst, {})
    assert [(f["kind"], f["name"], f["error"]) for f in new] == [("sensor", "new_exports",
                                                                  "permission denied: /data/okan")]

    inst.latest["new_exports"] = ("SENSOR", tick(400, "FAILURE", "permission denied: /data/okan"))
    assert jobs.failed_ticks(inst, seen)[0][0]["at"] == 400  # still failing, a later tick: said again
    assert jobs.failed_ticks(inst, {"new_exports": 400})[0] == []  # the same tick: said once

    inst.latest["new_exports"] = ("SENSOR", tick(700, "SKIPPED"))
    new, seen = jobs.failed_ticks(inst, {"new_exports": 400})
    assert new == [] and seen == {}  # recovered: forgotten
    inst.latest["work_tick_schedule"] = ("SCHEDULE", tick(800, "FAILURE", "bad cron"))
    assert [(f["kind"], f["name"]) for f in jobs.failed_ticks(inst, seen)[0]] == [("schedule", "work_tick_schedule")]


def test_the_tick_failure_sensor_posts_each_new_failure_and_keeps_its_cursor(webhook, monkeypatch):
    calls = []

    def failed_ticks(instance, seen, skip):
        calls.append((seen, skip))
        return [{"kind": "sensor", "name": "new_exports", "at": 100.0, "error": "permission denied"}], \
            {"new_exports": 100.0}

    monkeypatch.setattr(jobs, "failed_ticks", failed_ticks)
    context = dg.build_sensor_context(instance=dg.DagsterInstance.ephemeral(), cursor='{"other": 5}')

    result = jobs.alert_on_tick_failure(context)

    assert calls == [({"other": 5.0}, frozenset({"alert_on_tick_failure"}))]
    assert webhook == [{"text": "Fuzer: sensor 'new_exports' failed, so the jobs it starts are not running until it "
                                "recovers: permission denied Jobs: https://fuzer.example/#/jobs"}]
    assert json.loads(context.cursor) == {"new_exports": 100.0}
    assert "1 new failure" in result.skip_message


def test_an_unreachable_webhook_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(alerts.settings, "secret", lambda conn, key: "http://127.0.0.1:9/nothing-listens")
    assert alerts.post("Fuzer: job 'x' failed") is False
    assert "alert webhook refused" in caplog.text


def test_without_an_app_url_the_alert_has_no_link():
    assert jobs.run_failed_text("sre_scan", "abcdef123456", "boom\n", app="") == \
        "Fuzer: job 'sre_scan' failed (run abcdef12): boom"
    assert jobs.load_seen("not json") == {} and jobs.load_seen(None) == {}
