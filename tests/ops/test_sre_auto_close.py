"""The SRE's auto-close gate (P5.10) on Okan's 5 November 2024 scan, in a database of its own: it calls a pattern a
false alarm eighteen times and closes the case that pattern's upsets share, which the hand-off tests beside it
(test_sre_handoff.py) triage themselves."""

import test_sre_handoff as handoff_tests
from psycopg.types.json import Jsonb
from test_sre_handoff import PACK, _repeat, _said

conn, handed_off = handoff_tests.conn, handoff_tests.handed_off  # the same fixtures, in this module's own database


def test_a_pattern_people_keep_calling_a_false_alarm_closes_itself_except_on_a_critical_asset(conn, handed_off):
    """At 95% (18 false alarms, none real) the auto-close gate lets the SRE close a repeat as a false alarm; the same
    repeat on a high-criticality asset still stops for a person (P5.10)."""
    from fuzer.ops import sre_handoff
    from fuzer.workflow import engine

    _, out, _, fake_ask = handed_off
    from fuzer.core import gates

    def crit(upset_id):
        u = conn.execute("select first_unit, units from upset where id = %s", (upset_id,)).fetchone()
        return min((c for c in (gates.criticality(conn, x) for x in [u["first_unit"], *u["units"]]) if c), default=None)

    seed = next(i["upset"] for i in out["items"] if crit(i["upset"]) != "A")  # not a high-criticality asset already
    pattern = conn.execute("select d.point_id, d.detector, d.key from upset u join detection d on d.id = u.first_detection "
                           "where u.id = %s", (seed,)).fetchone()
    conn.execute("insert into sre_suppress (point_id, detector, key, upset_id) values (%s, %s, %s, %s) "
                 "on conflict do nothing", (pattern["point_id"], pattern["detector"], pattern["key"], seed))
    # the module's other tests may already have closed a repeat of it as a false alarm (they run in any order)
    while sre_handoff.history(conn, tuple(pattern.values()))["false_alarms"] < 18:
        conn.execute("update upset set status = 'false_alarm' where id = %s", (_repeat(conn, seed),))
    was = None
    unit = conn.execute("select first_unit from upset where id = %s", (seed,)).fetchone()["first_unit"]
    conn.commit()
    try:
        assert sre_handoff.false_alarm_confidence(sre_handoff.history(conn, tuple(pattern.values()))) == 0.95
        closes = _repeat(conn, seed)
        conn.commit()
        [mine] = [i for i in sre_handoff.handoff(conn, PACK, ask=fake_ask)["items"] if i["upset"] == closes]
        assert mine["false_alarm"]["auto_closed"] is True
        assert engine.item(conn, mine["item_id"])["state"] == "cancelled"
        assert conn.execute("select status from upset where id = %s", (closes,)).fetchone()["status"] == "false_alarm"
        assert "95% meets the 95% needed" in _said(conn, mine["item_id"])

        was = conn.execute("select * from golden where fuzer_id = %s and field = 'criticality'", (unit,)).fetchone()
        conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, 'criticality', '\"A\"', "
                     "'test', 'test') on conflict (fuzer_id, field) do update set value = excluded.value", (unit,))
        stops = _repeat(conn, seed)
        conn.commit()
        [mine] = [i for i in sre_handoff.handoff(conn, PACK, ask=fake_ask)["items"] if i["upset"] == stops]
        assert mine["false_alarm"]["auto_closed"] is False and mine["false_alarm"]["threshold"] is None
        assert engine.item(conn, mine["item_id"])["state"] not in ("done", "cancelled")
        assert "always stops for a person" in _said(conn, mine["item_id"])
    finally:
        conn.execute("delete from golden where fuzer_id = %s and field = 'criticality'", (unit,))
        if was:
            conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, %s, %s, %s, %s)",
                         (unit, "criticality", Jsonb(was["value"]), was["source"], was["rule"]))
        conn.commit()
