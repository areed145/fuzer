"""Named windows (docs/reports.md): the one place a report window's name and its end day become dates.

    resolve(window, at=None) -> {"key", "at", "start", "end", "days", "label", "previous": {"start", "end"}}

`1d`, `7d`, `30d` and `90d` are that many days ending on `at`; `qtd` and `ytd` run from the start of `at`'s quarter
or year. Both ends are whole days and inclusive. `previous` is the window of the same length just before, which a
stat compares with.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

WINDOWS = ("1d", "7d", "30d", "90d", "qtd", "ytd")
DAYS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}


class WindowError(ValueError):
    """A window name that isn't one of WINDOWS."""


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def resolve(window: str, at: date | str | None = None) -> dict:
    """A named window ending on `at` (today when None) as dates."""
    if window not in WINDOWS:
        raise WindowError(f"no window {window!r}; it is one of {', '.join(WINDOWS)}")
    if isinstance(at, str):
        at = date.fromisoformat(at[:10])
    at = at or date.today()
    if window in DAYS:
        start = at - timedelta(days=DAYS[window] - 1)
        label = f"{_day(at)}" if window == "1d" else f"{DAYS[window]} days to {_day(at)}"
    elif window == "qtd":
        start = date(at.year, 3 * ((at.month - 1) // 3) + 1, 1)
        label = f"Quarter to {_day(at)}"
    else:
        start = date(at.year, 1, 1)
        label = f"Year to {_day(at)}"
    days = (at - start).days + 1
    before = start - timedelta(days=1)
    return {"key": window, "at": at.isoformat(), "start": start.isoformat(), "end": at.isoformat(), "days": days,
            "label": label, "previous": {"start": (before - timedelta(days=days - 1)).isoformat(),
                                         "end": before.isoformat()}}


# ---- measures read a window as times (core/metrics.py) ---------------------------------------------------------------
#
#     span(window, at, now)   Span(key, start, end, at, days): [start, end) as plant wall-clock times; a window ending
#                             today ends now, not at midnight. Besides WINDOWS it takes `<n>d` for any n up to MAX_DAYS
#                             (a measure's own period, such as production efficiency's 14 days) and `mtd`.
#     before(s)               the span of the same length just before it (what a stat's change is against)
#     steps(s, n)             the n spans of its length ending on its day, oldest first (a stat's sparkline)
#     daily(s)                each day in it as its own one-day span (a trend's daily points)
#     parse_time(v)           a source's time as plant wall clock
#
# A measure declares its cut-off, what `at` is when nobody gives one: `day` is today; `production_day` is the last day
# with a production report, as the daily report's day; `rebuild` is the latest rebuild, for scores kept with it.


CUTOFFS = ("day", "production_day", "rebuild")
MAX_DAYS = 731
_NDAYS = re.compile(r"^([1-9][0-9]{0,2})d$")


@dataclass(frozen=True)
class Span:
    key: str
    start: datetime  # inclusive, plant wall clock (naive)
    end: datetime  # exclusive; now, when the window ends today
    at: date  # the day it ends on
    days: int

    def holds(self, t: datetime | None) -> bool:
        return t is not None and self.start <= t < self.end


def valid(window: str | None) -> bool:
    if window in WINDOWS or window == "mtd":
        return True
    m = _NDAYS.match(window or "")
    return bool(m) and int(m.group(1)) <= MAX_DAYS


def _first(window: str, at: date) -> date:
    if window == "mtd":
        return at.replace(day=1)
    if window in ("qtd", "ytd"):
        return date.fromisoformat(resolve(window, at)["start"])
    return at - timedelta(days=int(window[:-1]) - 1)


def _close(key: str, start: date, at: date, now: datetime | None) -> Span:
    end = datetime.combine(at + timedelta(days=1), time())
    if now is not None and now.date() == at and now < end:
        end = now
    return Span(key, datetime.combine(start, time()), end, at, (at - start).days + 1)


def span(window: str, at: date, now: datetime | None = None) -> Span:
    """The window named `window` ending on the day `at`, as times."""
    if not valid(window):
        raise WindowError(f"no window {window!r}; it is one of {', '.join(WINDOWS)}")
    return _close(window, _first(window, at), at, now)


def before(s: Span) -> Span:
    at = s.start.date() - timedelta(days=1)
    return _close(s.key, at - timedelta(days=s.days - 1), at, None)


def steps(s: Span, n: int) -> list[Span]:
    out = [s]
    while len(out) < n:
        out.append(before(out[-1]))
    return out[::-1]


def daily(s: Span) -> list[Span]:
    d0 = s.start.date()
    return [Span("1d", datetime.combine(d0 + timedelta(days=k), time()),
                 min(datetime.combine(d0 + timedelta(days=k + 1), time()), s.end), d0 + timedelta(days=k), 1)
            for k in range(s.days)]


def parse_time(v) -> datetime | None:
    """A source's time as it writes it ("2024-01-07T08:00", "2024-12-16 06:00", "2024-01-01", an aware timestamp), as
    plant wall clock (naive); None when it isn't one."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            from . import clock

            v = v.astimezone(clock.zone()).replace(tzinfo=None)
        return v
    if isinstance(v, date):
        return datetime.combine(v, time())
    try:
        return parse_time(datetime.fromisoformat(str(v).strip().strip('"').replace(" ", "T", 1)))
    except ValueError:
        return None
