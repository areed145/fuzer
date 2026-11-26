"""Measures in YAML and row sets (the reports design): checked when read, so a measure that names something the model
doesn't have is refused with the reason, and windows read as times."""

from datetime import date, datetime

import pytest

from fuzer.core import measures, metrics, rowsets, windows

pytestmark = pytest.mark.unit

GOOD = {"label": "Oil", "unit": "bbl", "definition": "d", "formula": "f", "records": "r",
        "from": {"kind": "production_day", "at": "report_date", "links": ["reports_on"], "value": "oil_bbl"},
        "aggregate": "sum", "grains": ["facility"]}


def test_the_platform_measures_and_row_sets_are_valid():
    assert {"production.oil", "production.deferred", "downtime.events", "safety.permits_open"} <= set(measures.builtin())
    assert {"downtime.events", "incidents", "permits.open"} <= set(rowsets.builtin())
    reg = metrics.registry()
    assert {"work.time_to_dispatch", "production.uptime", "plan.unplanned_share", "production.oil"} <= set(reg)
    assert reg["production.oil"].scoped and reg["production.oil"].windowed and reg["production.oil"].source == "platform"
    assert reg["work.backlog"].stock and reg["work.backlog"].default_window == "7d"
    assert {"cases.open", "plan.lines", "downtime.events"} <= set(rowsets.registry())


@pytest.mark.parametrize(("change", "why"), [
    ({"from": {**GOOD["from"], "kind": "widget"}}, "isn't a kind"),
    ({"from": {**GOOD["from"], "links": ["owns"]}}, "aren't links"),
    ({"from": {**GOOD["from"], "links": ["bypasses.nope"]}}, "aren't links"),
    ({"grains": ["colour"]}, "isn't one of"),
    ({"grains": ["cause"]}, "neither a kind nor a cut"),
    ({"aggregate": "median"}, "isn't one of"),
    ({"aggregate": "ratio"}, "needs from.over"),
    ({"aggregate": "age_days"}, "needs stock"),
    ({"window": "2w"}, "isn't a named window"),
    ({"cutoff": "noon"}, "cutoff"),
    ({"colour": "red"}, "unknown field"),
])
def test_a_measure_naming_what_the_model_lacks_is_refused(change, why):
    with pytest.raises(measures.MeasureError, match=why):
        measures.checked({"x.y": {**GOOD, **change}})


def test_a_pack_rebinds_a_platform_measure():
    class Pack:
        measures = measures.checked({"production.target": {**GOOD, "label": "Plan oil"}})
    reg = metrics.registry(Pack())
    assert reg["production.target"].label == "Plan oil" and reg["production.target"].source == "bundle"
    assert metrics.registry()["production.target"].source == "platform"


def test_a_row_set_column_must_be_text():
    spec = {"label": "x", "from": GOOD["from"], "columns": [{"key": "on", "label": True, "field": "x"}]}
    with pytest.raises(rowsets.RowSetError, match="quote On"):
        rowsets.checked({"x": spec})


def test_spans_end_on_their_day_and_today_ends_now():
    s = windows.span("7d", date(2026, 11, 24), datetime(2026, 11, 24, 10))
    assert (s.start, s.end, s.days) == (datetime(2026, 11, 18), datetime(2026, 11, 24, 10), 7)
    assert windows.before(s).end == datetime(2026, 11, 18) and windows.before(s).days == 7
    assert [x.at for x in windows.steps(s, 3)] == [date(2026, 11, 10), date(2026, 11, 17), date(2026, 11, 24)]
    assert len(windows.daily(s)) == 7 and windows.daily(s)[-1].end == s.end
    assert windows.span("14d", date(2026, 11, 24)).days == 14 and windows.span("qtd", date(2026, 11, 24)).days == 55
    assert windows.parse_time("2024-12-16 06:00") == datetime(2024, 12, 16, 6)
    with pytest.raises(windows.WindowError):
        windows.span("1000d", date(2026, 11, 24))


def test_a_daily_figure_the_same_for_a_week_is_frozen_and_zero_or_a_change_is_not():
    """#1386: gas 109,476 Mscf on every daily report read as a reading."""
    from fuzer.ops.measures import FROZEN_DAYS, frozen

    assert frozen([109476.0] * FROZEN_DAYS) == 109476.0
    assert frozen([1.0, *[109476.0] * FROZEN_DAYS]) == 109476.0
    assert frozen([109476.0] * (FROZEN_DAYS - 1)) is None  # too few to say
    assert frozen([0.0] * 30) is None  # no injection for a month is a reading
    assert frozen([*[5.0] * (FROZEN_DAYS - 1), 6.0]) is None and frozen([*[5.0] * 6, None]) is None
