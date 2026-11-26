"""Pipeline outage scheduling on Harrier (P5.22, #1253), from its bundle alone: its customer and contract register
loaded as a source, each outage on its plan with the contracts and delivery points it hits."""

import pytest

from fuzer.ops import outages, pipeline


@pytest.fixture(scope="module")
def c(harrier_db):
    pipeline.sync_plan(harrier_db)  # the plan from its sources: SAP orders and plans, and the mainline outage's schedule
    harrier_db.commit()
    return harrier_db


def test_harrier_outages_know_the_contracts_and_delivery_points_they_hit(c):
    """P5.22a: the contracts come in through the contracts source like any export, each linked to its delivery point,
    and an outage hits the ones whose delivery point it takes down or leaves with no feed, while they run."""
    book = {x["key"]: x for x in outages.contracts(c)}
    assert set(book) == {"HR-TSA-101", "HR-TSA-102", "HR-TML-201"}
    assert book["HR-TSA-101"]["customer"] == "Northgate Crude Marketing" and book["HR-TSA-101"]["point_label"] == "A1-FT-1010"
    assert book["HR-TML-201"]["point_label"] == "F1-FT-6010" and book["HR-TML-201"]["volume_bbl_d"] == 30000
    out = {o["key"]: o for o in outages.outages(c)["outages"]}
    pig = out["HR-PIG-2027"]  # the mainline outage, its fourteen activities as one
    assert len(pig["item_ids"]) == 14 and pig["hours"] > 24
    assert {u["label"] for u in pig["units"]} == {"A1-FT-1010", "D1-FT-4002"}
    # the marine loading contract starts after the pig run: only the two transportation contracts are hit
    assert {h["contract"] for h in pig["hits"]} == {"HR-TSA-101", "HR-TSA-102"}
    north = next(h for h in pig["hits"] if h["contract"] == "HR-TSA-101")
    assert north["point"] == "A1-FT-1010" and north["hours"] == pig["hours"]
    assert north["volume_bbl"] == round(38000 * pig["hours"] / 24) and north["usd"] == round(north["volume_bbl"] * 2.15)
    # the origin's relief valve test takes the origin meter down: everything downstream loses its feed, so the
    # marine loading contract at the custody meter is hit too
    valve = next(o for o in out.values() if o["title"] == "Relief valve bench test")
    assert {h["contract"] for h in valve["hits"]} == {"HR-TSA-101", "HR-TSA-102", "HR-TML-201"}
    # tank A's seal inspection takes the tank down: the custody meter it feeds is hit, not the origin's shippers
    tank = next(o for o in out.values() if o["title"].startswith("Floating roof seal inspection"))
    assert {h["contract"] for h in tank["hits"]} == {"HR-TML-201"} and tank["hits"][0]["point"] == "F1-FT-6010"


def test_okan_and_kestrel_load_no_contracts_and_their_outages_hit_no_one(okan_db, kestrel_db):
    """P5.22: the register is an optional source; a site that exports none has outages that hit no customer."""
    for conn in (okan_db.conn, kestrel_db):
        pipeline.sync_plan(conn)
        got = outages.outages(conn)
        conn.rollback()
        assert got["contracts"] == 0 and got["outages"], got
        assert all(not o["hits"] and o["usd"] is None for o in got["outages"])
