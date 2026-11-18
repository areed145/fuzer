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


def test_some_reports_back_from_the_field_wait_for_the_ops_lead_to_close_out(crewed, monkeypatch):
    """The persona tour never saw anything under Back from the field: the lead closed every report within the hour.
    Some now wait in the ops lead queue, still reading as back from the field, for a visitor to close out (#1115)."""
    monkeypatch.setattr(sample_crew, "LEFT", 0.0)
    monkeypatch.setattr(sample_crew, "COULD_NOT", 1.0)  # every first visit comes back for the IOC to decide
    monkeypatch.setattr(sample_crew, "BACK_LEFT", 1.0)
    raised = [raise_exception(crewed, FIELD_TYPE, f"crew-back-{i}", f"Crew test back {i}", "detector:test")
              for i in range(6)]
    crewed.commit()
    _run_for(crewed, 24)  # a whole day: how fast the field gets to a job depends on where in the shift the clock starts
    items = [r["item_id"] for r in exceptions.listing(crewed, state=None, type=FIELD_TYPE, limit=100)
             if r["id"] in {x["id"] for x in raised} and r["item_id"]]
    left = crewed.execute(
        "select count(*) as n from work_event where item_id = any(%s) and kind = 'handed_off' "
        "and data->>'to' = 'role:ops_lead' and data->>'reason' like 'Back from the field:%%'", (items,)).fetchone()["n"]
    assert left, "some of the field's reports were left for the ops lead to close out"


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
    assert closers & {"casey"} and not closers & {"sam", "alex"}


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
    assert "sample crew" in kinds[sample_crew.FIELD_REPORT]["label"]


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
