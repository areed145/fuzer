"""A handover you can read on a phone (#1351): it says what changed this shift, not every open line; a source problem
that already cleared is not the next shift's news; a line's time goes to the page to say with its date (P5.26i); a past
handover nobody acknowledged says so, not "draft"; and saving notes mid-shift doesn't hand the shift over."""

from datetime import UTC, datetime, timedelta

import pytest

from fuzer.core import work
from fuzer.core.exceptions import clear_exception, raise_exception
from fuzer.ops import handover
from fuzer.sources import alerts

pytestmark = pytest.mark.integration


def test_a_line_carries_its_time_for_the_page_to_date():
    """P5.26i, #1371: a time written "22:50" on the shift change's day read without a day the next morning. A line's
    time is its `at`, written `{at}` in its words, so the page says it with its date."""
    t = datetime(2026, 11, 24, 22, 50, tzinfo=UTC)
    line = handover._line("Swap the strainer: done {at}", "case:3", at=t)
    assert line == {"text": "Swap the strainer: done {at}", "cites": ["case:3"], "at": "2026-11-24T22:50+00:00"}
    assert handover._line("Pump PM due", "case:4") == {"text": "Pump PM due", "cites": ["case:4"]}
    assert handover._at("2026-11-26T08:00") == "2026-11-26T08:00", "the plant's clock stays as written"


def test_the_same_line_said_again_is_one_line_with_how_many_times():
    lines = [{"text": "Gauge check sent to the operator queue {at}", "cites": ["case:1"], "at": "2026-11-24T08:00+00:00"},
             {"text": "Other", "cites": ["case:2"]},
             {"text": "Gauge check sent to the operator queue {at}", "cites": ["case:3"], "at": "2026-11-24T09:00+00:00"}]
    assert handover._once(lines) == [
        {"text": "Gauge check sent to the operator queue {at} (2 times)", "cites": ["case:1"], "at": "2026-11-24T08:00+00:00"},
        {"text": "Other", "cites": ["case:2"]}]


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
        assert any(f"case:{done['id']}" in x["cites"] and x["text"] == "Swap the strainer on P-101: done {at}" and x["at"]
                   for x in closed)
        if cleared.get("item_id"):  # its case closed when it cleared: not news under Closed either
            assert not any(f"case:{cleared['item_id']}" in x["cites"] for x in closed)
        assert failing["raised"]
    finally:
        conn.rollback()


def test_a_shift_of_repeats_is_a_few_lines_with_links_to_the_rest(okan_db):
    """P5.26i, #1371: the handover ran to nine phone screens. The same work done again is one line, and a section the
    List can show links there, so the page lists a few lines and links the rest."""
    conn = okan_db.conn
    conn.rollback()
    start = conn.execute("select now() as t").fetchone()["t"]
    try:
        for _ in range(12):
            c = work.open_case(conn, "handover_test", "Gauge check on 10-PT-2001", "test", role="ops_lead")
            work.close_case(conn, c["id"], "done", "test", reason="checked")
        f = handover.facts(conn, start, datetime.now(UTC) + timedelta(seconds=1))
        closed = [x for x in f["sections"]["Closed"] if x["text"].startswith("Gauge check on 10-PT-2001")]
        assert len(closed) == 1 and closed[0]["text"].endswith("(12 times)") and closed[0]["at"]
        assert set(f["links"]) <= set(handover.LINKS) and all(f["sections"][k] for k in f["links"])
        assert all("{at}" not in x["text"] or x.get("at") for xs in f["sections"].values() for x in xs), \
            "every time in a line is a time the page can date"
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
