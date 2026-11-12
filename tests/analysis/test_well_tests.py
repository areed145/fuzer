"""P3.1b (#339): a well test overdue against the well's own interval, or a water cut or GOR shift on its latest test,
is routed to the production engineer; on Okan, OK-J03-02's June 2025 water breakthrough."""

from datetime import datetime, timedelta

import pytest

from fuzer.analysis import well_tests
from fuzer.core import detectors
from fuzer.core.pack import load_pack

PACK = load_pack("okan")


def _tests(wcs, gors=None, every=30, start=datetime(2025, 1, 5)):
    gors = gors or [None] * len(wcs)
    return [{"at": start + timedelta(days=every * i), "record": f"t{i}", "label": "W", "water_cut": w, "gor": g}
            for i, (w, g) in enumerate(zip(wcs, gors))]


@pytest.mark.unit
def test_each_check_reads_the_wells_own_history():
    steady = _tests([20, 21, 20, 22, 21, 22, 21, 22])
    end = steady[-1]["at"] + timedelta(days=10)
    assert well_tests._check(steady, end) is None
    assert well_tests._check(steady, steady[-1]["at"] + timedelta(days=61))["flags"] == ["test_overdue"]
    assert well_tests._check(steady, steady[-1]["at"] + timedelta(days=400)) is None  # off the schedule, not overdue
    jump = _tests([20, 21, 20, 22, 21, 22, 21, 36])
    c = well_tests._check(jump, end)
    assert c["flags"] == ["water_cut_shift"] and c["water_cut_before"] == 21
    noisy = [103, 115, 158, 129, 94, 143, 108, 97, 129, 112, 90, 115, 92, 93, 92, 153, 158]
    assert well_tests._check(_tests([20] * len(noisy), noisy), end) is None  # GOR noise is never a shift
    gas = [120, 118, 125, 122, 119, 121, 124, 120, 260, 270]
    assert well_tests._check(_tests([20] * len(gas), gas), end)["flags"] == ["gor_shift"]
    one = [120, 118, 125, 122, 119, 121, 124, 120, 122, 270]
    assert well_tests._check(_tests([20] * len(one), one), end) is None  # one test is never a shift


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


def test_a_well_whose_water_cut_jumps_is_raised_until_a_newer_test_settles_it(conn):
    import json

    from fuzer.workflow import engine

    engine.load_defaults(conn)
    conn.commit()
    out = detectors.run(conn, PACK, "well_tests", until="2025-06-20T00:00")
    assert [(r["well"], r["flags"]) for r in out["raised"]] == [("OK-J03-02", ["water_cut_shift"])], out
    x = conn.execute("select * from exception_record where type = 'well_test_review' and state = 'open'").fetchone()
    assert x["key"] == "OK-J03-02" and "water cut shift" in x["title"] and x["item_id"]
    assert conn.execute("select role from work_item where id = %s", (x["item_id"],)).fetchone()["role"] == \
        "production_engineer"
    said = json.dumps(x["evidence"])
    assert "not a reservoir diagnosis" in said and "72" in said
    assert detectors.run(conn, PACK, "well_tests", until="2025-06-20T00:00")["raised"][0]["new"] is False
    later = detectors.run(conn, PACK, "well_tests", until="2025-11-01T00:00")  # its new level is its history now
    assert later["cleared"] == ["OK-J03-02"] and not later["raised"], later
    conn.commit()
