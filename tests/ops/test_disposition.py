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
    conn.execute("insert into entity (fuzer_id, kind) values ('fz-p101', 'equipment')")
    case = work.open_case(conn, "manual", "P-101 vibration high on the drive end", by="ioc-ann", subjects=["fz-p101"])["id"]
    work.comment(conn, case, "fred", "bearing worn, replaced it")
    form = disposition.form(conn, PACK, case)
    assert form["record"] is None and form["equipment"] is True
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


def test_a_real_problem_off_equipment_needs_no_failure_terms(conn):
    """A drawing-vs-alarm limit that was really wrong is a real problem, but not an equipment failure (#1145): the
    ISO 14224 picks are optional there, and kept when given."""
    from fuzer.core import exceptions

    conn.execute("insert into entity (fuzer_id, kind) values ('fz-v2002', 'equipment'), ('fz-lt2002', 'point')")
    x = exceptions.raise_exception(conn, "drawing_disagreement", "pid:lt2002:high", "20-LT-2002 high limit: drawing 52, "
                                   "DCS 57", "stewardship", refs=["fz-v2002"])
    item = conn.execute("select item_id from exception_record where id = %s", (x["id"],)).fetchone()["item_id"]
    assert disposition.form(conn, PACK, item)["equipment"] is False
    row = disposition.record(conn, PACK, item, "riley", "real", outcome="fixed", note="DCS limit set to 52")
    assert row["disposition"] == "real" and row["failure_mode"] is None
    bare = work.open_case(conn, "manual", "Point limit looks wrong", by="ioc-ann", subjects=["fz-lt2002"])["id"]
    assert disposition.form(conn, PACK, bare)["equipment"] is False  # a point on no equipment
    conn.execute("insert into edge (src, dst, rel, sources, confidence) values ('fz-lt2002', 'fz-v2002', 'installed_at', '{test}', 1)")
    assert disposition.form(conn, PACK, bare)["equipment"] is True  # installed on the vessel: an equipment case
    conn.commit()


def test_a_case_off_equipment_pre_fills_no_failure_terms_the_form_folds_away(conn):
    """A stuck level control valve reported in a message was closed as "A real problem: Noise", the failure mode taken
    from the folded, optional section nobody opened (#1366). Off equipment the pre-fill says what it was and nothing
    the form doesn't show."""
    case = work.open_case(conn, "manual", "Contactor level high, LCV stuck; noisy, vibration on the line",
                          by="ioc-ann")["id"]
    work.comment(conn, case, "alex", "Field: Done: stroked the LCV by hand, replaced the positioner")
    words = disposition.suggest(conn, PACK, case)
    assert words["disposition"] == "real" and words.get("failure_mode")  # the words alone name a failure mode
    form = disposition.form(conn, PACK, case)
    assert form["equipment"] is False
    assert form["suggestion"] == {"from": "words", "disposition": "real"}
    row = disposition.record(conn, PACK, case, "riley", "real", outcome="monitoring", suggested=form["suggestion"])
    assert row["failure_mode"] is None and row["cause"] is None and row["activity"] is None
    conn.commit()


def test_a_false_alarm_or_duplicate_needs_no_cause_and_a_closed_case_shows_it_is_missing(conn):
    alarm = work.open_case(conn, "manual", "Tank level alarm: spurious, nothing wrong", by="ioc-ann")["id"]
    assert disposition.suggest(conn, PACK, alarm)["disposition"] == "false_alarm"
    # the field's answer, a comment, fills it in (#34): a gauge put right is a data problem; "not as it should be" is no
    # false alarm
    gauge = work.open_case(conn, "manual", "81-HCV-1003 pressure off", by="ioc-ann")["id"]
    work.comment(conn, gauge, "alex", "Field: Done: Local gauge agrees with the DCS after blowing down the impulse line.")
    assert disposition.suggest(conn, PACK, gauge)["disposition"] == "data_problem"
    checked = work.open_case(conn, "manual", "Bonnet check", by="ioc-ann")["id"]
    work.comment(conn, checked, "alex", "Field: Done: Found it not as it should be")
    assert disposition.suggest(conn, PACK, checked).get("disposition") != "false_alarm"
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


def test_a_due_recheck_asks_whoever_closed_it_and_a_no_reopens_the_case_as_a_repeat(conn):
    case = work.open_case(conn, "manual", "P-301 leaking at the seal", by="ioc-ann", subjects=["fz-p301"])["id"]
    row = disposition.record(conn, PACK, case, "fred", "real", failure_mode="ELP", cause="3", activity="1")
    assert row["recheck_at"] is not None
    assert disposition.sweep(conn, PACK)["rechecks"] == []  # not due yet
    conn.execute("update case_disposition set recheck_at = now() - interval '1 minute' where item_id = %s", (case,))
    [check] = disposition.sweep(conn, PACK)["rechecks"]
    asked = conn.execute("select type, assignee, title, payload from work_item where id = %s", (check,)).fetchone()
    assert (asked["type"], asked["assignee"], asked["payload"]["recheck_of"]) == ("recheck", "fred", case)
    assert asked["title"] == "Did the fix hold? P-301 leaking at the seal"
    assert disposition.sweep(conn, PACK)["rechecks"] == []  # asked once
    out = disposition.answer_recheck(conn, case, "fred", held=False, note="weeping again")
    assert out["held"] is False and out["held_note"] == "weeping again"
    states = {r["id"]: r["state"] for r in conn.execute(
        "select id, state from work_item where id = any(%s)", ([case, check],)).fetchall()}
    assert states == {case: "open", check: "done"}  # the first case is open again, the re-check answered
    assert [r["again"] for r in disposition.form(conn, PACK, case)["repeats"]] == [check]
    conn.commit()


def test_the_same_failure_on_the_same_asset_inside_the_window_reopens_the_first_case(conn):
    first = work.open_case(conn, "manual", "K-100 vibration high", by="ioc-ann", subjects=["fz-k100"])["id"]
    disposition.record(conn, PACK, first, "fred", "real", failure_mode="VIB", cause="3", activity="4")
    conn.commit()
    other_asset = work.open_case(conn, "manual", "K-200 vibration high", by="ioc-ann", subjects=["fz-k200"])["id"]
    other_mode = work.open_case(conn, "manual", "K-100 seal leaking", by="ioc-ann", subjects=["fz-k100"])["id"]
    again = work.open_case(conn, "manual", "K-100 vibrating again", by="ioc-ann", subjects=["fz-k100"])["id"]
    found = disposition.sweep(conn, PACK)["repeats"]
    assert found == [{"first": first, "again": again}]
    rows = {r["id"]: r for r in conn.execute("select id, state from work_item where id = any(%s)",
                                             ([first, other_asset, other_mode, again],)).fetchall()}
    assert rows[first]["state"] == "open" and rows[again]["state"] == "cancelled"
    assert rows[other_asset]["state"] == rows[other_mode]["state"] == "open"
    assert disposition.sweep(conn, PACK)["repeats"] == []
    conn.commit()


def test_contained_with_a_follow_up_planned_sends_the_follow_up(conn):
    """#1385: "Contained, follow-up planned" made no follow-up. Closing it contained sends the follow-up to whoever the
    IOC names, as a case of its own linked from the closed one; a follow-up without that outcome is refused."""
    from fuzer.workflow import engine

    engine.set_person(conn, "pat", ["maintenance_planner"])
    case = work.open_case(conn, "manual", "Seal weep on the booster pump", by="ioc-ann")["id"]
    with pytest.raises(ValueError, match="contained"):
        disposition.record(conn, PACK, case, "riley", "real", outcome="fixed", follow_up_to="role:maintenance_planner")
    row = disposition.record(conn, PACK, case, "riley", "real", outcome="contained", note="clamped, seal change due",
                             follow_up_to="role:maintenance_planner")
    after = row["follow_up"]
    assert after and after != case
    sent = conn.execute("select title, state from work_item where id = %s", (after,)).fetchone()
    assert sent["title"] == "Follow-up: Seal weep on the booster pump" and sent["state"] != "done"
    step = conn.execute("select role, assignee from work_token where item_id = %s and state = 'waiting' and kind = 'user'",
                        (after,)).fetchone()
    assert step == {"role": "maintenance_planner", "assignee": None}
    # #1390 (D5): the close-out's note is what the follow-up asks, shown as its ask (the case page's `summary`)
    payload = conn.execute("select payload from work_item where id = %s", (after,)).fetchone()["payload"]
    assert (payload.get("start") or payload).get("summary") == "clamped, seal change due", payload
    assert disposition.form(conn, PACK, case)["follow_up"] == after
    said = [e["data"]["text"] for e in conn.execute(
        "select data from work_event where item_id = %s and kind = 'commented'", (case,))]
    assert f"Follow-up sent to maintenance planner queue: case {after}" in said
    # correcting the close-out sends no second one
    again = disposition.record(conn, PACK, case, "riley", "real", outcome="contained",
                               follow_up_to="role:maintenance_planner")
    assert again["follow_up"] == after
    conn.commit()


def test_after_the_field_said_done_the_outcome_is_filled_in_as_fixed(conn):
    """#1390 (D18): after a field Done "Gland refitted, cover bolt replaced" the close-out's Outcome read Not known."""
    from psycopg.types.json import Jsonb

    from fuzer.core import exceptions

    conn.execute("insert into entity (fuzer_id, kind) values ('fz-pt2100', 'equipment') on conflict do nothing")
    x = exceptions.raise_exception(conn, "drawing_disagreement", "pid:pt2100:gland", "20-P-2100 vibration high on the drive end",
                                   "stewardship", refs=["fz-pt2100"])
    item = conn.execute("select item_id from exception_record where id = %s", (x["id"],)).fetchone()["item_id"]
    before = disposition.suggest(conn, PACK, item)
    assert before.get("disposition") == "real" and "outcome" not in before, before
    field = [{"status": "on_site", "by": "sam"}, {"status": "done", "by": "sam", "note": "Gland refitted"},
             {"status": "note", "by": "sam", "note": "Area tidied"}]
    conn.execute("update exception_record set evidence = evidence || %s where id = %s",
                 (Jsonb({"field": field}), x["id"]))
    assert disposition.suggest(conn, PACK, item)["outcome"] == "fixed"
    conn.rollback()


def test_a_false_alarm_saves_no_outcome_and_nothing_found_pre_fills_no_fix(conn):
    """#1400 (E1): 1155's field said "no leak or damage found and the reading agrees locally"; the form read the leak
    and filled in "A real problem / Fixed", and choosing False alarm hid Outcome but saved "False alarm: fixed"."""
    from psycopg.types.json import Jsonb

    from fuzer.core import exceptions

    x = exceptions.raise_exception(conn, "drawing_disagreement", "pid:pt2001:erratic", "PT-2001 reading erratic, jumping 73 psi",
                                   "stewardship")
    case = conn.execute("select item_id from exception_record where id = %s", (x["id"],)).fetchone()["item_id"]
    work.comment(conn, case, "sam", "Field: Done: Checked on site: no leak or damage found and the reading agrees locally.")
    conn.execute("update exception_record set evidence = evidence || %s where id = %s",
                 (Jsonb({"field": [{"status": "done", "by": "sam"}]}), x["id"]))
    said = disposition.suggest(conn, PACK, case)
    assert said["disposition"] == "false_alarm" and "outcome" not in said, said
    row = disposition.record(conn, PACK, case, "ioc-ann", "false_alarm", outcome="fixed", note="no leak")
    assert row["outcome"] is None
    assert disposition.summary(row, disposition.vocabulary(PACK)) == "False alarm. no leak"
    conn.commit()


def test_contained_names_who_the_follow_up_goes_to_and_a_person_gets_a_step(conn):
    """#1400 (E4, E15): 1163 closed "contained, follow-up planned" with no follow-up; follow-up 1244 sent to pat read
    "No step is open" and no Waiting on."""
    from fuzer.workflow import engine

    engine.set_person(conn, "pat", ["maintenance_planner"])
    case = work.open_case(conn, "manual", "Gland leak on the transfer pump", by="ioc-ann")["id"]
    with pytest.raises(ValueError, match="who the follow-up goes to"):
        disposition.record(conn, PACK, case, "riley", "real", outcome="contained")
    row = disposition.record(conn, PACK, case, "riley", "real", outcome="contained", note="packing nipped up",
                             follow_up_to="pat")
    step = conn.execute("select assignee, state from work_token where item_id = %s and kind = 'user' "
                        "and state in ('ready', 'waiting')", (row["follow_up"],)).fetchone()
    assert step and step["assignee"] == "pat", step
    # correcting it later needs no second target: the follow-up already went
    assert disposition.record(conn, PACK, case, "riley", "real", outcome="contained")["follow_up"] == row["follow_up"]
    conn.commit()


def test_a_repeat_opens_its_event_again_with_its_case(conn):
    """#1400 (E16): a case opened again as a repeat kept its event closed, so it had no Options."""
    from fuzer.core import exceptions

    x = exceptions.raise_exception(conn, "drawing_disagreement", "pid:k300:surge", "K-300 running near surge",
                                   "stewardship")
    case = conn.execute("select item_id from exception_record where id = %s", (x["id"],)).fetchone()["item_id"]
    disposition.record(conn, PACK, case, "fred", "real", outcome="monitoring")
    assert conn.execute("select state from exception_record where id = %s", (x["id"],)).fetchone()["state"] == "closed"
    disposition.answer_recheck(conn, case, "fred", held=False, note="surging again")
    assert conn.execute("select state from exception_record where id = %s", (x["id"],)).fetchone()["state"] == "open"
    conn.commit()


def test_not_fixed_names_who_the_follow_up_goes_to_as_contained_does(conn):
    """Tour F, F10: "Not fixed" closed 1168 with no owner and no follow-up. It leaves work to do: it names who the
    follow-up goes to, as Contained does, and sends it."""
    from fuzer.workflow import engine

    engine.set_person(conn, "pat", ["maintenance_planner"])
    case = work.open_case(conn, "manual", "Sight glass fogged on the test separator", by="ioc-ann")["id"]
    with pytest.raises(ValueError, match="not fixed says who the follow-up goes to"):
        disposition.record(conn, PACK, case, "riley", "real", outcome="not_fixed")
    row = disposition.record(conn, PACK, case, "riley", "real", outcome="not_fixed", note="glass needs replacing",
                             follow_up_to="role:maintenance_planner")
    assert row["follow_up"] and row["follow_up"] != case
    assert disposition.form(conn, PACK, case)["follow_up"] == row["follow_up"]
    conn.commit()


def test_a_follow_up_to_an_operator_is_a_field_check_with_the_note_as_its_ask(conn):
    """Tour F, F16: the follow-up of 989 sent to sam (1252) was a desk Task due in 4 h. To a field operator, or the
    operator queue, it is a field job: a Field check on their list that asks the close-out's note."""
    from fuzer.ops import exceptions as ops_exceptions
    from fuzer.workflow import engine

    engine.set_person(conn, "sam", ["operator"])
    case = work.open_case(conn, "manual", "Flange weep on the export pump", by="ioc-ann")["id"]
    conn.commit()
    row = disposition.record(conn, PACK, case, "riley", "real", outcome="contained", note="Re-torque the flange bolts",
                             follow_up_to="sam")
    after = row["follow_up"]
    steps = conn.execute("select element, role, assignee from work_token where item_id = %s and kind = 'user' "
                         "and state in ('ready', 'waiting')", (after,)).fetchall()
    assert [(s["element"], s["assignee"]) for s in steps] == [("field_check", "sam")], steps
    job = next(j for j in ops_exceptions.field_jobs(conn, "sam") if j["item_id"] == after)
    assert job["sent"]["reason"] == "Re-torque the flange bolts", job["sent"]
    assert disposition.follow_up(conn, case) == after
    conn.commit()


def test_a_duplicate_is_suggested_and_carries_its_report_to_the_case_it_duplicates(conn):
    """Tour F, F9 (E14): 1237 (grinding at the coupling) closed as a duplicate of 210 (bearing temperature, same pump)
    needed the case number typed, and 210 showed nothing of 1237's report. The form suggests the case on the same
    equipment, and the original gets the duplicate's words and its number."""
    conn.execute("insert into entity (fuzer_id, kind) values ('fz-p300', 'equipment'), ('fz-tt300', 'point') "
                 "on conflict do nothing")
    conn.execute("insert into edge (src, rel, dst, sources, confidence) "
                 "values ('fz-tt300', 'installed_at', 'fz-p300', '{test}', 1)")
    first = work.open_case(conn, "manual", "P-300 bearing temperature high", by="ioc-ann", subjects=["fz-tt300"])["id"]
    other = work.open_case(conn, "manual", "Somewhere else entirely", by="ioc-ann")["id"]
    again = work.open_case(conn, "manual", "Grinding noise at the P-300 coupling", by="ioc-ann", subjects=["fz-p300"])["id"]
    assert disposition.form(conn, PACK, again)["duplicate_of"] == {"item_id": first,
                                                                    "title": "P-300 bearing temperature high"}
    assert disposition.form(conn, PACK, other)["duplicate_of"] is None
    disposition.record(conn, PACK, again, "riley", "duplicate", duplicate_of=first)
    said = [e["data"]["text"] for e in conn.execute(
        "select data from work_event where item_id = %s and kind = 'commented'", (first,))]
    assert f"Also reported in case {again}, closed as a duplicate of this one: Grinding noise at the P-300 coupling" in said
    conn.commit()
