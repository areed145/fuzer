"""P3.1d (#342): a lab analyte at a sample point whose latest samples all moved off its own baseline is routed to
the engineer, with no on or off specification claim; on Okan, the HP separator's oil in water after OK-J03-02's June
2025 water breakthrough."""

import json
from datetime import datetime, timedelta

import pytest

from fuzer.analysis import quality
from fuzer.core import detectors
from fuzer.core.pack import load_pack

PACK = load_pack("okan")


def _samples(vals, start=datetime(2025, 1, 5), every=14):
    return [{"at": start + timedelta(days=every * i), "value": v, "qualifier": None} for i, v in enumerate(vals)]


@pytest.mark.unit
def test_a_drift_is_every_latest_sample_past_its_own_range():
    steady = _samples([20, 22, 19, 21, 23, 20, 22, 21, 19, 22])
    end = steady[-1]["at"] + timedelta(days=3)
    assert quality._check(steady, end) is None
    up = _samples([20, 22, 19, 21, 23, 20, 22, 35, 38, 41])
    c = quality._check(up, end)
    assert c["direction"] == "up" and c["baseline"] == 21 and c["level"] == 38
    assert quality._check(_samples([20, 22, 19, 21, 23, 20, 22, 21, 22, 41]), end) is None  # one sample is not a drift
    assert quality._check(_samples([20, 22, 19, 21, 23, 20, 22, 24, 24, 25]), end) is None  # past the range, too small
    assert quality._check(up, end + timedelta(days=200)) is None  # no longer sampled
    since = up[-3]["at"]
    held = up + _samples([40, 39, 42], start=end)
    assert quality._check(held, held[-1]["at"]) is None and quality._persists(held, since, "up")
    back = up + _samples([22, 21, 20], start=end)
    assert not quality._persists(back, since, "up")


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


def test_a_drifting_analyte_is_raised_until_it_comes_back(conn):
    from fuzer.workflow import engine

    engine.load_defaults(conn)
    conn.commit()
    assert not detectors.run(conn, PACK, "quality_drift", until="2025-05-01T00:00")["raised"]
    out = detectors.run(conn, PACK, "quality_drift", until="2025-07-26T00:00")
    key = "10-V-2001 · HP separator water outlet · oil_in_water"
    assert [r["key"] for r in out["raised"]] == [key], out
    x = conn.execute("select * from exception_record where type = 'quality_drift' and state = 'open'").fetchone()
    assert x["key"] == key and x["title"].startswith("oil_in_water up") and x["item_id"]
    assert conn.execute("select role from work_item where id = %s", (x["item_id"],)).fetchone()["role"] == "engineer"
    said = json.dumps(x["evidence"])
    assert "not an off specification claim" in said and x["evidence"]["direction"] == "up"
    later = detectors.run(conn, PACK, "quality_drift", until="2025-12-31T00:00")  # the step is the new normal: held
    assert later["held"] == 1 and not later["cleared"] and not later["raised"], later
    tag = conn.execute("select fuzer_id from entity_label where label = '10-V-2001'").fetchone()["fuzer_id"]
    for i in range(3):  # three samples back at the old level
        conn.execute("insert into measurement (record, quantity, fuzer_id, at, value, unit, method, location) values "
                     "(%s, 'oil_in_water', %s, %s, 18, 'mg/L', 'test', 'HP separator water outlet')",
                     (f"test:back:{i}", tag, datetime(2026, 1, 5) + timedelta(days=7 * i)))
    try:
        back = detectors.run(conn, PACK, "quality_drift", until="2026-01-25T00:00")
        assert back["cleared"] == [key] and not back["raised"], back
    finally:
        conn.execute("delete from measurement where record like 'test:back:%'")
        conn.commit()
