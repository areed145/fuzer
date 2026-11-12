"""The machinery engineer's second look, on the Okan stories: the API 670 protection channels (20-K-2200B's thrust
bearing walked half a millimetre before its March 2024 trips), outright surges on 20-K-2100C in December 2025, train
B's 3rd stage dry gas seal passing more and more gas, train A's turbine lube oil filter plugging, train C's turbine
with a lean combustor behind exhaust thermocouple 4, train B's turbine close to its hot section inspection on fired
hours, real-gas compressor performance from the inlet gas analysis, and whether each machine makes the April 2026
turnaround."""

import pytest

from fuzer.analysis import compression, drivers, prognosis, protection, rotating
from fuzer.analysis.calcs import CalcError
from fuzer.core.pack import load_pack
from fuzer.graph import queries

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    from fuzer.workflow import engine

    engine.load_defaults(okan_db.conn)
    okan_db.conn.commit()
    yield okan_db.conn


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_the_thrust_bearing_walked_toward_its_trip_before_the_march_trips(conn):
    p = protection.protection(conn, fid(conn, "20-K-2200B"), until="2024-03-12")
    assert p["flag"] == "thrust_wear" and "API 670" in p["basis"]
    ch = {c["channel"]: c for c in p["channels"]}
    assert set(ch) == {"shaft vibration", "axial position", "thrust bearing temperature", "journal bearing temperature"}
    assert ch["axial position"]["state"] == "alarm" and ch["axial position"]["alarm"] == 0.4
    assert ch["axial position"]["per_week"] > 0.03 and ch["axial position"]["reaches_trip"] < "2024-04-15"
    trip = next(e for e in p["etas"] if e["what"].startswith("axial position"))
    assert trip["range"][0] <= trip["reaches"] <= trip["range"][1]
    after = protection.protection(conn, fid(conn, "20-K-2200B"), until="2024-05-20")  # new bearings on 3 April
    assert after["flag"] is None and {c["state"] for c in after["channels"]} == {"normal"}


def test_outright_surges_on_the_slow_anti_surge_valve(conn):
    s = rotating.surge_events(conn, fid(conn, "20-K-2100C"))
    assert s["flag"] == "surge_events" and s["value"] == 3 and s["last_30_days"] == 3
    assert [e["at"][:10] for e in s["events"]] == ["2025-12-08", "2025-12-15", "2025-12-22"]
    assert all(e["asv_after"] >= 95 and e["flow_min"] < 0.5 * e["flow_before"] and e["thrust_kick_mm"] < -0.2
               for e in s["events"])
    assert rotating.surge_events(conn, fid(conn, "20-K-2100A"))["flag"] is None


def test_the_dry_gas_seal_and_the_lube_oil_filter(conn):
    seal = protection.seal_gas(conn, fid(conn, "20-K-2300B"))
    assert seal["flag"] == "seal_leak" and seal["times_clean"] >= 2.5 and "API 692" in seal["basis"]
    assert seal["value"] > seal["alarm"] and seal["etas"][0]["reaches"].startswith("2026-")
    assert protection.seal_gas(conn, fid(conn, "20-K-2300A"))["flag"] is None
    lube = protection.lube_oil(conn, fid(conn, "20-GT-2000A"))
    assert lube["flag"] == "lube_pressure_low" and lube["pressure"]["now"] < lube["pressure"]["alarm"]
    assert lube["etas"][0]["reaches"] < "2026-04-06" and "API 614" in lube["basis"]
    assert protection.lube_oil(conn, fid(conn, "20-GT-2000B"))["flag"] is None
    with pytest.raises(CalcError, match="lube oil"):
        protection.lube_oil(conn, fid(conn, "20-K-2300B"))


def test_the_lean_combustor_and_the_hot_section_hours(conn):
    sp = drivers.exhaust_spread(conn, fid(conn, "20-GT-2000C"))
    assert sp["flag"] == "exhaust_spread" and sp["wandered"].endswith("20-TT-2014C.PV") and sp["wandered_by"] < -30
    assert drivers.exhaust_spread(conn, fid(conn, "20-GT-2000A"))["flag"] is None
    b = drivers.hot_section(conn, fid(conn, "20-GT-2000B"))
    assert b["flag"] == "inspection_due" and b["expected"] == 30000 and 90 <= b["used_pct"] < 100
    assert "2026-01-15" <= b["due"] <= "2026-03-31"
    assert drivers.hot_section(conn, fid(conn, "20-GT-2000C"))["flag"] is None
    with pytest.raises(CalcError, match="after that date"):  # the plan only knows the latest inspection
        drivers.hot_section(conn, fid(conn, "20-GT-2000C"), until="2024-06-01")


def test_real_gas_performance_and_the_power_balance(conn):
    g = compression.gas_performance(conn, fid(conn, "20-K-2200A"))
    assert g["gas"]["from"] == "gas analysis" and 0.66 < g["gas"]["relative_density"] < 0.70
    assert 1.22 < g["k"] < 1.30 and 0.9 < g["z_suction"] < 1.0 and g["gas_power_kw"] > 0
    assert g["value"] < g["ideal_gas_efficiency"]  # a fixed k of 1.3 overstates the efficiency of this gas
    first = compression.gas_performance(conn, fid(conn, "20-K-2100A"))
    assert "ambient temperature + 8" in first["basis"]
    pb = compression.power_balance(conn, fid(conn, "20-GT-2000A"))
    assert pb["flag"] is None and 0.6 < pb["value"] < 1.0 and len(pb["by_stage"]) == 3
    assert abs(pb["gas_power_kw"] - sum(s["gas_power_kw"] for s in pb["by_stage"])) < 50


def test_which_machines_make_the_april_turnaround(conn):
    seal = prognosis.remaining_life(conn, fid(conn, "20-K-2300B"))
    assert seal["window"]["name"] == "turnaround OK-TAR-2026" and seal["window"]["start"] == "2026-04-06"
    assert seal["flag"] == "fails_before_window" and seal["first"]["check"] == "seal_gas"
    assert seal["first"]["verdict"] == "before the window" and "ISO 13381-1" in seal["basis"]
    gt = prognosis.remaining_life(conn, fid(conn, "20-GT-2000B"))
    assert gt["first"]["check"] == "hot_section" and gt["flag"] == "fails_before_window"
    fine = prognosis.remaining_life(conn, fid(conn, "20-K-2200A"))
    assert fine["flag"] is None


def test_every_projection_carries_its_range_and_the_shared_sufficiency_verdict(conn):
    """P4.6: a date a trend reaches its limit comes with its 90 % range, its window and point count, and the shared
    sufficiency verdict on the days behind it, whichever check made it."""
    found = []
    for ref in ("20-K-2300B", "20-GT-2000B", "20-K-2200B", "20-GT-2000A"):
        found += [e for e in prognosis.remaining_life(conn, fid(conn, ref))["etas"] if e["reaches"] != "now"]
    assert {e["check"] for e in found} >= {"seal_gas", "hot_section"}
    for e in found:
        assert {"range", "level", "window", "points", "sufficiency"} <= set(e), e["what"]
        assert e["sufficiency"]["quotable"] and e["points"] == e["sufficiency"]["points"] > 0, e["what"]
        if e["range"]:
            assert e["range"][0] <= e["reaches"] <= (e["range"][1] or "9999"), e["what"]


def test_the_screen_raises_the_new_findings_with_their_remaining_life(conn):
    s = rotating.screen(conn, limit=50)
    by = {m["machine"]: m for m in s["machines"]}
    assert "seal_leak" in by["20-K-2300B"]["flags"] and by["20-K-2300B"]["status"] == "act"
    assert "lube_pressure_low" in by["20-GT-2000A"]["flags"] and "exhaust_spread" in by["20-GT-2000C"]["flags"]
    assert "surge_events" in by["20-K-2100C"]["flags"] and "inspection_due" in by["20-GT-2000B"]["flags"]
    out = rotating.watch(conn, "2025-12-31T00:00")
    assert {"20-K-2300B", "20-GT-2000A", "20-GT-2000C"} <= {r["machine"] for r in out["raised"]}
    row = conn.execute("select * from exception_record where type = 'machine_health' and key = '20-K-2300B' "
                       "and state = 'open'").fetchone()
    said = {i["label"]: i["value"] for i in row["evidence"]["finding"]["items"]}
    assert "dry gas seal" in row["title"] and "seal cartridge" in said["Recommended first"]
    assert said["Next window"] == "turnaround OK-TAR-2026 from 2026-04-06"
    assert said["Makes the window"] == "before the window" and "high-high" in said["Remaining life"]


def test_a_check_asked_as_of_a_past_time_answers_for_that_time_not_today(conn):
    """`calcs.run` hands a check the options its analysis names, `until` and `days` among them, though the check
    itself is a wrapper that names none (#676 review): asked as of the March 2024 trips, protection answers for then."""
    from fuzer.analysis import calcs

    k = fid(conn, "20-K-2200B")
    then = calcs.run(conn, "protection", k, until="2024-03-12", days=30)
    direct = protection.protection(conn, k, until="2024-03-12", days=30)
    assert then["flag"] == direct["flag"] == "thrust_wear"
    assert then["channels"] == direct["channels"]
    assert calcs.run(conn, "protection", k)["channels"] != then["channels"]
