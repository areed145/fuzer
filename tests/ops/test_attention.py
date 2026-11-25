"""Where attention is needed (Home's map): open exceptions gathered at the located places they are about."""

import os
import tempfile

import pytest

from fuzer.core import db
from fuzer.core.exceptions import TYPES, ExceptionType, clear_exception, raise_exception
from fuzer.ops import attention


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        from fuzer.workflow import engine

        engine.load_defaults(c)
        c.execute("insert into entity (fuzer_id, kind) values ('fz-jkt-7', 'area'), ('fz-well-73', 'well'), "
                  "('fz-tag-pt', 'tag'), ('fz-plat', 'area'), ('fz-pump', 'equipment'), ('fz-nowhere', 'equipment')")
        c.execute("insert into entity_key (kind, key, fuzer_id) values ('tag', 'PT-1', 'fz-tag-pt'), "
                  "('tag', 'P-201', 'fz-pump'), ('tag', 'X-1', 'fz-nowhere'), ('well', 'W-73', 'fz-well-73')")
        c.execute("insert into entity_label (fuzer_id, label) values ('fz-jkt-7', 'OKAN-67'), ('fz-plat', 'OKAN-40') "
                  "on conflict do nothing")
        c.execute("insert into golden (fuzer_id, field, value, source, rule) values "
                  "('fz-jkt-7', 'gis_name', '\"Jacket 07\"', 'gis', 'test') on conflict do nothing")
        for fid, kind, x, y in [("fz-jkt-7", "area", 4.94, 5.41), ("fz-well-73", "well", 4.941, 5.411),
                                ("fz-plat", "area", 4.99, 5.38)]:
            c.execute("insert into geo_index (fuzer_id, kind, geom_type, minx, miny, maxx, maxy, cx, cy, geometry) "
                      "values (%s, %s, 'Point', %s, %s, %s, %s, %s, %s, '{}')", (fid, kind, x, y, x, y, x, y))
        c.execute("insert into edge (src, dst, rel, sources, confidence) values "
                  "('fz-well-73', 'fz-jkt-7', 'part_of', '{test}', 1), ('fz-tag-pt', 'fz-well-73', 'part_of', '{test}', 1), "
                  "('fz-pump', 'fz-plat', 'installed_at', '{test}', 1)")
        c.commit()
        yield c


@pytest.fixture(scope="module")
def raised(conn):
    mp = pytest.MonkeyPatch()
    mp.setitem(TYPES, "trip", ExceptionType("trip", "Machine trip"))
    mp.setitem(TYPES, "finding", ExceptionType("finding", "Round finding"))
    mp.setitem(TYPES, "housekeeping", ExceptionType("housekeeping", "Register gap", role=attention.DATA_ROLE, joins=False))
    raise_exception(conn, "trip", "w73", "W-73 shut in, lift gas short", "sre", refs=["tag:PT-1"], priority=0.9)
    raise_exception(conn, "finding", "w73-seep", "Wellhead seepage", "rounds", refs=["well:W-73"], priority=0.3)
    raise_exception(conn, "finding", "p201", "Vibration on P-201", "rounds", refs=["tag:P-201"], priority=0.6)
    raise_exception(conn, "finding", "lost", "Something with no place", "rounds", refs=["tag:X-1"], priority=0.9)
    raise_exception(conn, "housekeeping", "crit", "Criticality not assessed", "steward", refs=["tag:P-201"])
    conn.commit()
    yield
    mp.undo()


def test_open_work_gathers_at_the_platform_an_operator_goes_to(conn, raised):
    out = attention.hotspots(conn)
    jacket, platform = [p for p in out["places"] if p["id"] in ("fz-jkt-7", "fz-plat")]
    # A tag on a well on a jacket lands on the jacket (a place), not the well, though the well has a location too.
    assert (jacket["id"], jacket["name"], jacket["band"], jacket["open"]) == ("fz-jkt-7", "Jacket 07", "high", 2)
    assert jacket["counts"] == {"high": 1, "medium": 0, "low": 1} and jacket["unassigned"] == 2
    assert [i["title"] for i in jacket["top"]] == ["W-73 shut in, lift gas short", "Wellhead seepage"]
    assert (platform["id"], platform["band"], platform["open"]) == ("fz-plat", "medium", 1)
    # Unlocated work is counted, not dropped; the data steward's register work is not somewhere to send anyone.
    assert out["unplaced"] == 1 and out["data_work"] >= 1 and out["open"] >= 4


def test_all_work_filtered_to_a_place_holds_what_its_marker_counts(conn, raised):
    from fuzer.ops import exceptions

    rows = exceptions.listing(conn, state="open", limit=1000)
    raise_exception(conn, "housekeeping", "w73-crit", "W-73 criticality not assessed", "steward", refs=["well:W-73"])
    conn.commit()
    rows = exceptions.listing(conn, state="open", limit=1000)
    assert sorted(r["title"] for r in attention.only_at(conn, rows, "fz-jkt-7")) == [
        "W-73 shut in, lift gas short", "Wellhead seepage"]


def test_all_work_filtered_to_not_located_holds_what_home_counts(conn, raised):
    from fuzer.ops import exceptions

    rows = exceptions.listing(conn, state="open", limit=1000)
    unplaced = attention.only_at(conn, rows, attention.UNPLACED)
    assert [r["title"] for r in unplaced] == ["Something with no place"]
    assert len(unplaced) == attention.hotspots(conn)["unplaced"]


def test_a_cleared_condition_leaves_the_map(conn, raised):
    at_platform = lambda: {p["id"]: p["open"] for p in attention.hotspots(conn)["places"]}.get("fz-plat")
    raise_exception(conn, "trip", "p201-trip", "P-201 tripped", "sre", refs=["tag:P-201"], priority=0.9)
    conn.commit()
    assert at_platform() == 2
    clear_exception(conn, "trip", "p201-trip", "sre", "restarted")
    conn.commit()
    assert at_platform() == 1


def test_platforms_side_by_side_are_one_marker_naming_both(conn, raised):
    from fuzer.ops import exceptions

    c = conn
    c.execute("insert into entity (fuzer_id, kind) values ('fz-pp', 'area'), ('fz-cp', 'area'), ('fz-sep', 'equipment'), "
              "('fz-comp', 'equipment') on conflict do nothing")
    c.execute("insert into entity_key (kind, key, fuzer_id) values ('tag', 'V-1', 'fz-sep'), ('tag', 'K-1', 'fz-comp') "
              "on conflict do nothing")
    for fid, x in (("fz-pp", 5.2), ("fz-cp", 5.2007)):  # about 80 m apart
        c.execute("insert into geo_index (fuzer_id, kind, geom_type, minx, miny, maxx, maxy, cx, cy, geometry) "
                  "values (%s, 'area', 'Point', %s, 5.5, %s, 5.5, %s, 5.5, '{}')", (fid, x, x, x))
    c.execute("insert into edge (src, dst, rel, sources, confidence) values ('fz-sep', 'fz-pp', 'part_of', '{test}', 1), "
              "('fz-comp', 'fz-cp', 'part_of', '{test}', 1)")
    raise_exception(c, "trip", "k1", "K-1 tripped", "sre", refs=["tag:K-1"], priority=0.95)
    raise_exception(c, "finding", "v1", "V-1 level drifting", "rounds", refs=["tag:V-1"], priority=0.6)
    c.commit()
    [spot] = [p for p in attention.hotspots(c)["places"] if "fz-pp" in p["places"]]
    assert sorted(spot["places"]) == ["fz-cp", "fz-pp"] and spot["open"] == 2 and spot["band"] == "high"
    rows = exceptions.listing(c, state="open", limit=1000)
    assert sorted(r["title"] for r in attention.only_at(c, rows, ",".join(spot["places"]))) == ["K-1 tripped",
                                                                                               "V-1 level drifting"]


def test_bands():
    assert [attention.band(p) for p in (None, 0.2, 0.5, 0.79, 0.8, 1.0)] == ["low", "low", "medium", "medium", "high", "high"]


def test_locate_names_the_place_a_feed_item_or_incident_sits_at(conn):
    conn.execute("insert into entity (fuzer_id, kind) values ('fz-inc-1', 'incident') on conflict do nothing")
    conn.execute("insert into edge (src, dst, rel, sources, confidence) values ('fz-inc-1', 'fz-tag-pt', 'about', '{test}', 1)")
    out = attention.locate(conn, ["fz-tag-pt", "fz-pump", "fz-inc-1", "fz-nowhere"])
    # A tag on a well on a jacket is at the jacket, as its open work is; an incident reaches it through what it is about.
    assert out["fz-tag-pt"] == {"id": "fz-jkt-7", "label": "OKAN-67", "name": "Jacket 07", "kind": "area", "lon": 4.94, "lat": 5.41}
    assert out["fz-inc-1"]["id"] == "fz-jkt-7"
    assert out["fz-pump"]["id"] == "fz-plat"
    assert "fz-nowhere" not in out
    conn.rollback()


def test_each_kind_of_work_has_one_label(conn, raised):
    """#1358: the List's rows, its Kind filter and every other page read one label per kind (GET /work/kinds)."""
    from fuzer.ops import exceptions, worklist

    labels = exceptions.work_labels(conn)
    assert labels["sap_order"] == "SAP order" and labels["writeback"] == "Write-back" and labels["trip"] == "Machine trip"
    assert exceptions.work_label(labels, "odd_kind") == "Odd kind"
    rows = worklist.rows(conn, kind="trip")["rows"]
    assert rows and {r["kind_label"] for r in rows} == {"Machine trip"}


def test_an_asset_is_filed_under_its_register_tag_not_a_serial_number():
    """#1358: the asset filter listed serial numbers. Equipment is labelled by serial number, so work on it is filed
    under the tag it is installed at; an instrument under the tag it is part of; a well stays the well."""
    from fuzer.ops.worklist import _asset

    tt, gt, sn = ({"id": "fz-tt", "kind": "tag", "label": "20-TT-2001"}, {"id": "fz-gt", "kind": "tag", "label": "20-GT-2000A"},
                  {"id": "fz-sn", "kind": "equipment", "label": "SN-4471"})
    area, well = {"id": "fz-a", "kind": "area", "label": "OKAN-20"}, {"id": "fz-w", "kind": "well", "label": "W-1"}
    assert _asset([sn, gt, area]) == gt, "equipment reads as the tag it is installed at"
    assert _asset([tt, gt, area]) == gt, "an instrument reads as the tag it is part of"
    assert _asset([tt, well, area]) == well
    assert _asset([sn, area]) == sn, "untagged equipment is still an asset"
    assert _asset([area]) is None


def test_the_list_filters_and_counts_work_by_facility_area_and_asset(conn, raised):
    """The List and Board (ops.worklist): each row's area and asset come from what it is about and what that sits in,
    so work can be filtered and counted by them, and `within` keeps the work about an entity or anything in it."""
    from fuzer.ops import worklist

    out = worklist.rows(conn, kind="trip,finding")
    by_title = {r["title"]: r for r in out["rows"]}
    trip = by_title["W-73 shut in, lift gas short"]
    assert (trip["area"], trip["asset"]) == ("fz-jkt-7", "fz-well-73"), "a tag on a well on a jacket"
    assert by_title["Vibration on P-201"]["asset"] == "fz-pump"
    assert by_title["Something with no place"]["area"] == "none"
    assert out["facets"]["area"]["fz-jkt-7"] == 2 and out["labels"]["fz-jkt-7"] == "OKAN-67"
    jacket = worklist.rows(conn, kind="trip,finding", area="fz-jkt-7")
    assert {r["title"] for r in jacket["rows"]} == {"W-73 shut in, lift gas short", "Wellhead seepage"}
    assert jacket["facets"]["area"]["fz-plat"] == 1, "the area's own counts leave its filter out"
    inside = worklist.rows(conn, kind="trip,finding", within="fz-well-73")
    assert {r["title"] for r in inside["rows"]} == {"W-73 shut in, lift gas short", "Wellhead seepage"}
    assert worklist.rows(conn, kind="trip,finding", asset="none")["total"] == 0
