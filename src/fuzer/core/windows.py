"""Named windows (docs/reports.md): the one place a report window's name and its end day become dates.

    resolve(window, at=None) -> {"key", "at", "start", "end", "days", "label", "previous": {"start", "end"}}

`1d`, `7d`, `30d` and `90d` are that many days ending on `at`; `qtd` and `ytd` run from the start of `at`'s quarter
or year. Both ends are whole days and inclusive. `previous` is the window of the same length just before, which a
stat compares with.
"""

from datetime import date, timedelta

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
