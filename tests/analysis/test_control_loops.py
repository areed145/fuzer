"""Control loops (#355): Okan's loop list lands each loop as one entity owning its PV, SP, OP and mode points, and
the planted faults read on them: the test separator's sticky level valve, the export flow controller out of valve,
and the lift gas header's pressure controller left in manual. The loops are synthetic and say so."""

import json

from fuzer.analysis import control_loops as cl


def test_a_loops_points_resolve_as_one_entity(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "control_loops.json").read_text())
    all_loops = cl.loops(conn)
    assert {x["loop"] for x in all_loops} == set(key)
    lic = cl.loop(conn, "10-lic-2003")
    assert lic["holds"] == "10-V-2003" and not lic["missing"]
    assert lic["points"] == {r: f"OKA_PP.10LIC2003.{r.upper()}" for r in cl.ROLES}
    assert cl.loop(conn, "20-PIC-3003")["points"]["mode"] == "OK_GGCP:20-PIC-3003.MODE"


def test_the_planted_faults_read_on_the_loop_points(okan_db):
    conn = okan_db.conn
    stuck = cl.series(conn, "10-LIC-2003", days=1, until="2025-02-13T00:00")
    assert stuck["synthetic"] and len(stuck["times"]) > 250
    assert max(stuck["op"]) - min(stuck["op"]) > 8  # the output ramps across the stick band
    sp = stuck["sp"][0]
    assert sum(abs(v - sp) > 2.5 for v in stuck["pv"]) > 0.6 * len(stuck["pv"])  # the level sits off setpoint, jumping
    out = cl.series(conn, "10-FIC-1001", days=3, until="2025-11-25T00:00")
    assert sum(v >= 99.9 for v in out["op"]) > 0.8 * len(out["op"]) and max(out["pv"]) < out["sp"][0]
    man = cl.series(conn, "20-PIC-3003", days=3, until="2025-12-10T00:00")
    assert set(man["mode"]) == {0.0} and len(set(man["op"])) == 1
    ok = cl.series(conn, "10-LIC-2002", days=3, until="2025-12-10T00:00")
    assert set(ok["mode"]) == {1.0} and max(abs(p - s) for p, s in zip(ok["pv"], ok["sp"])) < 2.0
    assert max(ok["op"]) < 99


def test_each_planted_fault_ranks_at_the_top_of_the_worklist(okan_db):
    conn = okan_db.conn
    feb = cl.health(conn, days=5, until="2025-02-15T00:00")
    assert feb["loops"][0]["loop"] == "10-LIC-2003" and "oscillating" in feb["loops"][0]["flags"]
    assert feb["summary"]["flagged"] == ["10-LIC-2003"] and feb["synthetic"]
    dec = cl.health(conn, days=7, until="2025-12-01T00:00")
    top = {m["loop"]: m for m in dec["loops"][:2]}
    assert set(top) == {"10-FIC-1001", "20-PIC-3003"}
    assert "saturated" in top["10-FIC-1001"]["flags"] and top["20-PIC-3003"]["flags"][0] == "manual"
    assert all(not m["flags"] for m in dec["loops"][2:])
    assert "not a change request" in dec["note"]
    from fuzer.plots import general
    fig = general.loop_health(conn, days=7, until="2025-12-01T00:00")
    assert fig.summary["verdict"] == "flagged" and fig.data[0]["y"][-1] == dec["loops"][0]["loop"]  # worst on top


def test_a_loops_settings_are_dated_and_read_as_of_a_date(okan_db):
    """#358: the DCS configuration export lands each change of a loop's PID settings as dated values: the night
    shift's halved gain on the sticking level loop, then the gain put back once the valve was repaired."""
    conn = okan_db.conn
    lic = cl.settings(conn, "10-LIC-2003")
    assert [h["kc"] for h in lic["history"]] == [2.0, 1.0, 2.0] and lic["in_force"]["kc"] == 2.0
    assert cl.settings(conn, "10-LIC-2003", at="2025-02-14T00:00")["in_force"]["kc"] == 1.0  # detuned, still cycling
    assert "night shift" in lic["history"][1]["by"] and "MOC-OK-0219" in lic["history"][2]["by"]
    assert lic["history"][1]["effective_from"] == "2025-02-12T03:20" and lic["in_force"]["ti_s"] == 600.0
    assert "never writes to a controller" in lic["note"]
    assert len(cl.settings(conn, "10-LIC-2002")["history"]) == 1  # as commissioned
    tests = cl.step_tests(conn)
    assert {t["loop"] for t in tests} == {"10-FIC-1001", "10-PIC-2001", "20-PIC-3003"}
    fic = cl.step_tests(conn, "10-FIC-1001")[0]
    assert (fic["op_from"], fic["op_to"], fic["sample_s"]) == (72.0, 77.0, 1.0) and fic["step_at"] == "2025-06-10T10:02:00"


def test_a_high_rate_read_shows_a_fast_step_and_is_bounded(okan_db):
    """#358: a step test is read every second, fast enough to see the export flow answer the output within seconds,
    which the 5-minute grid can't; a read past the bounds is refused rather than pulling a history."""
    import pytest

    from fuzer.analysis.calcs import CalcError

    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "step_tests.json").read_text())["ST-2025-001"]
    got = cl.high_rate(conn, "10-FIC-1001", "2025-06-10T10:01:00", "2025-06-10T10:04:00", 1)
    assert got["synthetic"] and got["step_s"] == 1 and len(got["times"]) == 181 and got["times"][0] == "2025-06-10T10:01:00"
    assert set(got["mode"]) == {0.0} and got["op"][0] == 72.0 and got["op"][-1] == 77.0
    i = got["times"].index("2025-06-10T10:02:00")
    before, settled = got["pv"][i - 1], sum(got["pv"][-20:]) / 20
    assert settled - before == pytest.approx(key["gain"] * 5, rel=0.1)  # gain x step, settled within the minute
    assert abs(got["pv"][i + 1] - before) < 1.5  # still in the dead time a second after the step
    with pytest.raises(CalcError, match="at most 6 h"):
        cl.high_rate(conn, "10-FIC-1001", "2025-06-10T04:00:00", "2025-06-10T12:00:00", 10)
    with pytest.raises(CalcError, match="more than 7200 values"):
        cl.high_rate(conn, "10-FIC-1001", "2025-06-10T08:00:00", "2025-06-10T12:00:00", 1)
    assert len(cl.high_rate(conn, "10-FIC-1001", "2025-06-10T08:00:00", "2025-06-10T12:00:00", 2)["times"]) == 7201
