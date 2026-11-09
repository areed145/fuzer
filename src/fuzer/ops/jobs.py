"""Scheduled jobs (Dagster): load exports, rebuild the register, have Claude read new text.

    uv run fuzer jobs            # Dagster UI on http://127.0.0.1:3000

Assets: one per dataset in the source catalog (#225; each loads only itself, as its load mode says), then
`register` (the rebuild), then the Claude readers. What runs when:

- `new_exports` sensor: a changed export file in the data folder loads that source and rebuilds.
- `dataset_schedules` sensor: a dataset with its own schedule (source_dataset.schedule, a cron) loads then.
- `refresh_logs` hourly and `refresh_all` daily (02:00); `source_health` every 30 minutes tells owners about
  stale or broken sources (work items and the alert webhook).
- Each source asset has a check: its dataset's checks and health.
- `claude_reading` nightly, off by default because it costs money. Turn it on in the UI.
- Every schedule and sensor runs once per project this database holds (multi-project phase 4): each run is
  tagged `fuzer/project`, and the `fuzer` resource runs it in that project's schema, with its pack and (for a
  project added beside the first) its own exports folder. Filter runs by that tag in the Dagster UI.
- Asset checks on the register flag data-quality problems; failed runs post to
  FUZER_ALERT_WEBHOOK (Teams / Slack / any JSON webhook) when it is set.

Settings come from the environment: FUZER_DATABASE_URL, FUZER_DATA_DIR (default data),
FUZER_TIMEZONE (default UTC); the Claude model and effort are the
settings llm.model and llm.effort, the day close the setting daily_report.close_cron (#131).
"""

import json
import os
from pathlib import Path

import dagster as dg
from pydantic import PrivateAttr

from ..core import db, projects, settings
from ..core.pack import Pack, load_pack, resolve
from ..graph import llm_links
from ..sources import ingest, llm_pids
from . import pipeline


PROJECT_TAG = "fuzer/project"


def project_tags(project: str) -> dict[str, str]:
    return {PROJECT_TAG: project} if project else {}


class Fuzer(dg.ConfigurableResource):
    """Where the exports are and which database / asset pack / project to use."""

    data_dir: str = os.environ.get("FUZER_DATA_DIR") or "data"
    pack: str = ""  # a packaged pack to use instead; empty: the pack of the project the run is for (#376)
    database_url: str | None = os.environ.get("FUZER_DATABASE_URL")
    project: str = ""  # empty: the run's fuzer/project tag, else the database's first project

    _data: str | None = PrivateAttr(default=None)

    def setup_for_execution(self, context) -> None:
        """Run this step for its project: the code and every connection it opens use that project's schema. Each
        run executes in its own process (Dagster's run launcher), so the process's scope is the run's; there is no
        teardown, which would make this a context-manager resource that sensors must open explicitly."""
        run = getattr(context, "run", None)
        name = self.project or (run.tags.get(PROJECT_TAG, "") if run is not None else "")
        if not name:
            return
        with db.connect(url=self.database_url) as conn:
            scope = projects.scope(conn, name)
            data = projects.config(conn, name).get("data")
        db.set_default(scope)
        # A project added beside the first has its own exports; the first keeps the deployment's folder.
        self._data = data if data and scope.schema != db.PROJECT_SCHEMA else None

    def folder(self) -> str:
        """The exports folder of the project this run is for."""
        return self._data or self.data_dir

    def targets(self) -> list[tuple[str, str]]:
        """(project, exports folder) for each project a schedule or sensor runs for: every project this
        database holds, else one untagged run for the database as it is."""
        with db.connect(url=self.database_url) as conn:
            names = [self.project] if self.project else projects.stored(conn)
            out = []
            for name in names:
                scope = projects.scope(conn, name)
                data = projects.config(conn, name).get("data")
                out.append((name, data if data and scope.schema != db.PROJECT_SCHEMA else self.data_dir))
        return out or [("", self.data_dir)]

    def connect(self):
        conn = db.connect(url=self.database_url)
        db.init_schema(conn)
        return conn

    def pack_for(self, conn) -> Pack:
        """The configured pack, else the one the database's project names, resolved when the run starts."""
        return load_pack(self.pack) if self.pack else resolve(conn)


class ClaudeSettings(dg.ConfigurableResource):
    """Limits for scheduled Claude runs, so a schedule can't run up a large bill by surprise."""

    model: str = ""  # empty: the settings llm.model and llm.effort (#131)
    effort: str = ""
    batch: bool = True  # half price; a run waits for the batch (usually under an hour)
    max_log_entries: int = 2000
    max_drawings: int = 200
    max_upset_diagnoses: int = 10  # per SRE run (#96); the rest are diagnosed in the workflow after triage
    max_sre_usd: float = 5.0


def export_signature(data_dir: Path, name: str) -> str | None:
    """Cheap change marker for one dataset (its connector's fingerprint: file sizes and times, the
    files a manifest names, or a Delta version). None when the dataset isn't there, or comes from a live
    system (PI Web API, ADX): those have no cheap marker and load on the daily schedule instead."""
    c = ingest.dataset(data_dir, name)
    return c.fingerprint() if c and not getattr(c, "live", False) else None


def _source_asset(name: str):
    @dg.asset(name=name, group_name="sources", description=f"Loads the {name} dataset through its catalog connection "
                                                                 "(a folder of CSV / Parquet / Delta, or PI Web API / ADX).")
    def load(context: dg.AssetExecutionContext, fuzer: Fuzer) -> dg.MaterializeResult:
        with fuzer.connect() as conn:
            trigger = "schedule" if context.run.tags.get("dagster/schedule_name") or context.run.tags.get(
                "fuzer/dataset_schedule") else "sensor" if context.run.tags.get("dagster/sensor_name") else "manual"
            stats = ingest.ingest(conn, fuzer.folder(), [name], trigger=trigger, allow_missing=True)
            c = ingest.dataset(Path(fuzer.folder()), name, conn) if not (stats["missing"] or stats["paused"]) else None
        if stats["missing"]:
            context.log.warning(f"{name} unavailable through its connection; nothing loaded. Inspect Sources.")
        if stats["held"]:
            context.log.warning(f"{name} held for schema or blocking-check review in Sources")
        return dg.MaterializeResult(metadata={"dataset": name, "connector": c.kind if c else "",
                                              "path": str(c.path) if c else "",
                                              **{k: v for k, v in stats.items() if not isinstance(v, list)},
                                              **{k: bool(stats[k]) for k in ("loaded", "held", "paused", "unchanged", "missing")}})
    return load


def _source_check(name: str):
    @dg.asset_check(asset=name, name="source_checks", description=f"{name}: its checks pass and it is healthy (#225).")
    def check(fuzer: Fuzer) -> dg.AssetCheckResult:
        from ..sources import quality as source_quality

        with fuzer.connect() as conn:
            h = (source_quality.health(conn, name) or [{}])[0]
        passed, blocking = source_quality.health_check_outcome(h)
        failing = h.get("failing_checks") or []
        return dg.AssetCheckResult(
            passed=passed,
            severity=dg.AssetCheckSeverity.ERROR if blocking else dg.AssetCheckSeverity.WARN,
            metadata={"state": h.get("state", "unknown"), "problems": dg.MetadataValue.json(h.get("problems") or []),
                      "failing_checks": dg.MetadataValue.json(failing), "age_hours": h.get("age_hours") or 0})
    return check


def catalog_names() -> list[str]:
    """The catalog's datasets (not retired), read when the definitions load; the seed files' without a database
    (an import doesn't start the embedded one)."""
    if not os.environ.get("FUZER_DATABASE_URL") and db._server is None:
        return ingest.export_names()
    try:
        with db.connect(url=os.environ.get("FUZER_DATABASE_URL")) as conn:
            db.init_schema(conn)
            from ..sources import catalog

            catalog.ensure(conn)
            conn.commit()
            return list(catalog.datasets(conn, ("active", "paused", "draft")))
    except Exception:  # noqa: BLE001  (no database yet: the built-in datasets)
        return ingest.export_names()


DATASETS = catalog_names()
SOURCES = [_source_asset(name) for name in DATASETS]
SOURCE_CHECKS = [_source_check(name) for name in DATASETS]


@dg.asset(group_name="register", deps=SOURCES,
          description="Match records, choose golden values and rebuild the graph (full rebuild).")
def register(fuzer: Fuzer) -> dg.MaterializeResult:
    from . import board, intake, plan
    with fuzer.connect() as conn:
        pack = fuzer.pack_for(conn)
        stats = pipeline.rebuild(conn, pack)
        board.refresh(conn, pack)  # the work board follows the register (#99)
        planned = plan.sync_work_orders(conn)  # and the integrated plan, its SAP work orders
        notified = intake.sync_notifications(conn)  # and open SAP notifications nobody has an order for yet
        conn.commit()
    return dg.MaterializeResult(metadata={
        "entities": stats["entities"], "merged_clusters": stats["merged_clusters"], "edges": stats["edges"],
        "match_reviews": stats["match_reviews"], "conflict_reviews": stats["conflict_reviews"],
        "unresolved_edges": sum(stats["unresolved_edges"].values()),
        "seconds": dg.MetadataValue.json(stats["seconds"]), "plan": dg.MetadataValue.json(planned),
        "notifications": dg.MetadataValue.json(notified),
    })


def _claude_result(stats: dict, rebuilt: bool) -> dg.MaterializeResult:
    keep = ("entries", "sheets", "sections", "stored", "failed_requests", "input_tokens", "output_tokens",
            "cache_read_tokens", "findings")
    return dg.MaterializeResult(metadata={**{k: v for k, v in stats.items() if k in keep},
                                          "model": stats.get("model", ""), "provider": stats.get("provider", ""),
                                          "rebuilt": rebuilt,
                                          "errors": dg.MetadataValue.json(stats.get("errors", []))})


@dg.asset(group_name="claude", deps=[register],
          description="Claude reads new or changed operator log entries: links and events. Rebuilds if any reading is newer than the register.")
def claude_log_reads(fuzer: Fuzer, claude: ClaudeSettings) -> dg.MaterializeResult:
    with fuzer.connect() as conn:
        pack = fuzer.pack_for(conn)
        stats = llm_links.run(conn, pack, model=claude.model, effort=claude.effort, batch=claude.batch,
                              limit=claude.max_log_entries)
        rebuilt = pipeline.readings_newer(conn)  # new readings, or earlier ones never built
        if rebuilt:
            pipeline.rebuild(conn, pack)
    if stats.get("failed_requests") and not stats.get("stored"):
        raise dg.Failure(f"every Claude request failed: {stats.get('errors')}")
    return _claude_result(stats, rebuilt)


@dg.asset(group_name="claude", deps=[register],
          description="Claude reads new or re-scanned P&ID drawings. Rebuilds if anything new.")
def claude_pid_reads(fuzer: Fuzer, claude: ClaudeSettings) -> dg.MaterializeResult:
    with fuzer.connect() as conn:
        pack = fuzer.pack_for(conn)
        stats = llm_pids.run(conn, pack, model=claude.model, effort=claude.effort, batch=claude.batch,
                             limit=claude.max_drawings)
        rebuilt = pipeline.readings_newer(conn)  # new readings, or earlier ones never built
        if rebuilt:
            pipeline.rebuild(conn, pack)
    if stats.get("failed_requests") and not stats.get("stored"):
        raise dg.Failure(f"every Claude request failed: {stats.get('errors')}")
    return _claude_result(stats, rebuilt)


@dg.asset(group_name="claude", deps=[register],
          description="Claude reads new or changed document sections: failure modes and specific links.")
def claude_doc_reads(fuzer: Fuzer, claude: ClaudeSettings) -> dg.MaterializeResult:
    from ..sources import llm_docs
    with fuzer.connect() as conn:
        pack = fuzer.pack_for(conn)
        stats = llm_docs.run(conn, pack, model=claude.model, effort=claude.effort, batch=claude.batch,
                             limit=claude.max_log_entries)
        rebuilt = pipeline.readings_newer(conn)  # new readings, or earlier ones never built
        if rebuilt:
            pipeline.rebuild(conn, pack)
    if stats.get("failed_requests") and not stats.get("stored"):
        raise dg.Failure(f"every Claude request failed: {stats.get('errors')}")
    return _claude_result(stats, rebuilt)


# --- data-quality checks on the register (warnings: they flag, they don't block) ---------------


@dg.asset_check(asset=register, description="Open review questions stay a small share of the register.")
def review_backlog(fuzer: Fuzer) -> dg.AssetCheckResult:
    from ..graph import quality

    with fuzer.connect() as conn:
        result = quality.review_backlog(conn)
    return dg.AssetCheckResult(passed=result["passed"], severity=dg.AssetCheckSeverity.WARN,
                               metadata={k: v for k, v in result.items() if k != "passed"})


@dg.asset_check(asset=register, description="PI points are attached to a tag or well.")
def orphan_timeseries(fuzer: Fuzer) -> dg.AssetCheckResult:
    from ..graph import quality

    with fuzer.connect() as conn:
        result = quality.orphan_timeseries(conn)
    return dg.AssetCheckResult(passed=result["passed"], severity=dg.AssetCheckSeverity.WARN,
                               metadata={**{k: v for k, v in result.items() if k not in ("passed", "examples")},
                                         "examples": dg.MetadataValue.json(result["examples"])})


@dg.asset_check(asset=register, description="Every tag seen in other systems exists in SAP (the master).")
def tags_missing_from_sap(fuzer: Fuzer) -> dg.AssetCheckResult:
    from ..graph import quality

    with fuzer.connect() as conn:
        result = quality.tags_missing_from_sap(conn)
    return dg.AssetCheckResult(passed=result["passed"], severity=dg.AssetCheckSeverity.WARN,
                               metadata={**{k: v for k, v in result.items() if k not in ("passed", "examples")},
                                         "examples": dg.MetadataValue.json(result["examples"])})


@dg.asset_check(asset=register, description="Downtime reason codes agree with what the comments say.")
def downtime_reason_codes(fuzer: Fuzer) -> dg.AssetCheckResult:
    from ..graph import quality

    with fuzer.connect() as conn:
        result = quality.downtime_reason_codes(conn)
    return dg.AssetCheckResult(passed=result["passed"], severity=dg.AssetCheckSeverity.WARN,
                               metadata={**{k: v for k, v in result.items() if k not in ("passed", "examples")},
                                         "examples": dg.MetadataValue.json(result["examples"])})


@dg.asset_check(asset=register, description="Maintenance and downtime codes exist in their site catalogs.")
def unmapped_site_codes(fuzer: Fuzer) -> dg.AssetCheckResult:
    from ..graph import quality

    with fuzer.connect() as conn:
        result = quality.unmapped_site_codes(conn)
    return dg.AssetCheckResult(passed=result["passed"], severity=dg.AssetCheckSeverity.WARN,
                               metadata={**{k: v for k, v in result.items() if k not in ("passed", "examples")},
                                         "examples": dg.MetadataValue.json(result["examples"])})


@dg.asset_check(asset=register, description="Each document number has one current revision.")
def document_revisions(fuzer: Fuzer) -> dg.AssetCheckResult:
    from ..graph import quality

    with fuzer.connect() as conn:
        result = quality.document_revisions(conn)
    return dg.AssetCheckResult(passed=result["passed"], severity=dg.AssetCheckSeverity.WARN,
                               metadata={k: dg.MetadataValue.json(v) if k == "examples" else v
                                         for k, v in result.items() if k != "passed"})


@dg.asset_check(asset=register, description="Energy Components flows are backed by P&ID piping between the same tags.")
def flow_vs_piping(fuzer: Fuzer) -> dg.AssetCheckResult:
    from ..graph import quality

    with fuzer.connect() as conn:
        result = quality.flow_vs_piping(conn)
    return dg.AssetCheckResult(passed=result["passed"], severity=dg.AssetCheckSeverity.WARN,
                               metadata={k: dg.MetadataValue.json(v) if k == "examples" else v
                                         for k, v in result.items() if k != "passed"})


@dg.op(description="Fire due work-engine timers, escalate person tasks past their SLA (#97) and deliver the "
                   "event outbox.")
def work_tick_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from ..core import events, exceptions
    from ..workflow import engine
    with fuzer.connect() as conn:
        context.log.info(f"work tick: {engine.tick(conn)}")
        closed = exceptions.reconcile(conn)
        conn.commit()
        context.log.info(f"exceptions: {closed}, events: {events.deliver(conn)}")


@dg.job(description="Work engine timers and SLAs: re-checks that are due, reminders and escalations.")
def work_tick():
    work_tick_op()


@dg.op(description="Agentic SRE (#96): detect upsets in the process data since the last run.")
def sre_scan_op(context: dg.OpExecutionContext, fuzer: Fuzer, claude: ClaudeSettings) -> None:
    from ..workflow import engine
    from . import sre, sre_handoff
    with fuzer.connect() as conn:
        pack = fuzer.pack_for(conn)
        context.log.info(f"sre scan: {sre.scan(conn, pack)}")
        engine.load_defaults(conn)
        out = sre_handoff.handoff(conn, pack, max_diagnoses=claude.max_upset_diagnoses, max_usd=claude.max_sre_usd)
        context.log.info(f"sre hand-off: {({k: v for k, v in out.items() if k != 'items'})}")
        from . import board
        context.log.info(f"board: {board.refresh(conn, pack)}")


@dg.job(description="SRE every 6 hours: detect, group into upsets, prioritise, diagnose (capped) and hand off.")
def sre_scan():
    sre_scan_op()


@dg.op(description="Daily operations report (#98): close yesterday (and re-check the week for late records).")
def day_close_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from datetime import date

    from ..graph import operations
    from ..workflow import engine
    from . import daily_report
    with fuzer.connect() as conn:
        pack = fuzer.pack_for(conn)
        engine.load_defaults(conn)
        last = date.fromisoformat(operations.as_of(conn))
        for day in daily_report.recheck_days(last):
            day = day.isoformat()
            try:
                context.log.info(f"report {day}: {daily_report.close_day(conn, pack, day)}")
            except ValueError as e:  # no DAY001 for that day yet
                context.log.info(f"report {day}: {e}")
        from ..analysis import hydraulics
        from . import board, opportunities
        context.log.info(f"debottleneck options: {hydraulics.store_options(conn, pack)}")
        context.log.info(f"opportunity scan: {opportunities.scan(conn, pack)}")
        board.refresh(conn, pack)


@dg.job(description="Daily operations report: facts from the day's records, a new version when they change, the ops "
                    "lead's review in the workflow (the reporter agent drafts it).")
def day_close():
    day_close_op()


@dg.op(description="Run queued correlation and clustering studies (#106).")
def studies_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from . import study
    with fuzer.connect() as conn:
        context.log.info(f"studies: {study.run_queued(conn, fuzer.pack_for(conn))}")


@dg.job(description="Big correlation and clustering studies, queued from the API or an agent.")
def studies_job():
    studies_op()


@dg.op(description="Tell owners about stale or broken sources, and close what cleared (#225).")
def source_health_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from ..core import events
    from ..sources import alerts
    with fuzer.connect() as conn:
        out = alerts.check(conn)
        conn.commit()
        context.log.info(f"source health: {out}, events: {events.deliver(conn)}")


@dg.job(description="Source health: stale data, failed or held loads, failing checks -> exceptions, cases and alerts.")
def source_health():
    source_health_op()


# --- jobs, schedules, sensors -------------------------------------------------------------------

refresh_all = dg.define_asset_job("refresh_all", selection=dg.AssetSelection.groups("sources", "register"),
                                  description="Load every export and rebuild.")
refresh_logs = dg.define_asset_job("refresh_logs", selection=dg.AssetSelection.assets(
                                       *(["oplogs"] if "oplogs" in DATASETS else []), register),
                                   description="Load the operator logs and rebuild.")
claude_reading = dg.define_asset_job("claude_reading", selection=dg.AssetSelection.groups("claude"),
                                     description="Claude reads new log entries and drawings (costs money).")

TZ = os.environ.get("FUZER_TIMEZONE", "UTC")


def per_project(job, cron: str, status: dg.DefaultScheduleStatus) -> dg.ScheduleDefinition:
    """A schedule that starts the job once for each project, tagged with it."""
    @dg.schedule(name=f"{job.name}_schedule", job=job, cron_schedule=cron, execution_timezone=TZ,
                 default_status=status, required_resource_keys={"fuzer"})
    def schedule(context: dg.ScheduleEvaluationContext):
        at = context.scheduled_execution_time.isoformat()
        return [dg.RunRequest(run_key=f"{project}@{at}", tags=project_tags(project))
                for project, _ in context.resources.fuzer.targets()]
    return schedule


RUNNING, STOPPED = dg.DefaultScheduleStatus.RUNNING, dg.DefaultScheduleStatus.STOPPED
schedules = [
    per_project(refresh_logs, "0 * * * *", RUNNING),
    per_project(refresh_all, "0 2 * * *", RUNNING),
    per_project(claude_reading, "0 3 * * *", STOPPED),
    per_project(work_tick, "*/5 * * * *", RUNNING),
    per_project(sre_scan, "0 */6 * * *", RUNNING),
    per_project(studies_job, "*/5 * * * *", RUNNING),
    per_project(day_close, settings.get(None, "daily_report.close_cron"), RUNNING),
    per_project(source_health, "*/30 * * * *", RUNNING),
]


def due(cron: str, since: float, now: float, tz: str = TZ) -> bool:
    """A cron tick fell in (since, now] (epoch seconds)."""
    from dagster._utils.schedules import cron_string_iterator

    for t in cron_string_iterator(since, cron, tz):
        return t.timestamp() <= now
    return False


@dg.sensor(job=refresh_all, minimum_interval_seconds=60, default_status=dg.DefaultSensorStatus.RUNNING,
           description="Loads each dataset with its own schedule (source_dataset.schedule) when it is due, then rebuilds.")
def dataset_schedules(context: dg.SensorEvaluationContext, fuzer: Fuzer):
    import time

    now = time.time()
    since = float(context.cursor) if context.cursor else now - 60
    requests, scheduled = [], 0
    for project, _ in fuzer.targets():
        with db.use(_scope_of(fuzer, project)), fuzer.connect() as conn:
            rows = conn.execute("select name, schedule from source_dataset where status = 'active' "
                                "and coalesce(schedule, '') <> ''").fetchall()
        scheduled += len(rows)
        due_now = sorted(r["name"] for r in rows if r["name"] in DATASETS and due(r["schedule"], since, now))
        missing = [r["name"] for r in rows if r["name"] not in DATASETS]
        if missing:
            context.log.warning(f"datasets added since the definitions loaded (reload them to schedule): {missing}")
        if due_now:
            requests.append(dg.RunRequest(run_key=f"{project}:{','.join(due_now)}@{int(now)}",
                                          asset_selection=[dg.AssetKey(n) for n in due_now] + [register.key],
                                          tags={"fuzer/dataset_schedule": ",".join(due_now), **project_tags(project)}))
    if not requests:
        return dg.SensorResult(skip_reason=f"no dataset due ({scheduled} scheduled)", cursor=str(now))
    return dg.SensorResult(run_requests=requests, cursor=str(now))


def _scope_of(fuzer: Fuzer, project: str) -> db.Scope:
    if not project:
        return db.scope()
    with db.connect(url=fuzer.database_url) as conn:
        return projects.scope(conn, project)


@dg.sensor(job=refresh_all, minimum_interval_seconds=60, default_status=dg.DefaultSensorStatus.RUNNING,
           description="Loads any export file that changed in the data folder, then rebuilds.")
def new_exports(context: dg.SensorEvaluationContext, fuzer: Fuzer):
    raw = json.loads(context.cursor) if context.cursor else {}
    targets = fuzer.targets()
    # A cursor from before projects holds the signatures of the database's first project.
    seen = raw["projects"] if "projects" in raw else ({targets[0][0]: raw} if raw else {})
    cursor, requests, folders = {}, [], []
    for project, folder in targets:
        data_dir = Path(folder)
        folders.append(str(data_dir))
        current = {name: sig for name in DATASETS if (sig := export_signature(data_dir, name))}
        cursor[project] = current
        changed = sorted(name for name, sig in current.items() if seen.get(project, {}).get(name) != sig)
        if changed:
            requests.append(dg.RunRequest(
                run_key=json.dumps({"project": project, **{n: current[n] for n in changed}}, sort_keys=True),
                asset_selection=[dg.AssetKey(n) for n in changed] + [register.key],
                tags={"changed_exports": ",".join(changed), **project_tags(project)},
            ))
    if not requests:
        return dg.SkipReason(f"no changed exports in {', '.join(folders)}")
    return dg.SensorResult(run_requests=requests, cursor=json.dumps({"projects": cursor}, sort_keys=True))


@dg.run_failure_sensor(default_status=dg.DefaultSensorStatus.RUNNING,
                       description="Posts failed runs to FUZER_ALERT_WEBHOOK when it is set.")
def alert_on_failure(context: dg.RunFailureSensorContext):
    message = f"Fuzer job '{context.dagster_run.job_name}' failed (run {context.dagster_run.run_id[:8]}): " \
              f"{context.failure_event.message}"
    context.log.error(message)
    from ..sources import alerts

    alerts.post(message)


defs = dg.Definitions(
    assets=[*SOURCES, register, claude_log_reads, claude_pid_reads, claude_doc_reads],
    asset_checks=[review_backlog, orphan_timeseries, tags_missing_from_sap, downtime_reason_codes, unmapped_site_codes,
                  document_revisions, flow_vs_piping, *SOURCE_CHECKS],
    jobs=[refresh_all, refresh_logs, claude_reading, work_tick, sre_scan, day_close, studies_job, source_health],
    schedules=schedules,
    sensors=[new_exports, dataset_schedules, alert_on_failure],
    resources={"fuzer": Fuzer(), "claude": ClaudeSettings()},
)
