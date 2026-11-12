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
