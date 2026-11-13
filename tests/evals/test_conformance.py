"""Conformance (P8.2) on Kestrel: the operating model it runs, its one named deviation and whether it is justified,
its work in practice, and a deviation past its review date as work for its owner until it is reviewed."""

from datetime import timedelta

from fuzer.core import clock
from fuzer.workflow import conformance
from fuzer.workflow import operating_model as om


def test_a_deviation_past_its_review_is_work_for_its_owner_until_it_is_reviewed(kestrel_db):
    conn = kestrel_db
    om.adopt(conn, by="test")
    s = conformance.standing(conn)
    assert (s["model"], s["version"], s["current"], s["applied"]) == ("standard", 1, True, True)
    [d] = s["deviations"]
    assert (d["part"], d["key"], d["owner"], d["named"], d["overdue"]) == ("settings", "handover.shift_times",
                                                                          "ops_lead", True, False)
    assert d["review_by"] == (clock.today() + timedelta(days=om.REVIEW_DAYS)).isoformat()  # a year, by default
    assert s["verdict"] == "justified" and s["unnamed"] == 0
    assert set(s["practice"]) == {"days", "sla", "overrides", "skipped", "rounds"}

    # a year on, nobody has reviewed it: the view says it drifted and the owner gets the work
    om.name(conn, "settings", "handover.shift_times", d["reason"], "ops_lead", "test", clock.today() - timedelta(days=1))
    s = conformance.standing(conn)
    assert s["verdict"] == "drifted" and s["overdue"] == 1
    out = conformance.reviews(conn)
    assert len(out["raised"]) == 1 and out["raised"][0]["key"] == "settings:handover.shift_times", out
    row = conn.execute("select e.state, i.role from exception_record e join work_item i on i.id = e.item_id "
                       "where e.id = %s", (out["raised"][0]["exception_id"],)).fetchone()
    assert row["state"] == "open" and row["role"] == "ops_lead"

    # reviewed: kept, with the next review a year out; the work clears and the view is justified again
    om.name(conn, "settings", "handover.shift_times", d["reason"], "ops_lead", "test")
    assert conformance.reviews(conn)["cleared"] == 1
    assert conformance.standing(conn)["verdict"] == "justified"

    fleet = conformance.fleet(None)
    assert [p["project"] for p in fleet["projects"]] == ["kestrel"] and fleet["left_out"] == []
    assert fleet["projects"][0]["verdict"] == "justified"
