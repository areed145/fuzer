"""The Jobs page (#208): what the scheduled Dagster jobs did, read from Dagster's own instance.

The UI never talks to Dagster. These routes open the instance in $DAGSTER_HOME (default .dagster, where
`fuzer up` / `fuzer jobs` keep it) read-only and pair it with the job, schedule, sensor and check
definitions in fuzer.ops.jobs. When there is no instance, or the daemon isn't running, they say so instead
of failing.
"""

import os
import time
import urllib.request
from datetime import UTC, datetime
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException

from ..resources import ROOT
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)

DAY = 24 * 3600
HEARTBEAT_STALE = 180  # seconds without a scheduler heartbeat before we call the daemon stopped
CLAUDE_JOB = "claude_reading"


# --- where the data comes from (overridden in tests) ------------------------------------------------

def dagster_home() -> Path:
    return Path(os.environ.get("DAGSTER_HOME") or ROOT / ".dagster")


@lru_cache(maxsize=1)
def _instance_at(home: str):
    from dagster import DagsterInstance

    return DagsterInstance.from_config(home)


def get_instance():
    """Dagster's instance, or None when Dagster has never run here."""
    home = dagster_home()
    if not home.is_dir():
        return None
    return _instance_at(str(home))


@lru_cache(maxsize=1)
def _definitions():
    from ..ops import jobs

    return jobs.defs


def get_definitions():
    return _definitions()


def dagster_url() -> str:
    return os.environ.get("FUZER_DAGSTER_URL", "http://127.0.0.1:3000").rstrip("/")


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/server_info", timeout=0.4):
            return True
    except Exception:
        return False


def get_dagster_link():
    url = dagster_url()
    if os.environ.get("FUZER_PUBLIC_URL"):
        try:
            host = urlsplit(url).hostname
            try:
                address = ip_address(host)
                local_host = address.is_loopback or address.is_unspecified
            except ValueError:
                local_host = host == "localhost" or (host or "").endswith(".localhost")
            if local_host:
                return {"url": "", "reachable": False}
        except ValueError:
            pass
    return {"url": url, "reachable": _reachable(url)}


# --- helpers ----------------------------------------------------------------------------------------

def _plain(v: Any) -> Any:
    """Dagster metadata values -> JSON-friendly values."""
    v = getattr(v, "value", v)
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def _metadata(md) -> dict:
    return {k: _plain(v) for k, v in (md or {}).items()}


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts else None


def _enum(v) -> str | None:
    return getattr(v, "value", v) if v is not None else None


def _run_summary(rec) -> dict:
    run = rec.dagster_run
    start, end = rec.start_time, rec.end_time
    return {
        "run_id": run.run_id,
        "job": run.job_name,
        "status": _enum(run.status),
        "start": _iso(start),
        "end": _iso(end),
        "seconds": round(end - start, 1) if start and end else None,
        "trigger": run.tags.get("dagster/schedule_name") or run.tags.get("dagster/sensor_name") or "manual",
    }


def _runs(instance, job: str | None = None, limit: int = 10, **filters) -> list:
    from dagster import RunsFilter

    f = RunsFilter(job_name=job, **filters) if (job or filters) else None
    return list(instance.get_run_records(filters=f, limit=limit))


def _states(instance) -> dict:
    """Latest instigator state per schedule / sensor name (old code locations leave stale duplicates)."""
    best: dict[str, tuple] = {}
    for s in instance.all_instigator_state():
        ticks = instance.get_ticks(s.instigator_origin_id, s.selector_id, limit=1)
        tick = ticks[0] if ticks else None
        ts = tick.timestamp if tick else -1
        if s.instigator_name not in best or ts > best[s.instigator_name][2]:
            best[s.instigator_name] = (s, tick, ts)
    return {name: (s, tick) for name, (s, tick, _) in best.items()}


def _tick(tick) -> dict | None:
    if tick is None:
        return None
    err = getattr(tick, "error", None)
    return {"status": _enum(tick.status), "time": _iso(tick.timestamp), "skip_reason": tick.skip_reason,
            "run_keys": list(getattr(tick, "run_keys", []) or []), "error": getattr(err, "message", None)}


def _status(state, default) -> str:
    """'on' or 'off'. A state declared in code follows the definition's default."""
    s = _enum(getattr(state, "status", None))
    if s == "RUNNING":
        return "on"
    if s == "STOPPED":
        return "off"
    return "on" if _enum(default) == "RUNNING" else "off"


def _next_tick(cron: str, tz: str | None) -> str | None:
    try:
        from dagster._utils.schedules import cron_string_iterator

        return next(cron_string_iterator(time.time(), cron, tz)).isoformat()
    except Exception:
        return None


def scheduler(instance) -> dict:
    beats = instance.get_daemon_heartbeats() or {}
    beat = beats.get("SCHEDULER")
    ts = getattr(beat, "timestamp", None)
    running = ts is not None and time.time() - ts < HEARTBEAT_STALE
    return {"running": running, "last_heartbeat": _iso(ts)}


def _check_level(instance, rec) -> tuple[str, dict, str | None]:
    """(pass / warn / fail / running / not_run, metadata, a note on what happened).

    Dagster records SUCCEEDED (passed) or FAILED (didn't pass) with the evaluation; PLANNED means no
    evaluation yet, which after the run has ended means the check crashed."""
    if rec is None:
        return "not_run", {}, None
    status = _enum(rec.status)
    data = getattr(rec, "evaluation", None)
    if status == "PLANNED" or data is None:
        run = instance.get_run_record_by_id(rec.run_id) if rec.run_id else None
        run_status = _enum(run.dagster_run.status) if run else None
        if run_status in ("FAILURE", "CANCELED", "SUCCESS"):
            return "fail", {}, f"the check didn't finish (run {run_status.lower()})"
        return "running", {}, "the check is running"
    md = _metadata(data.metadata)
    if data.passed:
        return "pass", md, None
    return ("warn" if _enum(data.severity) == "WARN" else "fail"), md, None


def checks(instance, defs) -> list:
    specs = [spec for checks_def in defs.asset_checks or [] for spec in checks_def.check_specs]
    latest = instance.event_log_storage.get_latest_asset_check_execution_by_key([s.key for s in specs])
    out = []
    for spec in specs:
        rec = latest.get(spec.key)
        level, md, note = _check_level(instance, rec)
        out.append({
            "name": spec.name, "asset": spec.asset_key.to_user_string(), "description": spec.description,
            "level": level, "metadata": md, "note": note,
            "time": _iso(rec.create_timestamp) if rec else None, "run_id": rec.run_id if rec else None,
        })
    return out


def health(instance, defs, check_rows: list | None = None) -> dict:
    """green / amber / red for the nav: failed runs in the last 24 h are red, failing checks or a stopped
    scheduler amber."""
    if instance is None:
        return {"level": "unknown", "available": False, "failed_runs_24h": 0, "failing_checks": 0,
                "scheduler_running": False, "summary": "Dagster hasn't run here yet"}
    from dagster import DagsterRunStatus

    since = datetime.fromtimestamp(time.time() - DAY, UTC)
    failed = len(_runs(instance, limit=100, statuses=[DagsterRunStatus.FAILURE], updated_after=since))
    rows = checks(instance, defs) if check_rows is None else check_rows
    failing = sum(1 for c in rows if c["level"] in ("warn", "fail"))
    sched = scheduler(instance)["running"]
    level = "red" if failed else "amber" if (failing or not sched) else "green"
    parts = []
    if failed:
        parts.append(f"{failed} failed run{'s' * (failed != 1)} in 24 h")
    if failing:
        parts.append(f"{failing} check{'s' * (failing != 1)} flagging")
    if not sched:
        parts.append("scheduler not running")
    return {"level": level, "available": True, "failed_runs_24h": failed, "failing_checks": failing,
            "scheduler_running": sched, "summary": "; ".join(parts) or "All jobs healthy"}


def _claude_spend(instance, defs) -> dict:
    from ..core.pricing import cost

    limits = (getattr(defs, "resources", None) or {}).get("claude")  # the ClaudeSettings the jobs run with
    batch = bool(getattr(limits, "batch", False))
    runs = []
    for rec in _runs(instance, CLAUDE_JOB, limit=20):
        mats = _materializations(instance, rec.dagster_run.run_id)
        tin = sum(int(m["metadata"].get("input_tokens") or 0) for m in mats)
        tout = sum(int(m["metadata"].get("output_tokens") or 0) for m in mats)
        tcached = sum(int(m["metadata"].get("cache_read_tokens") or 0) for m in mats)
        model = next((m["metadata"].get("model") for m in mats if m["metadata"].get("model")), "")
        provider = next((m["metadata"].get("provider") for m in mats if m["metadata"].get("provider")),
                        "anthropic")
        usd = cost(model, tin, tout, tcached, provider=provider)[0] if (tin or tout) else 0.0
        if batch and provider == "anthropic":
            usd /= 2  # batch requests are half price
        runs.append({**_run_summary(rec), "model": model, "input_tokens": tin, "output_tokens": tout,
                     "cache_read_tokens": tcached,
                     "provider": provider, "usd": round(usd, 2)})
    sched = next((s for s in defs.schedules or [] if s.job_name == CLAUDE_JOB), None)
    state = _states(instance).get(sched.name) if sched else None
    return {
        "providers": sorted({r["provider"] for r in runs}),
        "schedule": _status(state[0] if state else None, sched.default_status) if sched else "none",
        "runs": runs,
        "usd_30d": round(sum(r["usd"] for r in runs if r["start"] and r["start"] >= _iso(time.time() - 30 * DAY)), 2),
        "limits": {k: getattr(limits, k) for k in ("max_log_entries", "max_drawings", "max_upset_diagnoses",
                                                    "max_sre_usd", "batch") if hasattr(limits, k)},
    }


def _materializations(instance, run_id: str) -> list:
    from dagster import DagsterEventType

    out = []
    conn = instance.get_records_for_run(run_id, of_type=DagsterEventType.ASSET_MATERIALIZATION)
    for r in conn.records:
        m = r.event_log_entry.dagster_event.event_specific_data.materialization
        out.append({"asset": m.asset_key.to_user_string(), "time": _iso(r.event_log_entry.timestamp),
                    "metadata": _metadata(m.metadata)})
    return out


# --- routes -----------------------------------------------------------------------------------------

@router.get("/jobs/health")
def jobs_health(instance=Depends(get_instance), defs=Depends(get_definitions)):
    """Compact status for the nav indicator."""
    return health(instance, defs)


@router.get("/jobs/tiers")
def jobs_tiers(conn=Depends(get_conn)):
    """Each detector tier (P3.2): its detectors, last run, and over the last day its runs, seconds and findings."""
    from ..ops import cadence

    return {"tiers": cadence.summary(conn)}


@router.get("/jobs")
def jobs_overview(instance=Depends(get_instance), defs=Depends(get_definitions), link=Depends(get_dagster_link)):
    """Every job with its triggers, on/off state, last and recent runs; sensors; asset checks; Claude spend."""
    if instance is None:
        return {"available": False, "dagster": link, "health": health(None, defs), "jobs": [], "sensors": [],
                "checks": [], "claude": None, "scheduler": {"running": False, "last_heartbeat": None}}
    sched = scheduler(instance)
    states = _states(instance)

    triggers: dict[str, list] = {}
    for s in defs.schedules or []:
        state, tick = states.get(s.name, (None, None))
        on = _status(state, s.default_status)
        triggers.setdefault(s.job_name, []).append({
            "kind": "schedule", "name": s.name, "cron": s.cron_schedule, "timezone": s.execution_timezone,
            "status": on, "last_tick": _tick(tick),
            "next_tick": _next_tick(s.cron_schedule, s.execution_timezone) if on == "on" and sched["running"] else None,
        })
    sensors = []
    for s in defs.sensors or []:
        state, tick = states.get(s.name, (None, None))
        try:
            target = s.job_name
        except Exception:
            target = None
        row = {"kind": "sensor", "name": s.name, "description": s.description, "job": target,
               "status": _status(state, s.default_status), "last_tick": _tick(tick)}
        sensors.append(row)
        if target:
            triggers.setdefault(target, []).append(row)

    jobs = []
    for j in defs.jobs or []:
        recent = [_run_summary(r) for r in _runs(instance, j.name, limit=10)]
        jobs.append({"name": j.name, "description": j.description, "triggers": triggers.get(j.name, []),
                     "last_run": recent[0] if recent else None, "recent_runs": recent})
    check_rows = checks(instance, defs)
    return {"available": True, "dagster": link, "scheduler": sched, "health": health(instance, defs, check_rows),
            "jobs": jobs, "sensors": sensors, "checks": check_rows,
            "claude": _claude_spend(instance, defs)}


@router.get("/jobs/runs/{run_id}")
def job_run(run_id: str, instance=Depends(get_instance)):
    """One run: status, timing, tags, materialized assets with their metadata, errors and the log's tail."""
    from dagster import DagsterEventType, RunsFilter

    if instance is None:
        raise HTTPException(404, "Dagster hasn't run here yet")
    recs = instance.get_run_records(filters=RunsFilter(run_ids=[run_id]), limit=1)
    if not recs:
        raise HTTPException(404, f"no run {run_id}")
    rec = recs[0]
    run = rec.dagster_run
    tags = {k: v for k, v in run.tags.items() if not k.startswith(".dagster")}

    errors, log = [], []
    for e in instance.all_logs(run_id):
        ev = e.dagster_event
        if ev is not None and ev.event_type in (DagsterEventType.STEP_FAILURE, DagsterEventType.RUN_FAILURE):
            err = _root_cause(getattr(ev.event_specific_data, "error", None))
            errors.append({"step": ev.step_key, "kind": ev.event_type.value,
                           "message": (getattr(err, "message", None) or e.message or "").strip(),
                           "error_class": getattr(err, "cls_name", None),
                           "stack": "".join(getattr(err, "stack", None) or [])[-4000:] or None})
        log.append({"time": _iso(e.timestamp), "level": e.level if isinstance(e.level, str) else _level_name(e.level),
                    "step": e.step_key, "message": e.user_message or e.message})
    return {**_run_summary(rec), "tags": tags, "assets": _materializations(instance, run_id),
            "errors": errors, "log_tail": log[-40:]}


def _root_cause(err):
    """Dagster wraps the op's exception ("Error occurred while executing op ..."); show the one underneath."""
    while err is not None and getattr(err, "cause", None) is not None:
        err = err.cause
    return err


def _level_name(level) -> str:
    import logging

    return logging.getLevelName(level) if isinstance(level, int) else str(level)
