"""P3.14a (#1181): the permit to work system brings work. On Okan, every fourth permit lapsed two days ago without
being closed, and most still hold their isolations: plant isolated under a permit nobody holds. Each is work for the
ops lead until the permit is closed or nothing on it is isolated."""

from datetime import UTC, datetime

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


NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _iso(id: str, state: str, point: str = "83-HCV-1004 inlet valve") -> dict:
    return {"id": f"fz-iso-{id}", "isolation": f"ISO-{id}", "point": point, "method": "closed and locked",
            "state": state}


def _permit(state="issued", valid_to="2026-11-25T06:00:00+00:00", isolations=None) -> dict:
    return {"id": "fz-permit-1", "label": "PTW-30031", "type": "cold work", "state": state, "valid_to": valid_to,
            "work": "Maintenance on production choke", "issued_to": "Mechanical maintenance crew",
            "locations": ["fz-tag-choke"], "location_labels": ["83-HCV-1004"],
            "isolations": [_iso("1", "applied"), _iso("2", "verified"), _iso("3", "planned")]
            if isolations is None else isolations}


@pytest.mark.unit
@pytest.mark.parametrize("row, n", [
    (_permit(), 2),                                         # 6 h past its valid-to: the applied and the verified
    (_permit(valid_to="2026-11-26T06:00:00+00:00"), 0),     # still in date
    (_permit(state="expired", valid_to="2026-11-26T06:00:00+00:00"), 2),  # the register says expired
    (_permit(state="expired", valid_to=None), 2),
    (_permit(state="issued", valid_to=None), 0),            # no valid-to and not marked expired: not lapsed
    (_permit(state="Closed"), 0), (_permit(state="cancelled"), 0),
    (_permit(isolations=[_iso("1", "removed"), _iso("2", "planned")]), 0),  # lapsed, nothing isolated: paperwork
    (_permit(isolations=[]), 0),
])
def test_a_permit_is_lapsed_work_only_while_past_date_open_and_isolating(row, n):
    assert len(permits.held(row, NOW)) == n


@pytest.mark.unit
def test_the_case_reads_as_the_permit_its_isolations_and_where():
    p = {**_permit(), "held": permits.held(_permit(), NOW), "points": ["fz-tag-choke", "fz-valve-1"]}
    assert permits.title(p) == "Permit PTW-30031 lapsed with 2 isolations still in place on 83-HCV-1004"
    assert permits.title({**p, "held": p["held"][:1]}).startswith("Permit PTW-30031 lapsed with 1 isolation still")
    # its location first, the isolation points (each once), then the permit
    assert permits.subjects(p) == ["fz-tag-choke", "fz-valve-1", "fz-iso-1", "fz-iso-2", "fz-permit-1"]
    f = permits.finding(p)
    shown = {i.label: i.value for i in f.items}
    assert shown["Permit"] == "PTW-30031 (cold work)" and shown["Location"] == "83-HCV-1004"
    assert shown["Valid to"].startswith("2026-11-25T06:00")
    assert shown["Isolation ISO-1"] == "83-HCV-1004 inlet valve: closed and locked (applied)"
    assert shown["Isolation ISO-2"].endswith("(verified)") and "Isolation ISO-3" not in shown  # planned: not held
    assert f.data["isolations"] == ["ISO-1", "ISO-2"]
    bare = {**p, "valid_to": None, "location_labels": []}
    assert {i.label: i.value for i in permits.finding(bare).items}["Valid to"] == "not recorded"
    assert permits.title(bare).endswith("on its location")


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
        first, second = found[0], found[1]
        x = conn.execute("select * from exception_record where type = 'permit_lapsed' and key = %s",
                         (first["label"],)).fetchone()
        assert x["state"] == "open" and x["item_id"], x
        n = len(first["held"])
        assert x["title"] == f"Permit {first['label']} lapsed with {n} isolations still in place on " \
                             f"{', '.join(first['location_labels'])}", x["title"]
        # its location, the isolations still in place and the permit
        assert set(x["subjects"]) >= {*first["locations"], *(i["id"] for i in first["held"]), first["id"]}
        shown = {i["label"]: i["value"] for i in x["evidence"]["finding"]["items"]}
        assert {f"Isolation {i['isolation']}" for i in first["held"]} <= set(shown) and "Valid to" in shown
        assert all(not r["new"] for r in permits.watch_permits(conn)["raised"])  # the same work, not a second case

        # the permit is closed out: its work clears
        conn.execute("update golden set value = to_jsonb('closed'::text) where fuzer_id = %s and field = 'state'",
                     (first["id"],))
        done = permits.watch_permits(conn)
        assert done["cleared"] == [first["label"]]
        # the plant is reinstated under the other: its isolations removed, its work clears too
        conn.execute("update golden set value = to_jsonb('removed'::text) where fuzer_id = any(%s) and field = 'state'",
                     ([i["id"] for i in second["held"]],))
        assert permits.watch_permits(conn)["cleared"] == [second["label"]]

        assert detectors.run(conn, PACK, "permit_lapsed")["raised"]  # as a schedule runs it
    finally:
        conn.rollback()
