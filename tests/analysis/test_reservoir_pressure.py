"""Reservoir pressure (#364): productivity index from paired static and flowing surveys, and a one-tank material
balance against the reservoir volume taken out net of injection, on Okan's pressure surveys. Every result returns
its assumptions."""

import json

import pytest

from fuzer.analysis import reservoir_pressure as rp


def test_productivity_index_from_surveys_and_the_well_that_scaled_up(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "pressure.json").read_text())
    out = rp.productivity(conn)
    assert out["assumptions"] and {w["well"] for w in out["wells"]} == set(key["wells"])
    by = {w["well"]: w for w in out["wells"]}
    for name, j in key["pi"].items():
        assert by[name]["first_pi"] == pytest.approx(j, rel=0.08), name
    hurt = by[key["damaged"]["well"]]
    assert hurt["verdict"] == "fallen" and hurt["ratio"] == pytest.approx(key["damaged"]["pi_factor"], abs=0.08)
    assert all(w["verdict"] == "holding" for n, w in by.items() if n != key["damaged"]["well"])
    one = rp.productivity(conn, well=hurt["well"].lower())
    assert [w["well"] for w in one["wells"]] == [hurt["well"]] and one["assumptions"]
    conn.rollback()


def test_material_balance_recovers_the_tank_and_states_its_assumptions(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "pressure.json").read_text())
    mb = rp.material_balance(conn)
    assert mb["verdict"] == "fitted" and mb["assumptions"] and mb["r2"] > 0.8
    assert mb["bar_per_million_rm3"] == pytest.approx(key["k_bar_per_rm3"] * 1e6, rel=0.35)
    assert mb["decline_bar_per_year"] > 0 and mb["compressible_volume_rm3_per_bar"] > 0
    assert mb["wells"] == len(key["wells"])
    early = rp.material_balance(conn, since="2024-01-01", until="2024-03-01")  # one survey round only
    assert early["verdict"] == "insufficient" and early["assumptions"]
    conn.rollback()


def test_the_charts_and_the_card_reach_people_and_agents(okan_db):
    from fastapi.testclient import TestClient

    from fuzer import api
    from fuzer.agents.capabilities import catalog
    from fuzer.plots import ChartError, production

    conn = okan_db.conn
    pi = production.productivity_index(conn)
    assert pi.summary["assumptions"] and any(w["verdict"] == "fallen" for w in pi.summary["wells"])
    mb = production.material_balance(conn)
    assert mb.summary["assumptions"] and mb.data[-1]["name"].startswith("fit:")
    with pytest.raises(ChartError):
        production.material_balance(conn, since="2024-01-01", until="2024-03-01")
    r = TestClient(api.app).get("/plant/reservoir-pressure")
    assert r.status_code == 200 and r.json()["material_balance"]["verdict"] == "fitted"
    assert r.json()["productivity"]["assumptions"] and r.json()["material_balance"]["assumptions"]
    wells = [u for u in catalog.UNAVAILABLE if "pressure surveys" in u[2]]
    assert wells and "Reservoir pressure" not in wells[0][1]  # surveys are no longer listed as missing
    conn.rollback()
