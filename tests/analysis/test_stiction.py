"""Valve stiction (#357): a cycling loop's output and value tell a sticking valve from tuning or a disturbance by
their cross-correlation (Horch 1999). On Okan the test separator's sticky level valve is found while it sticks, the
healthy loops are not, and a suspected valve is handed to Maintenance & Integrity for the technician to confirm."""

import numpy as np
import pytest

from fuzer.analysis import stiction as st

T = np.arange(864)  # three days of 5-minute readings


def _noise(seed: int, sd: float) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, sd, len(T))


@pytest.mark.unit
def test_a_sticking_valve_reads_odd_and_a_tuning_cycle_reads_even():
    phase = T / 12  # a cycle every 12 readings (an hour)
    tri = 2 * np.abs(2 * (phase % 1) - 1) - 1  # the output ramps across the stick band...
    stuck = st.indicator(40 + 5 * tri + _noise(1, 0.3), 45 + 4 * np.tanh(4 * np.sin(2 * np.pi * phase)) + _noise(2, 0.2),
                         12)  # ...while the value jumps a quarter cycle later
    assert stuck["verdict"] == "stiction_suspected" and stuck["r0_share"] < st.ODD_MAX
    assert stuck["peak_before"] * stuck["peak_after"] < 0
    pv = 45 + 3 * np.sin(2 * np.pi * phase) + _noise(3, 0.2)  # an aggressive tuning: both sinusoidal, near antiphase
    tuned = st.indicator(40 - 6 * np.sin(2 * np.pi * phase - np.pi / 6) + _noise(4, 0.3), pv, 12)
    assert tuned["verdict"] == "tuning_or_disturbance" and tuned["r0_share"] >= st.EVEN_MIN
    assert st.indicator([1.0] * 20, [2.0] * 20, 12)["verdict"] == "inconclusive"  # too short and flat to say


def test_the_sticky_level_valve_is_found_and_the_healthy_loops_are_not(okan_db):
    conn = okan_db.conn
    feb = st.stiction(conn, days=3, until="2025-02-14T00:00")
    top = feb["loops"][0]
    assert feb["summary"]["suspected"] == ["10-LIC-2003"] and top["valve"] == "10-LV-2003" and feb["synthetic"]
    assert top["period_min"] == pytest.approx(60, rel=0.1) and top["r0_share"] < st.ODD_MAX
    assert top["caveat"] and "weaker indication" in top["caveat"]  # a level loop says the test is weaker there
    assert all(m["verdict"] == "not_cycling" for m in feb["loops"][1:]), feb["loops"][1:]
    assert "not a diagnosis" in feb["note"]
    after = st.stiction(conn, "10-LIC-2003", days=3, until="2025-03-01T00:00")
    assert after["verdict"] == "none_suspected" and after["loops"][0]["verdict"] == "not_cycling"
    manual = st.stiction(conn, "20-PIC-3003", days=3, until="2025-12-10T00:00")["loops"][0]
    assert manual["verdict"] == "not_tested" and manual["auto_pct"] == 0  # in manual, the controller isn't moving it


def test_a_suspected_valve_is_handed_to_maintenance_and_plotted(okan_db):
    from fuzer.agents.capabilities import operations
    from fuzer.plots import general

    conn = okan_db.conn
    got = operations.valve_stiction(days=3, until="2025-02-14T00:00")
    lic = got["loops"][0]
    assert lic["route"]["agent"] == "maintenance-integrity" and lic["route"]["label"] == "Maintenance & Integrity"
    assert "10-LV-2003" in lic["route"]["why"] and all("route" not in m for m in got["loops"][1:])
    fig = general.valve_stiction(conn, "10-LIC-2003", days=1, until="2025-02-14T00:00")
    assert fig.summary["verdict"] == "stiction_suspected" and fig.data[0]["x"] and fig.data[0]["y"]
    assert fig.layout["xaxis"]["title"]["text"].startswith("controller output")
