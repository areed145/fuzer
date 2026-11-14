"""Okan's flare system: the GGCP flare boom and the alternate stack on its own jacket, fed by both PP's and GGCP's
relief, blowdown and pressure control valves, metered by 25-FT-4003 from September 2025, and the daily flare allocated
back to PP and GGCP by the production accounting system (EC)."""

import pytest

from fuzer.analysis import flare, flare_review
from fuzer.graph import scene


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


def _id(conn, label):
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def test_the_flare_takes_relief_from_both_installations(conn):
    [s] = flare_review.systems(conn)
    assert s["label"] == "25-FT-4003" and s["devices"] == 13
    by = {g["area_label"]: g for g in s["sources"]}
    assert set(by) == {"OKAN-10", "OKAN-20"}
    assert "10-PSV-2001" in by["OKAN-10"]["devices"] and "10-V-2001" in by["OKAN-10"]["items"]
    assert "20-PV-3003" in by["OKAN-20"]["devices"] and "20-K-2300B" in by["OKAN-20"]["items"]
    assert "Production Platform" in by["OKAN-10"]["area_name"]


def test_the_unexplained_day_is_read_on_the_meter_once(conn):
    [e] = flare.events(conn, "2025-11-09T00:00", 3)
    assert e["source"] == "point" and e["label"] == "25-FT-4003" and not e["attributed"]
    assert e["start"].startswith("2025-11-08") and 150 < e["volume"] < 190  # the daily report's 170 kSm3 over routine
    # before the meter was installed, the daily report is still the flare's only reading
    assert all(x["source"] == "daily_report" for x in flare.events(conn, "2025-02-11T00:00", 3))


def test_ec_allocates_the_flare_back_to_pp_and_ggcp(conn):
    a = flare_review.allocation(conn, "2025-01-01", "2026-01-01")
    by = {g["label"]: g for g in a["sources"]}
    assert set(by) == {"OKAN-10", "OKAN-20"} and by["OKAN-20"]["ksm3"] > by["OKAN-10"]["ksm3"]
    assert "non-routine: export curtailment" in by["OKAN-20"]["by_kind"]
    assert "non-routine: export curtailment" not in by["OKAN-10"]["by_kind"]
    assert {"metered", "estimated"} <= set(by["OKAN-20"]["by_basis"])
    booked = sum(float(r["v"]) for r in conn.execute(
        "select g.value #>> '{}' as v from golden g join golden d on d.fuzer_id = g.fuzer_id and d.field = 'report_date'"
        " where g.field = 'flare_ksm3' and d.value #>> '{}' >= '2025-01-01'"))
    assert abs(a["ksm3"] - booked) < 1  # every kSm3 the daily report flared is allocated, no more
    assert flare_review.review(conn)["allocation"]["sources"]


def test_the_boom_and_the_alternate_stack_are_drawn_where_they_stand(conn):
    sc = scene.scene(conn)
    plates = {p["label"]: p for p in sc["plates"]}
    assert plates["OKAN-26"]["frame"] == "lattice" and plates["OKAN-25"]["items"] >= 3
    shape = {i["label"]: i["shape"] for i in sc["items"]}
    assert shape["25-FL-4010"] == "flare_boom" and shape["26-FL-4030"] == "flare" and shape["25-V-4001"] == "drum"
    assert sc["shapes"]["flare_boom"]["solid"] == "boom"
    flare_links = [x for x in sc["links"] if x["product"] == "flare"]
    assert flare_links  # the relief and flare lines carry the flare palette colour
    assert conn.execute("select 1 from edge where rel = 'part_of' and src = %s and dst = %s",
                        (_id(conn, "OKAN-25"), _id(conn, "OKAN-20"))).fetchone()  # the boom stands on GGCP
