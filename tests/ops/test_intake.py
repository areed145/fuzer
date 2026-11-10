"""Intake (ops/intake.py): open SAP notifications without a work order become exceptions the IOC can dispatch."""

import os
import tempfile
from datetime import date, timedelta

import pytest
from psycopg.types.json import Jsonb

from fuzer.core import db
from fuzer.ops import intake

TODAY = date.today().isoformat()
OLD = (date.today() - timedelta(days=200)).isoformat()


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        c.execute("insert into entity (fuzer_id, kind) values ('fz-pump-1', 'equipment'), ('fz-n-1', 'notification'), "
                  "('fz-n-2', 'notification'), ('fz-n-3', 'notification'), ('fz-n-4', 'notification'), "
                  "('fz-wo-9', 'work_order')")
        c.execute("insert into entity_key (kind, key, fuzer_id) values ('notification', '1001', 'fz-n-1'), "
                  "('notification', '1002', 'fz-n-2'), ('notification', '1003', 'fz-n-3'), "
                  "('notification', '1004', 'fz-n-4')")
        c.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-n-1', 'concerns', 'fz-pump-1', "
                  "'{t}', 1), ('fz-wo-9', 'for_notification', 'fz-n-3', '{t}', 1)")
        golden = {"fz-n-1": {"short_text": "P-101 seal weeping", "status": "OSNO", "created": TODAY, "priority": "2",
                             "failure_mode": "ELU"},
                  "fz-n-2": {"short_text": "Done already", "status": "NOCO", "created": TODAY},
                  "fz-n-3": {"short_text": "Has an order", "status": "OSNO", "created": TODAY},
                  "fz-n-4": {"short_text": "Ancient", "status": "OSNO", "created": OLD}}
        for fid, fields in golden.items():
            for k, v in fields.items():
                c.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, %s, %s, 'sap', 't')",
                          (fid, k, Jsonb(v)))
        c.commit()
        yield c


def _open(conn):
    return {r["key"]: r for r in conn.execute(
        "select * from exception_record where type = 'sap_notification' and state = 'open'")}


def test_it_is_on_by_default_and_a_deployment_can_turn_it_off(conn):
    from fuzer.core import settings
    assert settings.get(conn, "intake.sap_notifications") is True
    before = conn.execute("select count(*) as n, max(updated_at) as at from exception_record").fetchone()
    assert intake.sync_notifications(conn, enabled=False) == {"raised": 0, "cleared": 0, "enabled": False}
    # nothing raised or cleared, whatever order the tests ran in
    assert conn.execute("select count(*) as n, max(updated_at) as at from exception_record").fetchone() == before


def test_open_notifications_without_an_order_are_raised_once_and_matched(conn):
    assert intake.sync_notifications(conn, enabled=True)["cleared"] == 0  # raised now, or by an earlier test
    rows = _open(conn)
    assert set(rows) == {"1001"}  # not the closed one, the one with an order, or the old backlog
    n = rows["1001"]
    assert n["subjects"] == ["fz-pump-1"] and n["priority"] == 3 and n["item_id"] is None  # waits to be dispatched
    assert n["evidence"]["failure_mode"] == "ELU" and "P-101 seal weeping" in n["title"]
    assert intake.sync_notifications(conn, enabled=True) == {"raised": 0, "cleared": 0, "held": 0}
    assert _open(conn)["1001"]["raised_count"] == 1  # a refresh is not a repeat sighting
    conn.commit()


def test_an_order_or_completion_clears_it(conn):
    intake.sync_notifications(conn, enabled=True)  # raised here or by the test before; either way it is open
    assert "1001" in _open(conn)
    conn.commit()
    conn.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-wo-9', 'for_notification', "
                 "'fz-n-1', '{t}', 1)")
    assert intake.sync_notifications(conn, enabled=True)["cleared"] == 1
    assert not _open(conn)
    row = conn.execute("select state, evidence from exception_record where key = '1001'").fetchone()
    assert row["state"] == "cleared"
    conn.rollback()
    conn.execute("update golden set value = '\"NOCO\"' where fuzer_id = 'fz-n-1' and field = 'status'")
    assert intake.sync_notifications(conn, enabled=True)["cleared"] == 1
    conn.rollback()


def test_a_notification_done_in_the_field_is_held_until_sap_moves(conn):
    """SAP never hears that the field closed a notification, so intake holds it while SAP's status is the one it
    had, instead of raising the same notification as a new exception on the next refresh."""
    from fuzer.ops import exceptions as ex

    conn.execute("insert into entity (fuzer_id, kind) values ('fz-n-5', 'notification')")
    conn.execute("insert into entity_key (kind, key, fuzer_id) values ('notification', '1005', 'fz-n-5')")
    for k, v in {"short_text": "P-101 flange drip", "status": "OSNO", "created": TODAY}.items():
        conn.execute("insert into golden (fuzer_id, field, value, source, rule) values ('fz-n-5', %s, %s, 'sap', 't')",
                     (k, Jsonb(v)))
    conn.commit()
    status = lambda v: conn.execute("update golden set value = %s where fuzer_id = 'fz-n-5' and field = 'status'",
                                    (Jsonb(v),))
    try:
        intake.sync_notifications(conn, enabled=True)
        n = _open(conn)["1005"]
        ex.dispatch(conn, n["id"], "ioc-ann", "fred", reason="check the flange")
        assert ex.report(conn, n["id"], "fred", "done", note="flange bolts torqued")["state"] == "closed"
        out = intake.sync_notifications(conn, enabled=True)
        assert out["raised"] == 0 and out["held"] == 1 and "1005" not in _open(conn)
        status("NOPR")
        assert intake.sync_notifications(conn, enabled=True)["raised"] == 1  # SAP moved and it is still open
        assert "1005" in _open(conn)
    finally:  # leave it closed in SAP, so the other tests see only their own notifications
        conn.rollback()
        status("NOCO")
        intake.sync_notifications(conn, enabled=True)
        conn.commit()
