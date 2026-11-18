"""P3.13a (#1180): the materials records bring work. On Okan, the reservations plant a part whose purchase order lands
five days after its job needs it, and a part with none in stock, nothing on order and a planned delivery time longer
than the time left (the answer key names both). Each is work on its order for the maintenance planner until the part
is covered."""

import json

import pytest

from fuzer.core import detectors
from fuzer.core.pack import load_pack
from fuzer.ops import materials
from fuzer.sources import packs

PACK = load_pack("okan")


@pytest.mark.unit
def test_the_sap_pack_brings_the_parts_detector_and_its_work_type():
    import yaml

    assert "parts_not_ready" in packs.detectors_of(yaml.safe_load(packs.builtin("sap")))
    assert detectors.get("parts_not_ready").raises == (materials.TYPE,)


@pytest.mark.integration
def test_a_job_whose_parts_wont_arrive_is_work_until_covered(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "material_readiness.json").read_text())
    late, short = key["late_order"]["work_order"], key["not_on_order"]["work_order"]
    try:
        out = materials.watch_parts(conn)
        got = {r["work_order"]: r for r in out["raised"]}
        assert {late, short} <= set(got) and all(r["new"] for r in got.values()), out
        x = conn.execute("select * from exception_record where type = 'parts_not_ready' and key = %s", (late,)).fetchone()
        assert x["state"] == "open" and x["item_id"] and "on order too late" in x["title"], x["title"]
        assert len(x["subjects"]) >= 2  # the order and the equipment it concerns
        assert all(not r["new"] for r in materials.watch_parts(conn)["raised"])  # the same work, not a second case

        # stock arrives for every part on the short order: its work clears
        order = next(o for o in materials.not_ready(conn) if o["work_order"] == short)
        for line in order["lines"]:
            conn.execute("update golden set value = to_jsonb('9'::text) where fuzer_id = %s and field = 'stock'",
                         (line["material_id"],))
        done = materials.watch_parts(conn)
        assert short in done["cleared"] and short not in {r["work_order"] for r in done["raised"]}

        assert detectors.run(conn, PACK, "parts_not_ready")["raised"]  # as a schedule runs it
    finally:
        conn.rollback()
