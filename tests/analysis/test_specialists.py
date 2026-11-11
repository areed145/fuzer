"""The lighter specialist calcs (#515) on the Okan stories: a well test the choke and pressures don't support, a gas
lift valve the lift curve catches, each well's shortfall with its cause, and a separator's loading in its own units."""

import pytest

from fuzer.analysis import calcs, facilities, surveillance
from fuzer.core.pack import load_pack
from fuzer.graph import queries

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    yield okan_db.conn


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_a_test_separator_over_read_is_caught_before_anyone_acts_on_it(conn):
    """OK-J08-04's October 2025 test reads 45 % high on oil; the choke and the wellhead pressures say the well didn't
    change, and November's test is back on its trend."""
    w = fid(conn, "OK-J08-04")
    bad = calcs.run(conn, "test_check", w, until="2025-10-31")
    assert bad["flag"] == "test_suspect" and bad["at"].startswith("2025-10") and bad["choke_deviation_pct"] > 30
    assert bad["value"] > bad["expected"] and "choke" in bad["basis"] and bad["unit"] == "Sm3/d liquid"
    assert len(bad["sources"]) == 3 and bad["sources"][1].startswith("ec:well_test:")
    assert surveillance.test_check(conn, w, until="2025-11-30")["flag"] is None
    assert surveillance.test_check(conn, fid(conn, "OK-J09-02"), until="2025-10-31")["flag"] is None


def test_a_failed_gas_lift_valve_shows_as_a_believable_test_below_the_lift_curve(conn):
    """OK-J07-05's lift gas valve leaks after the April 2025 slickline job: the June test is consistent with its
    pressures (not the test) but makes far less oil than its lift curve expects at that lift gas."""
    r = surveillance.test_check(conn, fid(conn, "OK-J07-05"), until="2025-06-15")
    assert r["flag"] == "off_lift_curve" and r["lift_deviation_pct"] < -25 and abs(r["inflow_deviation_pct"]) < 25


def test_each_wells_shortfall_gets_one_cause_and_they_add_up(conn):
    may = surveillance.deferment_attribution(conn, fid(conn, "OKAN-67"), at="2025-05-01")
    j0705 = next(x for x in may["wells"] if x["well"] == "OK-J07-05")
    assert j0705["cause"] == "recorded deferment" and "valve" in j0705["evidence"].lower() and j0705["deferred_oil_sm3"] > 0
    july = calcs.run(conn, "deferment_attribution", fid(conn, "OKAN-63"), until="2025-07-01")
    j0302 = july["wells"][0]
    assert j0302["well"] == "OK-J03-02" and j0302["cause"] == "water" and j0302["gap_sm3d"] > 20
    assert july["by_cause"][0]["cause"] == "water" and july["unit"] == "Sm3/d oil"
    assert july["value"] == pytest.approx(sum(b["gap_sm3d"] for b in july["by_cause"]), abs=0.5)
    assert {b["cause"] for b in july["by_cause"]} <= set(surveillance.CAUSES)


def test_a_separator_loading_rises_with_water_cut_against_its_own_rating(conn):
    """The PP HP separator's liquid rating was cut by an MOC; the wells behind it offer more liquid than that as
    their water cut climbs, so it holds production back."""
    hp = fid(conn, "10-V-2001")
    r = calcs.run(conn, "vessel_loading", hp)
    assert r["flag"] == "overloaded" and r["loads"][0]["quantity"] == "liquid_capacity" and r["loads"][0]["grade"] == "moc"
    assert r["loading_pct"] > r["loading_pct_before"] and r["unit"] == "Sm3/d" and r["wells_upstream"] > 100
    early = facilities.vessel_loading(conn, hp, until="2024-06-01")
    assert early["loads"][0]["grade"] == "statement" and early["water_cut_pct"] < r["water_cut_pct"]
    st2 = facilities.vessel_loading(conn, fid(conn, "40-V-2002"))
    assert st2["flag"] is None and st2["loading_pct"] < 60
    with pytest.raises(calcs.CalcError, match="no liquid, oil or gas rating"):
        facilities.vessel_loading(conn, fid(conn, "10-V-2003"))
