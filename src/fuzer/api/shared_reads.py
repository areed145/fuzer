"""One computation serves everyone who asks for the same read until the database changes (#1069).

A case's alarms and log around its event, and a metric's weekly history, are computed from the historian and the work
tables: a fifth of a second of pure-Python CPU each on a quiet laptop. On the shared 1-CPU server five people opening
the same case paid it five times over while holding the interpreter, and the page took 8 to 17 s. Reads asked for
together wait for one computation, and its answer serves later askers until anything is written to the database (its
WAL position moves) or SECONDS pass."""

import threading
import time
from collections.abc import Callable
from typing import Any

from ..core import db

SECONDS = 300.0  # an upper bound even when nothing is written; 0 turns sharing off

_values: dict[tuple, tuple[float, str, Any]] = {}
_locks: dict[tuple, threading.Lock] = {}
_guard = threading.Lock()


def _position(conn) -> str:
    row = conn.execute("select pg_current_wal_lsn()::text as position").fetchone()
    return row["position"] if isinstance(row, dict) else row[0]


def shared(conn, name: str, key: tuple, read: Callable[[], Any]) -> Any:
    """`read()`, or the answer it gave someone else for the same `name` and `key` in this project with nothing written
    since. Callers must not change what it returns."""
    if SECONDS <= 0:
        return read()
    k = (name, db.database_url(), db.scope().schema, *key)
    position = _position(conn)
    with _guard:
        now = time.monotonic()
        for old in [x for x, (until, _, _) in _values.items() if until <= now]:
            del _values[old]
        hit = _values.get(k)
        if hit and hit[1] == position:
            return hit[2]
        lock = _locks.setdefault(k, threading.Lock())
    with lock:
        with _guard:
            hit = _values.get(k)
            if hit and hit[1] == position and hit[0] > time.monotonic():
                return hit[2]
        value = read()
        with _guard:
            _values[k] = (time.monotonic() + SECONDS, position, value)
        return value
