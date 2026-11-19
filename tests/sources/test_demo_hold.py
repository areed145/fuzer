"""The demo's held load (#1145): a dataset set up as if its export renamed a column holds on the next load, opens a
case for the data steward, and loads again the moment the steward accepts the new columns."""

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from fuzer.graph import data_work
from fuzer.sources import demo_hold, ingest


def test_the_demo_holds_a_load_the_steward_fixes_by_accepting_its_columns(okan_db):
    conn = okan_db.conn
    name = demo_hold.seed(conn)
    assert name
    report = ingest.ingest(conn, okan_db.data)
    assert name in report["held"]
    assert demo_hold.seed(conn) is None  # one held load at a time
    data_work.sync(conn)
    item = conn.execute("select id, payload from review_item where kind = 'schema' and subject = %s and status = 'open'",
                        (name,)).fetchone()
    assert item and item["payload"]["removed"] and item["payload"]["added"]
    case = conn.execute("select item_id from exception_record where type = 'mapping_drift' and state = 'open' "
                        "and key like %s", (f"schema:{name}%",)).fetchone()
    assert case and case["item_id"], "the steward has it as a case"
    conn.commit()

    from fuzer import api

    with TestClient(api.app) as client:
        r = client.post(f"/review/{item['id']}", json={"decision": "accept", "by": "ana"})
    assert r.status_code == 200, r.text
    assert r.json()["loaded"]["dataset"] == name and name in r.json()["loaded"]["loaded"]
    conn.rollback()
    ds = conn.execute("select schema from source_dataset where name = %s", (name,)).fetchone()
    assert ds["schema"] == item["payload"]["new"]
    assert conn.execute("select state from exception_record where type = 'mapping_drift' and key like %s "
                        "order by id desc limit 1", (f"schema:{name}%",)).fetchone()["state"] != "open"
    conn.rollback()


def test_a_held_load_after_an_accepted_one_asks_again(okan_db):
    """The next deploy seeds the same rename once the steward accepted it: the load holds again, so the question must
    open again, or Sources shows a held load that Master data has nothing to answer for."""
    conn = okan_db.conn
    name = demo_hold.seed(conn)
    ingest.ingest(conn, okan_db.data)
    item = conn.execute("select id, payload from review_item where kind = 'schema' and subject = %s",
                        (name,)).fetchone()
    # accepted, as the steward does on Master data: the new columns become the expected ones
    conn.execute("update review_item set status = 'decided', decision = '{\"decision\": \"accept\"}' where id = %s",
                 (item["id"],))
    conn.execute("update source_dataset set schema = %s where name = %s", (Jsonb(item["payload"]["new"]), name))
    assert demo_hold.seed(conn) == name
    assert name in ingest.ingest(conn, okan_db.data)["held"]
    assert conn.execute("select status from review_item where id = %s", (item["id"],)).fetchone()["status"] == "open"
    conn.execute("update review_item set status = 'decided', decision = '{\"decision\": \"reject\"}' where id = %s",
                 (item["id"],))
    ingest.ingest(conn, okan_db.data, full=True)
    assert conn.execute("select status from review_item where id = %s", (item["id"],)).fetchone()["status"] == "decided"
    from fuzer.sources import quality

    [h] = [r for r in quality.health(conn) if r["dataset"] == name]
    assert "held" in h["problems"], "a held last run shows as held even with nothing open to answer"
    conn.rollback()
