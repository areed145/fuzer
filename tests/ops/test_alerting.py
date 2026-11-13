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


def _marked_failed(instance, tags=None):
    """A run the run monitor marked failed from outside its process, as when a deploy stops the instance running it."""
    run = refresh_logs.execute_in_process(instance=instance, raise_on_error=False, tags=tags or {}).dagster_run
    instance.report_run_failed(run)
    event = [e for e in instance.all_logs(run.run_id) if e.dagster_event and e.dagster_event.is_run_failure][-1]
    return run, event, instance.get_run_record_by_id(run.run_id).end_time


def _context(instance, run, event):
    return dg.build_run_status_sensor_context(
        sensor_name="alert_on_failure", dagster_instance=instance, dagster_run=run,
        dagster_event=event.dagster_event).for_run_failure()


def test_a_run_a_deploy_interrupted_is_not_reported_but_one_with_no_stop_beside_it_is(webhook, tmp_path, monkeypatch):
    """Every job failure report from 12 to 13 November (#812 to #895) came within 15 minutes of a merge: the deploy
    stopped the instance mid-run. A run interrupted around a recorded stop isn't a failure; one with none is."""
    from fuzer.ops import stops

    monkeypatch.setenv("DAGSTER_HOME", str(tmp_path))
    instance = dg.DagsterInstance.ephemeral()
    run, event, ended = _marked_failed(instance, {jobs.PROJECT_TAG: "okan", "dagster/schedule_name": "logs_schedule"})
    assert "marked as failed from outside the execution context" in event.dagster_event.message
    jobs.alert_on_failure(_context(instance, run, event))  # no stop recorded: an unexplained death is reported
    assert len(webhook) == 1
    stops.mark_stopped(now=ended + 20)  # the supervisor was told to stop seconds after
    jobs.alert_on_failure(_context(instance, run, event))
    assert len(webhook) == 1
    by_hand, by_hand_event, _ = _marked_failed(instance)  # started by hand: nothing restarts it, so it is posted
    jobs.alert_on_failure(_context(instance, by_hand, by_hand_event))
    assert len(webhook) == 2
    stops.mark_stopped(now=ended + 3 * 3600)  # a later deploy doesn't forget the earlier one
    assert stops.interrupted_by_stop(event.dagster_event.message, ended)
    assert not stops.interrupted_by_stop("the export folder is empty", ended)  # a job's own error


def test_stops_are_kept_and_bounded(tmp_path):
    from fuzer.ops import stops

    for t in range(stops.KEEP + 5):
        stops.mark_stopped(tmp_path, now=1000.0 * t)
    kept = stops.stops(tmp_path)
    assert len(kept) == stops.KEEP and kept[-1] == 1000.0 * (stops.KEEP + 4)
    msg = "Execution was interrupted unexpectedly. No user initiated termination request was found"
    assert stops.interrupted_by_stop(msg, kept[-1] + 30, tmp_path)
    assert stops.interrupted_by_stop(msg, kept[-1] - 60, tmp_path)  # the run monitor saw it a poll early
    assert not stops.interrupted_by_stop(msg, kept[-1] + stops.AFTER_SECONDS + 1, tmp_path)
    assert not stops.interrupted_by_stop(msg, 0.0, tmp_path / "none")  # nothing recorded
