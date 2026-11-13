"""Process model fits and tuning ranges (#359): a step test's high-rate response fits a first order plus dead time
model that recovers the fake plant's known one within a stated tolerance; the SIMC range it suggests carries its
method, data window and fit quality; the export flow's retune is compared before and after; and no tool can write to
a controller."""

import json

import numpy as np
import pytest

from fuzer.analysis import loop_tuning as lt

# The tolerance a fit recovers a known model within: gain within 10 %, time constant within 15 %, and dead time within
# a second plus 5 % of the time constant (on a slow loop the first seconds of the answer sit under the noise, and what
# tuning reads is the dead time against the time constant).
GAIN_TOL, TAU_TOL = 0.10, 0.15
dead_tol = lambda tau: 1.0 + 0.05 * tau


@pytest.mark.unit
def test_a_fit_recovers_a_known_model_and_simc_gives_a_range():
    t = np.arange(0, 600.0)
    op = np.where(t >= 60, 55.0, 50.0)
    pv = 30 + lt.fopdt(t - 60, 0.8, 45.0, 8.0, 5.0) + np.random.default_rng(1).normal(0, 0.03, len(t))
    f = lt.fit_step(t, op, pv, 60)
    assert f["gain"] == pytest.approx(0.8, rel=GAIN_TOL) and f["tau_s"] == pytest.approx(45, rel=TAU_TOL)
    assert abs(f["dead_s"] - 8) < dead_tol(45) and f["r2"] > 0.99 and f["settled"] and f["step"] == 5.0
    s = lt.simc(0.8, 45.0, 8.0)
    assert s["tight"]["kc"] == pytest.approx(45 / (0.8 * 16), rel=1e-3) and s["tight"]["ti_s"] == 45.0
    assert s["smooth"]["kc"] == pytest.approx(45 / (0.8 * 32), rel=1e-3) and s["kc"] == [s["smooth"]["kc"], s["tight"]["kc"]]
    assert s["action"] == "reverse" and lt.simc(-0.8, 45.0, 8.0)["action"] == "direct"
    with pytest.raises(lt.CalcError, match="barely moved"):
        lt.fit_step(t, np.full_like(t, 50.0), pv, 60)


def test_each_step_test_recovers_its_known_model(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "step_tests.json").read_text())
    for test, truth in key.items():
        got = lt.tuning(conn, test)
        m = got["model"]
        assert got["loop"] == truth["loop"] and got["synthetic"], test
        assert m["gain"] == pytest.approx(truth["gain"], rel=GAIN_TOL), (test, m)
        assert m["tau_s"] == pytest.approx(truth["tau_s"], rel=TAU_TOL), (test, m)
        assert abs(m["dead_s"] - truth["dead_s"]) < dead_tol(truth["tau_s"]), (test, m)
        assert got["fit_quality"]["r2"] >= lt.R2_MIN and got["suggested"], (test, got["why_no_suggestion"])
        assert got["window"]["sample_s"] == 1 and got["window"]["readings"] > 800 and "SIMC" in got["method"]
        assert got["assumptions"] and "nothing is written to a controller" in got["boundary"]
    fic = lt.tuning(conn, "10-FIC-1001")  # the latest test on the loop
    assert fic["test"] == "ST-2025-001" and fic["in_force_at_test"]["kc"] == 0.45
    lo, hi = fic["suggested"]["kc"]
    assert lo < hi and fic["model"]["gain_pct_per_pct"] == pytest.approx(5.2 * 100 / 600, rel=GAIN_TOL)


def test_the_retune_is_compared_before_and_after(okan_db):
    conn = okan_db.conn
    fic = lt.tuning(conn, "ST-2025-001")
    assert [c["kc"] for c in fic["changed_since"]] == [0.3]
    ba = fic["before_after"]
    assert ba["change"]["effective_from"] == "2025-06-12T14:00" and ba["windows"]["before"][1] == "2025-06-12T14:00"
    assert ba["setpoint_error"]["sd_change_pct"] < -30  # it holds its setpoint tighter after the retune
    assert ba["process_value_median"] is not None and "compare_periods" in ba["reads"]
    assert lt.tuning(conn, "ST-2025-002")["before_after"] is None  # no change since that test


def test_no_tool_can_write_to_a_controller():
    """The boundary in code: every tool is a query, a calc or a plot, except the four workflow tools, which move work
    items between agents (tests/agents/test_agent_boundary.py); the loop tools are reads; and the historian clients
    only read (GET, PI's batch of GETs, ADX's query endpoint). Fuzer holds no DCS write path at all."""
    import inspect
    import re

    from fuzer.agents import capabilities, mcp_server  # noqa: F401  (declaring the tools fills the registry)
    from fuzer.core.capabilities import REGISTRY
    from fuzer.sources import adx, pi_web_api

    work = {n for n, c in REGISTRY.items() if c.kind == "work"}
    assert work == {"claim_work", "complete_work", "hand_off", "send_evidence"}, work
    assert all(c.kind in ("query", "calc", "plot", "work") for c in REGISTRY.values())
    loops = {"control_loop", "loop_health", "valve_stiction", "controller_settings", "loop_high_rate", "loop_tuning",
             "plot_loop_health", "plot_valve_stiction", "plot_step_fit"}
    assert loops <= set(REGISTRY) and all(REGISTRY[n].kind in ("query", "calc", "plot") for n in loops)
    for mod in (pi_web_api, adx):
        src = inspect.getsource(mod)
        assert not re.search(r"\.(put|patch|delete)\(", src), mod.__name__
        assert set(re.findall(r'\.post\(f"\{self\.url\}(/[^"]*)"', src)) <= {"/batch", "/v1/rest/query"}, mod.__name__
    assert '"Method": "GET"' in inspect.getsource(pi_web_api.Client.batch)  # the batch only carries reads


def test_the_tool_and_chart_carry_the_fit_and_its_boundary(okan_db):
    from fuzer.agents.capabilities import operations
    from fuzer.plots import general

    got = operations.loop_tuning("ST-2025-002")
    assert got["loop"] == "10-PIC-2001" and got["suggested"]["action"] == "direct"  # opening the valve lowers pressure
    assert "management of change" in got["boundary"] and "error" in operations.loop_tuning("10-LIC-2002")
    fig = general.step_fit(okan_db.conn, "ST-2025-002")
    assert [d["name"] for d in fig.data] == ["value (every second)", "fitted model", "controller output (%)"]
    assert fig.summary["fit_quality"]["r2"] >= 0.9 and fig.summary["window"]["sample_s"] == 1
