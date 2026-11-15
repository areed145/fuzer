"""The demo's held load (#1145): a dataset set up as if its export renamed a column holds on the next load, opens a
case for the data steward, and loads again the moment the steward accepts the new columns."""

from fastapi.testclient import TestClient

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
