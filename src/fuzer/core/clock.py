"""The plant's clock (#882). Sources write times without a zone, and those are the plant's wall clock: `2026-11-12
07:00` at a plant in Denver is 07:00 there. Code that meets a naive time makes it aware in the plant's zone, and code that
compares plant data with "now" uses the plant's wall clock, never the server's.

    zone()        the project's timezone (its bundle's `timezone`; UTC when none is set or no project is active)
    aware(t)      a naive time as the plant's wall clock; an aware one unchanged
    now()         the plant's wall clock now, naive (to compare with naive plant data)
    today()       the plant's date

Postgres sessions run in the same zone (core/db.connect), so a naive time or a date cast to timestamptz there is
the plant's too.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = ZoneInfo("UTC")


def name() -> str:
    """The project's timezone name, or UTC."""
    try:
        from . import projects

        tz = projects.field("timezone")
    except Exception:  # no project, or no database to read it from
        tz = None
    try:
        ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"
    return tz or "UTC"


def zone() -> ZoneInfo:
    return ZoneInfo(name())


def aware(t: datetime) -> datetime:
    """A naive time read as the plant's wall clock."""
    return t if t.tzinfo else t.replace(tzinfo=zone())


def wall(v) -> datetime | None:
    """A recorded time (an ISO string or a datetime, with or without a zone) as the plant's wall clock, without a
    zone; None when it is empty or not a time. A source that writes "10:00+00:00" or "10:00Z" and one that writes
    "10:00:00" both come back naive, so they compare (#1107)."""
    if not v:
        return None
    if not isinstance(v, datetime):
        try:
            v = datetime.fromisoformat(str(v).strip())
        except ValueError:
            return None
    return v.astimezone(zone()).replace(tzinfo=None) if v.tzinfo else v


def now() -> datetime:
    """The plant's wall clock now, without a zone."""
    return datetime.now(zone()).replace(tzinfo=None)


def today() -> date:
    return datetime.now(zone()).date()
