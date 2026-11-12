"""Operational against accounting volumes, and unit cost (P7.5c, #372), on the Okan field. Okan's books close each
month a little under the daily sum; August 2025's oil is booked 3.2 % under it (the export meter was proved
mid-month), and December 2025 isn't closed. Logistics is billed in NGN, the rest in USD."""

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from fuzer.agents import mcp_server
from fuzer.analysis import accounting
from fuzer.plots import general


def _month(out, period, product="oil"):
    return next(m for m in out["months"] if m["period"] == period and m["product"] == product)


def test_differences_cite_both_sources_and_periods(okan_db):
    conn = okan_db.conn
    out = accounting.reconcile(conn)  # the year to Okan's latest daily report
    assert out["summary"]["differs"] == 1 and out["summary"]["agrees"] >= 20
    aug = _month(out, "2025-08")
    assert aug["verdict"] == "differs" and 2.5 < aug["difference_pct"] < 4.0
    acct, op = aug["accounting"], aug["operational"]
    assert acct["source"] == accounting.ACCOUNTING and op["source"] == accounting.OPERATIONAL
    assert (acct["since"], acct["through"]) == ("2025-08-01", "2025-08-31")
    assert (op["since"], op["through"], op["days"]) == ("2025-08-01", "2025-08-31", 31)
    assert acct["record"].startswith("fz-accounting_volume") and len(op["refs"]) == 31
    assert "proved" in acct["comment"]
    assert aug["difference"] == op["value"] - acct["value"]  # the difference is shown, neither figure restated
    # the gas that month agrees: only the oil meter was proved
    assert _month(out, "2025-08", "gas")["verdict"] == "agrees"
    # December isn't closed, and its daily reports stop a day short: preliminary, with the gap said
    dec = _month(out, "2025-12")
    assert dec["verdict"] == "preliminary" and "30 of its 31 days" in dec["why"]
    conn.rollback()


def test_months_that_cant_be_compared_say_why(okan_db):
    conn = okan_db.conn
    # a closed month missing a daily report is incomplete, never compared on a short sum
    day = conn.execute("select fuzer_id from golden where field = 'report_date' and value #>> '{}' = '2025-10-15'"
                       ).fetchone()["fuzer_id"]
    conn.execute("update entity set retired_at = now() where fuzer_id = %s", (day,))
    oct_ = _month(accounting.reconcile(conn), "2025-10")
    assert oct_["verdict"] == "incomplete" and "30 of the 31 days" in oct_["why"]
    # a month with daily reports and no accounted volume is listed, not dropped
    sep = conn.execute("select fuzer_id from golden where field = 'description' and value #>> '{}' = "
                       "'Accounted oil 2025-09'").fetchone()["fuzer_id"]
    conn.execute("update entity set retired_at = now() where fuzer_id = %s", (sep,))
    s = _month(accounting.reconcile(conn), "2025-09")
    assert s["verdict"] == "no_accounting" and s["accounting"] is None and s["operational"]["days"] == 30
    conn.rollback()


def test_unit_cost_keeps_currencies_apart(okan_db):
    conn = okan_db.conn
    out = accounting.unit_cost(conn)
    assert out["currencies"] == ["NGN", "USD"] and set(out["window"]) == {"NGN", "USD"}
    nov = [m for m in out["months"] if m["period"] == "2025-11"]
    assert {m["currency"] for m in nov} == {"NGN", "USD"}
    usd = next(m for m in nov if m["currency"] == "USD")
    assert abs(usd["per_sm3"] - usd["cost"] / usd["oil_sm3"]) < 0.01
    assert "Logistics" not in usd["by_element"]  # billed in NGN, never added to the USD
    # a month whose oil isn't closed has no unit cost
    oil = conn.execute("select fuzer_id from golden where field = 'description' and value #>> '{}' = "
                       "'Accounted oil 2025-11'").fetchone()["fuzer_id"]
    conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, 'status', %s, 'test', 'test') "
                 "on conflict (fuzer_id, field) do update set value = excluded.value", (oil, Jsonb("preliminary")))
    nov2 = next(m for m in accounting.unit_cost(conn)["months"] if m["period"] == "2025-11" and m["currency"] == "USD")
    assert nov2["per_sm3"] is None and "isn't closed" in nov2["why_unknown"]
    conn.rollback()


def test_accounting_reaches_people_and_agents(okan_db):
    from fuzer import api
    r = TestClient(api.app).get("/plant/accounting")
    assert r.status_code == 200, r.text
    assert r.json()["reconciliation"]["summary"]["differs"] == 1 and r.json()["unit_cost"]["window"]
    assert mcp_server.accounting_reconciliation()["months"]
    assert mcp_server.unit_cost()["currencies"] == ["NGN", "USD"]
    fig = general.reconciliation(okan_db.conn)
    assert fig.summary["differs"][0]["period"] == "2025-08"
    assert any(m["verdict"] == "preliminary" for m in accounting.reconcile(okan_db.conn)["months"])
    okan_db.conn.rollback()
