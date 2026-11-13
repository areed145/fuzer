"""Cadence by criticality (P3.2, #757): each scheduled detector runs in a tier, not all of them every six hours.
Minutes for trips, hourly for machine health, daily for records and opportunities; the window detectors (excursions,
steps, drifts, models) stay in the SRE scan, which keeps its own watermark.

    TIERS                         5min, 30min, hourly, 6h, daily: the schedule each tier's job runs on
    tier(conn, d)                 a detector's tier: `detectors.cadence@<name>` (per asset, in the settings history),
                                  else the cadence it declares
    members(conn, tier)           the detectors that run in a tier's job
    run_tier(conn, pack, tier)    run them, each from its own watermark (the end of its last good run) to now
    summary(conn)                 each tier's detectors, last run, runs and seconds over the last day, findings, failures

A detector that reads a window (`since`, `until`) resumes where its last good run ended, less half an hour's
overlap (a stop needs the running before it), from the 10-minute mark, so a condition is placed at the same time on
every run and raised once, and a missed run catches up. One that fails keeps its watermark, so the
next run reads the window again.
"""

import inspect
from datetime import datetime, timedelta

from ..core import clock, detectors, settings

TIERS = {"5min": "*/5 * * * *", "30min": "*/30 * * * *", "hourly": "7 * * * *", "6h": "20 */6 * * *",
         "daily": "40 2 * * *"}
PERIOD = {"5min": timedelta(minutes=5), "30min": timedelta(minutes=30), "hourly": timedelta(hours=1),
          "6h": timedelta(hours=6), "daily": timedelta(days=1)}
OWN_JOB = {"source_health"}  # detectors a job of their own runs (ops.jobs.source_health)
OVERLAP = timedelta(minutes=30)  # read again before each window: a stop needs the running before it in the window
GRID = 10  # minutes: windows start on the 10-minute mark, so a condition is placed at the same time on every run
FIRST = timedelta(hours=6)  # a detector with no watermark reads this far back
MAX_BACK = timedelta(days=7)  # and never further, however long it was off


def tier(conn, d: detectors.Detector) -> str:
    """The tier a detector runs in on this asset."""
    chosen = settings.get(conn, "detectors.cadence", qualifier=d.name)
    return chosen or d.cadence


def members(conn, name: str) -> list[detectors.Detector]:
    """The detectors a tier's job runs: scheduled ones that aren't window detectors or run by a job of their own."""
    if name not in TIERS:
        raise detectors.DetectorError(f"no tier {name!r}; one of {', '.join(TIERS)}")
    return [d for d in detectors.registry().values()
            if not d.window and d.name not in OWN_JOB and d.cadence != "request" and tier(conn, d) == name]


def watermark(conn, name: str) -> datetime | None:
    """The end of the detector's last good run."""
    r = conn.execute("select max(until) as t from detector_run where detector = %s and status = %s",
                     (name, detectors.RAN)).fetchone()
    return r["t"]


def _now(conn) -> datetime:
    """Now, or the end of the data's today for a fake plant whose history stops earlier (as the SRE scan reads it)."""
    from ..graph import operations

    return min(clock.now().replace(second=0, microsecond=0),
               datetime.fromisoformat(operations.as_of(conn)) + timedelta(days=1))


def run_tier(conn, pack, name: str, now: datetime | None = None) -> dict:
    """Run a tier's detectors once; commits after each. A detector that fails is reported and the rest still run."""
    import time

    end = now or _now(conn)
    out = {"tier": name, "until": end.isoformat(timespec="minutes"), "ran": {}, "failed": {}}
    t0 = time.perf_counter()
    for d in members(conn, name):
        kw = {}
        takes = inspect.signature(d.fn).parameters
        if "since" in takes and "until" in takes:
            mark = watermark(conn, d.name)
            start = max(mark - OVERLAP if mark else end - FIRST, end - MAX_BACK)
            start = start.replace(minute=start.minute - start.minute % GRID, second=0, microsecond=0)
            if start >= end:
                continue
            kw = {"since": start.isoformat(timespec="minutes"), "until": end.isoformat(timespec="minutes")}
        try:
            res = detectors.run(conn, pack, d.name, **kw)
        except Exception as e:  # recorded on detector_run by detectors.run; the watermark stays where it was
            out["failed"][d.name] = str(e)[:200]
            continue
        conn.commit()
        out["ran"][d.name] = res.get("status") or (d.found(res) if d.found else "ran")
    out["seconds"] = round(time.perf_counter() - t0, 2)
    conn.execute("insert into detector_tier_run (tier, until, seconds, ran, failed) values (%s, %s, %s, %s, %s)",
                 (name, end, out["seconds"], len(out["ran"]), len(out["failed"])))
    conn.commit()
    return out


def summary(conn) -> list[dict]:
    """Each tier on this asset: its detectors, its last run, and over the last day its runs, seconds and findings."""
    runs = {r["tier"]: dict(r) for r in conn.execute(
        """
        select tier, count(*)::int as runs, coalesce(sum(seconds), 0)::float as seconds,
               coalesce(avg(seconds), 0)::float as avg_seconds, max(at) as last_at,
               coalesce(sum(failed), 0)::int as failures
        from detector_tier_run where at > now() - interval '1 day' group by tier
        """)}
    found = {r["detector"]: r["found"] for r in conn.execute(
        "select detector, coalesce(sum(found), 0)::int as found from detector_run "
        "where at > now() - interval '1 day' group by detector")}
    out = []
    for name, cron in TIERS.items():
        ds = members(conn, name)
        r = runs.get(name, {})
        out.append({"tier": name, "label": detectors.CADENCES[name], "cron": cron,
                    "detectors": [{"name": d.name, "title": d.title} for d in ds],
                    "runs": r.get("runs", 0), "seconds": round(r.get("seconds", 0.0), 1),
                    "avg_seconds": round(r.get("avg_seconds", 0.0), 2), "last_at": r.get("last_at"),
                    "failures": r.get("failures", 0), "found": sum(found.get(d.name, 0) for d in ds)})
    return out
