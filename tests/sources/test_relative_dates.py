"""A demonstration facility that doesn't go stale (#456): hand-written exports and the process model write dates
relative to today, read as real dates; coolers reject heat to the site's own medium; and the fake historian
serves any bundle with its own tag grammar (#457)."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from fakeplant import historian
from fuzer.sources import connectors, process_model, relative_dates

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel" / "exports"
DAY = date(2026, 11, 9)


def test_relative_values_resolve_and_free_text_is_left_alone():
    r = relative_dates.resolve
    assert r("today", DAY) == "2026-11-09"
    assert r("today-1d 03:10", DAY) == "2026-11-08 03:10"
    assert r("today+14dT06:00", DAY) == "2026-11-23T06:00"
    assert r(" today-30d ", DAY) == "2026-10-10"
    assert r("tripped today on high vibration", DAY) == "tripped today on high vibration"
    assert r("2026-11-06T03:10", DAY) == "2026-11-06T03:10" and r(42, DAY) == 42
    assert relative_dates.resolve_all({"a": ["today-2d"], "b": {"c": "today"}}, DAY) == {
        "a": ["2026-11-07"], "b": {"c": "2026-11-09"}}


def test_a_csv_with_relative_dates_reads_real_ones_and_changes_its_fingerprint_daily(tmp_path, monkeypatch):
    (tmp_path / "rel.csv").write_text("id,created,text\n1,today-1d,Tripped today\n2,today 07:00,ok\n")
    (tmp_path / "real.csv").write_text("id,created,text\n1,2026-11-08,Tripped today\n")
    rel, real = connectors.find(tmp_path, "rel"), connectors.find(tmp_path, "real")
    rows = list(rel.rows())
    today = date.today()
    assert rows[0]["created"] == (today - timedelta(days=1)).isoformat() and rows[0]["text"] == "Tripped today"
    assert rows[1]["created"] == f"{today} 07:00"
    assert list(real.rows())[0]["created"] == "2026-11-08"  # real dates untouched

    before_rel, before_real = rel.fingerprint(), real.fingerprint()

    class Tomorrow(date):
        @classmethod
        def today(cls):
            return today + timedelta(days=1)

    monkeypatch.setattr(connectors.dt, "date", Tomorrow)
    assert rel.fingerprint() != before_rel  # tomorrow's refresh reads it again
    assert real.fingerprint() == before_real  # a real export is still skipped when unchanged


def test_kestrel_trip_is_placed_relative_to_today():
    spec = process_model.read(KESTREL, DAY)
    trip = next(e for e in spec["events"] if e["kind"] == "trip")
    assert (trip["start"], trip["end"]) == ("2026-11-08 03:10", "2026-11-08 06:40")
    # Inside the charts' default window (48 h before now, whatever the time of day).
    m = process_model.load(KESTREL)
    y = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())
    pd = [v for _, v in m.values("KRGP:B1.PT2001.PV", y + timedelta(hours=2), y + timedelta(hours=8), 10)]
    assert max(pd) > 60 and min(pd) < 35


def test_coolers_reject_heat_to_the_sites_medium():
    """Okan's coolers keep seawater; an inland plant's air cooler follows the air, cold in a winter night."""
    base = json.loads((KESTREL / "process_model.json").read_text())
    winter = datetime(2026, 1, 15, 4), datetime(2026, 1, 15, 6)

    def outlet(climate):
        m = process_model.ProcessModel({**base, "climate": climate, "events": []})
        return np.mean([v for _, v in m.values("KRGP:B1.TT2011.PV", *winter, 30)])

    air = outlet(base["climate"])
    sea = outlet({**base["climate"], "coolant": "sea"})
    assert air < 15 and sea > 30  # ambient about -5 degC plus the approach, against 25 degC seawater plus it


def test_fake_historian_serves_kestrel_with_its_own_tag_grammar():
    plant = historian.Plant(KESTREL)
    assert plant.af_database == "KRGP" and "KRGP:B1.TT2011.PV" in plant.points
    assert historian.loops("B1-K-2001 tripped on high vibration (B1-VT-2003)", plant.grammar) == [
        ("B1", "2001"), ("B1", "2003")]
    assert historian.loops("20-K-2200B tripped") == [("20", "2200B")]  # Okan's numeric areas without a bundle
    assert ("B1", "2001") in plant.upsets  # yesterday's trip, from its own downtime and log records
    client = TestClient(historian.app(KESTREL))
    found = client.get("/piwebapi/dataservers").json()["Items"][0]
    assert found["Name"] == "KRGP-PI"
