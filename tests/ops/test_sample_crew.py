"""The sample crew (#1073) on the Okan field: only a project whose bundle asks for one gets it; it works the queue on
today's clock through the ordinary domain operations (field jobs out and back, desks taking and closing, rounds
walked), leaves about one in ten for nobody, and never touches work a real person holds."""

from collections import Counter
from datetime import timedelta

import pytest

from fuzer.core import projects
from fuzer.core.exceptions import TYPES, exception_type, raise_exception
from fuzer.ops import exceptions, rounds, sample_crew
from fuzer.workflow import engine

FIELD_TYPE, DESK_TYPE = "crew_field_check", "crew_desk_review"


@pytest.fixture(scope="module", autouse=True)
def crew_types():
    """Two exception types for these tests only: declared for the module, gone after it (other suites count types)."""
    exception_type(FIELD_TYPE, "Crew test: field check", role="role:engineer", audience=("ioc", "field"))
    exception_type(DESK_TYPE, "Crew test: desk review", role="role:data_steward", audience=("ioc",))
    yield
    TYPES.pop(FIELD_TYPE, None)
    TYPES.pop(DESK_TYPE, None)


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    engine.load_defaults(c)
    c.commit()
    return c


@pytest.fixture
def crewed(conn, monkeypatch):
    monkeypatch.setattr(sample_crew, "enabled", lambda conn: True)
    yield conn
    conn.rollback()


def _now(conn):
    return conn.execute("select now() as t").fetchone()["t"]


def _run_for(conn, hours: float, step_minutes: int = 15, start=None) -> Counter:
    """The crew's ticks over `hours`, its clock ahead of the database's (what the work engine stamps is real time)."""
    start = start or _now(conn)
    did: Counter = Counter()
    for k in range(int(hours * 60 / step_minutes) + 1):
        out = sample_crew.run(conn, start + timedelta(minutes=step_minutes * k))
        assert not out["errors"], out["errors"]
        did.update(out["did"])
    return did


# What each thing the field notices is about, and so what the field says when it is done: tour F (F4) saw a fogged
# gauge glass answered "Local gauge agrees with the DCS after blowing down the impulse line".
NOTICED_TOPICS = {
    "Unusual noise from {tag} ({what})": "noise",
    "Oil level low in the sight glass on {tag} ({what})": "oil",
    "Weeping mechanical seal on {tag} ({what})": "seal",
    "Coupling guard bolt missing on {tag} ({what})": None,
    "Weeping bonnet flange on {tag} ({what})": "leak",
    "Local gauge downstream of {tag} ({what}) reads higher than the DCS": "gauge",
    "Small drip from the drain valve beside {tag} ({what})": "leak",
    "Corroded flange bolting on {tag} ({what})": "corrosion",
    "Weeping stem packing on {tag} ({what})": "leak",
    "Position indicator on {tag} ({what}) disagrees with the DCS": "position",
    "Weeping channel head flange on {tag} ({what})": "leak",
    "Corrosion under insulation suspected on {tag} ({what})": "corrosion",
    "Local outlet temperature gauge on {tag} ({what}) reads higher than the DCS": "temperature",
    "Level in the sight glass on {tag} ({what}) disagrees with the DCS": "level",
    "Small drip from a drain valve on {tag} ({what})": "leak",
    "Loose cable gland and missing cover bolt on {tag} ({what})": "electrical",
    "Local display on {tag} ({what}) reads higher than the DCS": "gauge",
    "Weeping impulse line fitting at {tag} ({what})": "leak",
    "Weeping flange on {tag} ({what})": "leak",
}


def test_the_fields_reply_answers_what_it_was_asked():
    noticed = {t for ts in sample_crew.NOTICED.values() for t in ts}
    assert noticed == set(NOTICED_TOPICS)
    for title, topic in NOTICED_TOPICS.items():
        assert sample_crew._topic({"title": title}) == topic, title
    fogged = {"title": "30-V-2001 level gauge glass is fogged inside, cannot read the level locally."}
    assert sample_crew._topic(fogged) == "glass"
    for k in range(20):
        done = sample_crew._note(sample_crew.DONE_NOTES, fogged, k, "note")
        assert "glass" in done and "impulse line" not in done, done
    # a level that disagrees is answered at the glass, an indicator at the valve, never as a pressure gauge or an oil top-up
    for title, word in (("Level in the sight glass on 30-V-2001 disagrees with the DCS", "gauge glass"),
                        ("Position indicator on 68-HCV-1004 disagrees with the DCS", "indicator")):
        for k in range(20):
            assert word in sample_crew._note(sample_crew.DONE_NOTES, {"title": title}, k, "note")
    for topic in {t for t in NOTICED_TOPICS.values() if t} | {"glass"}:
        assert topic in sample_crew.DONE_NOTES, topic


def test_only_a_project_whose_bundle_asks_gets_a_crew(conn):
    assert not sample_crew.enabled(conn)
    assert sample_crew.run(conn) == {}  # nothing staffed, nothing done
    name = projects.current(conn)
    cfg = projects.config(conn, name)
    projects.save(conn, name, {**cfg, "sample_crew": True}, "test")
    conn.commit()
    try:
        assert sample_crew.enabled(conn)
    finally:
        projects.save(conn, name, cfg, "test")
        conn.commit()


def test_the_crew_joins_the_roster_and_leaves_changed_entries_alone(crewed):
    crewed.execute("delete from work_person where name = any(%s)", (list(sample_crew.CREW),))
    engine.set_person(crewed, "pat", ["maintenance_planner", "engineer"], by="an owner")
    added = sample_crew.staff(crewed)
    crewed.commit()
    assert "pat" not in added and {"riley", "sam", "alex"} <= set(added)
    people = {p["name"]: p["roles"] for p in engine.people(crewed)}
    assert people["pat"] == ["maintenance_planner", "engineer"]
    assert "operator" in people["sam"] and "ops_lead" in people["riley"]
    assert sample_crew.staff(crewed) == []


def test_field_work_goes_out_comes_back_and_closes(crewed):
    raised = [raise_exception(crewed, FIELD_TYPE, f"crew-field-{i}", f"Crew test field check {i}", "detector:test")
              for i in range(12)]
    crewed.commit()
    _run_for(crewed, 30)
    rows = {r["id"]: r for r in exceptions.listing(crewed, state=None, type=FIELD_TYPE, limit=100)}
    left = [r for r in raised if sample_crew._u(r["id"], "path") < sample_crew.LEFT]
    worked = [r for r in raised if r not in left]
    assert worked
    for r in left:  # nobody's: still in its queue, untouched
        row = rows[r["id"]]
        assert row["state"] == "open" and not row["assignee"] and not (row["evidence"] or {}).get("field")
    by = Counter()
    for r in worked:
        row = rows[r["id"]]
        for f in (row["evidence"] or {}).get("field") or []:
            by[f["by"]] += 1
    assert set(by) <= set(sample_crew.FIELD) and by, by  # only the field reports, as themselves
    closed = [r for r in worked if rows[r["id"]]["state"] == "closed"]
    assert closed, "some field work closed on the field's report"
    item = closed[0]["item_id"] if closed[0]["item_id"] else rows[closed[0]["id"]]["item_id"]
    history = crewed.execute("select actor, kind from work_event where item_id = %s order by id", (item,)).fetchall()
    assert {"riley"} & {h["actor"] for h in history}
    closed_out = crewed.execute("select item_id, by, disposition from case_disposition where item_id = any(%s)",
                                ([rows[r["id"]]["item_id"] for r in closed],)).fetchall()
    assert closed_out and {c["by"] for c in closed_out} == {"riley"}  # the lead closes out what the field did (#1115)


def test_reports_back_from_the_field_wait_in_a_queue_for_the_ops_lead_to_close_out(crewed, monkeypatch):
    """The persona tour never saw anything under Back from the field: the lead closed every report within the hour.
    Every report now comes back to a queue, not to the lead in person (#1364), and some wait there for hours,
    still reading as back from the field, for a visitor to close out (#1115)."""
    monkeypatch.setattr(sample_crew, "LEFT", 0.0)
    monkeypatch.setattr(sample_crew, "COULD_NOT", 1.0)  # every first visit comes back for the IOC to decide
    monkeypatch.setattr(sample_crew, "BACK_LEFT", 1.0)
    raised = [raise_exception(crewed, FIELD_TYPE, f"crew-back-{i}", f"Crew test back {i}", "detector:test")
              for i in range(6)]
    crewed.commit()
    _run_for(crewed, 24)  # a whole day: how fast the field gets to a job depends on where in the shift the clock starts
    items = [r["item_id"] for r in exceptions.listing(crewed, state=None, type=FIELD_TYPE, limit=100)
             if r["id"] in {x["id"] for x in raised} and r["item_id"]]
    # back in the queue its step sits in (this type's is the engineer queue), never with a person (#1364)
    back = crewed.execute(
        "select data->>'to' as to, count(*) as n from work_event where item_id = any(%s) and kind = 'handed_off' "
        "and data->>'reason' like 'Back from the field:%%' group by 1", (items,)).fetchall()
    assert back and {b["to"] for b in back} == {"role:engineer"}, back


def test_desks_take_work_and_close_it_and_a_real_persons_work_is_theirs(crewed):
    raised = [raise_exception(crewed, DESK_TYPE, f"crew-desk-{i}", f"Crew test desk review {i}", "detector:test")
              for i in range(10)]
    crewed.commit()
    mine = next(r for r in raised if sample_crew._u(r["id"], "path") >= sample_crew.LEFT)
    exceptions.dispatch(crewed, mine["id"], "user:someone", "user:someone", "mine")
    crewed.commit()
    _run_for(crewed, 4 * 24, step_minutes=60)
    rows = {r["id"]: r for r in exceptions.listing(crewed, state=None, type=DESK_TYPE, limit=100)}
    assert rows[mine["id"]]["state"] == "open" and rows[mine["id"]]["assignee"] == "user:someone"
    others = [r for r in raised if r is not mine and sample_crew._u(r["id"], "path") >= sample_crew.LEFT]
    assert any(rows[r["id"]]["state"] == "closed" for r in others)
    closers = {e["actor"] for e in crewed.execute(
        "select actor from work_event where kind in ('state_changed', 'closed', 'completed') and item_id = any(%s)",
        ([rows[r["id"]]["item_id"] for r in others],))}
    assert closers & {"casey"} and not closers & set(sample_crew.FIELD)


def test_most_rounds_are_walked_through_the_shift(crewed):
    _, end = rounds.shift(crewed, _now(crewed))
    if not rounds.rounds(crewed):
        pytest.skip("no rounds in this export")
    late = end - timedelta(minutes=10)
    sample_crew.walk_rounds(sample_crew._Tick(crewed, late))
    runs = rounds.runs(crewed, late)
    full = [r for r in runs if r["taken"] >= r["points"]]
    assert len(full) >= 0.75 * len(runs)
    takers = {t["by"] for t in crewed.execute("select by from round_reading")}
    assert takers <= set(sample_crew.FIELD)
    readings = crewed.execute("select count(*) as n, count(*) filter (where not passed) as bad from round_reading "
                              "").fetchone()
    assert readings["bad"] <= 0.1 * readings["n"]


def test_the_field_reports_what_it_notices_a_few_times_a_shift_once_each(crewed):
    sample_crew.staff(crewed)
    crewed.commit()
    start, end = rounds.shift(crewed, _now(crewed))
    late = end - timedelta(minutes=1)
    for _ in range(2):  # a second tick in the same shift raises nothing more
        sample_crew.field_reports(sample_crew._Tick(crewed, late))
    rows = crewed.execute("select key, title, source, subjects, state from exception_record where type = %s and key like %s",
                          (sample_crew.FIELD_REPORT, f"{start.isoformat(timespec='minutes')}#%")).fetchall()
    assert len(rows) == sample_crew.REPORTS_PER_SHIFT
    assert all(r["source"] == "sample crew" and r["subjects"] for r in rows)
    kinds = {t["name"]: t for t in exceptions.type_listing(crewed)}
    # one name for the kind (#1358); each report says it is synthetic by its source, and the kind by its description
    assert kinds[sample_crew.FIELD_REPORT]["label"] == "Field report"
    assert "synthetic" in kinds[sample_crew.FIELD_REPORT]["description"]


def test_desk_work_with_no_queue_goes_to_the_engineers_not_the_field(crewed):
    """An alarm flood is "not an alarm to acknowledge": the lead sends it to the engineers' desk, never to an operator
    (the persona tour found the field's queue holding alarm floods it could only hand back)."""
    exception_type("crew_desk_only", "Crew test: desk only", audience=("ioc",))
    try:
        raised = [raise_exception(crewed, "crew_desk_only", f"crew-desk-{i}", f"Crew test desk work {i}",
                                  "detector:test") for i in range(12)]
        crewed.commit()
        _run_for(crewed, 6)
        rows = {r["id"]: r for r in exceptions.listing(crewed, state=None, type="crew_desk_only", limit=100)}
        sent = [rows[r["id"]] for r in raised if rows[r["id"]]["item_id"]]
        assert sent, "the lead triaged some of it"
        for row in sent:
            assert row["role"] != "operator" and row["assignee"] not in sample_crew.FIELD, row
            assert not (row["evidence"] or {}).get("field")
    finally:
        TYPES.pop("crew_desk_only", None)



def test_a_bulletin_is_never_sent_to_the_field_and_a_message_goes_out_in_its_own_words(crewed, monkeypatch):
    """Tour F, B-N10: the vendor's service bulletin (1214) went to morgan as "Go and have a look, report what you find",
    and a ride-along then joined it. A message that asks for no field work is logged by the lead, never sent out; one
    that does goes out asking what it said."""
    from fuzer.ops import messages, options

    monkeypatch.setattr(sample_crew, "LEFT", 0.0)
    monkeypatch.setattr(sample_crew, "PLANNED", 0.0)
    # every field case the lead triages goes out (else whether a message is sent hangs on its case number's hash)
    monkeypatch.setattr(sample_crew, "FIELD_SHARE", {True: 1.0, False: 0.0})
    monkeypatch.setattr(sample_crew, "QUEUED", 100)
    said = {}
    for i in range(6):
        bulletin = i % 2 == 0
        text = (f"Service bulletin {i} attached for the fuel nozzles. It applies to all three drivers." if bulletin
                else f"Weeping flange {i} on the glycol pump, please check")
        r = raise_exception(crewed, messages.MESSAGE_REPORT, f"crew-msg-{i}", text[:60], "messages")
        crewed.execute("insert into message (id, channel, sender, text, received_at, outcome, exception_id) "
                       "values (%s, 'mailbox', 'Vendor', %s, now(), 'case', %s)", (f"crew-m-{i}", text, r["id"]))
        said[r["id"]] = (bulletin, text)
    crewed.commit()
    _run_for(crewed, 6)
    logged = sent = 0
    for eid, (bulletin, text) in said.items():
        item = crewed.execute("select item_id from exception_record where id = %s", (eid,)).fetchone()["item_id"]
        asks = [e["r"] for e in crewed.execute(
            "select data->>'reason' as r from work_event where item_id = %s and kind in ('handed_off', 'created') "
            "and data->>'reason' is not null", (item,))]
        assert options.ASK not in asks, asks
        if bulletin:
            field = crewed.execute("select 1 from work_token where item_id = %s and role = 'operator'", (item,)).fetchone()
            assert field is None, f"bulletin {eid} went to the field"
            closed = crewed.execute("select disposition from case_disposition where item_id = %s", (item,)).fetchone()
            assert closed is None or closed["disposition"] == "log_only", closed
            logged += closed is not None
        elif asks:
            assert text in asks, asks
            sent += 1
    assert logged and sent, (logged, sent)


def test_what_the_field_found_does_not_wait_behind_the_leads_long_cases(crewed, monkeypatch):
    """On the live demo the lead held five long cases, so every field report was "queued behind the work in hand" and
    152 piled up (live demo, 24 Nov). What the field found (QUICK) is read and closed as it comes in, whatever the lead holds."""
    exception_type("crew_lead_long", "Crew test: lead long case", role="role:ops_lead", audience=("ioc",))
    exception_type("crew_lead_quick", "Crew test: lead quick read", role="role:ops_lead", audience=("ioc",))
    monkeypatch.setattr(sample_crew, "QUICK", (*sample_crew.QUICK, "crew_lead_quick"))
    monkeypatch.setattr(sample_crew, "LEFT", 0.0)
    monkeypatch.setattr(sample_crew, "_desk_hours", lambda i, t=None: 2.0 if t == "crew_lead_quick" else 500.0)
    try:
        sample_crew.staff(crewed)
        for i in range(12):  # more long cases on the lead's desk than any desk size
            r = raise_exception(crewed, "crew_lead_long", f"crew-long-{i}", f"Crew test long {i}", "detector:test")
            exceptions.dispatch(crewed, r["id"], "riley", "riley", "mine")
        quick = [raise_exception(crewed, "crew_lead_quick", f"crew-quick-{i}", f"Crew test quick {i}",
                                 "detector:test") for i in range(6)]
        crewed.commit()
        _run_for(crewed, 12, step_minutes=30)
        rows = {r["id"]: r for r in exceptions.listing(crewed, state=None, type="crew_lead_quick", limit=100)}
        assert all(rows[r["id"]]["state"] == "closed" for r in quick), [rows[r["id"]]["state"] for r in quick]
    finally:
        TYPES.pop("crew_lead_long", None)
        TYPES.pop("crew_lead_quick", None)


def test_the_crew_keeps_up_with_what_the_field_reports(crewed):
    """At the old desk sizes the field's own reports came in at 16 a day and closed at under 2 (live demo, 24 Nov): open work only
    grew. Over three days of ticks most of what the field reports is worked to closed, not left to pile up."""
    exception_type("crew_lead_backlog", "Crew test: lead backlog", role="role:ops_lead", audience=("ioc",))
    try:
        backlog = [raise_exception(crewed, "crew_lead_backlog", f"crew-backlog-{i}", f"Crew test backlog {i}",
                                   "detector:test") for i in range(30)]  # the lead's long cases, weeks in
        sample_crew.staff(crewed)
        for r in backlog[:6]:  # and a hand full of them on the lead's desk already
            exceptions.dispatch(crewed, r["id"], "riley", "riley", "mine")
        for i in range(12):
            raise_exception(crewed, FIELD_TYPE, f"crew-keepup-{i}", f"Crew test keep up {i}", "detector:test")
        crewed.commit()
        start = _now(crewed)
        _run_for(crewed, 72, step_minutes=30, start=start)
    finally:
        TYPES.pop("crew_lead_backlog", None)
    rows = crewed.execute(
        "select state from exception_record where type = %s and first_seen >= %s - interval '1 hour' "
        "and first_seen < %s + interval '48 hours'", (sample_crew.FIELD_REPORT, start, start)).fetchall()
    assert len(rows) >= 2 * sample_crew.REPORTS_PER_SHIFT, len(rows)
    closed = sum(r["state"] == "closed" for r in rows)
    assert closed >= 0.8 * len(rows), (closed, len(rows))
