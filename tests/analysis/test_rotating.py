"""Rotating equipment analysis (#474) on the Okan stories: 20-K-2200B's bearing wear shows as vibration climbing
through the ISO zones before its trip; 10-P-1001A's internal wear as head below its curve; each machine's health
summary combines the checks with basis, data dates and confidence, and says what it couldn't check."""

import pytest

from fuzer.analysis import calcs, rotating
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
