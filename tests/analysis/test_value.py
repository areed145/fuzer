"""The shared value / risk score (#100): each component on its own, missing data shown as unknown, the
pack's config re-ranking without code changes, calibration against an engineer's ordering, and every kind
of item on the Okan demo scored with its inputs and sources."""

import asyncio
import dataclasses

import pytest

from fuzer import api
from fuzer.agents import mcp_server
from fuzer.analysis import value
from fuzer.analysis.value import Item
from fuzer.core.pack import load_pack
from fuzer.graph import queries

PACK = load_pack("okan")
CFG = value.config(PACK)


def test_config_merges_pack_over_defaults():
    assert CFG["prices"] == {"oil_sm3": 470, "gas_ksm3": 150} and CFG["source"] == "pack okan"
    bare = value.config(dataclasses.replace(PACK, value={"prices": {"oil_sm3": 600}}))
    assert bare["prices"] == {"oil_sm3": 600, "gas_ksm3": 150.0}  # one price changed, the rest default
    assert bare["risk_matrix"] == value.DEFAULTS["risk_matrix"]
    assert value.config(dataclasses.replace(PACK, value={}))["source"] == "built-in defaults"


# --- components --------------------------------------------------------------------------------------

def test_production():
    p = value.production(Item("work_order", "x", "x", oil_sm3d=10, gas_ksm3d=2, duration_days=5), CFG)
    assert p["status"] == "ok" and p["value_usd"] == (10 * 470 + 2 * 150) * 5 and p["oil_sm3"] == 50
    assert value.production(Item("work_order", "x", "x", oil_sm3d=10), CFG)["days"] == 90  # the horizon
    half = value.production(Item("incident", "x", "x", oil_sm3d=10, duration_days=1, chance=0.5), CFG)
    assert half["value_usd"] == 2350
    assert value.production(Item("work_order", "x", "x"), CFG) == {
        "status": "unknown", "value_usd": None, "note": "no rate at risk or to gain"}
    assert value.production(Item("incident", "x", "x", oil_sm3d=10, chance=None), CFG)["status"] == "unknown"
    zero = value.production(Item("work_order", "x", "x", oil_sm3d=0.0), CFG)
    assert zero["status"] == "ok" and zero["value_usd"] == 0  # known to be nothing is not unknown


def test_risk_on_the_matrix():
    r = value.risk(Item("work_order", "x", "x", criticality="A", failures_per_year=2.0), CFG)
    assert r["consequence_level"] == 4 and r["likelihood_level"] == 4 and r["matrix_cell"] == 16
    assert r["band"] == "extreme"
    assert r["value_usd"] == round(1e7 * 0.01 * 2.0 * 90 / 365.25)
    assert r["sources"]["matrix"] == "pack okan"
    low = value.risk(Item("work_order", "x", "x", criticality="C", failures_per_year=0.05), CFG)
    assert (low["consequence_level"], low["likelihood_level"], low["band"]) == (2, 1, "low")
    assert value.risk(Item("work_order", "x", "x", criticality="B", failures_per_year=10), CFG)["likelihood_level"] == 5


def test_risk_unknown_criticality_is_unknown_not_zero():
    r = value.risk(Item("work_order", "x", "x", criticality=None, failures_per_year=2.0), CFG)
    assert r["status"] == "unknown" and r["value_usd"] is None and r["criticality"] == "unknown"
    assert "criticality unknown" in r["note"]
    assert value.risk(Item("work_order", "x", "x", criticality="Z", failures_per_year=1), CFG)["status"] == "unknown"
    assert "failures per year" in value.risk(Item("work_order", "x", "x", criticality="A"), CFG)["note"]
    assert value.risk(Item("opportunity", "x", "x", risk_applies=False), CFG)["status"] == "n/a"
    s = value.score(Item("work_order", "x", "20-P-1", oil_sm3d=1, failures_per_year=1), CFG)
    assert s["criticality"] == "unknown" and "risk" in s["unknown"]
    assert s["components"]["risk"]["value_usd"] is None and "criticality unknown" in s["reason"]


def test_compliance():
    c = value.compliance(Item("maintenance_plan", "x", "x", criticality="A", overdue_days=10,
                              compliance_kind="INSP"), CFG)
    assert c["value_usd"] == 10 * 400 * 3 and c["criticality_factor"] == 3
    capped = value.compliance(Item("maintenance_plan", "x", "x", criticality="C", overdue_days=1000,
                                   compliance_kind="PM"), CFG)
    assert capped["value_usd"] == 365 * 100 and "counted to 365 d" in capped["note"]
    unknown = value.compliance(Item("maintenance_plan", "x", "x", overdue_days=10, compliance_kind="INSP"), CFG)
    assert unknown["criticality"] == "unknown" and unknown["criticality_factor"] == 1 and "unknown" in unknown["note"]
    assert value.compliance(Item("work_order", "x", "x"), CFG)["status"] == "n/a"
    assert value.compliance(Item("maintenance_plan", "x", "x", compliance_kind="NEW"), CFG)["status"] == "unknown"


def test_urgency_and_effort():
    assert value.urgency(Item("x", "x", "x", time_to_consequence_days=0), CFG)["factor"] == 1.0
    assert value.urgency(Item("x", "x", "x", time_to_consequence_days=30), CFG)["factor"] == 0.5
    assert value.urgency(Item("x", "x", "x", time_to_consequence_days=-5), CFG)["factor"] == 1.0  # overdue
    u = value.urgency(Item("x", "x", "x"), CFG)
    assert u["status"] == "unknown" and u["factor"] == 0.5
    assert value.effort(Item("work_order", "x", "x", effort_usd=1234.4, effort_basis="SAP"), CFG)["value_usd"] == 1234
    e = value.effort(Item("work_order", "x", "x"), CFG)
    assert e["status"] == "assumed" and e["value_usd"] == 20000
    assert value.effort(Item("new_kind", "x", "x"), CFG)["status"] == "unknown"


def test_score_adds_up_and_explains():
    item = Item("maintenance_plan", "p1", "INSP-1 20-V-2001", oil_sm3d=1.0, criticality="A", failures_per_year=0.5,
                overdue_days=20, compliance_kind="INSP", time_to_consequence_days=0, effort_usd=10000,
                sources={"criticality": "SAP functional location ABC indicator"})
    s = value.score(item, CFG)
    parts = [s["components"][k]["value_usd"] for k in ("production", "risk", "compliance")]
    assert s["value_usd"] == sum(parts) and s["value_per_effort"] == round(s["value_usd"] / 10000, 2)
    assert s["unknown"] == [] and s["components"]["risk"]["sources"]["criticality"].startswith("SAP")
    assert s["reason"].startswith("INSP-1 20-V-2001: 90 Sm3 oil at risk over 90 d ($42k); INSP 20 d overdue")
    assert "due now" in s["reason"]  # biggest part first
    later = value.score(dataclasses.replace(item, time_to_consequence_days=30), CFG)
    assert later["value_usd"] == round(s["value_usd"] / 2)
    nothing = value.score(Item("work_order", "w", "W1", oil_sm3d=0.0, criticality="C", failures_per_year=0.0,
                               time_to_consequence_days=7), CFG)
    assert nothing["value_usd"] == 0 and "nothing at stake found" in nothing["reason"]


def test_changing_price_or_matrix_reranks():
    oil = Item("opportunity", "gl", "gas lift", oil_sm3d=4, risk_applies=False, time_to_consequence_days=0)
    comp = Item("maintenance_plan", "pm", "inspection", criticality="A", failures_per_year=0.2, overdue_days=150,
                compliance_kind="INSP", time_to_consequence_days=0)

    def order(cfg):
        return [s["id"] for s in sorted((value.score(i, cfg) for i in (oil, comp)), key=lambda s: -s["value_usd"])]

    assert order(CFG) == ["pm", "gl"]
    rich = value.config(dataclasses.replace(PACK, value={**PACK.value, "prices": {"oil_sm3": 1500, "gas_ksm3": 150}}))
    assert order(rich) == ["gl", "pm"]
    lax = value.config(dataclasses.replace(PACK, value={**PACK.value, "compliance": {
        **PACK.value["compliance"], "usd_per_day_overdue": {"PM": 10, "INSP": 40}}}))
    assert order(lax) == ["gl", "pm"]


def test_agreement():
    assert value.agreement(["a", "b", "c", "d"], ["a", "b", "c", "d"])["kendall_tau"] == 1.0
    rev = value.agreement(["a", "b", "c", "d"], ["d", "c", "b", "a"])
    assert rev["kendall_tau"] == -1.0 and rev["top3_overlap"] == round(2 / 3, 2)
    one = value.agreement(["a", "b", "c", "x"], ["b", "a", "c", "zz"])  # unknown ids are left out
    assert one["items"] == 3 and one["kendall_tau"] == round(1 / 3, 3)
    assert {d["id"] for d in one["biggest_disagreements"]} == {"a", "b"}
    assert value.agreement(["a"], ["a"])["kendall_tau"] is None


# --- on the Okan demo --------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    yield c


def test_sap_abc_indicator_is_criticality(conn):
    def crit(tag):
        g = queries.entity(conn, queries.resolve_ref(conn, PACK, tag))["golden"]
        return (g.get("criticality") or {}).get("value")
    assert crit("20-K-2200B") == "A" and crit("10-E-3001") == "B" and crit("20-PT-3003") == "C"
    assert crit("61-PT-3002") is None  # jacket instruments were never assessed


def test_every_kind_scored_on_okan(conn):
    r = value.ranked(conn, PACK, limit=1000)
    assert r["as_of"] == "2025-12-30" and r["config"] == "pack okan"
    assert all(r["by_kind"][k] > 0 for k in value.KINDS), r["by_kind"]
    vals = [s["value_usd"] for s in r["items"]]
    assert vals == sorted(vals, reverse=True)
    for s in r["items"]:
        assert set(s["components"]) == {"production", "risk", "compliance"} and s["reason"].startswith(s["label"])
        for p in s["components"].values():
            if p["status"] == "ok":
                assert p["sources"], (s["label"], p)
    by = {k: [s for s in r["items"] if s["kind"] == k] for k in value.KINDS}
    # the 20-K-2300A trips of mid 2025: scored as the cost of a repeat, with the unit's failure history
    trip = next(s for s in by["incident"] if s["tag"] == "20-K-2300A")
    assert trip["components"]["risk"]["band"] == "extreme" and 0 < trip["components"]["production"]["chance"] < 1
    # the overdue inlet separator inspection (due 2025-09-30, needs a shutdown)
    sep = next(s for s in by["maintenance_plan"] if s["tag"] == "20-V-2001" and s["label"].startswith("INSP"))
    assert sep["components"]["compliance"]["overdue_days"] > 60 and sep["urgency"]["factor"] == 1.0
    assert "needs a shutdown" in sep["notes"]
    # open orders have no cost in SAP: estimated from completed orders, or the pack default, and said so
    assert all(s["effort"]["basis"] for s in by["work_order"])
    assert any(s["effort"]["basis"].startswith("average of") for s in by["work_order"])
    # the failed gas lift valve on OK-J07-05: its lift gas is worth more elsewhere
    gl = by["opportunity"][0]
    assert "OK-J07-05" in gl["label"] and gl["components"]["risk"]["status"] == "n/a" and gl["value_usd"] > 0
    assert value.ranked(conn, PACK, by="per_effort", limit=1)["items"][0]["kind"] == "opportunity"


def test_ranking_follows_pack_config(conn):
    base = [s["id"] for s in value.ranked(conn, PACK, limit=10)["items"]]
    cheap_oil = dataclasses.replace(PACK, value={**PACK.value, "prices": {"oil_sm3": 5, "gas_ksm3": 1}})
    moved = value.ranked(conn, cheap_oil, limit=10)["items"]
    assert [s["id"] for s in moved] != base and all(s["kind"] != "opportunity" for s in moved[:5])


def test_value_over_mcp_and_api(conn, monkeypatch):
    monkeypatch.setattr(mcp_server, "PACK", PACK)
    monkeypatch.setattr(api, "PACK", PACK)
    assert "value_score" in {t.name for t in asyncio.run(mcp_server.server.list_tools())}
    one = mcp_server.value_score("20-K-2200B")
    assert one["items"] and all(s["tag"] == "20-K-2200B" for s in one["items"])
    assert set(mcp_server.value_score(kinds=["opportunity"])["by_kind"]) == {"opportunity"}
    assert "error" in mcp_server.value_score("no-such-thing")

    from fastapi.testclient import TestClient
    client = TestClient(api.app)
    top = client.get("/value", params={"limit": 6}).json()["items"]
    assert client.get("/value/calibration").json()["rankings"] == 0
    mine = [s["id"] for s in top]
    assert client.post("/value/rankings", json={"items": mine[::-1], "by": "test"}).status_code == 200
    cal = client.get("/value/calibration").json()
    assert cal["rankings"] == 1 and cal["each"][0]["items"] == 6 and cal["each"][0]["kendall_tau"] == -1.0
