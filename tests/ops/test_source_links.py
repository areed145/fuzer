"""Source links and status mapping (step 8 of docs/work-model.md): SAP orders as jobs with their status read through
the mapping, each with exactly one case."""

import os
import tempfile

import pytest
from psycopg.types.json import Jsonb

from fuzer.core import db
from fuzer.core.exceptions import raise_exception
from fuzer.ops import plan, source_links


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        from fuzer.ops import intake  # noqa: F401  the sap_notification type
        from fuzer.workflow import engine

        engine.load_defaults(c)
        c.execute("insert into entity (fuzer_id, kind) values ('fz-pump-1', 'equipment'), ('fz-n-1', 'notification')")
        c.execute("insert into entity_key (kind, key, fuzer_id) values ('notification', '1000123', 'fz-n-1')")
        c.commit()
        yield c


def _order(conn, number: str, status: str, notification: str | None = None):
    fid = f"fz-wo-{number}"
    conn.execute("insert into entity (fuzer_id, kind) values (%s, 'work_order') on conflict do nothing", (fid,))
    conn.execute("insert into entity_key (kind, key, fuzer_id) values ('work_order', %s, %s) on conflict do nothing",
                 (number, fid))
    conn.execute("insert into edge (src, rel, dst, sources, confidence) values (%s, 'concerns', 'fz-pump-1', '{t}', 1) "
                 "on conflict do nothing", (fid,))
    if notification:
        conn.execute("insert into edge (src, rel, dst, sources, confidence) values (%s, 'for_notification', %s, "
                     "'{t}', 1) on conflict do nothing", (fid, notification))
    conn.execute("delete from golden where fuzer_id = %s", (fid,))
    for k, v in {"status": status, "start": "2026-12-01T06:00:00Z", "finish": "2026-12-01T14:00:00Z",
                 "text": f"Order {number}"}.items():
        conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, %s, %s, 'sap', 't')",
                     (fid, k, Jsonb(v)))


def _job(conn, number: str) -> dict:
    return conn.execute("select * from plan_item where source = 'sap' and source_key = %s", (number,)).fetchone()


def test_statuses_read_through_the_mapping_most_specific_and_furthest_along_first(conn):
    assert source_links.state_of(conn, "sap", "order", "CRTD") == "proposed"
    assert source_links.state_of(conn, "sap", "order", "REL  MANC") == "scheduled"
    assert source_links.state_of(conn, "sap", "order", "REL WMAT") == "proposed"  # waiting on materials
    assert source_links.state_of(conn, "sap", "order", "REL PCNF") == "in_progress"
    assert source_links.state_of(conn, "sap", "order", "REL PCNF WMAT") == "in_progress"
    assert source_links.state_of(conn, "sap", "order", "TECO") == "done"
    assert source_links.state_of(conn, "sap", "order", "XYZZ") is None
    assert source_links.state_of(conn, "p6", "activity", "In Progress") == "in_progress"
    row = source_links.set_status(conn, "sap", "order", "xyzz", "scheduled", "dana", note="the site's own")
    assert row["status"] == "XYZZ" and source_links.state_of(conn, "sap", "order", "XYZZ") == "scheduled"
    with pytest.raises(source_links.LinkError):
        source_links.set_status(conn, "sap", "order", "ABCD", "sometime", "dana")
    conn.execute("delete from work_status_map where status = 'XYZZ'")
    conn.commit()


def test_an_order_is_a_job_with_its_link_and_one_case_and_its_case_closes_with_the_order(conn):
    _order(conn, "4000001", "REL")
    plan.sync_work_orders(conn)
    job = _job(conn, "4000001")
    assert job["status"] == "scheduled"
    [lk] = source_links.links(conn, "job", job["id"])
    assert (lk["system"], lk["object_type"], lk["key"], lk["status"], lk["state"]) == \
        ("sap", "order", "4000001", "REL", "scheduled") and lk["owns"] == ["status", "title"]
    out = source_links.sync_orders(conn)
    assert out["opened"] == 1
    job = _job(conn, "4000001")
    case = conn.execute("select * from work_item where id = %s", (job["item_id"],)).fetchone()
    assert case["type"] == "sap_order" and case["role"] == "maintenance_planner" and case["state"] not in ("done",)
    assert source_links.sync_orders(conn)["opened"] == 0  # once
    assert [x["key"] for x in source_links.links(conn, "case", case["id"])] == ["4000001"]

    _order(conn, "4000001", "TECO")
    plan.sync_work_orders(conn)
    assert _job(conn, "4000001")["status"] == "done"
    assert source_links.sync_orders(conn)["closed"] == 1
    assert conn.execute("select state from work_item where id = %s", (case["id"],)).fetchone()["state"] == "done"
    conn.commit()


def test_an_order_from_a_notification_joins_its_case_and_an_unknown_status_is_flagged_not_guessed(conn):
    e = raise_exception(conn, "sap_notification", "1000123", "Seal leak on P-101", "sap", refs=["fz-pump-1"],
                        source="sap")
    assert e["item_id"]
    _order(conn, "4000002", "CRTD", notification="fz-n-1")
    plan.sync_work_orders(conn)
    source_links.sync_orders(conn)
    assert _job(conn, "4000002")["item_id"] == e["item_id"]  # the notification's job: one case

    _order(conn, "4000002", "QQQQ")  # a status no row maps
    out = plan.sync_work_orders(conn)
    assert out["held"] == 1 and _job(conn, "4000002")["status"] == "proposed"  # held at its last known state
    [lk] = source_links.links(conn, "job", _job(conn, "4000002")["id"])
    assert lk["status"] == "QQQQ" and lk["state"] == "proposed"
    q = conn.execute("select * from exception_record where type = 'source_issue' and key = 'status:sap:order:QQQQ'"
                     ).fetchone()
    assert q and q["state"] == "open"
    source_links.set_status(conn, "sap", "order", "QQQQ", "scheduled", "dana")  # the steward maps it
    assert conn.execute("select state from exception_record where id = %s", (q["id"],)).fetchone()["state"] != "open"
    plan.sync_work_orders(conn)
    assert _job(conn, "4000002")["status"] == "scheduled"
    conn.execute("delete from work_status_map where status = 'QQQQ'")
    conn.commit()
