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
    assert next(iter(real.rows()))["created"] == "2026-11-08"  # real dates untouched

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


def test_kestrel_trip_reaches_the_rest_of_the_plant():
    """Yesterday's compressor trip, read across the expanded plant: the inlet gas goes to flare and the custody meter
    reads nothing while B1-K-2001 is down, the dew point plant warms without gas, and the amine contactor foams when
    gas comes back after the restart."""
    m = process_model.load(KESTREL)
    y = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())

    def at(point, hh, mm=0):
        t = y + timedelta(hours=hh, minutes=mm)
        return m.values(point, t, t, 5)[0][1]

    assert at("KRGP:H1.FT8003.PV", 1) < 10 and at("KRGP:H1.FT8003.PV", 5) > 500  # purge, then flaring
    assert at("KRGP:H1.FT8010.PV", 1) > 1200 and at("KRGP:H1.FT8010.PV", 6) < 50
    assert at("KRGP:E1.TT5003.PV", 1) < -15 and at("KRGP:E1.TT5003.PV", 6) > -5
    assert at("KRGP:D1.PDT4001.PV", 1) < 0.4 and at("KRGP:D1.PDT4001.PV", 7, 30) > 0.6  # foaming after the restart
    assert at("KRGP:D1.PDT4001.PV", 12) < 0.4


def test_kestrel_teg_reboiler_runs_cool_and_the_analyser_agrees_with_the_lab():
    """The TEG reboiler's burner fouling over six weeks: C1-TT-3008 drifts below its 195 degC alarm, and the online
    moisture analyser C1-AT-3011 follows the lab's water content at the contactor outlet."""
    m = process_model.load(KESTREL)
    now = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=9)
    reboiler = [v for _, v in m.values("KRGP:C1.TT3008.PV", now - timedelta(days=45), now, 24 * 60)]
    assert reboiler[0] > 200 and reboiler[-1] < 195
    lab = [r for r in connectors.find(KESTREL, "lims_results").rows() if r["analyte"] == "water_content"]
    for r in lab:
        t = datetime.fromisoformat(r["sampled_at"])
        assert abs(m.values("KRGP:C1.AT3011.PV", t, t, 5)[0][1] - float(r["result"])) < 10, r


def test_a_controlled_value_holds_steps_drifts_and_idles():
    spec = {"points": {"P": ["U", "value"]}, "units": {
        "train:X": {"type": "train"},
        "U": {"type": "controlled", "value": 100.0, "train": "train:X", "idle": 20.0, "noise": 0.0, "tau": 5}},
        "events": [{"unit": "U", "kind": "setpoint", "at": "2026-01-01 06:00", "to": 110.0},
                   {"unit": "U", "kind": "drift", "points": [["2026-01-01 08:00", 0], ["2026-01-01 09:00", -30],
                                                            ["2026-01-01 10:00", 0]]},
                   {"unit": "train:X", "kind": "trip", "start": "2026-01-01 12:00", "end": "2026-01-01 15:00"}]}
    m = process_model.ProcessModel(spec)

    def at(hh, mm=0):
        t = datetime(2026, 1, 1, hh, mm)
        return m.values("P", t, t, 5)[0][1]

    assert abs(at(5) - 100) < 0.5 and abs(at(7) - 110) < 0.5  # holds, then steps to its new setpoint
    assert abs(at(9, 5) - 80) < 3 and abs(at(11) - 110) < 0.5  # drifts and comes back
    assert abs(at(14) - 20) < 2 and abs(at(17) - 110) < 2  # idles while its train is down


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


def test_a_meter_export_is_suggested_as_measurements_on_its_tags_and_wells():
    """Kestrel's gas measurement export (#457 follow-up) has no id column and names plant meters and wellhead meters
    in one column: it is keyed on meter and day, and its numbers are readings on whichever tag or well each row names,
    with units read from the headers."""
    from fuzer.core import bundles
    from fuzer.sources import suggest

    pack = bundles.read(KESTREL.parent)["pack"]
    c = connectors.find(KESTREL.parent / "measurement", "daily_volumes")
    sug = suggest.rules("daily_volumes", suggest.profile(c, pack))
    assert sug["key"] == ["meter", "gas_day"]  # the plant tag or well, not the measurement system's own meter id
    assert sug["columns"]["meter"]["meaning"] == "tag_or_well" and sug["shape"] == "measurements"
    m = suggest.draft("daily_volumes", sug, "measurement", "daily_volume")
    assert "entity" not in m and m["measurements"]["of"] == {"from": "meter", "as": "tag_or_well"}
    assert {v["quantity"]: v.get("unit") for v in m["measurements"]["values"]} == {
        "volume": "kSm3", "energy": "GJ", "hhv": "MJ/Sm3", "flow_hours": "h"}
    volume = next(x for x in suggest.profile(c, pack)["columns"] if x["name"] == "volume_ksm3")
    assert volume["place"] == 0  # 1426.9 is a volume, not well KR 14-26 written without its prefix (#459)
    rows = list(c.rows())
    assert {r["gas_day"] for r in rows} >= {(date.today() - timedelta(days=1)).isoformat()}  # relative dates too
