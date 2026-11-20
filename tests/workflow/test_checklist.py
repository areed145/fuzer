"""A case's checklist: what a multi-part ask needs, ticked off on the case and kept as its record."""

import pytest

from fuzer.core.pack import load_pack
from fuzer.ops import disposition
from fuzer.workflow import checklist, work_drafts
from fuzer.workflow import engine as work


@pytest.fixture
def conn(okan_db):
    from fuzer.core import db
    with db.connect() as c:
        work.load_defaults(c)
        yield c


def look_ahead(conn, steps=("Confirm WO 50000292 status on 10-E-3001", "Raise orders for PM-30000004",
                            "Check standby cover on 10-P-1001B")):
    d = work_drafts.create(conn, "Okan PP look-ahead, 23-29 Nov", "maintenance_planner", "The week needs decisions.",
                           ["fz-a"], "ana", ["fz-pump"], [{"text": t, "subjects": ["fz-pump"] if "P-1001" in t else []}
                                                          for t in steps])
    return work_drafts.confirm(conn, d["id"], "ana")["item_id"]


def test_a_confirmed_draft_opens_its_case_with_the_steps_as_a_checklist(conn):
    item_id = look_ahead(conn)
    rows = checklist.items(conn, item_id)
    assert [(r["text"], r["state"]) for r in rows] == [
        ("Confirm WO 50000292 status on 10-E-3001", "open"), ("Raise orders for PM-30000004", "open"),
        ("Check standby cover on 10-P-1001B", "open")]
    assert rows[2]["subjects"] == ["fz-pump"]
    assert work.item(conn, item_id)["checklist"] == rows


def test_queue_rows_carry_the_checklist_progress(conn):
    item_id = look_ahead(conn)
    first = checklist.items(conn, item_id)[0]
    checklist.mark(conn, item_id, first["id"], "ana", "done")
    listed = next(r for r in work.list_items(conn, limit=1000)["items"] if r["id"] == item_id)
    assert listed["checklist"] == {"done": 1, "total": 3}
    queued = next(r for r in work.my_work(conn, "ana", ["maintenance_planner"])["queue"] if r["item_id"] == item_id)
    assert queued["checklist"] == {"done": 1, "total": 3}
    plain = work.create_item(conn, "note", "No list", "ana")
    assert next(r for r in work.list_items(conn, limit=1000)["items"] if r["id"] == plain["id"])["checklist"] is None


def test_ticking_skipping_and_reopening_are_kept_in_the_history(conn):
    item_id = look_ahead(conn)
    a, b, _ = checklist.items(conn, item_id)
    assert checklist.mark(conn, item_id, a["id"], "ana", "done")["done_by"] == "ana"
    with pytest.raises(work.WorkError, match="why"):
        checklist.mark(conn, item_id, b["id"], "ana", "skipped")
    assert checklist.mark(conn, item_id, b["id"], "ana", "skipped", "orders already raised")["note"] == "orders already raised"
    reopened = checklist.mark(conn, item_id, a["id"], "ana", "open")
    assert (reopened["state"], reopened["done_by"], reopened["done_at"]) == ("open", None, None)
    kinds = [e["kind"] for e in work.item(conn, item_id)["events"]]
    assert kinds.count("checked") == 3 and "checklist_added" in kinds


def test_items_are_added_reworded_and_removed_while_the_case_is_open(conn):
    item_id = look_ahead(conn, steps=())
    assert checklist.items(conn, item_id) == []
    [row] = checklist.add(conn, item_id, "ana", [{"text": "  New permit   for 10-V-2004 "}])
    assert row["text"] == "New permit for 10-V-2004"
    assert checklist.edit(conn, item_id, row["id"], "ana", "New permit and isolations")["text"] == "New permit and isolations"
    checklist.remove(conn, item_id, row["id"], "ana")
    assert checklist.items(conn, item_id) == []
    with pytest.raises(work.WorkError, match="at most"):
        checklist.add(conn, item_id, "ana", [{"text": f"item {n}"} for n in range(checklist.MAX_ITEMS + 1)])


def test_an_asked_case_closes_as_done_and_says_what_its_checklist_left(conn):
    item_id = look_ahead(conn)
    a, b, _ = checklist.items(conn, item_id)
    checklist.mark(conn, item_id, a["id"], "ana", "done")
    checklist.mark(conn, item_id, b["id"], "ana", "skipped", "not needed this week")
    pack = load_pack("okan")
    form = disposition.form(conn, pack, item_id)
    assert form["asked"] and form["suggestion"]["disposition"] == "done"
    disposition.record(conn, pack, item_id, "ana", "done")
    conn.commit()
    item = work.item(conn, item_id)
    assert item["state"] == "done"
    assert any("1 of 3 checklist items done, 1 not needed, 1 left open" in str(e["data"]) for e in item["events"])
    with pytest.raises(work.WorkError, match="closed"):
        checklist.mark(conn, item_id, a["id"], "ana", "open")
