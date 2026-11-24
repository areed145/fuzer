"""Named report windows (docs/reports.md): a name and an end day become inclusive dates, with the window before."""

from datetime import date

import pytest

from fuzer.core import windows

pytestmark = pytest.mark.unit


def test_days_end_on_at_and_include_it():
    w = windows.resolve("7d", date(2026, 11, 24))
    assert (w["start"], w["end"], w["days"]) == ("2026-11-18", "2026-11-24", 7)
    assert w["previous"] == {"start": "2026-11-11", "end": "2026-11-17"}
    assert w["label"] == "7 days to 24 Nov 2026"
    assert windows.resolve("1d", "2026-11-24")["start"] == "2026-11-24"


def test_quarter_and_year_to_date():
    assert windows.resolve("qtd", date(2026, 11, 24))["start"] == "2026-10-01"
    y = windows.resolve("ytd", date(2026, 2, 3))
    assert (y["start"], y["days"], y["previous"]["end"]) == ("2026-01-01", 34, "2025-12-31")


def test_unknown_window_is_refused():
    with pytest.raises(windows.WindowError, match="no window"):
        windows.resolve("2w")
