"""Replay harness (#103): detectors run window by window with the clock set back, scored against the
process model's injected events."""

import json
from datetime import datetime

import pytest

from fuzer.core.pack import load_pack
from fuzer.evals import replay

PACK = load_pack("okan")
SINCE, UNTIL = "2024-11-03T00:00", "2024-11-13T00:00"  # train C trips, a bad level instrument, level upsets


@pytest.fixture(scope="module")
def data(okan_data):
    return okan_data


@pytest.fixture(scope="module")
def spec(data):
    return json.loads((data / "process_model.json").read_text())


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    yield c


def test_ground_truth(spec):
    ev = replay.truth(spec, datetime(2024, 1, 1), datetime(2025, 12, 31))
    assert {"trip", "shutdown", "vibration", "fouling", "valve_failed_open", "erratic", "restriction"} <= {
        e["kind"] for e in ev if e["upset"]}
    assert not any(e["upset"] for e in ev if e["kind"] in ("choke", "stopped", "covering"))
    fouling = next(e for e in ev if e["kind"] == "fouling")  # a ramp: from leaving 0 to back at 0
    assert (fouling["start"], fouling["end"]) == (datetime(2024, 2, 1), datetime(2024, 8, 19, 6))
    assert fouling["scope"] == fouling["own"] == {"20-E-2350A"}
    trip = next(e for e in ev if e["unit"] == "train:C" and e["kind"] == "trip")
    assert {"20-K-2100C", "20-GT-2000C"} <= trip["own"] and "header:20" not in trip["own"]
    # a trip reaches the lift gas headers, their child headers, the wells on them and their jackets
    assert {"header:20", "header:40", "OK-J18-08", "jacket:78", "meter:oil_export", "10-P-1001A"} <= trip["scope"]
    shut = next(e for e in ev if e["kind"] == "shut_in")
    assert shut["own"] == {shut["unit"]} and "header:20" not in shut["scope"]


def test_detector_cannot_peek(conn, data):
    def peeker(conn, pack, since, until, src):
        p = {"point": "OKA_PP.10PT2001.PV", "uom": "barg", "upsets": []}
        src.series(p, since, until.replace(hour=23, minute=59), 10)
        return []

    with pytest.raises(replay.Peek):
        replay.run(conn, PACK, peeker, SINCE, UNTIL, data_dir=data, learn_days=None)


def test_scores_a_scripted_detector(conn, data, spec):
    """A detector that reports each upset on its own unit when it starts, plus one alarm on a unit nothing
    happens to, scores recall 1 and exactly one false alarm."""
    by_unit = {}
    for p, (u, _) in spec["points"].items():
        by_unit.setdefault(u, p)
    a = datetime.fromisoformat(SINCE)
    upsets = [e for e in replay.truth(spec, a, datetime.fromisoformat(UNTIL)) if e["upset"] and e["start"] >= a]
    quiet = next(p for p, (u, _) in spec["points"].items() if u == "OK-J01-01")

    def scripted(conn, pack, since, until, src):
        out = [{"point": by_unit[min(u for u in e["own"] if u in by_unit)], "start": e["start"], "end": e["end"],
                "key": e["kind"]} for e in upsets if since <= e["start"] < until]
        if since == datetime(2024, 11, 7):
            out.append({"point": quiet, "start": since, "end": None, "key": "noise"})
        return out

    res = replay.run(conn, PACK, scripted, SINCE, UNTIL, data_dir=data, learn_days=None)
    assert res["detector"] == "scripted" and res["windows"] == 40
    assert res["upsets"] == len(upsets) >= 4 and res["recall"] == 1.0
    assert res["false_alarms"] == 1 and res["false_alarm_examples"][0].startswith(quiet)
    # the noise carries on across windows as one detection, first seen at the end of its window
    assert res["by_kind"]["trip"]["median_hours_to_detect"] < 6


def test_limits_baseline(conn, data):
    """The #101 excursions as a stand-in SRE: every upset in the period is found, with few false alarms.
    A detector change that loses one fails here."""
    before = conn.execute("select count(*) as n from point_normal").fetchone()["n"]
    res = replay.run(conn, PACK, "limits", SINCE, UNTIL, data_dir=data)
    assert res["recall"] == 1.0, res["by_kind"]
    assert res["by_kind"]["trip"]["median_hours_to_detect"] <= 1
    assert res["false_alarms_per_week"] <= 5, res["false_alarm_examples"]
    assert conn.execute("select count(*) as n from point_normal").fetchone()["n"] == before  # put back

    first = replay.store(conn, res)
    assert first == {"previous": None, "recall_dropped": []}
    worse = json.loads(json.dumps(res, default=str))
    worse["recall"], worse["by_kind"]["trip"]["recall"] = 0.8, 0.5
    again = replay.store(conn, worse)
    assert again["previous"]["recall"] == 1.0 and again["recall_dropped"] == ["overall", "trip"]


def test_needs_a_process_model(conn, tmp_path):
    with pytest.raises(ValueError, match="process_model.json"):
        replay.run(conn, PACK, "limits", SINCE, UNTIL, data_dir=tmp_path)
