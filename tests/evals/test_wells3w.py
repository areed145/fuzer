"""Real well upsets from the 3W slice, replayed onto a fake Okan well and scored (#904)."""

from datetime import timedelta

import numpy as np
import pytest

from fuzer.evals import wells3w
from fuzer.sources.timeseries import SyntheticProvider


@pytest.fixture(scope="module")
def src(okan_data):
    return SyntheticProvider(okan_data)


def test_the_slice_is_two_normal_records_and_six_upsets_with_their_licence():
    inst = wells3w.instances()
    assert [i.event for i in inst] == [0, 0, 1, 2, 2, 5, 6, 6]
    assert [i.onset for i in inst] == [None, None, 139, 84, 78, 68, 310, 153]
    assert all(set(i.values) == set(wells3w.VARS) and i.minutes > 60 for i in inst)
    assert all(np.mean(~np.isnan(v)) > 0.95 for i in inst for v in i.values.values())
    # WELL-00002's annulus pressure holds one value for hours in the source; the other wells' never does
    assert {(i.well, var) for i in inst for var, spans in i.flat.items() if spans} == {("WELL-00002", "CASP")}
    assert (wells3w.ROOT / "LICENSE").read_text().startswith("Creative Commons Attribution 4.0 International")


def test_the_overlay_replays_a_record_onto_one_well_scaled_and_held(src):
    inst = next(i for i in wells3w.instances() if i.event == 2)
    at, well = wells3w.AT, wells3w.WELL
    over = wells3w.Overlay(src, well, inst, at)
    s = {"point": f"{well}.WHP", "uom": "", "upsets": []}
    a, b = at - timedelta(hours=2), over.end + timedelta(hours=2)
    under, read = dict(src.series(s, a, b, 1)), dict(over.series(s, a, b, 1))
    t = lambda m: (at + timedelta(minutes=m)).isoformat(timespec="minutes")
    assert all(read[k] == under[k] for k in read if k < t(0) or k >= t(inst.minutes + wells3w.HOLD_MINUTES))
    rep = over.replayed("WHP")
    assert read[t(0)] == pytest.approx(rep[0], abs=1e-3)
    before = np.median([under[t(-m)] for m in range(1, 61)])
    assert np.nanmedian(rep[:wells3w.REF_MINUTES]) == pytest.approx(before, rel=1e-6)  # joins on at the well's level
    after = [read[t(m)] for m in range(inst.minutes, inst.minutes + wells3w.HOLD_MINUTES)]
    assert len(set(after)) == 1  # the end state holds
    assert after[0] < 0.7 * before  # the DHSV closed: the tree pressure fell by more than 30 %
    other = {"point": f"{well}.CHOKE", "uom": "%", "upsets": []}
    assert over.series(other, a, b, 10) == src.series(other, a, b, 10)


def test_change_detection_finds_the_real_upsets_as_the_sre_reads_and_health_finds_the_frozen_readings(src):
    res = wells3w.score(src)
    assert (res["step"], res["window_minutes"]) == (10, 180)
    assert res["upsets"] == 6 and res["found"] >= 5, res["missed"]
    assert res["frozen"] == 3 and res["frozen_found"] == 3
    assert res["false_alarms"] <= 4, [r["false_alarms"] for r in res["instances"]]
    normal = [r for r in res["instances"] if not r["onset"]]
    assert not any(r["false_alarms"] for r in normal)


def test_at_one_minute_steps_every_upset_is_found_within_the_hour_after_its_onset(src):
    res = wells3w.score(src, step=1, window_minutes=60)
    assert res["found"] == 6
    assert all(0 <= r["minutes_to_detect"] <= wells3w.LAG for r in res["instances"] if r["onset"])
    assert not any(c for r in res["instances"] for c in r["changes"] if not r["onset"])
