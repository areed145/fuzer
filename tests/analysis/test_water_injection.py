"""Water injection (#365): voidage replacement month by month and each injector's Hall plot, on Okan's three NWP
injectors. A window with no injection says so instead of a ratio of zero."""

import json
from itertools import pairwise

from fuzer.analysis import water_injection as wi


def test_voidage_replacement_reads_injection_against_produced_reservoir_volume(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "injection.json").read_text())
    v = wi.voidage(conn)
    assert set(v["summary"]["injectors"]) == set(key["injectors"])
    assert not v["missing_pvt"] and v["pvt"]["oil_fvf"]["method"] == "pvt study"
    first = key["first_injection"][:7]
    for r in v["months"]:
        if r["month"] < first:
            assert r["status"] == "no_injection" and r["vrr"] is None, r
        else:
            assert r["status"] == "injecting" and 0 < r["vrr"] < 2, r
            assert r["voidage_rm3"] > r["oil_sm3"]  # oil shrinks to the stock tank; water and free gas add to it
    # Okan's three injectors replace about two thirds of what it produces, and less as OK-NWP-W2 plugs
    assert v["verdict"] == "under_replacing" and v["summary"]["latest_vrr"] < wi.VRR_LOW
    conn.rollback()


def test_a_window_with_no_injection_says_so_not_zero(okan_db):
    conn = okan_db.conn
    v = wi.voidage(conn, since="2024-01-01", until="2024-07-01")
    assert v["verdict"] == "no_injection" and v["summary"]["latest_vrr"] is None
    assert all(r["vrr"] is None and r["status"] == "no_injection" for r in v["months"])
    assert "not a ratio of zero" in v["note"]
    h = wi.hall(conn, since="2024-01-01", until="2024-07-01")
    assert h["injectors"] == [] and h["verdict"] == "no_injection"
    conn.rollback()


def test_the_plugging_injector_bends_its_hall_plot(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "injection.json").read_text())
    h = wi.hall(conn)
    by = {i["well"]: i for i in h["injectors"]}
    plugged = key["plugging"]["well"]
    assert by[plugged]["verdict"] == "rising" and by[plugged]["slope_ratio"] > wi.HALL_RISE
    assert all(i["verdict"] == "steady" for w, i in by.items() if w != plugged)
    pts = by[plugged]["points"]
    assert all(b["cumulative_water_sm3"] > a["cumulative_water_sm3"] for a, b in pairwise(pts))
    assert wi.hall(conn, well=plugged.lower())["injectors"][0]["well"] == plugged
    conn.rollback()


def test_the_charts_and_the_card_reach_people_and_agents(okan_db):
    import pytest
    from fastapi.testclient import TestClient

    from fuzer import api
    from fuzer.plots import ChartError, production

    conn = okan_db.conn
    fig = production.voidage_replacement(conn)
    assert fig.summary["verdict"] == "under_replacing" and fig.summary["pvt"]["water_fvf"]["value"]
    hall = production.hall_plot(conn)
    assert len(hall.data) == 3 and any(i["verdict"] == "rising" for i in hall.summary["injectors"])
    with pytest.raises(ChartError, match="No water injection"):
        production.voidage_replacement(conn, since="2024-01-01", until="2024-07-01")
    r = TestClient(api.app).get("/plant/water-injection")
    assert r.status_code == 200, r.text
    assert r.json()["voidage"]["verdict"] == "under_replacing" and r.json()["hall"]["verdict"] == "flagged"
    conn.rollback()
