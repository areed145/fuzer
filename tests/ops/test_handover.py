"""A handover you can read on a phone (#1351): it says what changed this shift, not every open line; a source problem
that already cleared is not the next shift's news; a time carries its date when it isn't the shift change's day; a past
handover nobody acknowledged says so, not "draft"; and saving notes mid-shift doesn't hand the shift over."""

from datetime import UTC, date, datetime, timedelta

import pytest

from fuzer.core import work
from fuzer.core.exceptions import clear_exception, raise_exception
from fuzer.ops import handover
from fuzer.sources import alerts

pytestmark = pytest.mark.integration


def test_a_time_carries_its_date_unless_it_is_the_shift_change_s_day(monkeypatch):
    monkeypatch.setattr(handover, "_tz", lambda: UTC)
    t = datetime(2026, 11, 24, 22, 50, tzinfo=UTC)
    assert handover._at(t, date(2026, 11, 24)) == "22:50"
    assert handover._at(t, date(2026, 11, 25)) == "24 Nov 22:50"
    assert handover._at(t.isoformat(), date(2026, 11, 25)) == "24 Nov 22:50"
    assert handover._at(None, date(2026, 11, 25)) == "?"


def test_a_handover_says_where_it_stands_in_words():
    now = datetime(2026, 11, 25, 12, tzinfo=UTC)
    row = lambda state, end: {"state": state, "shift_end": end}
    assert handover.status(row("draft", now + timedelta(hours=6)), now) == "in progress"
    assert handover.status(row("draft", now - timedelta(hours=6)), now) == "not acknowledged"
    assert handover.status(row("acknowledged", now - timedelta(hours=6)), now) == "acknowledged"


def test_the_handover_lists_what_changed_this_shift_not_every_open_line(okan_db):
    conn = okan_db.conn
    conn.rollback()
    start = conn.execute("select now() as t").fetchone()["t"]  # this transaction's: the plant's own rows came before
    try:
        cleared = raise_exception(conn, alerts.TYPE, "handover_test_a:failed", "Source handover_test_a (sap) failed to load",
                                  "test", source="sources")
        clear_exception(conn, alerts.TYPE, "handover_test_a:failed", "test", reason="the problem cleared")
        failing = raise_exception(conn, alerts.TYPE, "handover_test_b:failed",
                                  "Source handover_test_b (sap) failed to load", "test", source="sources")
        done = work.open_case(conn, "handover_test", "Swap the strainer on P-101", "test", role="ops_lead")
        work.close_case(conn, done["id"], "done", "test", reason="swapped")

        f = handover.facts(conn, start, datetime.now(UTC) + timedelta(seconds=1))
        s = f["sections"]
        assert "Open work" not in s and list(s) == handover.SECTIONS
        new = " ".join(x["text"] for x in s["New exceptions"])
        assert "handover_test_b" in new, "a source still failing is news"
        assert "handover_test_a" not in new, "a source that failed and loaded again is not"
        closed = s["Closed"]
        assert any(f"case:{done['id']}" in x["cites"] and x["text"].startswith("Swap the strainer on P-101: done ")
                   for x in closed)
        if cleared.get("item_id"):  # its case closed when it cleared: not news under Closed either
            assert not any(f"case:{cleared['item_id']}" in x["cites"] for x in closed)
        assert failing["raised"]
    finally:
        conn.rollback()


def test_saving_notes_mid_shift_leaves_the_shift_in_progress(okan_db):
    conn = okan_db.conn
    try:
        running = handover.draft(conn, "lead-out", force=True, current=True)
        assert running["status"] == "in progress" and running["item_id"] is None
        noted = handover.add_notes(conn, running["id"], "lead-out", "Compressor B on recycle overnight")
        assert noted["state"] == running["state"] == "draft" and noted["status"] == "in progress"
        assert noted["notes"] == "Compressor B on recycle overnight"

        # once its shift has ended and nobody acknowledged it, it says so: not a draft, not handed over
        conn.execute("update shift_handover set shift_end = now() - interval '1 minute' where id = %s", (running["id"],))
        [listed] = [r for r in handover.listing(conn) if r["id"] == running["id"]]
        assert listed["status"] == "not acknowledged"
        assert handover.acknowledge(conn, running["id"], "lead-in")["status"] == "acknowledged"
    finally:
        conn.rollback()
