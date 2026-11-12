"""Shared analysis foundation (P4.6): one data sufficiency check, and forecasts that carry their range.

Every method that projects a series forward, or ranks from a sparse record, asks the same two questions before a
person reads its answer: is there enough data to say anything, and how wide is the answer? This module answers both
once, so a decline forecast, a trend to a trip and a due date read the same way on the case:

    sufficiency(times, min_points=7)                   {verdict, quotable, points, window, span_days, largest_gap_days,
                                                        reasons}: "sufficient", "thin" (quotable, with the reasons)
                                                        or "too_little_data" (say so, not a number)
    trend_interval([0, 1, 2], [5, 6, 8], 4)            the straight line's value at x and its prediction range
    arps_interval(fit, ts, qs, t)                      an Arps decline's rate at t and its prediction range, from the
                                                        scatter of the tests about the fit in log rate

A forecast is an estimate and says so: it never becomes a plan or a commitment.
"""

import math
from datetime import date, datetime
from itertools import pairwise

LEVEL = 0.9  # every forecast range is a two-sided 90 % prediction interval unless the caller asks for another


def _t(level: float, dof: int) -> float:
    """The two-sided Student t quantile for `level` with `dof` degrees of freedom."""
    from scipy import stats

    return float(stats.t.ppf(0.5 + level / 2, max(dof, 1)))


def _day(x) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return datetime.fromisoformat(str(x)[:19]).date()


def sufficiency(times: list, min_points: int = 7, min_span_days: float = 0, max_gap_days: float | None = None) -> dict:
    """Is a series enough to quote a result from? `times` are the readings' dates or times, in any order.

    too_little_data: fewer than `min_points` readings, or they span less than `min_span_days`. Say so; give no number.
    thin: enough to quote, but the largest gap between readings is over `max_gap_days`. Quote it with the reason.
    sufficient: quote it, with its window and point count.
    """
    days = sorted(_day(t) for t in times)
    n = len(days)
    span = (days[-1] - days[0]).days if n else 0
    gap = max(((b - a).days for a, b in pairwise(days)), default=0)
    short, thin = [], []
    if n < min_points:
        short.append(f"{n} reading{'s' if n != 1 else ''}, fewer than the {min_points} it needs")
    if min_span_days and span < min_span_days:
        short.append(f"they span {span} days, less than the {min_span_days:g} it needs")
    if max_gap_days is not None and gap > max_gap_days:
        thin.append(f"a gap of {gap} days between readings, over {max_gap_days:g}")
    verdict = "too_little_data" if short else "thin" if thin else "sufficient"
    return {"verdict": verdict, "quotable": not short, "points": n,
            "window": [days[0].isoformat(), days[-1].isoformat()] if n else None, "span_days": span,
            "largest_gap_days": gap, "reasons": short + thin,
            "needs": {"min_points": min_points, "min_span_days": min_span_days, "max_gap_days": max_gap_days}}


def _ols(xs: list[float], ys: list[float]) -> tuple[float, float, float, float, float]:
    """Intercept, slope, residual standard error, mean x and the sum of squares of x."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    a = my - b * mx
    sse = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys))
    return a, b, math.sqrt(sse / (n - 2)) if n > 2 else 0.0, mx, sxx


def _half_width(s: float, n: int, x: float, mx: float, sxx: float, level: float) -> float:
    return _t(level, n - 2) * s * math.sqrt(1 + 1 / n + ((x - mx) ** 2 / sxx if sxx else 0.0))


def trend_interval(xs: list[float], ys: list[float], x: float, level: float = LEVEL) -> dict:
    """A least squares straight line's value at `x`, with the prediction range a new reading there would fall in."""
    if len(xs) < 3:
        return {"value": None, "range": None, "level": level, "points": len(xs)}
    a, b, s, mx, sxx = _ols(xs, ys)
    v, h = a + b * x, _half_width(s, len(xs), x, mx, sxx, level)
    return {"value": round(v, 6), "range": [round(v - h, 6), round(v + h, 6)], "level": level, "points": len(xs)}


def arps_interval(fit: dict, ts: list[float], qs: list[float], t: float, level: float = LEVEL) -> dict:
    """An Arps fit's rate at `t` (years from its start) with a prediction range, from the tests' scatter about the
    fit in log rate (the scale the fit is made on). The range widens the further `t` is from the tests."""
    from .calcs import rate

    q = rate(fit, t)
    n = len(ts)
    if n < 3:
        return {"value": q, "range": None, "level": level, "points": n}
    res = [math.log(y) - math.log(rate(fit, x)) for x, y in zip(ts, qs)]
    s = math.sqrt(sum(r * r for r in res) / (n - 2))
    mx = sum(ts) / n
    h = _half_width(s, n, t, mx, sum((x - mx) ** 2 for x in ts), level)
    return {"value": round(q, 6), "range": [round(q * math.exp(-h), 6), round(q * math.exp(h), 6)], "level": level,
            "points": n}
