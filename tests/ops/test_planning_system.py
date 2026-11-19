"""The planning system's export in the Okan data (P5.18): its built-in source loads with the rest, so its projects
are schedules on the integrated plan after any register build, with no source to add first."""

from fuzer.core.pack import load_pack
from fuzer.ops import pipeline, schedule


def test_okan_s_planning_system_schedules_are_on_the_plan(okan_db):
    conn = okan_db.conn
    kinds = conn.execute("select count(*) as n from entity where kind = 'activity' and retired_at is null").fetchone()
    assert kinds["n"] >= 20
    out = pipeline.follow_register(conn, load_pack("okan"))["plan"]["planning_system"]
    assert out["schedules"] >= 2 and out["activities"] >= 10
    codes = {r["code"] for r in conn.execute("select code from plan_project where created_by = 'p6'")}
    assert {"OK-CAP-0142", "OK-CAP-0157"} <= codes
    board = schedule.analyse(conn, "OK-CAP-0157")
    assert len(board["activities"]) == 3 and all(a["start"] for a in board["activities"])
    # the turnaround's activities in the export execute SAP orders, which are their groups on the Gantt
    tar = conn.execute("select count(*) as n from plan_item where project = 'OK-TAR-2026' and source = 'schedule' "
                       "and parent_id is not null").fetchone()
    assert tar["n"] >= 5
