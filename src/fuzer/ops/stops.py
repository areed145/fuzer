"""Runs a deploy interrupts (#812 and the job failure reports after it). Every merge redeploys the instance: Render
sends it SIGTERM, the supervisor stops the Dagster daemon, and a run still in flight ends as "interrupted
unexpectedly" or is "marked as failed from outside the execution context" by the run monitor. Its schedule starts it
again on the new instance within minutes, so it is not a job failure, and alerting on it buried the real ones.

The supervisor records when it was told to stop (in $DAGSTER_HOME, on the persistent disk, so the next instance's
failure sensor reads it). A run failure is a deploy interruption only when its message is one of Dagster's
interruption messages and it happened around a recorded stop; an interrupted run with no stop beside it (the
instance killed for memory, a run worker that died) is still reported. Light on imports: the supervisor never loads
Dagster."""

import os
import time
from pathlib import Path

MARKER = "stops"
INTERRUPTED = ("Execution was interrupted unexpectedly", "marked as failed from outside the execution context")
BEFORE_SECONDS = 120  # the run monitor can see a run worker gone a poll before the supervisor's own stop lands
AFTER_SECONDS = 600  # SIGTERM, the 30 s wait for the daemon and its runs, and the container's own shutdown
KEEP = 50  # stops remembered


def _home(home: Path | str | None = None) -> Path | None:
    h = home or os.environ.get("DAGSTER_HOME")
    return Path(h) if h else None


def mark_stopped(home: Path | str | None = None, now: float | None = None) -> None:
    """Record that this instance was told to stop, now, beside the last few stops (two deploys can land minutes apart,
    before the failure sensor has read the first one's runs)."""
    if (h := _home(home)) is None:
        return
    try:
        h.mkdir(parents=True, exist_ok=True)
        kept = stops(h)[-(KEEP - 1):]
        (h / MARKER).write_text("".join(f"{t:.3f}\n" for t in [*kept, now if now is not None else time.time()]))
    except OSError:
        pass  # a stop that can't be recorded only means its interrupted runs are reported


def stops(home: Path | str | None = None) -> list[float]:
    """The recorded stops, oldest first."""
    if (h := _home(home)) is None:
        return []
    try:
        lines = (h / MARKER).read_text().split()
    except OSError:
        return []
    out = []
    for x in lines:
        try:
            out.append(float(x))
        except ValueError:
            continue
    return out


def interrupted_by_stop(message: str, at: float, home: Path | str | None = None) -> bool:
    """Whether a run that failed at `at` (epoch seconds) with `message` was ended by a deploy, not by its own work."""
    if not any(m in (message or "") for m in INTERRUPTED):
        return False
    return any(t - BEFORE_SECONDS <= at <= t + AFTER_SECONDS for t in stops(home))
