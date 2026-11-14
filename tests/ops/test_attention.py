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
