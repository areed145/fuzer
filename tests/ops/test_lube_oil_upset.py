"""Okan's agent demo upset (#1148): train A's gas turbine lube oil filter, the day before the data's today. The SRE watch
raises it as one upset whose first cause is the turbine, apart from the wells' nuisance low alarms that keep coming back
all day, and a catch-up scan keeps the windows it finished when it is cut short."""

from datetime import datetime, timedelta

import pytest

from fuzer.core.pack import load_pack
from fuzer.ops import sre, sre_handoff

PACK = load_pack("okan")


@pytest.mark.parametrize("first", ["2025-12-30T00:00", "2025-12-29T18:00"])  # scan windows from 00:00 or from 23:00
def test_the_lube_oil_upset_is_raised_on_its_own_with_the_turbine_first(okan_db, first):
    from fuzer.workflow import engine

    c = okan_db.conn
    c.execute("delete from detection; delete from upset; delete from sre_run")
    c.commit()
    engine.load_defaults(c)
    sre.scan(c, PACK, "2025-12-29T12:00", first)
    sre.scan(c, PACK, None, "2025-12-31T00:00")
    label = {r["fuzer_id"]: r["label"] for r in c.execute("select fuzer_id, label from entity_label")}
    ups = [u for u in sre_handoff.candidates(c) if label.get(u["first_unit"]) == "20-GT-2000A"]
    assert len(ups) == 1, [label.get(u["first_unit"]) for u in sre_handoff.candidates(c)]
    units = {label.get(x) for x in ups[0]["units"]}
    assert {"20-GT-2000A", "20-K-2100A", "20-K-2200A", "20-K-2300A"} <= units
    assert not units & {"OK-J06-05", "OK-J08-03", "10-V-2004", "OK-J21-05"}  # the wells' nuisance alarms stay out
    first = sre.upset_detail(c, ups[0]["id"])["timeline"][0]
    assert first["start"].startswith("2025-12-30T13") and first["point"] == "OK_GGCP:20-PT-2005A.PV"


def test_an_alarm_that_keeps_coming_back_does_not_make_an_upset():
    t = datetime(2025, 12, 30)
    d = [{"point": "W.WHP", "key": "alarm:lo", "detector": "excursions", "unit_id": "w", "start": t + timedelta(hours=h),
          "end": t + timedelta(hours=h, minutes=30)} for h in (0, 4, 8, 12)]
    kinds = [g["kind"] for g in sre.group_detections(d, {})]
    assert kinds[:2] == ["process", "process"] and set(kinds[2:]) == {"standing"}


def test_a_scan_cut_short_carries_on_from_the_windows_it_kept(okan_db):
    c = okan_db.conn
    run = c.execute("insert into sre_run (since, until, status, reached) values "
                    "('2025-12-20T00:00', '2025-12-27T00:00', 'running', '2025-12-21T06:00') returning id").fetchone()["id"]
    c.commit()
    try:
        assert sre.watermark(c) >= datetime(2025, 12, 21, 6)
    finally:
        c.execute("delete from sre_run where id = %s", (run,))
        c.commit()
