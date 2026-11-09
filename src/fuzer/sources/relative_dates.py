"""Dates relative to the day they are read, for hand-written synthetic data that should not go stale (#456).

A demonstration facility's exports and process model can write a date as `today`, `today-3d` or `today+14d`,
optionally with a time: `today-1d 03:10` (or `today-1dT03:10`). Read on 9 November that is `2026-11-08 03:10`.
Only a whole value is resolved, never a word inside free text, so an operator log saying "tripped today" is
left alone, and real exports, which hold real dates, are never touched.

Values move a day at a time, not continuously, so a timestamp reads the same all day. A file holding such
values says so through `mentions()`, and its connector's fingerprint then includes the day, so the daily
refresh reads it again and the records follow the calendar.
"""

import re
from datetime import date, timedelta

TOKEN = re.compile(r"today(?:(?P<sign>[+-])(?P<days>\d+)d)?(?:(?P<sep>[ T])(?P<time>\d{2}:\d{2}(?::\d{2})?))?")
# a whole CSV field or JSON string: after a delimiter or quote, up to the next one
_ANY = re.compile(rb'(?:^|[,"\t])\s*today(?:[+-]\d+d)?(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\s*(?:$|[,"\t\r])', re.MULTILINE)


def resolve(value, today: date | None = None):
    """The value with a relative date replaced by the real one; anything else unchanged."""
    if not isinstance(value, str) or "today" not in value:
        return value
    m = TOKEN.fullmatch(value.strip())
    if not m:
        return value
    d = (today or date.today()) + timedelta(days=int(m["days"] or 0) * (-1 if m["sign"] == "-" else 1))
    return f"{d.isoformat()}{m['sep']}{m['time']}" if m["time"] else d.isoformat()


def resolve_all(obj, today: date | None = None):
    """resolve() through nested dicts and lists."""
    today = today or date.today()
    if isinstance(obj, dict):
        return {k: resolve_all(v, today) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_all(v, today) for v in obj]
    return resolve(obj, today)


def mentions(raw: bytes) -> bool:
    """Whether file contents may hold a relative date (a cheap check before resolving rows)."""
    return _ANY.search(raw) is not None
