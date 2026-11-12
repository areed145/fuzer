"""Rotating equipment analysis (#474) on the Okan stories: 20-K-2200B's bearing wear shows as vibration climbing
through the ISO zones before its trip; 10-P-1001A's internal wear as head below its curve; each machine's health
summary combines the checks with basis, data dates and confidence, and says what it couldn't check."""

import pytest

from fuzer.analysis import calcs, rotating
from fuzer.analysis.calcs import CalcError
from fuzer.core.pack import load_pack
from fuzer.graph import queries

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    yield okan_db.conn


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_bearing_wear_climbs_through_the_iso_zones_before_the_trips(conn):
    k = fid(conn, "20-K-2200B")
    early = rotating.vibration(conn, k, until="2024-02-15T00:00")
    assert early["group"] == "group1" and early["zone"] == "C" and early["flag"] == "zone_c"
    assert early["trend_mm_s_per_week"] > 0 and "2024-02-15" < early["reaches_zone_d"] < "2024-04-01"
    late = rotating.vibration(conn, k, until="2024-03-05T00:00")
    assert late["zone"] == "D" and late["flag"] == "zone_d" and late["value"] > late["zones"]["C/D"]
    after = rotating.vibration(conn, k, until="2024-05-01T00:00")  # bearings replaced on 3 April
    assert after["zone"] == "A" and after["flag"] is None
    assert after["data_to"] == "2024-05-01" and after["readings"] > 100 and "ISO 10816-3" in after["basis"]


def test_a_pump_against_its_best_efficiency_point(conn):
    p = rotating.pump_bep(conn, fid(conn, "10-P-1001A"))
    assert p["unit"] == "% of BEP flow" and 70 <= p["value"] <= 120 and p["flag"] is None
    assert p["in_preferred_pct"] > 80 and p["below_min_flow_pct"] < 20 and p["bep_flow"] > 0
    with pytest.raises(calcs.CalcError, match="no OEM curve"):
        rotating.pump_bep(conn, fid(conn, "20-K-2200B"))  # a compressor: no pump curve
    with pytest.raises(calcs.CalcError, match="condition-monitoring"):
        rotating.spectrum(conn, fid(conn, "10-P-1001A"))


def test_a_health_summary_combines_the_checks_with_basis_dates_and_confidence(conn):
    h = calcs.run(conn, "rotating_health", fid(conn, "20-K-2200B"), until="2024-03-05T00:00")
    assert h["check"] == "rotating_health" and h["value"] == "act"
    by = {f["check"]: f for f in h["findings"]}
    assert by["vibration"]["flag"] == "zone_d" and by["vibration"]["data"]["data_to"] == "2024-03-05"
    assert all(f["basis"] and f["confidence"] in ("high", "medium", "low") and f["sources"] for f in h["findings"])
    assert any("zone D" in r for r in h["recommendations"])
    assert "pump_curve" not in by  # a compressor: no pump checks
    pump = rotating.health(conn, fid(conn, "10-P-1001A"), until="2024-03-01T00:00")
    checks = {f["check"] for f in pump["findings"]} | {u["check"] for u in pump["unknown"]}
    assert {"pump_curve", "pump_bep", "vibration", "reliability"} <= checks
    assert all(u["why"] for u in pump["unknown"])  # what couldn't be checked says why


def test_fleet_screen_finds_the_planted_faults_unnamed(conn):
    """Asked which machines need attention, the screen puts the planted fault first at each date."""
    march = rotating.screen(conn, "2024-03-05T00:00")
    assert march["machines"][0]["machine"] == "20-K-2200B" and march["machines"][0]["status"] == "act"
    assert "zone_d" in march["machines"][0]["flags"] and march["by_status"]["act"] == 1
    sept = rotating.screen(conn, "2025-09-08T00:00")
    first = next(m for m in sept["machines"] if "head_loss" in m["flags"])
    assert first["machine"] == "10-P-1001A" and first["status"] == "act"
    # the compressor class's failure rate is listed once, not as a reason to look at each compressor
    assert sept["class_wide_failures"] and all("class_wide_failures" not in m["flags"] for m in sept["machines"])
    assert sept["machines_checked"] >= 12


def test_a_starved_compressor_runs_on_its_anti_surge_valve_and_dips_toward_surge(conn):
    """Train C is starved of gas from mid-October 2025: its 1st stage recycles to hold the control line and, with a
    slow anti-surge valve, dips toward surge on load swings in December. Its own OEM map gives the surge line."""
    k = fid(conn, "20-K-2100C")
    before = rotating.surge(conn, k, until="2025-09-01T00:00")
    assert before["flag"] is None and before["value"] > 40 and before["asv_open_pct"] < 5
    dec = rotating.surge(conn, k, until="2025-12-30T00:00")
    assert dec["flag"] == "near_surge" and dec["margin_p5"] < 5 and dec["asv_open_pct"] > 50
    assert 5 < dec["value"] < 15 and 80 < dec["speed_pct"] < 100 and dec["surge_flow"] < dec["flow"]
    # load sharing: the same stage on trains A and B runs far more gas
    assert {s["machine"] for s in dec["siblings"]} == {"20-K-2100A", "20-K-2100B"}
    assert all(s["flow"] > 1.5 * dec["flow"] for s in dec["siblings"])
    assert "map" in dec["basis"] and dec["data_to"] == "2025-12-30"
    later = rotating.surge(conn, fid(conn, "20-K-2200C"), until="2025-12-30T00:00")  # held at the control line
    assert later["flag"] == "recycling" and later["head"] and abs(later["head"]["vs_map_pct"]) < 15
    with pytest.raises(calcs.CalcError, match="no OEM compressor map"):
        rotating.surge(conn, fid(conn, "20-K-2300A"), until="2025-12-30T00:00")  # Solar's C40 maps never came
    sharing = rotating.screen(conn, "2025-12-30T00:00")
    c = next(m for m in sharing["machines"] if m["machine"] == "20-K-2100C")
    assert c["status"] == "act" and "near_surge" in c["flags"] and any("surge" in r for r in c["recommendations"])
    h = calcs.run(conn, "rotating_health", k, until="2025-12-30T00:00")
    assert {f["check"]: f for f in h["findings"]}["surge"]["confidence"] == "high"


def test_a_fouling_gas_turbine_burns_more_fuel_than_its_curves_expect_until_washed(conn):
    """Train B's gas turbine fouls from June 2025 until the offline water wash on 12 November: its heat rate climbs
    above what Solar's curves expect at the same load and ambient temperature, and recovers after the wash."""
    k = fid(conn, "20-GT-2000B")
    clean = rotating.heat_rate(conn, k, until="2025-05-15T00:00")
    assert clean["flag"] is None and abs(clean["excess_pct"]) < 1.5 and clean["unit"] == "kJ/kWh"
    fouled = rotating.heat_rate(conn, k, until="2025-11-10T00:00")
    assert fouled["flag"] == "heat_rate_high" and fouled["excess_pct"] > 5 and fouled["trend_pct_per_week"] > 0
    assert fouled["value"] > fouled["expected"] and "LHV" in fouled["basis"] and 60 < fouled["load_pct"] <= 100
    washed = rotating.heat_rate(conn, k, until="2025-12-01T00:00")
    assert washed["flag"] is None and washed["excess_pct"] < 3
    assert rotating.heat_rate(conn, fid(conn, "20-GT-2000A"), until="2025-11-10T00:00")["flag"] is None
    nov = rotating.screen(conn, "2025-11-10T00:00")
    b = next(m for m in nov["machines"] if m["machine"] == "20-GT-2000B")
    assert "heat_rate_high" in b["flags"] and any("water wash" in r for r in b["recommendations"])


def test_a_pump_with_a_passing_recirculation_valve_runs_its_motor_above_the_service_factor(conn):
    """Oil export pump B's minimum-flow recirculation valve passes from late October 2025 until it is repaired on
    9 December: the pump runs out along its curve and its motor draws above the nameplate's service factor (the
    rating on its SAP equipment record). The fleet screen finds it without being told which pump to check."""
    b = fid(conn, "10-P-1001B")
    clean = rotating.motor_load(conn, b, until="2025-10-01T00:00")
    assert clean["flag"] is None and clean["unit"] == "kW" and clean["rated_kw"] == 260
    assert clean["service_factor"] == 1.15 and 75 < clean["load_pct"] < 100
    assert clean["hours_above_service_factor"] == 0 and "SAP equipment" in clean["basis"]
    over = rotating.motor_load(conn, b, until="2025-11-25T00:00")
    assert over["flag"] == "motor_overload" and over["load_pct"] > 115 and over["hours_above_service_factor"] > 100
    assert over["first_above_service_factor"] >= "2025-10-25" and len(over["sources"]) == 3
    assert rotating.motor_load(conn, b, until="2025-12-31T00:00")["flag"] is None
    assert rotating.motor_load(conn, fid(conn, "10-P-1001A"), until="2025-11-25T00:00")["flag"] is None
    h = calcs.run(conn, "rotating_health", b, until="2025-11-25T00:00")
    f = {x["check"]: x for x in h["findings"]}["motor_load"]
    assert f["flag"] == "motor_overload" and f["confidence"] == "high" and h["value"] == "act"
    nov = rotating.screen(conn, "2025-11-25T00:00")
    m = next(x for x in nov["machines"] if x["machine"] == "10-P-1001B")
    assert m["status"] == "act" and "motor_overload" in m["flags"]
    assert any("recirculation" in r for r in m["recommendations"])
    with pytest.raises(CalcError, match="motor rating"):
        rotating.motor_load(conn, fid(conn, "20-K-2300A"))


def test_surge_heat_rate_motor_load_and_cooler_fouling_read_roles_not_descriptions(conn):
    """P1.4d: with every point's description blanked, surge, heat rate, motor load and cooler fouling come out the
    same: they find their points by measurement role, and the driver by its class in the train. (The stage order
    behind a stage's scrubber, cooler and siblings still comes from its tag name until the register holds it.)"""
    from fuzer.analysis import equipment

    ids = {ref: fid(conn, ref) for ref in ("20-K-2100C", "20-GT-2000B", "10-P-1001B", "20-E-2350A")}

    def seen() -> list:
        s = rotating.surge(conn, ids["20-K-2100C"], until="2025-12-30T00:00")
        h = rotating.heat_rate(conn, ids["20-GT-2000B"], until="2025-11-10T00:00")
        m = rotating.motor_load(conn, ids["10-P-1001B"], until="2025-12-01T00:00")
        c = equipment.cooler_fouling(conn, ids["20-E-2350A"], 180, "2024-08-10T00:00", pack=PACK)
        return [(r["value"], r["flag"], r["sources"]) for r in (s, h, m, c)]

    before = seen()
    try:
        conn.execute("update golden set value = '\"\"' where field = 'description' "
                     "and fuzer_id in (select fuzer_id from entity where kind = 'timeseries')")
        assert seen() == before
    finally:
        conn.rollback()
