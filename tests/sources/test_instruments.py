"""What instruments and the historian do to a fake plant's values (#868): planted faults, digital states,
interface outages and compression, and instrument health finding what was planted."""

import json
from datetime import datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fakeplant import historian
from fuzer.analysis import signals
from fuzer.analysis.limits import _values
from fuzer.sources import instruments, pi_web_api
from fuzer.sources.timeseries import SyntheticProvider

STEP = 10


@pytest.fixture(scope="module")
def spec(okan_data):
    return json.loads((okan_data / "instrument_faults.json").read_text())


@pytest.fixture(scope="module")
def src(okan_data):
    return SyntheticProvider(okan_data)


def _read(src, point, a, b, uom=""):
    return _values(src, {"point": point, "uom": uom, "description": ""}, a, b, STEP, len(signals._grid(a, b, STEP)))


def test_each_kind_does_what_it_says_on_a_flat_line():
    t0 = datetime(2025, 1, 1)
    secs = np.array([instruments.epoch(t0 + timedelta(minutes=10 * i)) for i in range(60)])
    clean = np.linspace(10, 20, 60)
    span = lambda kind, **k: {"id": kind, "point": "P", "kind": kind, "start": "2025-01-01T02:00",
                              "end": "2025-01-01T05:00", **k}
    at = lambda t: float(np.interp(t, secs, clean))
    inside = slice(12, 30)
    for f, check in ((span("frozen"), lambda v: np.all(v[inside] == clean[12])),
                     (span("stuck", factor=0.02), lambda v: np.ptp(v[inside]) < 0.03 * np.ptp(clean[inside])),
                     (span("noisy", sigma=2.0), lambda v: np.std(v[inside] - clean[inside]) > 1.0),
                     (span("pinned", value=100.0), lambda v: np.all(v[inside] == 100.0)),
                     (span("drift", offset=5.0), lambda v: np.isclose(v[29] - clean[29], 5.0 * 17 / 18)),
                     (span("bad", state="Bad Input"), lambda v: np.isnan(v[inside]).all())):
        v = instruments.Faults({"faults": [f]}).apply("P", secs, clean, at)
        assert check(v), f["kind"]
        assert np.array_equal(v[:12], clean[:12]) and np.array_equal(v[30:], clean[30:]), f["kind"]


def test_swinging_door_keeps_the_ends_and_the_bends_and_drops_a_straight_line():
    secs = np.arange(0.0, 100 * 300, 300)
    ramp = np.linspace(0, 10, 100)
    assert instruments.swinging_door(secs, ramp, 0.01) == [0, 99]
    bend = np.where(np.arange(100) < 50, 0.0, np.arange(100) - 50.0)
    kept = instruments.swinging_door(secs, bend, 0.01)
    assert kept[0] == 0 and kept[-1] == 99 and any(48 <= i <= 50 for i in kept) and len(kept) <= 5
    gap = ramp.copy()
    gap[40:45] = np.nan
    assert 40 in instruments.swinging_door(secs, gap, 0.01)


def test_okan_plants_faults_of_every_kind_and_interface_outages(spec):
    kinds = {f["kind"] for f in spec["faults"]}
    assert kinds == set(instruments.KINDS)
    assert len(spec["faults"]) >= 20 and len(spec["outages"]) == 3
    assert all(o["state"] == "I/O Timeout" and o["points"] for o in spec["outages"])
    learned = (datetime(2025, 1, 1), datetime(2025, 3, 31))  # the quarter test_limits learns normal ranges on
    for f in spec["faults"] + spec["outages"]:
        a, b = datetime.fromisoformat(f["start"]), datetime.fromisoformat(f["end"])
        assert not (a < learned[1] and b > learned[0]), f["id"]


def test_instrument_health_finds_every_planted_fault_it_should(src, spec):
    for f in spec["faults"]:
        if not f["finds"]:
            continue
        a, b = datetime.fromisoformat(f["start"]), datetime.fromisoformat(f["end"])
        s, e = a - timedelta(hours=6), b + timedelta(hours=6)
        uom = "%" if f["kind"] == "pinned" else ""
        v = _read(src, f["point"], s, e, uom)
        base = _read(src, f["point"], s - timedelta(days=7), s - timedelta(minutes=STEP), uom)
        lo, hi = signals.RANGES.get(uom, (None, None))
        found = signals.health(signals._grid(s, e, STEP), v, STEP, base, None, lo, hi)
        assert f["finds"] in {x["check"] for x in found if x["start"] <= f["end"] and x["end"] >= f["start"]}, f


def test_bad_values_and_outages_are_gaps_and_any_window_reads_the_same(src, spec):
    bad = next(f for f in spec["faults"] if f["kind"] == "bad")
    a, b = datetime.fromisoformat(bad["start"]), datetime.fromisoformat(bad["end"])
    v = _read(src, bad["point"], a - timedelta(hours=1), b + timedelta(hours=1))
    assert np.isnan(v[6:-7]).all() and not np.isnan(v[:6]).any() and not np.isnan(v[-6:]).any()

    out = spec["outages"][0]
    a = datetime.fromisoformat(out["start"])
    for p in out["points"][:3]:
        assert np.isnan(_read(src, p, a, a + timedelta(minutes=30))).all()

    f = next(f for f in spec["faults"] if f["kind"] == "noisy")
    a = datetime.fromisoformat(f["start"]) + timedelta(hours=1)
    one = dict(src.values(f["point"], "", a, a + timedelta(hours=2), 10, []))
    other = dict(src.values(f["point"], "", a - timedelta(hours=5), a + timedelta(hours=3), 5, []))
    assert one and all(other[t] == x for t, x in one.items())


def test_the_fake_pi_stores_digital_states_and_a_compressed_archive(okan_data, spec):
    server = TestClient(historian.app(okan_data))
    pi = pi_web_api.Client("http://testserver/piwebapi", http=server)
    out = spec["outages"][0]
    point = out["points"][0]
    wid = pi.get("points", path=f"\\\\{pi.data_server()['Name']}\\{point}")["WebId"]
    a = datetime.fromisoformat(out["start"])
    window = {"startTime": (a - timedelta(hours=1)).isoformat(), "endTime": (a + timedelta(hours=1)).isoformat()}

    items = pi.get(f"streams/{wid}/interpolated", interval="10m", **window)["Items"]
    states = [i for i in items if not i["Good"]]
    assert states and all(i["Value"] == {"Name": "I/O Timeout", "Value": 246, "IsSystem": True} for i in states)
    assert any(i["Good"] for i in items)
    read = pi_web_api.Provider(pi).series({"point": point}, a - timedelta(hours=1), a + timedelta(hours=1), 10)
    assert len(read) == len(items) - len(states)  # Fuzer's reader leaves the states out

    recorded = pi.get(f"streams/{wid}/recorded", **window)["Items"]
    assert recorded[0]["Good"] and any(i["Value"] == states[0]["Value"] for i in recorded)
    raw = historian.Plant(okan_data).values(point, a - timedelta(hours=1), a + timedelta(hours=1), 5)
    assert len([i for i in recorded if i["Good"]]) < len(raw)
