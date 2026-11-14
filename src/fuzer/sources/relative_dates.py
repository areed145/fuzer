"""Dates relative to the day they are read, for hand-written synthetic data that should not go stale (#456).

A demonstration facility's exports and process model can write a date as `today`, `today-3d` or `today+14d`,
optionally with a time: `today-1d 03:10` (or `today-1dT03:10`). Read on 9 November that is `2026-11-08 03:10`.
Only a whole value is resolved, never a word inside free text, so an operator log saying "tripped today" is
left alone, and real exports, which hold real dates, are never touched.

`today` is the plant's date, the day in the project's timezone (its bundle's `timezone`, #882). The data names
plant wall-clock times and dates, and every reader takes a time without a zone as the plant's wall clock
(core/clock), so between midnight UTC and the plant's midnight the UTC date would put the plant's data a day ahead.

Values move a day at a time, not continuously, so a timestamp reads the same all day. A file holding such
values says so through `mentions()`, and its connector's fingerprint then includes the day, so the daily
refresh reads it again and the records follow the calendar.

A generated facility rolls forward the same way without tokens (#1085). Its project names the day its exports were
written as today (`rolls_from: 2025-12-31`), and every whole date or time read from them moves on by the days since
(`roll`): read on 14 November 2026, `2025-12-30T14:00` is `2026-11-13T14:00`. So its latest data day is always
yesterday, and its records, process model and historian sit on the same clock as the work Fuzer raises. Dates inside
identifiers (`OKLOG-2024-01-01-00295`) and free text are left as written: a key that moved every day would retire and
recreate its record, and changed text would be read by Claude again.
"""

import re
from datetime import date, datetime, timedelta

from ..core import clock

TOKEN = re.compile(r"today(?:(?P<sign>[+-])(?P<days>\d+)d)?(?:(?P<sep>[ T])(?P<time>\d{2}:\d{2}(?::\d{2})?))?")
# a whole CSV field or JSON string: after a delimiter or quote, up to the next one
_ANY = re.compile(rb'(?:^|[,"\t])\s*today(?:[+-]\d+d)?(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\s*(?:$|[,"\t\r])', re.MULTILINE)


def plant_today() -> date:
    """Today in the project's timezone (UTC when the project names none or no project is active)."""
    return clock.today()


def resolve(value, today: date | None = None):
    """The value with a relative date replaced by the real one; anything else unchanged."""
    if not isinstance(value, str) or "today" not in value:
        return value
    m = TOKEN.fullmatch(value.strip())
    if not m:
        return value
    d = (today or plant_today()) + timedelta(days=int(m["days"] or 0) * (-1 if m["sign"] == "-" else 1))
    return f"{d.isoformat()}{m['sep']}{m['time']}" if m["time"] else d.isoformat()


def resolve_all(obj, today: date | None = None):
    """resolve() through nested dicts and lists."""
    today = today or plant_today()
    if isinstance(obj, dict):
        return {k: resolve_all(v, today) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_all(v, today) for v in obj]
    return resolve(obj, today)


# a whole ISO date or time: 2025-12-30, 2025-12-30T14:00, 2025-12-30 14:00:05.123, 2025-12-30T14:00:00+00:00
ISO = re.compile(r"(?P<d>\d{4}-\d{2}-\d{2})(?P<rest>[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?")


def rolls_from() -> date | None:
    """The day a rolling facility's exports were written as today (its project's `rolls_from`), else None."""
    import os

    from ..core import db, projects

    try:  # only a project stored in this database rolls, never a shipped bundle's config read as a fallback
        cfg = projects._cached_config(db.scope().project or os.environ.get("FUZER_PROJECT")) or {}
        v = os.environ.get("FUZER_ROLLS_FROM") or (cfg.get("rolls_from") if cfg.get("source") == "database" else None)
    except Exception:  # no project, or no database to read it from
        return None
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def offset(today: date | None = None) -> int:
    """Days a rolling facility's dates move on today (0 when the project does not roll)."""
    anchor = rolls_from()
    return ((today or plant_today()) - anchor).days if anchor else 0


def roll(value, days: int):
    """A whole date or time moved on by `days`; anything else (numbers, identifiers, free text) unchanged."""
    if not days:
        return value
    if isinstance(value, datetime | date):
        return value + timedelta(days=days)
    if not isinstance(value, str) or len(value) < 10 or len(value) > 35 or not value[:4].isdigit():
        return value
    m = ISO.fullmatch(value)
    if not m:
        return value
    try:
        d = date.fromisoformat(m["d"]) + timedelta(days=days)
    except (ValueError, OverflowError):
        return value
    return d.isoformat() + (m["rest"] or "")


def roll_all(obj, days: int):
    """roll() through nested dicts and lists."""
    if not days:
        return obj
    if isinstance(obj, dict):
        return {k: roll_all(v, days) for k, v in obj.items()}
    if isinstance(obj, list):
        return [roll_all(v, days) for v in obj]
    return roll(obj, days)


def place(obj, today: date | None = None):
    """Hand-written settings (a bundle's evals, a pack's dated windows) on today's calendar: a rolling facility's
    dates moved on, then relative ones resolved."""
    today = today or plant_today()
    return resolve_all(roll_all(obj, offset(today)), today)


def day() -> str:
    """What a file of relative dates reads as today."""
    return plant_today().isoformat()


def mentions(raw: bytes) -> bool:
    """Whether file contents may hold a relative date (a cheap check before resolving rows)."""
    return _ANY.search(raw) is not None
