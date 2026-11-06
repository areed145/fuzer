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
- Asset checks on the register flag data-quality problems; failed runs post to
  FUZER_ALERT_WEBHOOK (Teams / Slack / any JSON webhook) when it is set.

Settings come from the environment: FUZER_DATABASE_URL, FUZER_DATA_DIR (default data),
FUZER_PROJECT (or FUZER_PACK), FUZER_TIMEZONE (default UTC); the Claude model and effort are the
settings llm.model and llm.effort, the day close the setting daily_report.close_cron (#131).
"""

import json
import os
from pathlib import Path

import dagster as dg

from ..core import db, settings
from ..core.pack import load_pack
from ..graph import llm_links
from ..sources import ingest, llm_pids
from . import pipeline


class Fuzer(dg.ConfigurableResource):
    """Where the exports are and which database / asset pack to use."""

    data_dir: str = os.environ.get("FUZER_DATA_DIR") or "data"
    pack: str = os.environ.get("FUZER_PACK", "offshore_demo")
    database_url: str | None = os.environ.get("FUZER_DATABASE_URL")

    def connect(self):
        conn = db.connect(url=self.database_url)
        db.init_schema(conn)
        return conn


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
            if (c := ingest.dataset(Path(fuzer.data_dir), name, conn)) is None:
                context.log.warning(f"{name} not found through its connection; nothing loaded")
                return dg.MaterializeResult(metadata={"dataset": name, "records": 0, "missing": True})
            trigger = "schedule" if context.run.tags.get("dagster/schedule_name") or context.run.tags.get(
                "fuzer/dataset_schedule") else "sensor" if context.run.tags.get("dagster/sensor_name") else "manual"
            stats = ingest.ingest(conn, fuzer.data_dir, [name], trigger=trigger)
        if stats["held"]:
            context.log.warning(f"{name} changed schema; held until the schema review is answered")
        return dg.MaterializeResult(metadata={"dataset": name, "connector": c.kind, "path": str(c.path),
                                              **{k: v for k, v in stats.items() if not isinstance(v, list)},
                                              "held": bool(stats["held"])})
    return load


def _source_check(name: str):
    @dg.asset_check(asset=name, name="source_checks", description=f"{name}: its checks pass and it is healthy (#225).")
    def check(fuzer: Fuzer) -> dg.AssetCheckResult:
        from ..sources import quality

        with fuzer.connect() as conn:
            h = (quality.health(conn, name) or [{}])[0]
        passed, blocking = quality.health_check_outcome(h)
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
    from . import board
    pack = load_pack(fuzer.pack)
    with fuzer.connect() as conn:
        stats = pipeline.rebuild(conn, pack)
        board.refresh(conn, pack)  # the work board follows the register (#99)
    return dg.MaterializeResult(metadata={
        "entities": stats["entities"], "merged_clusters": stats["merged_clusters"], "edges": stats["edges"],
        "match_reviews": stats["match_reviews"], "conflict_reviews": stats["conflict_reviews"],
        "unresolved_edges": sum(stats["unresolved_edges"].values()),
        "seconds": dg.MetadataValue.json(stats["seconds"]),
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
    pack = load_pack(fuzer.pack)
    with fuzer.connect() as conn:
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
    pack = load_pack(fuzer.pack)
    with fuzer.connect() as conn:
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
    pack = load_pack(fuzer.pack)
    with fuzer.connect() as conn:
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


@dg.op(description="Fire due work-engine timers and escalate person tasks past their SLA (#97).")
def work_tick_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from ..workflow import engine
    with fuzer.connect() as conn:
        context.log.info(f"work tick: {engine.tick(conn)}")


@dg.job(description="Work engine timers and SLAs: re-checks that are due, reminders and escalations.")
def work_tick():
    work_tick_op()


@dg.op(description="Agentic SRE (#96): detect upsets in the process data since the last run.")
def sre_scan_op(context: dg.OpExecutionContext, fuzer: Fuzer, claude: ClaudeSettings) -> None:
    from ..workflow import engine
    from . import sre, sre_handoff
    pack = load_pack(fuzer.pack)
    with fuzer.connect() as conn:
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
    pack = load_pack(fuzer.pack)
    with fuzer.connect() as conn:
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
        context.log.info(f"studies: {study.run_queued(conn, load_pack(fuzer.pack))}")


@dg.job(description="Big correlation and clustering studies, queued from the API or an agent.")
def studies_job():
    studies_op()


@dg.op(description="Tell owners about stale or broken sources, and close what cleared (#225).")
def source_health_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from ..sources import alerts
    with fuzer.connect() as conn:
        out = alerts.check(conn)
        conn.commit()
        context.log.info(f"source health: {out}")


@dg.job(description="Source health: stale data, failed or held loads, failing checks -> work items and alerts.")
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
schedules = [
    dg.ScheduleDefinition(job=refresh_logs, cron_schedule="0 * * * *", execution_timezone=TZ,
                          default_status=dg.DefaultScheduleStatus.RUNNING),
    dg.ScheduleDefinition(job=refresh_all, cron_schedule="0 2 * * *", execution_timezone=TZ,
                          default_status=dg.DefaultScheduleStatus.RUNNING),
    dg.ScheduleDefinition(job=claude_reading, cron_schedule="0 3 * * *", execution_timezone=TZ,
                          default_status=dg.DefaultScheduleStatus.STOPPED),
    dg.ScheduleDefinition(job=work_tick, cron_schedule="*/5 * * * *", execution_timezone=TZ,
                          default_status=dg.DefaultScheduleStatus.RUNNING),
    dg.ScheduleDefinition(job=sre_scan, cron_schedule="0 */6 * * *", execution_timezone=TZ,
                          default_status=dg.DefaultScheduleStatus.RUNNING),
    dg.ScheduleDefinition(job=studies_job, cron_schedule="*/5 * * * *", execution_timezone=TZ,
                          default_status=dg.DefaultScheduleStatus.RUNNING),
    dg.ScheduleDefinition(job=day_close, cron_schedule=settings.get(None, "daily_report.close_cron"),
                          execution_timezone=TZ, default_status=dg.DefaultScheduleStatus.RUNNING),
    dg.ScheduleDefinition(job=source_health, cron_schedule="*/30 * * * *", execution_timezone=TZ,
                          default_status=dg.DefaultScheduleStatus.RUNNING),
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
    with fuzer.connect() as conn:
        rows = conn.execute("select name, schedule from source_dataset where status = 'active' "
                            "and coalesce(schedule, '') <> ''").fetchall()
    due_now = sorted(r["name"] for r in rows if r["name"] in DATASETS and due(r["schedule"], since, now))
    missing = [r["name"] for r in rows if r["name"] not in DATASETS]
    if missing:
        context.log.warning(f"datasets added since the definitions loaded (reload them to schedule): {missing}")
    if not due_now:
        return dg.SensorResult(skip_reason=f"no dataset due ({len(rows)} scheduled)", cursor=str(now))
    return dg.SensorResult(
        run_requests=[dg.RunRequest(run_key=f"{','.join(due_now)}@{int(now)}",
                                    asset_selection=[dg.AssetKey(n) for n in due_now] + [register.key],
                                    tags={"fuzer/dataset_schedule": ",".join(due_now)})],
        cursor=str(now))


@dg.sensor(job=refresh_all, minimum_interval_seconds=60, default_status=dg.DefaultSensorStatus.RUNNING,
           description="Loads any export file that changed in the data folder, then rebuilds.")
def new_exports(context: dg.SensorEvaluationContext, fuzer: Fuzer):
    data_dir = Path(fuzer.data_dir)
    seen = json.loads(context.cursor) if context.cursor else {}
    current = {name: sig for name in DATASETS if (sig := export_signature(data_dir, name))}
    changed = sorted(name for name, sig in current.items() if seen.get(name) != sig)
    if not changed:
        return dg.SkipReason(f"no changed exports in {data_dir}")
    return dg.SensorResult(
        run_requests=[dg.RunRequest(
            run_key=json.dumps({n: current[n] for n in changed}, sort_keys=True),
            asset_selection=[dg.AssetKey(n) for n in changed] + [register.key],
            tags={"changed_exports": ",".join(changed)},
        )],
        cursor=json.dumps(current, sort_keys=True),
    )


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
