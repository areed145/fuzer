"""Well test against allocated oil (#338), on the Okan field: the daily report books about three quarters of what the
producing wells offer at their latest tests, every month, so the factor reads low; each well's own downtime is taken
out, and days with a facility or tag downtime are left out."""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from fuzer.agents import mcp_server
from fuzer.analysis import well_test_factor as tf
from fuzer.plots import production


def test_the_factor_per_month_reads_low_with_its_test_age(okan_db):
    out = tf.trend(okan_db.conn)
    assert out["since"].endswith("-01") and out["months"][0]["month"] == out["since"][:7]  # whole months
    assert out["verdict"] == "low" and "low" in out["flags"]
    s = out["summary"]
    assert 0.6 < s["factor"] < s["band"][0]
    for m in out["months"]:
        assert m["factor"] == round(m["allocated_sm3"] / m["offered_sm3"], 3) or abs(
            m["factor"] - m["allocated_sm3"] / m["offered_sm3"]) < 0.002
        assert m["factor_ignoring_downtime"] <= m["factor"]  # taking a shut-in well out can only lower the offer
        assert m["median_test_age_days"] <= tf.TEST_DAYS and m["wells_offering"] > 100
        assert m["days"] + m["days_left_out"] <= 31
    assert s["days_left_out"] == sum(sum(m["left_out"].values()) for m in out["months"]) > 0
    assert "status history isn't loaded" in out["note"]


def test_a_well_down_all_day_offers_nothing_that_day(okan_db):
    conn = okan_db.conn
    before = tf.trend(conn, "2025-10-01", "2025-11-01")["months"][0]
    well = conn.execute(
        "select m.fuzer_id from measurement m join entity e using (fuzer_id) where e.kind = 'well' and m.method = "
        "'well test' and m.quantity = 'oil_rate' and m.value > 50 and m.at > '2025-09-01' limit 1").fetchone()["fuzer_id"]
    dt = "fz-downtime-test-338"
    conn.execute("insert into entity (fuzer_id, kind) values (%s, 'downtime')", (dt,))
    start = datetime(2025, 10, 10)
    for field, value in (("start", start.isoformat(timespec="minutes")),
                         ("end", (start + timedelta(days=5)).isoformat(timespec="minutes"))):
        conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, %s, %s, 'test', 'test')",
                     (dt, field, Jsonb(value)))
    conn.execute("insert into edge (src, rel, dst, sources, confidence) values (%s, 'affects', %s, '{test}', 1)", (dt, well))
    after = tf.trend(conn, "2025-10-01", "2025-11-01")["months"][0]
    assert after["well_days_shut_in"] == before["well_days_shut_in"] + 5
    assert after["offered_sm3"] < before["offered_sm3"] and after["factor"] > before["factor"]
    assert after["factor_ignoring_downtime"] == before["factor_ignoring_downtime"]
    conn.rollback()


def test_the_factor_reaches_people_and_agents(okan_db):
    from fuzer import api
    r = TestClient(api.app).get("/plant/allocation-factor")
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "low"
    assert mcp_server.field_allocation_factor()["summary"]["band"] == [0.9, 1.1]
    fig = production.field_allocation_factor(okan_db.conn)
    assert fig.data[0]["name"].startswith("allocated / tested") and fig.summary["flags"]
    okan_db.conn.rollback()
