"""Diagnostic calcs (#105 A, C, F): checks that return a number against what it should be, with their
basis and sources; instrument health and change detection on any series; what changed between two
times. Each on made-up series where the answer is known, then on the Okan stories."""

import asyncio
import json
from datetime import datetime, timedelta

import numpy as np
import pytest

from fuzer import plots
from fuzer.agents import mcp_server
from fuzer.analysis import calcs, signals
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.evals import replay
from fuzer.graph import operations, queries
from fuzer.plots import maintenance, production
from fuzer.sources import timeseries

PACK = load_pack("okan")
EVIDENCE = {"value", "expected", "deviation", "deviation_pct", "flag", "basis", "sources"}


@pytest.fixture(scope="module")
def data(okan_data):
    return okan_data


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    yield c


@pytest.fixture(scope="module")
def spec(data):
    return json.loads((data / "process_model.json").read_text())


@pytest.fixture
def okan_pack(monkeypatch):
    monkeypatch.setattr(mcp_server, "PACK", PACK)


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def point(conn, name: str) -> str:
    return conn.execute("select fuzer_id from entity_label join entity e using (fuzer_id) "
                        "where e.kind = 'timeseries' and label = %s", (name,)).fetchone()["fuzer_id"]


def near(a: str, b: str | datetime, hours: float = 1) -> bool:
    b = b if isinstance(b, datetime) else datetime.fromisoformat(b)
    return abs(datetime.fromisoformat(a) - b) <= timedelta(hours=hours)


# --- A: checks as evidence --------------------------------------------------------------------------

def test_every_check_returns_evidence_and_names_its_users(conn):
    k = fid(conn, "20-K-2200B")
    cls = conn.execute("select dst from edge where rel = 'classified_as' and src = %s", (k,)).fetchone()["dst"]
    line = conn.execute("select fuzer_id from measurement m join entity e using (fuzer_id) where m.quantity = "
                        "'wall_thickness' and e.kind = 'pipeline' group by 1 order by min(m.value) limit 1").fetchone()
    refs = {"decline": fid(conn, "OK-J07-03"), "gas_lift": fid(conn, "OK-J07-03"), "pump_curve": fid(conn, "10-P-1001A"),
            "choke": fid(conn, "OK-J12-02"), "reliability": cls, "wall_loss": line["fuzer_id"]}
    refs |= {"well_constraint": refs["decline"], "dp_drift": refs["decline"], "compressor_performance": fid(conn, "20-K-2300A"),
             "cooler_fouling": fid(conn, "20-E-2350A"), "peers": k, "vibration": k, "pump_bep": refs["pump_curve"],
             "rotating_health": refs["pump_curve"]}
    assert set(refs) == set(calcs.CHECKS)
    for check, ref in refs.items():
        out = calcs.run(conn, check, ref)
        assert EVIDENCE <= set(out) and out["sources"] and out["basis"], check
        assert out["of"]["id"] == ref and json.dumps(out, default=str)
        assert calcs.CHECKS[check]["used_by"].get("person", "plot_").startswith("plot_")
    with pytest.raises(calcs.CalcError, match="unknown check"):
        calcs.run(conn, "nope", ref)


def test_checks_flag_the_okan_stories_as_of_then(conn):
    """Run as of a past time, each check finds the injected story; run today, the fixed ones are clear."""
    pump = fid(conn, "10-P-1001A")
    worn = calcs.pump_curve(conn, pump, until="2025-09-01")
    assert worn["flag"] == "head_loss" and worn["value"] < -5 and worn["worst_month"]["month"] in ("2025-08", "2025-09")
    assert calcs.pump_curve(conn, pump)["flag"] is None  # new wear rings on 18 Sep

    choke = fid(conn, "OK-J12-02")
    assert calcs.choke(conn, choke, until="2025-10-01")["flag"] == "eroded"
    now = calcs.choke(conn, choke)  # replaced on 15 Oct: judged on the readings since
    assert now["flag"] is None and now["replaced"] == ["2025-10-15"] and now["before_replacement"] > 1.3

    valve = calcs.decline(conn, fid(conn, "OK-J07-03"), until="2024-10-01")  # gas lift valve failed open
    assert valve["flag"] == "step_down" and valve["deviation_pct"] < -30
    assert calcs.decline(conn, fid(conn, "OK-J03-02"), until="2025-06-20")["flag"] == "step_down"  # breakthrough

    line = conn.execute("select fuzer_id from measurement m join entity e using (fuzer_id) where m.quantity = "
                        "'wall_thickness' and e.kind = 'pipeline' group by 1 order by min(m.value) limit 1").fetchone()
    wall = calcs.wall_loss(conn, line["fuzer_id"], min_required_mm=6.0)
    assert wall["flag"] == "retire_soon" and wall["shortest_remaining_years"] < 5 and wall["unit"] == "mm/year"
    assert calcs.wall_loss(conn, line["fuzer_id"])["shortest_remaining_years"] is None  # no minimum, no life


def test_charts_draw_from_the_same_calcs(conn):
    well = fid(conn, "OK-J07-03")
    fig, out = production.decline(conn, well).summary, calcs.decline(conn, well)
    assert (fig["qi_sm3d"], fig["effective_decline_pct_per_year"]) == (out["qi"], out["effective_decline_pct_per_year"])
    gl, g = production.gas_lift_curve(conn, well).summary, calcs.gas_lift(conn, well)
    assert (gl["optimum"]["lift_gas_ksm3d"], gl["now"]["lift_gas_ksm3d"]) == (g["expected"], g["value"])
    pump = fid(conn, "10-P-1001A")
    assert maintenance.pump_curve(conn, pump).summary["worst_month"] == calcs.pump_curve(conn, pump)["worst_month"]
    k = fid(conn, "20-K-2200B")
    assert maintenance.reliability(conn, k, "crow_amsaa").summary["beta"] == calcs.reliability(conn, k, "crow_amsaa")["value"]


def test_fits_refuse_what_they_cannot_fit():
    d = datetime(2024, 1, 1).date()
    with pytest.raises(calcs.CalcError, match="too few days"):  # used to divide by zero
        calcs.crow_amsaa([d, d, d, d + timedelta(days=1)])
    with pytest.raises(calcs.CalcError, match="fewer than three"):
        calcs.weibull([30, 40])
    w = calcs.weibull([100, 200, 300, 400, 500, 600])
    assert 1.2 < w["beta"] < 3 and w["reading"].startswith("wear-out")
    assert calcs.remaining_life(10.0, -0.5, 6.0) == 8.0 and calcs.remaining_life(10.0, 0.1, 6.0) is None


# --- C: instrument health and changes on made-up series ---------------------------------------------

T0 = datetime(2024, 1, 1)
STEP = 10


def grid(n: int) -> list[datetime]:
    return [T0 + timedelta(minutes=STEP * i) for i in range(n)]


def noise(n: int, seed: int = 1, sd: float = 0.5) -> np.ndarray:
    return 50 + np.random.default_rng(seed).normal(0, sd, n)


def test_health_on_made_up_readings():
    n = 6 * 24 * 3  # three days at 10 minutes
    base = noise(n, 2)
    assert signals.health(grid(n), noise(n), STEP, base) == []  # a healthy reading

    v = noise(n)
    v[100:130] = v[100]  # frozen for 5 h
    v[200:250] = 50 + np.random.default_rng(3).normal(0, 0.01, 50)  # sticking for 8 h
    v[300:340] += np.random.default_rng(4).normal(0, 6, 40)  # hunting for 6.5 h
    found = {f["check"]: f for f in signals.health(grid(n), v, STEP, base)}
    assert set(found) == {"frozen", "stuck", "noisy"}
    assert near(found["frozen"]["start"], grid(n)[100], 0.2) and found["frozen"]["minutes"] >= 280
    assert near(found["noisy"]["start"], grid(n)[300], 1) and found["noisy"]["value"] > 4 * found["noisy"]["expected"]

    stopped = np.ones(n, bool)
    stopped[90:140] = False  # the unit was down while it sat still: not an instrument fault
    assert "frozen" not in {f["check"] for f in signals.health(grid(n), v, STEP, base, running=stopped)}

    pct = np.clip(noise(n) + np.where(np.arange(n) > 400, 60, 0), 0, 100)  # pinned at the top of its range
    oor = [f for f in signals.health(grid(n), pct, STEP, noise(n, 2), lo=0, hi=100) if f["check"] == "out_of_range"]
    assert oor and oor[0]["expected"] == 100 and near(oor[0]["start"], grid(n)[401], 0.2)


def test_redundant_transmitters_drifting_apart():
    n = 6 * 24 * 2
    a = noise(n, 5)
    b = a + 0.3 + np.random.default_rng(6).normal(0, 0.05, n)
    b[150:] += np.linspace(0, 4, n - 150)  # B walks away
    found = signals.redundant_drift(grid(n), a, b, STEP, a[:150], b[:150])  # baseline: before B walks
    assert len(found) == 1 and found[0]["start"] > grid(n)[150].isoformat() and abs(found[0]["expected"] + 0.3) < 0.05
    assert signals.redundant_drift(grid(n), a, a + 0.3, STEP) == []


def test_detect_changes_on_made_up_series():
    n = 6 * 24 * 4
    assert signals.detect_changes(grid(n), noise(n), STEP) == []

    v = noise(n)
    v[200:] += 10  # a step
    v[400:] += np.random.default_rng(7).normal(0, 4, n - 400)  # then the jitter grows
    found = signals.detect_changes(grid(n), v, STEP)
    kinds = {c["kind"]: c for c in found}
    assert set(kinds) == {"step", "variance"}
    assert near(kinds["step"]["start"], grid(n)[200], 0.5) and abs(kinds["step"]["after"] - kinds["step"]["before"] - 10) < 1
    assert near(kinds["variance"]["start"], grid(n)[400], 1.5) and kinds["variance"]["after"] > kinds["variance"]["before"]

    days = 30  # fouling: a slow climb under a daily swing, hourly values
    hourly = [T0 + timedelta(hours=i) for i in range(24 * days)]
    ramp = noise(len(hourly)) + np.linspace(0, 20, len(hourly)) + 3 * np.sin(np.arange(len(hourly)) * 2 * np.pi / 24)
    drift = [c for c in signals.detect_changes(hourly, ramp, 60) if c["kind"] == "drift"]
    assert len(drift) == 1 and 0.55 < drift[0]["per_day"] < 0.8


# --- C on Okan ------------------------------------------------------------------------------------

def _event(spec, unit, kind):
    return next(e for e in spec["events"] if e["unit"] == unit and e["kind"] == kind)


def test_health_finds_the_hunting_level_transmitter(conn, spec):
    fault = _event(spec, "20-V-2001", "erratic")
    out = signals.instrument_health(conn, PACK, fid(conn, "20-V-2001"), "2024-11-01T00:00", "2024-11-14T00:00")
    assert out["suspect"] == ["OK_GGCP:20-LT-2001.PV"]  # the pressure transmitter on the same vessel is fine
    noisy = [f for f in out["findings"] if f["flag"] == "noisy"]
    assert len(noisy) == 1 and near(noisy[0]["start"], fault["start"]) and near(noisy[0]["end"], fault["end"], 2)
    assert EVIDENCE <= set(noisy[0]) and noisy[0]["sources"] == [noisy[0]["point_id"]]


def test_changes_find_the_trips_the_fault_and_the_fouling(conn, spec):
    lt = signals.changes(conn, point(conn, "OK_GGCP:20-LT-2001.PV"), "2024-11-01T00:00", "2024-11-14T00:00")
    fault = _event(spec, "20-V-2001", "erratic")
    assert [(c["flag"], near(c["start"], t, 1.5)) for c, t in zip(lt["changes"], (fault["start"], fault["end"]))] == \
        [("variance", True), ("variance", True)]

    trips = [e["start"] for e in spec["events"] if e["unit"] == "train:C" and e["kind"] == "trip"
             and "2024-11-01" <= e["start"] < "2024-11-14"]
    out = signals.changes(conn, fid(conn, "20-K-2100C"), "2024-11-01T00:00", "2024-11-14T00:00")
    steps = [c for c in out["changes"] if c["flag"] == "step"]
    assert len(trips) == 2 and all(any(near(c["start"], t) for c in steps) for t in trips)

    foul = signals.changes(conn, fid(conn, "20-E-2350A"), "2024-04-06T00:00", "2024-08-18T00:00")
    drift = [c for c in foul["changes"] if c["flag"] == "drift"]
    assert drift and drift[0]["value"] > drift[0]["expected"] + 10 and drift[0]["per_day"] > 0


def test_signals_read_nothing_after_until(conn, data):
    """Clocked as in the replay: both read only up to `until` (health's baseline comes from before)."""
    clock = replay.Clocked(timeseries.SyntheticProvider(data), datetime(2024, 11, 6))
    out = signals.instrument_health(conn, PACK, fid(conn, "20-V-2001"), "2024-11-05T00:00", "2024-11-06T00:00", src=clock)
    assert out["suspect"] == ["OK_GGCP:20-LT-2001.PV"]
    signals.changes(conn, fid(conn, "20-V-2001"), "2024-11-05T00:00", "2024-11-06T00:00", src=clock)
    with pytest.raises(replay.Peek):
        signals.changes(conn, fid(conn, "20-V-2001"), "2024-11-05T00:00", "2024-11-06T06:00", src=clock)


# --- F: what changed ------------------------------------------------------------------------------

def test_what_changed_around_the_train_c_trip(conn):
    before = operations.what_changed(conn, "2024-11-05T00:00", "2024-11-05T06:00")
    after = operations.what_changed(conn, "2024-11-05T06:00", "2024-11-05T12:00")
    trip = before["downtime_started"]
    assert [d["at"] for d in trip] == ["2024-11-05T05:30"] and "Train C" in trip[0]["comment"]
    assert [d["id"] for d in after["downtime_ended"]] == [trip[0]["id"]] and not after["downtime_started"]
    assert before["counts"]["downtime_started"] == 1 and before["total"] == sum(before["counts"].values())
    day = operations.what_changed(conn, "2024-11-01", "2024-11-14", limit=2)
    assert day["counts"]["well_tests"] > 2 and len(day["well_tests"]) == 2  # counts are full, lists capped
    assert all(w["tag"] for w in day["work_orders_started"])


def test_dates_without_a_time_count_on_the_windows_of_their_day():
    a = datetime(2024, 11, 5, 6)
    assert operations._within("2024-11-05", a, a + timedelta(hours=6))
    assert not operations._within("2024-11-05", datetime(2024, 11, 6), datetime(2024, 11, 6, 6))
    assert not operations._within("2024-11-06", datetime(2024, 11, 5, 18), datetime(2024, 11, 6))  # until is exclusive
    assert operations._within("2024-11-05T05:30", datetime(2024, 11, 5), datetime(2024, 11, 5, 6))


# --- tools and charts ---------------------------------------------------------------------------

def test_tools_and_charts(conn, okan_pack):
    out = mcp_server.calc("pump_curve", "10-P-1001A", until="2025-09-01")
    assert out["flag"] == "head_loss" and out["check"] == "pump_curve"
    assert "error" in mcp_server.calc("decline", "20-K-2200B")  # a compressor has no well tests
    health = mcp_server.instrument_health("20-V-2001", "2024-11-01T00:00", "2024-11-14T00:00")
    assert health["suspect"] == ["OK_GGCP:20-LT-2001.PV"] and "series" not in health
    ch = mcp_server.detect_changes("20-K-2100C", "2024-11-01T00:00", "2024-11-14T00:00", limit=3)
    assert len(ch["changes"]) == 3 and ch["found"] > 3
    assert mcp_server.what_changed("2024-11-05T00:00", "2024-11-06T00:00")["counts"]["downtime_started"] == 1
    for tool, what in ((mcp_server.plot_instrument_health, "findings"), (mcp_server.plot_changes, "changes")):
        got = tool("20-V-2001", "2024-11-01T00:00", "2024-11-14T00:00")
        assert got["marker"] == f"[[figure:{got['figure']}]]" and got["summary"][what] and len(json.dumps(got)) < 6000
        with db.connect() as c:
            assert plots.png(plots.get(c, got["figure"])["spec"])[:4] == b"\x89PNG"
    names = {t.name for t in asyncio.run(mcp_server.server.list_tools())}
    assert {"calc", "instrument_health", "detect_changes", "what_changed", "plot_instrument_health", "plot_changes"} <= names
