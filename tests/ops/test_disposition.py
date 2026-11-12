"""Structured close-out (P6.1a, #655): what a closed case was, in ISO 14224 terms, pre-filled from its own words."""

import os
import tempfile

import pytest

from fuzer.core import db, work
from fuzer.core.pack import load_pack
from fuzer.ops import disposition

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        from fuzer.workflow import engine

        engine.load_defaults(c)
        c.commit()
        yield c


def test_a_real_problem_closes_with_its_failure_mode_cause_and_action_pre_filled(conn):
    case = work.open_case(conn, "manual", "P-101 vibration high on the drive end", by="ioc-ann")["id"]
    work.comment(conn, case, "fred", "bearing worn, replaced it")
    form = disposition.form(conn, PACK, case)
    assert form["record"] is None
    assert form["suggestion"] == {"from": "words", "disposition": "real", "failure_mode": "VIB", "activity": "1"}
    assert "VIB" in form["vocabulary"]["failure_modes"] and "3" in form["vocabulary"]["causes"]
    with pytest.raises(ValueError, match="needs its cause"):
        disposition.record(conn, PACK, case, "fred", "real", failure_mode="VIB", activity="1")
    with pytest.raises(ValueError, match="not a failure mode"):
        disposition.record(conn, PACK, case, "fred", "real", failure_mode="WOBBLE", cause="3", activity="1")
    row = disposition.record(conn, PACK, case, "fred", "real", failure_mode="VIB", mechanism="2.4", cause="3",
                             activity="1", outcome="fixed", suggested=form["suggestion"])
    assert (row["disposition"], row["failure_mode"], row["cause"], row["activity"]) == ("real", "VIB", "3", "1")
    item = conn.execute("select state, outcome, closed_at from work_item where id = %s", (case,)).fetchone()
    assert item["state"] == "done" and item["closed_at"]
    assert item["outcome"].startswith("A real problem: Vibration, cause failure related to operation / maintenance, replace")
    assert case not in {m["id"] for m in disposition.missing(conn)}
    conn.commit()


def test_a_false_alarm_or_duplicate_needs_no_cause_and_a_closed_case_shows_it_is_missing(conn):
    alarm = work.open_case(conn, "manual", "Tank level alarm: spurious, nothing wrong", by="ioc-ann")["id"]
    assert disposition.suggest(conn, PACK, alarm)["disposition"] == "false_alarm"
    row = disposition.record(conn, PACK, alarm, "ioc-ann", "false_alarm", failure_mode="VIB", cause="3")
    assert row["failure_mode"] is None and row["cause"] is None  # the failure terms are for real problems only
    first = work.open_case(conn, "manual", "Leak at P-102", by="ioc-ann")["id"]
    again = work.open_case(conn, "manual", "Leak at P-102 again", by="ioc-ann")["id"]
    with pytest.raises(ValueError, match="names the case"):
        disposition.record(conn, PACK, again, "ioc-ann", "duplicate")
    assert disposition.record(conn, PACK, again, "ioc-ann", "duplicate", duplicate_of=first)["duplicate_of"] == first
    work.close_case(conn, first, "done", "fred", reason="process ended")
    assert first in {m["id"] for m in disposition.missing(conn)}  # closed another way: still to fill in
    disposition.record(conn, PACK, first, "fred", "data_problem", note="the level transmitter read wrong")
    assert first not in {m["id"] for m in disposition.missing(conn)}
    conn.commit()


def test_an_agents_suggestion_pre_fills_the_form(conn):
    case = work.open_case(conn, "manual", "Compressor trip", by="ioc-ann",
                          payload={"closeout_suggestion": {"disposition": "real", "failure_mode": "UST", "cause": "3",
                                                           "activity": "4", "junk": 1}})["id"]
    assert disposition.suggest(conn, PACK, case) == {"disposition": "real", "failure_mode": "UST", "cause": "3",
                                                     "activity": "4", "from": "agent"}
    conn.commit()
