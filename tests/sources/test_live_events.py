"""Live operations (sources/live_events): each day of a live facility brings its own events, the same from any window
and on every later day, at about the rates set, and they move the process values the way a real event would."""

from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from fuzer.sources import live_events, process_model

KESTREL = Path(__file__).parents[2] / "fakeplant" / "bundles" / "kestrel" / "exports" / "process_model.json"


@pytest.fixture(autouse=True)
def live(monkeypatch):
    monkeypatch.delenv(live_events.ENV, raising=False)


def _spec():
    return process_model.read(KESTREL, today=date(2026, 11, 24))


def test_a_day_reads_the_same_from_any_window_and_any_later_day():
    spec = _spec()
    day = date(2026, 10, 3)
    one = live_events.of_day(spec, day)
    wide = [e for e in live_events.generate(spec, date(2026, 9, 1), date(2026, 11, 1)) if e["live"].startswith("2026-10-03")]
    assert one == wide
    later = process_model.read(KESTREL, today=date(2026, 12, 20))  # history does not rewrite itself
    assert [e for e in later["events"] if e.get("live", "").startswith("2026-10-03")] == one


def test_events_happen_at_about_their_rates():
    spec = _spec()
    days = 400
    kinds = Counter(e["live"].split("#")[1] for e in live_events.generate(spec, date(2025, 1, 1),
                                                                            date(2025, 1, 1) + timedelta(days - 1))
                    if e["kind"] not in ("curtail",) and not (e["live"].endswith("#trip") and e["kind"] == "vibration"))
    for kind, rate in live_events.RATES.items():
        assert abs(kinds[kind] / days - rate) < max(0.08, rate * 0.5), (kind, kinds[kind], rate)


def test_only_a_live_model_gets_live_events(monkeypatch):
    spec = {**_spec(), "live": False}
    assert live_events.of_day(spec, date(2026, 10, 3)) == []
    monkeypatch.setenv(live_events.ENV, "off")
    assert not [e for e in process_model.read(KESTREL, today=date(2026, 11, 24))["events"] if e.get("live")]


def test_a_live_trip_stops_the_train_and_cuts_the_meters():
    spec = _spec()
    trip_day = next(d for d in (date(2026, 11, 23) - timedelta(days=k) for k in range(1, 100))
                    if any(e["kind"] == "trip" for e in live_events.of_day(spec, d)))
    trip = next(e for e in live_events.of_day(spec, trip_day) if e["kind"] == "trip")
    model = process_model.ProcessModel(spec)
    start, end = datetime.fromisoformat(trip["start"]), datetime.fromisoformat(trip["end"])
    mid = start + (end - start) / 2
    assert model.at(trip["unit"], "running", mid) < 0.3
    assert model.at(trip["unit"], "running", start - timedelta(hours=2)) > 0.9
    if end - start > timedelta(minutes=60):
        assert model.at("meter:sales", "flow", mid) < 0.5 * model.at("meter:sales", "flow", start - timedelta(hours=2))
