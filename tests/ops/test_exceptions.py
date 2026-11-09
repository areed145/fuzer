"""The exception contract (core/exceptions.py) and the event outbox (core/events.py) on an empty database."""

import os
import re
import tempfile

import pytest

from fuzer.core import db, events
from fuzer.core.exceptions import TYPES, ExceptionError, ExceptionType, clear_exception, raise_exception
from fuzer.ops import board
from fuzer.ops import exceptions as impl
from fuzer.resources import PACKAGE


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        c.execute("insert into entity (fuzer_id, kind) values ('fz-pump-1', 'equipment'), ('fz-well-a12', 'well')")
        c.execute("insert into entity_key (kind, key, fuzer_id) values ('tag', 'P-101', 'fz-pump-1')")
        c.execute("insert into crosswalk (source, record_type, source_key, fuzer_id, method, confidence) "
                  "values ('sap', 'equipment', '10004711', 'fz-pump-1', 'key', 1)")
        c.commit()
        yield c


@pytest.fixture
def types(monkeypatch):
    def declare(name, **kw):
        monkeypatch.setitem(TYPES, name, ExceptionType(name, name, **kw))

    return declare


def test_refs_match_entities_in_any_terms_and_unmatched_ones_stay(conn):
    ids, unmatched = impl.match(conn, ["tag:P-101", "sap/10004711", "fz-well-a12", "tag:NOPE", "tag:P-101"])
    assert ids == ["fz-pump-1", "fz-well-a12"] and unmatched == ["tag:NOPE"]


def test_one_open_exception_per_condition_routed_to_its_queue(conn, types):
    types("pump_vibration", role="role:reliability")
    first = raise_exception(conn, "pump_vibration", "P-101", "P-101 vibration high", "sre", refs=["tag:P-101"],
                            priority=0.7, evidence={"mm_s": 9.1})
    again = raise_exception(conn, "pump_vibration", "P-101", "P-101 vibration still high", "sre",
                            refs=["tag:SPARE-9"], evidence={"mm_s": 9.8})
    assert first["raised"] and not again["raised"] and again["id"] == first["id"]
    assert again["raised_count"] == 2 and again["evidence"]["mm_s"] == 9.8
    assert again["subjects"] == ["fz-pump-1"] and again["unmatched"] == ["tag:SPARE-9"]
    case = conn.execute("select * from work_item where id = %s", (first["item_id"],)).fetchone()
    assert case["role"] == "role:reliability" and case["subjects"] == ["fz-pump-1"]
    assert case["payload"]["exception_id"] == first["id"] and case["payload"]["mm_s"] == 9.1
    raised = [e for e in events.pending(conn, "exception.raised") if e["ref"] == f"exception:{first['id']}"]
    assert len(raised) == 1 and raised[0]["payload"]["role"] == "role:reliability"

    cleared = clear_exception(conn, "pump_vibration", "P-101", "sre", reason="back to normal")
    assert cleared["state"] == "cleared"
    assert conn.execute("select state from work_item where id = %s", (first["item_id"],)).fetchone()["state"] == "done"
    assert clear_exception(conn, "pump_vibration", "P-101", "sre") is None
    conn.commit()


def test_a_case_closed_while_the_condition_holds_does_not_hide_it(conn, types):
    from fuzer.core import work

    types("late_material", role="role:planner")
    first = raise_exception(conn, "late_material", "PO-77", "PO 77 late", "sap")
    work.close_case(conn, first["item_id"], "done", "pam")
    second = raise_exception(conn, "late_material", "PO-77", "PO 77 still late", "sap")
    assert second["raised"] and second["id"] != first["id"] and second["item_id"] != first["item_id"]
    assert conn.execute("select state from exception_record where id = %s", (first["id"],)).fetchone()["state"] == "closed"
    conn.commit()


def test_an_exception_nobody_is_routed_to_waits_on_the_board(conn, types):
    types("field_report", audience=("ioc", "field"))
    e = raise_exception(conn, "field_report", "walkdown-3", "Leak at well A-12 flowline", "olga",
                        refs=["fz-well-a12"], value_usd=1200)
    assert e["item_id"] is None
    card = next(c for c in board._exception_cards(conn) if c["key"] == f"exception:{e['id']}")
    assert card["unit_id"] == "fz-well-a12" and card["value_usd"] == 1200
    assert any(r["id"] == e["id"] for r in impl.listing(conn, audience="field"))
    conn.commit()


def test_unknown_types_and_missing_keys_are_refused(conn, types):
    with pytest.raises(ExceptionError, match="no exception type"):
        raise_exception(conn, "nope", "k", "t", "me")
    types("keyed")
    with pytest.raises(ExceptionError, match="needs a key"):
        raise_exception(conn, "keyed", "", "t", "me")
    conn.rollback()


def test_the_outbox_delivers_and_retries_failed_handlers(conn, monkeypatch):
    seen = []
    monkeypatch.setattr(events, "HANDLERS", {})
    events.subscriber("t.ok")(lambda c, e: seen.append(e["payload"]["n"]))
    events.subscriber("t.bad")(lambda c, e: 1 / 0)
    conn.execute("update event_outbox set delivered_at = now()")  # only this test's events
    events.publish(conn, "t.ok", None, {"n": 1})
    bad = events.publish(conn, "t.bad", None, {})
    events.publish(conn, "t.nobody", None, {})
    assert events.deliver(conn) == {"delivered": 2, "failed": 1} and seen == [1]
    row = conn.execute("select * from event_outbox where id = %s", (bad,)).fetchone()
    assert row["delivered_at"] is None and row["attempts"] == 1 and "ZeroDivisionError" not in row["last_error"]
    assert "division by zero" in row["last_error"]


def test_every_subscriber_module_is_loaded_by_deliver():
    declared = set()
    for f in PACKAGE.rglob("*.py"):
        if re.search(r"^@events\.subscriber\(", f.read_text(), re.M):
            declared.add("fuzer." + ".".join(f.relative_to(PACKAGE).with_suffix("").parts))
    assert declared == set(events.SUBSCRIBERS)


def test_source_problems_opened_before_the_contract_are_adopted(conn):
    from fuzer.core import work

    item = work.open_case(conn, "source_issue", "Source x is stale", "fuzer", role="role:data_steward",
                          payload={"dataset": "x", "problem": "stale"})
    conn.execute((PACKAGE / "ops" / "schema.sql").read_text())  # idempotent, as on every schema change
    row = conn.execute("select * from exception_record where item_id = %s", (item["id"],)).fetchone()
    assert row["type"] == "source_issue" and row["key"] == "x:stale" and row["state"] == "open"
    conn.rollback()
