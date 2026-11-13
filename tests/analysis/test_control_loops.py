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
