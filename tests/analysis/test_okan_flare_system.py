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
    assert e["start"].startswith("2025-11-08") and 5300 < e["volume"] < 6700  # the daily report's 6,000 Mscf over routine
    # before the meter was installed, the daily report is still the flare's only reading
    assert all(x["source"] == "daily_report" for x in flare.events(conn, "2025-02-11T00:00", 3))


def test_ec_allocates_the_flare_back_to_pp_and_ggcp(conn):
    a = flare_review.allocation(conn, "2025-01-01", "2026-01-01")
    by = {g["label"]: g for g in a["sources"]}
    assert set(by) == {"OKAN-10", "OKAN-20"} and by["OKAN-20"]["mscf"] > by["OKAN-10"]["mscf"]
    assert "non-routine: export curtailment" in by["OKAN-20"]["by_kind"]
    assert "non-routine: export curtailment" not in by["OKAN-10"]["by_kind"]
    assert {"metered", "estimated"} <= set(by["OKAN-20"]["by_basis"])
    booked = sum(float(r["v"]) for r in conn.execute(
        "select g.value #>> '{}' as v from golden g join golden d on d.fuzer_id = g.fuzer_id and d.field = 'report_date'"
        " where g.field = 'flare_mscf' and d.value #>> '{}' >= '2025-01-01'"))
    assert abs(a["mscf"] - booked) < 1  # every Mscf the daily report flared is allocated, no more
    assert flare_review.review(conn)["allocation"]["sources"]


def test_the_boom_and_the_alternate_stack_are_drawn_where_they_stand(conn):
    sc = scene.scene(conn)
    plates = {p["label"]: p for p in sc["plates"]}
    plate_of = {i["label"]: i["plate"] for i in sc["items"]}
    # the flare deck stands on GGCP's jacket: drawn on GGCP's plate, never a jacket of its own; only the alternate
    # stack has its own jacket
    assert "OKAN-25" not in plates and plates["OKAN-26"]["frame"] == "lattice"
    assert plate_of["25-FL-4010"] == plate_of["25-V-4001"] == plate_of["20-V-2001"] == plates["OKAN-20"]["id"]
    assert plate_of["26-FL-4030"] == plates["OKAN-26"]["id"]
    for layout in ("schematic", "geo"):  # the boom's own scene is GGCP's, with the boom on its deck
        own = scene.scene(conn, _id(conn, "25-FL-4010"), layout=layout)
        assert own["root"]["label"] == "OKAN-20" and own["focus"]
        assert {i["id"]: i["label"] for i in own["items"]}[own["focus"]] == "25-FL-4010"
        assert "OKAN-25" not in {p["label"] for p in own["plates"]}
    assert not conn.execute("select 1 from geo_index where fuzer_id = %s", (_id(conn, "OKAN-25"),)).fetchone()
    shape = {i["label"]: i["shape"] for i in sc["items"]}
    assert shape["25-FL-4010"] == "flare_boom" and shape["26-FL-4030"] == "flare" and shape["25-V-4001"] == "drum"
    assert sc["shapes"]["flare_boom"]["solid"] == "boom"
    flare_links = [x for x in sc["links"] if x["product"] == "flare"]
    assert flare_links  # the relief and flare lines carry the flare palette colour
    assert conn.execute("select 1 from edge where rel = 'part_of' and src = %s and dst = %s",
                        (_id(conn, "OKAN-25"), _id(conn, "OKAN-20"))).fetchone()  # the boom stands on GGCP


def test_the_flare_deck_sheet_draws_the_boom_and_its_valves_on_their_lines(okan_data):
    """OKAN-25-PID-010: the meter on the drum outlet before the changeover tee, the boom's isolation valve on its leg,
    the alternate stack's on the line to its jacket; none of them on a header of their own at the foot."""
    import json

    truth = json.loads((okan_data / "pid_truth.json").read_text())["OKAN-25-PID-010"]
    assert truth["header"] is None
    via = {(c["from"], c["to"]): c["via"] for c in truth["connections"]}
    assert "25-XV-4020" in via[("25-V-4001", "25-FL-4010")]
    assert "25-XV-4021" in via[("25-V-4001", "26-V-4032")]
    meter = next(t for t in truth["tags"] if t["key"] == "25-FT-4003")
    drum = next(t for t in truth["tags"] if t["key"] == "25-V-4001")
    boom = next(t for t in truth["tags"] if t["key"] == "25-FL-4010")
    assert drum["x"] < meter["x"] < boom["x"]
    # the drum's level valve is on its drain, not on the flare line
    [lv] = [v for v in truth["valves"] if v.get("tag", "").startswith("25-LV")]
    assert lv["y"] > drum["y"]  # (drum["y"] is its tag, under the drum)
