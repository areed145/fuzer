"""P4.2b-2 (#354): a control loop cycling is told from noise by the regularity of its autocorrelation, with its
period and amplitude; on Okan the test separator's level limit-cycles while its level valve sticks."""

import numpy as np
import pytest

from fuzer.analysis import calcs, loops
from fuzer.core.pack import load_pack
from fuzer.graph import queries

PACK = load_pack("okan")
N = 864  # three days of 5-minute readings
T = np.arange(N)


def _noise(seed: int, tau: float, sd: float) -> np.ndarray:
    rng, x, a = np.random.default_rng(seed), np.zeros(N), np.exp(-1 / tau)
    for i in range(1, N):
        x[i] = a * x[i - 1] + rng.normal() * sd
    return x


@pytest.mark.unit
def test_a_regular_cycle_is_found_with_its_period_and_noise_is_not():
    hunting = loops.cycling(121 + 2 * np.sin(2 * np.pi * T * 5 / 120) + _noise(1, 3, 0.3), 5)
    assert hunting["r"] > loops.REGULAR and loops._cycles(hunting)
    assert hunting["period_min"] == pytest.approx(120, rel=0.05) and hunting["amplitude"] == pytest.approx(2, rel=0.2)
    sawtooth = loops.cycling(45 + 4 * (2 * ((T * 5 / 60) % 1) - 1) + _noise(2, 4, 1.0), 5)
    assert loops._cycles(sawtooth) and sawtooth["period_min"] == pytest.approx(60, rel=0.05)
    for seed in range(20):  # noise, fast or slowly wandering, is never a cycle
        for tau in (3, 20, 30):
            assert not loops._cycles(loops.cycling(50 + _noise(seed, tau, 1.0), 5)), (seed, tau)
    assert loops.cycling([1.0] * 10, 5) is None  # too short to say


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


def test_the_test_separator_level_cycles_while_its_valve_sticks(conn):
    sep = queries.resolve_ref(conn, PACK, "10-V-2003")
    during = calcs.run(conn, "loop_oscillation", sep, until="2025-02-14T00:00")
    assert during["flag"] == "oscillating" and during["point"].endswith("LT2003.PV")
    assert during["period_min"] == pytest.approx(60, rel=0.1) and during["method"]["id"] == "loop_oscillation"
    after = calcs.run(conn, "loop_oscillation", sep, until="2025-03-01T00:00")
    assert after["flag"] is None and after["oscillating"] == []


def test_a_cycling_level_is_a_loop_candidate_until_it_stops(conn):
    """P3.1a: the screen as a detector. While the valve sticks the separator's level is raised for the engineer as a
    candidate (never a diagnosis), saying the values are synthetic; when it stops cycling the case clears."""
    import json

    from fuzer.core import detectors
    from fuzer.workflow import engine

    engine.load_defaults(conn)
    conn.commit()
    out = detectors.run(conn, PACK, "loop_oscillation", until="2025-02-14T00:00")
    assert not out["errors"], out
    points = [r["point"] for r in out["raised"]]
    own = [p for p in points if "LIC2003" in p]  # the sticking loop's own PV and OP (#355) cycle with its level
    assert any(p.endswith("LT2003.PV") for p in points) and len(points) - len(own) <= 3, points  # not the field
    x = conn.execute("select * from exception_record where type = 'loop_candidate' and state = 'open' and key like "
                     "'%%LT2003.PV'").fetchone()
    assert "loop candidate" in x["title"] and "every 60" in x["title"] and x["item_id"]
    assert conn.execute("select role from work_item where id = %s", (x["item_id"],)).fetchone()["role"] == "engineer"
    said = json.dumps(x["evidence"])
    assert "not a diagnosed loop" in said and "synthetic values" in said, said
    assert detectors.run(conn, PACK, "loop_oscillation", until="2025-02-14T00:00")["raised"][0]["new"] is False

    after = detectors.run(conn, PACK, "loop_oscillation", until="2025-03-01T00:00")
    assert x["key"] in after["cleared"], after
    assert conn.execute("select state from exception_record where id = %s", (x["id"],)).fetchone()["state"] != "open"
    conn.commit()
