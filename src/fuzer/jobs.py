"""Scheduled jobs (Dagster): load exports, rebuild the register, have Claude read new text.

    uv run fuzer jobs            # Dagster UI on http://127.0.0.1:3000

Assets: one per source export (each loads only its own file, as a full snapshot), then
`register` (the rebuild), then the Claude readers. What runs when:

- `new_exports` sensor: a changed export file in the data folder loads that source and rebuilds.
- `refresh_logs` hourly and `refresh_all` daily (02:00).
- `claude_reading` nightly, off by default because it costs money. Turn it on in the UI.
- Asset checks on the register flag data-quality problems; failed runs post to
  FUZER_ALERT_WEBHOOK (Teams / Slack / any JSON webhook) when it is set.

Settings come from the environment: FUZER_DATABASE_URL, FUZER_DATA_DIR (default data),
FUZER_PACK, FUZER_TIMEZONE (default UTC), FUZER_LLM_MODEL / FUZER_LLM_EFFORT.
"""

import json
import os
import urllib.request
from pathlib import Path

import dagster as dg

from . import db, ingest, llm_links, llm_pids, pipeline
from .pack import load_pack


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

    model: str = llm_links.DEFAULT_MODEL
    effort: str = llm_links.DEFAULT_EFFORT
    batch: bool = True  # half price; a run waits for the batch (usually under an hour)
    max_log_entries: int = 2000
    max_drawings: int = 200


def export_signature(data_dir: Path, name: str) -> str | None:
    """Cheap change marker for one dataset (its connector's fingerprint: file sizes and times, the
    files a manifest names, or a Delta version). None when the dataset isn't there."""
    c = ingest.dataset(data_dir, name)
    return c.fingerprint() if c else None


def _source_asset(name: str):
    @dg.asset(name=name, group_name="sources", description=f"Loads the {name} dataset (CSV, Parquet or Delta).")
    def load(context: dg.AssetExecutionContext, fuzer: Fuzer) -> dg.MaterializeResult:
        if (c := ingest.dataset(Path(fuzer.data_dir), name)) is None:
            context.log.warning(f"{name} not found in {fuzer.data_dir}; nothing loaded")
            return dg.MaterializeResult(metadata={"dataset": name, "records": 0, "missing": True})
        with fuzer.connect() as conn:
            stats = ingest.ingest(conn, fuzer.data_dir, [name])
        if stats["held"]:
            context.log.warning(f"{name} changed schema; held until the schema review is answered")
        return dg.MaterializeResult(metadata={"dataset": name, "connector": c.kind, "path": str(c.path),
                                              **{k: v for k, v in stats.items() if not isinstance(v, list)},
                                              "held": bool(stats["held"])})
    return load


SOURCES = [_source_asset(name) for name in ingest.export_names()]


@dg.asset(group_name="register", deps=SOURCES,
          description="Match records, choose golden values and rebuild the graph (full rebuild).")
def register(fuzer: Fuzer) -> dg.MaterializeResult:
    with fuzer.connect() as conn:
        stats = pipeline.rebuild(conn, load_pack(fuzer.pack))
    return dg.MaterializeResult(metadata={
        "entities": stats["entities"], "merged_clusters": stats["merged_clusters"], "edges": stats["edges"],
        "match_reviews": stats["match_reviews"], "conflict_reviews": stats["conflict_reviews"],
        "unresolved_edges": sum(stats["unresolved_edges"].values()),
        "seconds": dg.MetadataValue.json(stats["seconds"]),
    })


def _claude_result(stats: dict, rebuilt: bool) -> dg.MaterializeResult:
    keep = ("entries", "sheets", "sections", "stored", "failed_requests", "input_tokens", "output_tokens", "findings")
    return dg.MaterializeResult(metadata={**{k: v for k, v in stats.items() if k in keep},
                                          "model": stats.get("model", ""), "rebuilt": rebuilt,
                                          "errors": dg.MetadataValue.json(stats.get("errors", []))})


@dg.asset(group_name="claude", deps=[register],
          description="Claude reads new or changed operator log entries: links and events. Rebuilds if anything new.")
def claude_log_reads(fuzer: Fuzer, claude: ClaudeSettings) -> dg.MaterializeResult:
    pack = load_pack(fuzer.pack)
    with fuzer.connect() as conn:
        stats = llm_links.run(conn, pack, model=claude.model, effort=claude.effort, batch=claude.batch,
                              limit=claude.max_log_entries)
        rebuilt = bool(stats.get("stored"))
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
        rebuilt = bool(stats.get("stored"))
        if rebuilt:
            pipeline.rebuild(conn, pack)
    if stats.get("failed_requests") and not stats.get("stored"):
        raise dg.Failure(f"every Claude request failed: {stats.get('errors')}")
    return _claude_result(stats, rebuilt)


@dg.asset(group_name="claude", deps=[register],
          description="Claude reads new or changed document sections: failure modes and specific links.")
def claude_doc_reads(fuzer: Fuzer, claude: ClaudeSettings) -> dg.MaterializeResult:
    from . import llm_docs
    pack = load_pack(fuzer.pack)
    with fuzer.connect() as conn:
        stats = llm_docs.run(conn, pack, model=claude.model, effort=claude.effort, batch=claude.batch,
                             limit=claude.max_log_entries)
        rebuilt = bool(stats.get("stored"))
        if rebuilt:
            pipeline.rebuild(conn, pack)
    if stats.get("failed_requests") and not stats.get("stored"):
        raise dg.Failure(f"every Claude request failed: {stats.get('errors')}")
    return _claude_result(stats, rebuilt)


# --- data-quality checks on the register (warnings: they flag, they don't block) ---------------

def _scalar(conn, sql: str) -> float:
    return conn.execute(sql).fetchone()["n"] or 0


LIVE = "merged_into is null and retired_at is null"
LIVE_E = "e.merged_into is null and e.retired_at is null"


@dg.asset_check(asset=register, description="Open review questions stay a small share of the register.")
def review_backlog(fuzer: Fuzer) -> dg.AssetCheckResult:
    with fuzer.connect() as conn:
        open_items = _scalar(conn, "select count(*) as n from review_item where status = 'open'")
        reviewable = _scalar(conn, f"select count(*) as n from entity where {LIVE} and kind in ('equipment', 'tag')")
    share = open_items / reviewable if reviewable else 0
    return dg.AssetCheckResult(passed=share <= 0.05, severity=dg.AssetCheckSeverity.WARN,
                               metadata={"open_reviews": int(open_items), "share_of_tags_and_equipment": round(share, 4)})


@dg.asset_check(asset=register, description="PI points are attached to a tag or well.")
def orphan_timeseries(fuzer: Fuzer) -> dg.AssetCheckResult:
    with fuzer.connect() as conn:
        total = _scalar(conn, f"select count(*) as n from entity where kind = 'timeseries' and {LIVE}")
        orphans = conn.execute(
            f"""
            select l.label from entity e left join entity_label l using (fuzer_id)
            where e.kind = 'timeseries' and {LIVE_E}
              and not exists (select 1 from edge x where x.dst = e.fuzer_id and x.rel = 'has_timeseries')
            order by l.label
            """
        ).fetchall()
    share = len(orphans) / total if total else 0
    return dg.AssetCheckResult(passed=share <= 0.02, severity=dg.AssetCheckSeverity.WARN,
                               metadata={"orphans": len(orphans), "share": round(share, 4),
                                         "examples": dg.MetadataValue.json([r["label"] for r in orphans[:20]])})


@dg.asset_check(asset=register, description="Every tag seen in other systems exists in SAP (the master).")
def tags_missing_from_sap(fuzer: Fuzer) -> dg.AssetCheckResult:
    with fuzer.connect() as conn:
        rows = conn.execute(
            f"""
            select l.label, array_agg(distinct c.source) as seen_in
            from entity e
            join crosswalk c using (fuzer_id)
            left join entity_label l using (fuzer_id)
            where e.kind = 'tag' and {LIVE_E}
            group by e.fuzer_id, l.label
            having not ('sap' = any(array_agg(c.source)))
            order by l.label
            """
        ).fetchall()
    return dg.AssetCheckResult(passed=not rows, severity=dg.AssetCheckSeverity.WARN,
                               metadata={"count": len(rows),
                                         "examples": dg.MetadataValue.json([dict(r) for r in rows[:20]])})


@dg.asset_check(asset=register, description="Downtime reason codes agree with what the comments say.")
def downtime_reason_codes(fuzer: Fuzer) -> dg.AssetCheckResult:
    from . import queries
    with fuzer.connect() as conn:
        rows = queries.downtime_reason_checks(conn, 20)
        total = _scalar(conn, "select count(*) as n from golden where field = 'reason_check'")
    return dg.AssetCheckResult(passed=total == 0, severity=dg.AssetCheckSeverity.WARN,
                               metadata={"mismatches": int(total),
                                         "examples": dg.MetadataValue.json([dict(r) for r in rows])})


@dg.asset_check(asset=register, description="Each document number has one current revision.")
def document_revisions(fuzer: Fuzer) -> dg.AssetCheckResult:
    from . import queries
    with fuzer.connect() as conn:
        rows = queries.documents_with_several_current_revisions(conn)
    return dg.AssetCheckResult(passed=not rows, severity=dg.AssetCheckSeverity.WARN,
                               metadata={"count": len(rows), "examples": dg.MetadataValue.json([dict(r) for r in rows])})


@dg.asset_check(asset=register, description="Energy Components flows are backed by P&ID piping between the same tags.")
def flow_vs_piping(fuzer: Fuzer) -> dg.AssetCheckResult:
    from . import queries
    with fuzer.connect() as conn:
        diff = queries.compare_networks(conn, limit=20)
    gaps = len(diff["in_ec_not_on_pids"])
    return dg.AssetCheckResult(passed=gaps == 0, severity=dg.AssetCheckSeverity.WARN,
                               metadata={"in_ec_not_on_pids": gaps, "on_pids_not_in_ec": len(diff["on_pids_not_in_ec"]),
                                         "examples": dg.MetadataValue.json([dict(r) for r in diff["in_ec_not_on_pids"]])})


# --- jobs, schedules, sensors -------------------------------------------------------------------

refresh_all = dg.define_asset_job("refresh_all", selection=dg.AssetSelection.groups("sources", "register"),
                                  description="Load every export and rebuild.")
refresh_logs = dg.define_asset_job("refresh_logs", selection=dg.AssetSelection.assets("oplogs", register),
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
]


@dg.sensor(job=refresh_all, minimum_interval_seconds=60, default_status=dg.DefaultSensorStatus.RUNNING,
           description="Loads any export file that changed in the data folder, then rebuilds.")
def new_exports(context: dg.SensorEvaluationContext, fuzer: Fuzer):
    data_dir = Path(fuzer.data_dir)
    seen = json.loads(context.cursor) if context.cursor else {}
    current = {name: sig for name in ingest.export_names() if (sig := export_signature(data_dir, name))}
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
    if url := os.environ.get("FUZER_ALERT_WEBHOOK"):
        req = urllib.request.Request(url, data=json.dumps({"text": message}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)


defs = dg.Definitions(
    assets=[*SOURCES, register, claude_log_reads, claude_pid_reads, claude_doc_reads],
    asset_checks=[review_backlog, orphan_timeseries, tags_missing_from_sap, downtime_reason_codes,
                  document_revisions, flow_vs_piping],
    jobs=[refresh_all, refresh_logs, claude_reading],
    schedules=schedules,
    sensors=[new_exports, alert_on_failure],
    resources={"fuzer": Fuzer(), "claude": ClaudeSettings()},
)
