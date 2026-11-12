"""Work order cost and the cost of unreliability (P7.3c, #335 and #352), on the Okan field. Okan's SAP export states no
currency, so its cost is in an unknown currency: counted, shown, never summed with a known one."""

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from fuzer.agents import mcp_server
from fuzer.analysis import costs
from fuzer.core.pack import load_pack
from fuzer.plots import maintenance


def _set_currency(conn, ids: list[str], cur: str) -> None:
    for i in ids:
        conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, 'currency', %s, 'test', "
                     "'test') on conflict (fuzer_id, field) do update set value = excluded.value", (i, Jsonb(cur)))


def test_spend_never_mixes_currencies_and_counts_orders_without_cost(okan_db):
    conn = okan_db.conn
    out = costs.spend(conn, by="type", limit=0)  # the year to Okan's latest daily report
    assert out["currencies"] == ["unknown"] and not out["mixed_currencies"]
    assert out["orders_without_cost"] and out["orders_without_cost"] < out["orders"]
    assert out["without_cost_pct"] == round(100 * out["orders_without_cost"] / out["orders"], 1)
    assert sum(r["orders"] for r in out["rows"]) == out["orders"]
    assert sum(r["orders_without_cost"] for r in out["rows"]) == out["orders_without_cost"]
    # put a third of the costed orders in EUR: the two currencies are totalled apart, never together
    ids = [r for row in costs.spend(conn, by="unit", limit=0)["rows"] for r in row["refs"]]
    eur = ids[: len(ids) // 3]
    _set_currency(conn, eur, "eur")
    mixed = costs.spend(conn, by="type", limit=0)
    assert mixed["currencies"] == ["EUR", "unknown"] and mixed["mixed_currencies"]
    assert sum(mixed["totals"].values()) == out["totals"]["unknown"]  # split, not lost or double counted
    assert {r["currency"] for r in mixed["rows"]} == {"EUR", "unknown"}
    for cur in ("EUR", "unknown"):
        assert sum(r["cost"] for r in mixed["rows"] if r["currency"] == cur) == mixed["totals"][cur]
    by_class = costs.spend(conn, by="class", limit=0)
    assert all(r["label"].startswith("ISO14224:") or r["label"] == "no class" for r in by_class["rows"])
    conn.rollback()


def test_the_cost_of_unreliability_adds_parts_only_in_one_currency(okan_db):
    conn = okan_db.conn
    pack = load_pack("okan")
    out = costs.unreliability(conn, pack, limit=0)
    assert out["price"] == {"oil_sm3": 470.0, "currency": "USD", "source": "pack okan"}
    sep = next(u for u in out["units"] if u["unit"] == "40-V-2002")
    assert sep["lost_oil_value"] == round(sep["lost_oil_sm3"] * 470) and sep["order_cost"] == {"unknown": 55936}
    assert sep["total"] is None and "no currency in the source" in sep["why_no_total"]  # never added
    only_oil = next(u for u in out["units"] if not u["order_cost"] and u["lost_oil_value"])
    assert only_oil["total"] == only_oil["lost_oil_value"] and only_oil["total_currency"] == "USD"
    # once its orders state USD, the separator's parts add up
    _set_currency(conn, [r for r in sep["refs"] if r.startswith("fz-work_order")], "USD")
    sep2 = next(u for u in costs.unreliability(conn, pack, limit=0)["units"] if u["unit"] == "40-V-2002")
    assert sep2["total"] == sep["lost_oil_value"] + 55936 and sep2["total_currency"] == "USD"
    conn.rollback()


def test_costs_reach_people_and_agents(okan_db):
    from fuzer import api
    client = TestClient(api.app)
    r = client.get("/plant/cost-of-unreliability")
    assert r.status_code == 200, r.text
    assert r.json()["price"]["currency"] == "USD" and r.json()["units"]
    assert mcp_server.maintenance_spend(by="class")["currencies"] == ["unknown"]
    assert "error" in mcp_server.maintenance_spend(by="colour")
    assert mcp_server.cost_of_unreliability()["units"][0]["refs"]
    conn = okan_db.conn
    fig = maintenance.spend(conn, by="unit")
    assert fig.data[0]["name"] == "cost, currency unknown"  # the plot labels an unknown currency as unknown
    fig = maintenance.unreliability(conn, load_pack("okan"))
    assert [t["name"] for t in fig.data] == ["lost oil (USD at 470 per Sm3)", "corrective orders, currency unknown"]
    conn.rollback()
