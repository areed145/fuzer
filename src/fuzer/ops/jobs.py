"""Scheduled jobs (Dagster): load exports, rebuild the register, have Claude read new text.

    uv run fuzer jobs            # Dagster UI on http://127.0.0.1:3000

Assets: one per dataset in the source catalog (#225; each loads only itself, as its load mode says), then
`register` (the rebuild), then the Claude readers. What runs when:

- `new_exports` sensor: a changed export file in the data folder loads that source and rebuilds.
- `dataset_schedules` sensor: a dataset with its own schedule (source_dataset.schedule, a cron) loads then.
- `refresh_logs` hourly and `refresh_all` daily (02:00); `source_health` every 30 minutes tells owners about
  stale or broken sources (work items and the alert webhook).
- `detect_5min`, `detect_30min`, `detect_hourly`, `detect_6h` and `detect_daily`: each detector tier (P3.2), its
  detectors from their own watermarks (compressor trips every 5 minutes, machine health hourly, records daily).
- `providers_poll` every 15 minutes reads each polled detection provider's alert table when it changed (P3.8c).
- Each source asset has a check: its dataset's checks and health.
- `claude_reading` nightly, off by default because it costs money. Turn it on in the UI.
- Every schedule and sensor runs once per project this database holds (multi-project phase 4): each run is
  tagged `fuzer/project`, and the `fuzer` resource runs it in that project's schema, with its pack and (for a
  project added beside the first) its own exports folder. Filter runs by that tag in the Dagster UI.
- Asset checks on the register flag data-quality problems; failed runs post to
  FUZER_ALERT_WEBHOOK (Teams / Slack / any JSON webhook) when it is set.

Settings come from the environment (FUZER_DATABASE_URL) and the projects: each project's exports folder from its
bundle, and the schedules' timezone from the first project's (default UTC); the Claude model and effort are the
settings llm.model and llm.effort, the day close the setting daily_report.close_cron (#131).
"""

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

import dagster as dg
from pydantic import PrivateAttr

from ..core import db, projects, settings
from ..core.pack import Pack, load_pack, resolve
from ..graph import llm_links
from ..sources import ingest, llm_pids
from . import pipeline
from .cadence import TIERS as CADENCE_CRON

PROJECT_TAG = "fuzer/project"


PROJECTS_TAG = "fuzer/projects"  # a run that does its work for several projects in turn, comma-separated


def project_tags(project: str) -> dict[str, str]:
    return {PROJECT_TAG: project} if project else {}


class Fuzer(dg.ConfigurableResource):
    """Where the exports are and which database / asset pack / project to use."""

    data_dir: str = os.environ.get("FUZER_DATA_DIR") or ""  # deprecated override of the first project's folder
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
        self._data = self._pick(scope.schema, data)

    def several(self, context) -> list[str]:
        """The projects a run tagged for several (`fuzer/projects`) takes in turn; empty for a run for one project
        (or none), which works in the scope its start set."""
        run = getattr(context, "run", None)
        return [n for n in (run.tags.get(PROJECTS_TAG, "") if run is not None else "").split(",") if n]

    @contextmanager
    def within(self, name: str):
        """Work in one project of a run for several: its schema and its exports folder. Steps run in the run's
        one process (in_process_executor), so the process's scope is safe to move and is put back after."""
        before, data = db.scope(), self._data
        with db.connect(url=self.database_url) as conn:
            scope = projects.scope(conn, name)
            folder = projects.config(conn, name).get("data")
        db.set_default(scope)
        self._data = self._pick(scope.schema, folder)
        try:
            yield
        finally:
            db.set_default(before)
            self._data = data

    def _pick(self, schema: str, data: str | None) -> str:
        """A project's exports folder: its own (from its bundle). The first project's can still be overridden by
        the deprecated FUZER_DATA_DIR; a project added beside it never is."""
        if schema != db.PROJECT_SCHEMA:
            return data or self.data_dir or "data"
        return self.data_dir or data or "data"

    def folder(self) -> str:
        """The exports folder of the project this run is for."""
        return self._data or self.data_dir or "data"

    def targets(self) -> list[tuple[str, str]]:
        """(project, exports folder) for each project a schedule or sensor runs for: every project this
        database holds, else one untagged run for the database as it is."""
        with db.connect(url=self.database_url) as conn:
            names = [self.project] if self.project else projects.stored(conn)
            out = []
            for name in names:
                scope = projects.scope(conn, name)
                data = projects.config(conn, name).get("data")
                out.append((name, self._pick(scope.schema, data)))
        return out or [("", self.data_dir or "data")]

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
    except Exception:
        return ingest.export_names()


DATASETS = catalog_names()
SOURCES = [_source_asset(name) for name in DATASETS]
SOURCE_CHECKS = [_source_check(name) for name in DATASETS]


@dg.asset(group_name="register", deps=SOURCES,
          description="Match records, choose golden values and rebuild the graph (full rebuild).")
def register(fuzer: Fuzer) -> dg.MaterializeResult:
    with fuzer.connect() as conn:
        pack = fuzer.pack_for(conn)
        stats = pipeline.rebuild(conn, pack)
        # the work board (#99), the integrated plan and the IOC's intake of open SAP notifications follow it
        followed = pipeline.follow_register(conn, pack)
        planned, notified = followed["plan"], followed["notifications"]
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


def in_each_project(context: dg.OpExecutionContext, fuzer: Fuzer, work) -> None:
    """Do `work(conn, say)` for each project the run is for, `say` logging with the project's name. In a run for
    several (Fuzer.several), one project failing does not stop the others: the run fails at the end, naming them."""
    names = fuzer.several(context)
    if not names:
        with fuzer.connect() as conn:
            work(conn, context.log.info)
        return
    failed: dict[str, Exception] = {}
    for project in names:
        try:
            with fuzer.within(project), fuzer.connect() as conn:
                work(conn, lambda text, project=project: context.log.info(f"{project}: {text}"))
        except Exception as e:
            context.log.error(f"{project}: failed: {e}")
            failed[project] = e
    if failed:
        first = next(iter(failed.values()))
        raise dg.Failure(f"failed for {', '.join(failed)}: {first}") from first


@dg.op(description="Fire due work-engine timers, escalate person tasks past their SLA (#97) and deliver the "
                   "event outbox.")
def work_tick_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from ..core import events, exceptions
    from ..workflow import engine
    from . import handover, rounds

    def work(conn, say) -> None:
        say(f"work tick: {engine.tick(conn)}")
        closed = exceptions.reconcile(conn)
        conn.commit()
        if drafted := handover.at_shift_change(conn):  # once per shift change (#467)
            say(f"shift handover {drafted['id']} drafted")
        conn.commit()
        say(f"rounds: {rounds.open_shift(conn)}")  # this shift's rounds, last shift's closed (P5.4)
        conn.commit()
        say(f"exceptions: {closed}, events: {events.deliver(conn)}")

    in_each_project(context, fuzer, work)


@dg.job(description="Work engine timers and SLAs: re-checks that are due, reminders and escalations.")
def work_tick():
    work_tick_op()


@dg.op(description="Tell people about work that moved to them and steps coming due (P5.2).")
def work_notify_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from ..workflow import engine
    with fuzer.connect() as conn:
        context.log.info(f"notify: {engine.notify_scan(conn)}")


@dg.job(description="Notifications: work sent, handed back, escalated or coming due, told to the person within a minute.")
def work_notify():
    work_notify_op()


@dg.op(description="Send the approved write-backs that are due by each system's cadence (P5.7).")
def writeback_run_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from . import writeback
    with fuzer.connect() as conn:
        context.log.info(f"write-backs: {writeback.run(conn)}")


@dg.job(description="Write-backs: approved changes sent to source systems by their cadence; nothing unapproved.")
def writeback_run():
    writeback_run_op()


@dg.op(description="Close-out re-checks that are due, and repeats folded back into the case they repeat (P6.1b).")
def closeout_sweep_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from . import disposition

    def work(conn, say) -> None:
        say(f"close-out: {disposition.sweep(conn, fuzer.pack_for(conn))}")
        conn.commit()

    in_each_project(context, fuzer, work)


@dg.job(description="Close-out: ask whether a fix held when its re-check is due; reopen a case whose problem returned.")
def closeout_sweep():
    closeout_sweep_op()


@dg.op(description="Tuning proposals for detectors whose cases keep closing as false alarms or duplicates (P6.3).")
def detector_tuning_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from . import tuning
    with fuzer.connect() as conn:
        out = tuning.propose(conn, fuzer.pack_for(conn))
        context.log.info(f"tuning: {[{k: p[k] for k in ('detector', 'param', 'class', 'proposed')} for p in out]}")
        conn.commit()


@dg.job(description="Tuning: a quieter value proposed, replayed on its closed cases, for a detector whose noise passed its threshold.")
def detector_tuning():
    detector_tuning_op()


@dg.op(description="The obligations calendar (P7.6c): reports drafted as their windows open, and work for one coming due.")
def report_calendar_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from . import reports
    with fuzer.connect() as conn:
        out = reports.run_calendar(conn, fuzer.pack_for(conn))
        context.log.info(f"report calendar: drafted {out['drafted']}, raised {[r['key'] for r in out['raised']]}, "
                         f"cleared {out['cleared']}, errors {out['errors']}")


@dg.job(description="Obligations: each regulatory report drafted ahead of its due date, and work for one coming due.")
def report_calendar():
    report_calendar_op()


@dg.op(description="Agentic SRE (#96): detect upsets in the process data since the last run.")
def sre_scan_op(context: dg.OpExecutionContext, fuzer: Fuzer, claude: ClaudeSettings) -> None:
    from ..workflow import engine
    from . import sre, sre_handoff
    with fuzer.connect() as conn:
        pack = fuzer.pack_for(conn)
        scanned = sre.scan(conn, pack)
        context.log.info(f"sre scan: {scanned}")
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
        from . import board
        context.log.info(f"debottleneck options: {hydraulics.store_options(conn, pack)}")
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
    from ..core import detectors, events

    def work(conn, say) -> None:
        out = detectors.run(conn, None, "source_health")
        conn.commit()
        say(f"source health: {out}, events: {events.deliver(conn)}")

    in_each_project(context, fuzer, work)


@dg.job(description="Source health: stale data, failed or held loads, failing checks -> exceptions, cases and alerts.")
def source_health():
    source_health_op()


def _tier_job(name: str):
    """A detector tier's job (P3.2): its detectors, each from its own watermark, then the board."""
    @dg.op(name=f"detect_{name}_op", description=f"Run the {name} tier's detectors from their watermarks (P3.2).")
    def op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
        from ..core import events
        from . import board, cadence

        def work(conn, say) -> None:
            pack = fuzer.pack_for(conn)
            out = cadence.run_tier(conn, pack, name)
            say(f"tier {name}: {out}")
            if out["ran"]:
                board.refresh(conn, pack)
            say(f"events: {events.deliver(conn)}")
            conn.commit()

        in_each_project(context, fuzer, work)

    @dg.job(name=f"detect_{name}", description=f"Detectors every {name}: each from its own watermark (P3.2).")
    def job():
        op()
    return job


DETECT = {name: _tier_job(name) for name in ("5min", "30min", "hourly", "6h", "daily")}


@dg.op(description="Read each polled detection provider's alert table when it has changed (P3.8c).")
def providers_poll_op(context: dg.OpExecutionContext, fuzer: Fuzer) -> None:
    from ..core import events
    from ..sources import providers

    def work(conn, say) -> None:
        out = providers.poll(conn, fuzer.folder())
        say(f"providers: {out}, events: {events.deliver(conn)}")
        conn.commit()

    in_each_project(context, fuzer, work)


@dg.job(description="Detection providers: polled alert tables read when changed -> findings joined to cases.")
def providers_poll():
    providers_poll_op()


# --- jobs, schedules, sensors -------------------------------------------------------------------

refresh_all = dg.define_asset_job("refresh_all", selection=dg.AssetSelection.groups("sources", "register"),
                                  description="Load every export and rebuild.")
refresh_logs = dg.define_asset_job("refresh_logs", selection=dg.AssetSelection.assets(
                                       *(["oplogs"] if "oplogs" in DATASETS else []), register),
                                   description="Load the operator logs and rebuild.")
claude_reading = dg.define_asset_job("claude_reading", selection=dg.AssetSelection.groups("claude"),
                                     description="Claude reads new log entries and drawings (costs money).")

def schedule_timezone() -> str:
    """The timezone schedules run in: the database's first project's (from its bundle), read when the code location
    loads; UTC without one. The deprecated FUZER_TIMEZONE still overrides it (projects.field)."""
    if not os.environ.get("FUZER_DATABASE_URL"):  # nothing to ask (an import in tests or tooling): no server started
        return os.environ.get("FUZER_TIMEZONE") or "UTC"
    try:
        return projects.field("timezone") or "UTC"
    except Exception:  # no database yet: the schedules still load
        return "UTC"


TZ = schedule_timezone()


def per_project(job, cron: str, status: dg.DefaultScheduleStatus, due=None) -> dg.ScheduleDefinition:
    """A schedule that starts the job once for each project, tagged with it. With `due` (conn -> bool, read in the
    project's schema), a project whose job has nothing to do at this tick starts no run: every run is a new Python
    process, and the minute jobs starting one per project per minute to find nothing held most of a one-CPU
    instance (13 Nov)."""
    @dg.schedule(name=f"{job.name}_schedule", job=job, cron_schedule=cron, execution_timezone=TZ,
                 default_status=status, required_resource_keys={"fuzer"})
    def schedule(context: dg.ScheduleEvaluationContext):
        at = context.scheduled_execution_time.isoformat()
        fuzer = context.resources.fuzer
        waiting = [project for project, _ in fuzer.targets() if not pending(context.instance, job.name, project)]
        if not waiting:
            return dg.SkipReason("the previous run for each project is still queued or running")
        requests = [dg.RunRequest(run_key=f"{project}@{at}", tags=project_tags(project))
                    for project in waiting if due is None or has_work(fuzer, project, due)]
        return requests or dg.SkipReason("nothing is due in any project")
    return schedule


def together(job, cron: str, status: dg.DefaultScheduleStatus) -> dg.ScheduleDefinition:
    """A schedule that starts the job once for every project together: its op takes them in turn (in_each_project).
    For the light jobs that run every hour or more often, where a process per project per tick cost more than the work;
    the long ones keep a run per project, so one project's rebuild neither waits for nor hides another's."""
    @dg.schedule(name=f"{job.name}_schedule", job=job, cron_schedule=cron, execution_timezone=TZ,
                 default_status=status, required_resource_keys={"fuzer"})
    def schedule(context: dg.ScheduleEvaluationContext):
        if pending(context.instance, job.name, ""):
            return dg.SkipReason("the previous run is still queued or running")
        names = [project for project, _ in context.resources.fuzer.targets() if project]
        at = context.scheduled_execution_time.isoformat()
        if len(names) < 2:
            return dg.RunRequest(run_key=f"{names[0] if names else ''}@{at}", tags=project_tags(names[0] if names else ""))
        return dg.RunRequest(run_key=f"all@{at}", tags={PROJECTS_TAG: ",".join(names)})
    return schedule


def has_work(fuzer: Fuzer, project: str, due) -> bool:
    """Whether `due` finds work for the project. A check that fails starts the run, which reports the failure."""
    try:
        with db.use(_scope_of(fuzer, project)), db.connect(url=fuzer.database_url) as conn:
            return bool(due(conn))
    except Exception:
        return True


def _notify_due(conn) -> bool:
    from ..workflow import engine
    return engine.notify_pending(conn)


def _writeback_due(conn) -> bool:
    from . import writeback
    return writeback.due(conn)


def _studies_due(conn) -> bool:
    from . import study
    return study.queued(conn)


PENDING = [dg.DagsterRunStatus.QUEUED, dg.DagsterRunStatus.NOT_STARTED, dg.DagsterRunStatus.STARTING,
           dg.DagsterRunStatus.STARTED, dg.DagsterRunStatus.CANCELING]


def pending(instance: dg.DagsterInstance, job_name: str, project: str) -> bool:
    """A run of this job for this project is still queued or running. Its schedule skips a tick rather than queue
    another behind it: every run picks up all the work pending when it starts, so a backlog of the same run only
    holds the instance's few run slots and its database."""
    return instance.get_runs_count(dg.RunsFilter(job_name=job_name, statuses=PENDING,
                                                 tags=project_tags(project))) > 0


RUNNING, STOPPED = dg.DefaultScheduleStatus.RUNNING, dg.DefaultScheduleStatus.STOPPED
schedules = [
    per_project(refresh_logs, "0 * * * *", RUNNING),
    per_project(refresh_all, "0 2 * * *", RUNNING),
    per_project(claude_reading, "0 3 * * *", STOPPED),
    together(work_tick, "*/5 * * * *", RUNNING),
    per_project(work_notify, "* * * * *", RUNNING, _notify_due),
    per_project(writeback_run, "* * * * *", RUNNING, _writeback_due),
    together(closeout_sweep, "*/5 * * * *", RUNNING),
    per_project(detector_tuning, "30 4 * * *", RUNNING),
    per_project(report_calendar, "0 5 * * *", RUNNING),
    per_project(sre_scan, "0 */6 * * *", RUNNING),
    per_project(studies_job, "*/5 * * * *", RUNNING, _studies_due),
    per_project(day_close, settings.get(None, "daily_report.close_cron"), RUNNING),
    together(source_health, "*/30 * * * *", RUNNING),
    together(providers_poll, "*/15 * * * *", RUNNING),
    *((together if name in ("5min", "30min", "hourly") else per_project)(job, CADENCE_CRON[name], RUNNING)
      for name, job in DETECT.items()),
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


# Telling someone when a scheduled job or sensor fails (R.4b), so that the board does not go stale unnoticed. A failed
# run, and a sensor or schedule whose evaluation fails, is posted once to the alert webhook (alerts.webhook), naming
# the project and the job and linking to the Jobs page when notify.app_url is set; the Jobs page counts the same
# failures (api/jobs.py health). A failing sensor or schedule starts no run, so `failed_ticks` finds it by its latest
# tick.

TICK_FAILURE = "FAILURE"


def _tick_status(v) -> str:
    return str(getattr(v, "value", v))


def _jobs_link(path: str, app: str | None = None) -> str:
    if app is None:
        try:
            from ..sources import alerts

            url = alerts.app_link(f"#{path}")
        except Exception:  # an alert must still go out when the database can't be read
            url = ""
    else:
        url = f"{app}/#{path}" if app else ""
    return f" Jobs: {url}" if url else ""


def run_failed_text(job: str, run_id: str, message: str, project: str = "", app: str | None = None) -> str:
    """What the alert channel is told about a failed run."""
    where = f" ({project})" if project else ""
    return (f"Fuzer{where}: job '{job}' failed (run {run_id[:8]}): {message.strip()}"
            f"{_jobs_link(f'/jobs/run/{run_id}', app)}")


def tick_failed_text(kind: str, name: str, error: str, app: str | None = None) -> str:
    """What the alert channel is told about a sensor or schedule whose evaluation failed."""
    return (f"Fuzer: {kind} '{name}' failed, so the jobs it starts are not running until it recovers: "
            f"{(error or 'no error message').strip()}{_jobs_link('/jobs', app)}")


def failed_ticks(instance, seen: dict[str, float], skip: frozenset[str] = frozenset()) -> tuple[list[dict], dict]:
    """Sensors and schedules whose latest tick failed since the last look (`seen`: name -> latest failed tick time
    already reported). Returns the new failures and the updated `seen`. A name that recovers is forgotten, so a
    second failure later is reported again."""
    latest: dict[str, tuple] = {}
    for state in instance.all_instigator_state():
        name = state.instigator_name
        if name in skip:
            continue
        ticks = instance.get_ticks(state.instigator_origin_id, state.selector_id, limit=1)
        if ticks and (name not in latest or ticks[0].timestamp > latest[name][1].timestamp):
            latest[name] = (state, ticks[0])
    out, now = [], {}
    for name, (state, tick) in sorted(latest.items()):
        if _tick_status(tick.status) != TICK_FAILURE:
            continue
        now[name] = tick.timestamp
        if seen.get(name, -1) < tick.timestamp:
            kind = "schedule" if _tick_status(state.instigator_type).upper() == "SCHEDULE" else "sensor"
            error = getattr(getattr(tick, "error", None), "message", "") or ""
            out.append({"kind": kind, "name": name, "at": tick.timestamp, "error": error})
    return out, now


def load_seen(cursor: str | None) -> dict[str, float]:
    try:
        return {str(k): float(v) for k, v in json.loads(cursor or "{}").items()}
    except (ValueError, AttributeError):
        return {}


@dg.run_failure_sensor(default_status=dg.DefaultSensorStatus.RUNNING,
                       description="Posts failed runs to FUZER_ALERT_WEBHOOK, and to GitHub issues, when they are set; a "
                                   "scheduled run a deploy interrupted is not a failure and is not posted.")
def alert_on_failure(context: dg.RunFailureSensorContext):
    from ..core import bugreports
    from ..sources import alerts
    from . import stops

    run = context.dagster_run
    event = context.failure_event
    record = context.instance.get_run_record_by_id(run.run_id)
    ended = (record and record.end_time) or time.time()  # when it failed, not when this sensor got to it
    # Only a scheduled run: its schedule starts it again. A run started by hand or by a sensor isn't restarted, so
    # its interruption is still posted.
    if "dagster/schedule_name" in run.tags and stops.interrupted_by_stop(event.message or "", ended):
        context.log.info(f"Run {run.run_id[:8]} of {run.job_name} was interrupted by a deploy; its schedule starts it "
                         "again, so it isn't reported as a failure")
        return
    message = run_failed_text(run.job_name, run.run_id, context.failure_event.message or "",
                                  run.tags.get(PROJECT_TAG, "") or run.tags.get(PROJECTS_TAG, ""))
    context.log.error(message)
    alerts.post(message)
    job = run.job_name
    bugreports.reporter.report(bugreports.Reported(message, f"dagster-run:{job}", kind=f"Dagster job {job} failed"),
                               "alert_on_failure", wait=True)


@dg.sensor(default_status=dg.DefaultSensorStatus.RUNNING, minimum_interval_seconds=300,
           description="Posts a sensor or schedule that failed to evaluate (and so starts no run) to "
                       "FUZER_ALERT_WEBHOOK, once per failure.")
def alert_on_tick_failure(context: dg.SensorEvaluationContext):
    from ..sources import alerts

    new, seen = failed_ticks(context.instance, load_seen(context.cursor),
                                      skip=frozenset({"alert_on_tick_failure"}))
    for f in new:
        message = tick_failed_text(f["kind"], f["name"], f["error"])
        context.log.error(message)
        alerts.post(message)
    context.update_cursor(json.dumps(seen, sort_keys=True))
    return dg.SkipReason(f"{len(new)} new failure(s) posted" if new else "no failed sensors or schedules")


defs = dg.Definitions(
    assets=[*SOURCES, register, claude_log_reads, claude_pid_reads, claude_doc_reads],
    asset_checks=[review_backlog, orphan_timeseries, tags_missing_from_sap, downtime_reason_codes, unmapped_site_codes,
                  document_revisions, flow_vs_piping, *SOURCE_CHECKS],
    jobs=[refresh_all, refresh_logs, claude_reading, work_tick, work_notify, writeback_run, closeout_sweep, detector_tuning, report_calendar, sre_scan, day_close, studies_job, source_health, providers_poll, *DETECT.values()],
    schedules=schedules,
    sensors=[new_exports, dataset_schedules, alert_on_failure, alert_on_tick_failure],
    resources={"fuzer": Fuzer(), "claude": ClaudeSettings()},
    # Each run executes its steps in its own process. The default multiprocess executor adds a second Python
    # process per step on top of the run's, which doubled every run's memory on the 2 GB instance (OOM kills,
    # 12 Nov); the instance has one CPU, so steps in parallel gained nothing.
    executor=dg.in_process_executor,
)
