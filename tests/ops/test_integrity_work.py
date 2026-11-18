"""P3.12a (#1179): the inspection source pack brings work. On Okan, OK-PL-005's wall loss leaves it under five years
to retirement, and four vessels' condition inspection plans are past their next date at the end of 2025. Each is
work for the engineer until a later survey or plan date takes the finding away."""

import pytest

from fuzer.core import detectors
from fuzer.core.pack import load_pack
from fuzer.ops import integrity
from fuzer.sources import packs

PACK = load_pack("okan")
OVERDUE = {"20-V-2001", "20-V-2002", "30-V-2001", "40-V-2001"}


@pytest.mark.unit
def test_the_inspection_pack_brings_its_detector_and_work_type():
    import yaml

    assert packs.detectors_of(yaml.safe_load(packs.builtin("inspection"))) == ["inspection_findings", "ili_anomalies"]
    assert detectors.get("inspection_findings").raises == (integrity.TYPE,)
    assert detectors.get("ili_anomalies").raises == (integrity.ILI_TYPE,)


@pytest.mark.integration
def test_an_item_running_out_of_wall_or_overdue_is_work_until_it_is_not(okan_db):
    conn = okan_db.conn
    try:
        out = integrity.watch_inspections(conn, now="2025-12-30")
        got = {r["item"]: r for r in out["raised"]}
        assert set(got) == OVERDUE | {"OK-PL-005"}, out
        assert got["OK-PL-005"]["reasons"] == ["retire_soon"] and all(r["new"] for r in got.values())
        assert all(got[v]["reasons"] == ["plan_overdue"] for v in OVERDUE)
        x = conn.execute("select * from exception_record where type = 'inspection_due' and key = 'OK-PL-005'").fetchone()
        assert x["state"] == "open" and x["item_id"] and "years of wall left" in x["title"], x["title"]
        v = conn.execute("select * from exception_record where type = 'inspection_due' and key = '30-V-2001'").fetchone()
        assert "inspection plan" in v["title"] and "days overdue" in v["title"], v["title"]
        assert [r["new"] for r in integrity.watch_inspections(conn, now="2025-12-30")["raised"]] == [False] * 5

        # the plan is done: its next date moves on, and the vessel's work clears
        plan = integrity.findings(conn, "2025-12-30")
        plan_id = next(f["plan"]["id"] for f in plan if f["item"] == "30-V-2001")
        conn.execute("update golden set value = to_jsonb('2028-04-15'::text) where fuzer_id = %s and field = 'next_due'",
                     (plan_id,))
        done = integrity.watch_inspections(conn, now="2025-12-30")
        assert done["cleared"] == ["30-V-2001"] and "30-V-2001" not in {r["item"] for r in done["raised"]}

        assert detectors.run(conn, PACK, "inspection_findings", now="2025-12-30")["raised"]  # as a schedule runs it
    finally:
        conn.rollback()
