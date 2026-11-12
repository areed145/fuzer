"""P4.6 Shared analysis foundation: one sufficiency check, and forecasts that carry their range."""

import numpy as np
import pytest

from fuzer.analysis import condition, forecast

pytestmark = pytest.mark.unit


def _days(n: int, start: int = 1) -> list[str]:
    return [f"2026-01-{d:02d}" for d in range(start, start + n)]


def test_a_sparse_series_is_too_little_data_and_says_why():
    s = forecast.sufficiency(_days(3), 7)
    assert s["verdict"] == "too_little_data" and not s["quotable"] and s["points"] == 3
    assert s["reasons"] == ["3 readings, fewer than the 7 it needs"]
    assert forecast.sufficiency([], 1)["window"] is None
    short = forecast.sufficiency(_days(8), 7, min_span_days=30)
    assert short["verdict"] == "too_little_data" and "span 7 days" in short["reasons"][0]


def test_a_gap_makes_a_series_thin_but_quotable():
    s = forecast.sufficiency([*_days(6), "2026-01-20"], 7, max_gap_days=7)
    assert s["verdict"] == "thin" and s["quotable"] and s["largest_gap_days"] == 14
    assert s["window"] == ["2026-01-01", "2026-01-20"]
    assert forecast.sufficiency(_days(10), 7, max_gap_days=7)["verdict"] == "sufficient"


def test_a_trend_on_too_few_days_projects_no_date():
    e = condition.eta(_days(5), np.array([1.0, 2, 3, 4, 5]), 10)
    assert e["reaches"] is None and e["range"] is None and e["sufficiency"]["verdict"] == "too_little_data"


def test_a_trend_to_a_limit_carries_its_range_window_and_points():
    ys = np.array([1.0 + 0.1 * i + (0.05 if i % 2 else -0.05) for i in range(20)])
    e = condition.eta(_days(20), ys, 5)
    assert e["reaches"] and e["range"] and e["range"][0] <= e["reaches"] <= e["range"][1]
    assert e["points"] == 20 and e["window"] == ["2026-01-01", "2026-01-20"] and e["level"] == forecast.LEVEL
    p = condition.projection("x reaches 5", "PT-1", e)
    assert {k: p[k] for k in ("reaches", "range", "points")} == {k: e[k] for k in ("reaches", "range", "points")}


def test_a_forecast_range_widens_away_from_the_data():
    xs, ys = [0, 1, 2, 3, 4], [1.0, 3.0, 2.0, 5.0, 4.0]
    near, far = forecast.trend_interval(xs, ys, 2), forecast.trend_interval(xs, ys, 10)
    assert near["range"][1] - near["range"][0] < far["range"][1] - far["range"][0]
    assert forecast.trend_interval(xs[:2], ys[:2], 3)["range"] is None


def test_a_slope_band_reaching_past_the_calendar_gives_no_date_instead_of_failing():
    """A clean but tiny rise (a reading that barely moves for a month) heads for its limit in billions of years: no
    date, not an overflow that stops the screen it is part of (seen on Harrier, P8.4)."""
    ys = np.array([1.0 + 1e-12 * i for i in range(20)])
    e = condition.eta(_days(20), ys, 5.0)
    assert e["per_day"] > 0 and e["r2"] >= 0.5
    assert e["reaches"] is None and e["range"] is None
