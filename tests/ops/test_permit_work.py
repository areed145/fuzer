"""P3.14a (#1181): the permit to work system brings work. On Okan, every fourth permit lapsed two days ago without
being closed, and most still hold their isolations: plant isolated under a permit nobody holds. Each is work for the
ops lead until the permit is closed or nothing on it is isolated."""

import pytest

from fuzer.core import detectors
from fuzer.core.pack import load_pack
from fuzer.ops import permits
from fuzer.sources import packs

PACK = load_pack("okan")


@pytest.mark.unit
def test_the_ptw_pack_brings_its_detector_and_work_type():
    import yaml

    assert packs.detectors_of(yaml.safe_load(packs.builtin("ptw"))) == ["permit_lapsed"]
    assert detectors.get("permit_lapsed").raises == (permits.TYPE,)


@pytest.mark.integration
def test_a_lapsed_permit_with_plant_isolated_is_work_until_closed(okan_db):
    conn = okan_db.conn
    try:
        found = permits.lapsed(conn)
        assert found and all(p["state"] == "expired" and p["held"] for p in found), found
        assert not any(p["label"].startswith("LPS-") for p in found)  # migrated permits are closed
        out = permits.watch_permits(conn)
        assert sorted(r["permit"] for r in out["raised"]) == sorted(p["label"] for p in found)
        assert all(r["new"] for r in out["raised"])
        first = found[0]
        x = conn.execute("select * from exception_record where type = 'permit_lapsed' and key = %s",
                         (first["label"],)).fetchone()
        assert x["state"] == "open" and x["item_id"] and "still in place" in x["title"], x["title"]
        assert len(x["subjects"]) >= 2  # its location and the permit
        assert all(not r["new"] for r in permits.watch_permits(conn)["raised"])  # the same work, not a second case

        # the permit is closed out: its work clears
        conn.execute("update golden set value = to_jsonb('closed'::text) where fuzer_id = %s and field = 'state'",
                     (first["id"],))
        done = permits.watch_permits(conn)
        assert done["cleared"] == [first["label"]]

        assert detectors.run(conn, PACK, "permit_lapsed")["raised"]  # as a schedule runs it
    finally:
        conn.rollback()
