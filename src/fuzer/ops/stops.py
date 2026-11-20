"""Runs a deploy interrupts (#812 and the job failure reports after it). Every merge redeploys the instance: Render
sends the old instance SIGTERM, the supervisor stops the Dagster daemon, and a run still in flight ends as
"interrupted unexpectedly" or is "marked as failed from outside the execution context" by the run monitor. Its
schedule starts it again on the new instance within minutes, so it is not a job failure, and alerting on it buried the
real ones.

The supervisor records when it was told to stop, in the deployment's Postgres (table fuzer.deploy_stop), so the next
instance's failure sensor reads it. It used to be a file on the persistent disk; the service has no disk now, so that
Render can start the new instance before stopping the old one (zero-downtime deploys). A run failure is a deploy
interruption only when its message is one of Dagster's interruption messages and it happened around a recorded stop;
an interrupted run with no stop beside it (the instance killed for memory, a run worker that died) is still reported.
Light on imports: the supervisor never loads Dagster."""

import time

INTERRUPTED = ("Execution was interrupted unexpectedly", "marked as failed from outside the execution context")
BEFORE_SECONDS = 120  # the run monitor can see a run worker gone a poll before the supervisor's own stop lands
AFTER_SECONDS = 600  # SIGTERM, the 30 s wait for the daemon and its runs, and the container's own shutdown
KEEP = 50  # stops remembered
TABLE = "fuzer.deploy_stop"


def _connect(url: str | None = None):
    from ..core import db

    return db.connect(autocommit=True, url=url or db.database_url())


def _ensure(conn) -> None:
    conn.execute("create schema if not exists fuzer")
    conn.execute(f"create table if not exists {TABLE} (at double precision primary key)")


def mark_stopped(url: str | None = None, now: float | None = None) -> None:
    """Record that this instance was told to stop, now, beside the last few stops (two deploys can land minutes apart,
    before the failure sensor has read the first one's runs)."""
    try:
        with _connect(url) as conn:
            _ensure(conn)
            conn.execute(f"insert into {TABLE} (at) values (%s) on conflict do nothing",
                         (now if now is not None else time.time(),))
            conn.execute(f"delete from {TABLE} where at not in (select at from {TABLE} order by at desc limit %s)",
                         (KEEP,))
    except Exception as e:  # a stop that can't be recorded only means its interrupted runs are reported
        print(f"Could not record this stop ({e}); runs it interrupts will be reported as failures", flush=True)


def stops(url: str | None = None) -> list[float]:
    """The recorded stops, oldest first."""
    try:
        with _connect(url) as conn:
            _ensure(conn)
            return [r["at"] for r in conn.execute(f"select at from {TABLE} order by at").fetchall()]
    except Exception:
        return []


def interrupted_by_stop(message: str, at: float, url: str | None = None) -> bool:
    """Whether a run that failed at `at` (epoch seconds) with `message` was ended by a deploy, not by its own work."""
    if not any(m in (message or "") for m in INTERRUPTED):
        return False
    return any(t - BEFORE_SECONDS <= at <= t + AFTER_SECONDS for t in stops(url))
