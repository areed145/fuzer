"""Options with consequences on a case (P5.20, #1251), on Okan: a gas lift compressor trips and four wells drop out.
Each option says when it is done, the production deferred until then in bbl and $, the risk of waiting, readiness and
clashes; one is recommended with the factor that decided it; one call carries it out and the choice, the
recommendation and the reason stay on the case and are counted per work type."""

from datetime import UTC, datetime, timedelta

import pytest

from fuzer.ops import exceptions as ex
from fuzer.ops import options, plan

WELLS = ["OK-J01-01", "OK-J01-02", "OK-J01-03", "OK-J01-04"]


@pytest.fixture(scope="module")
def conn(okan_db):
    from fuzer.analysis import trips  # noqa: F401  declares machine_trip
    from fuzer.core import work
    from fuzer.workflow import engine

    c = okan_db.conn
    engine.load_defaults(c)
    work.add_person(c, "sam", ["operator"], "test")
    work.add_person(c, "riley", ["ops_lead", "engineer"], "test")
    c.commit()
    return c


@pytest.fixture(autouse=True)
def fresh(conn):
    yield
    conn.rollback()
    conn.execute("update exception_record set state = 'closed' where state = 'open' and type = 'machine_trip'")
    conn.execute("update plan_item set status = 'cancelled' where status in ('proposed', 'scheduled', 'in_progress')")
    conn.commit()


def trip(conn, key: str = "K-2300B trip") -> dict:
    e = ex.raise_(conn, "machine_trip", key, "20-K-2300B tripped: four lifted wells on OKJ-01 down", "agent:sre",
                  refs=[f"well:{w}" for w in WELLS], priority=0.9, role=None)
    conn.commit()
    return e


def visit(conn, days: float, title: str = "OKJ-01 well work", kind: str = "maintenance",
          refs=(f"well:{WELLS[0]}",)) -> dict:
    start = datetime.now(UTC) + timedelta(days=days)
    row = plan.propose(conn, kind, title, "planner", start, start + timedelta(hours=8),
                       refs=list(refs), status="scheduled", crew="role:operator")
    conn.commit()
    return row


def by_key(out: dict) -> dict:
    return {o["key"]: o for o in out["options"]}


def test_four_wells_down_cost_by_the_hour_and_dispatch_now_is_recommended(conn):
    e = trip(conn)
    visit(conn, 3)
    out = options.for_exception(conn, e["id"])
    assert out["applies"] is True, out
    assert out["loss"]["wells"] == 4 and out["loss"]["oil_bbld"] > 0
    o = by_key(out)
    assert set(o) == {"dispatch", "window", "shelve", "escalate"}
    d, w, s = o["dispatch"], o["window"], o["shelve"]
    assert d["hours"] < w["hours"] < s["hours"]
    assert d["deferred"]["oil_bbl"] < w["deferred"]["oil_bbl"] < s["deferred"]["oil_bbl"]
    # deferred = rate x time, priced by the pack
    assert w["deferred"]["oil_bbl"] == pytest.approx(out["loss"]["oil_bbld"] * w["hours"] / 24, rel=0.01)
    assert w["deferred"]["usd"] >= w["deferred"]["oil_bbl"] * out["prices"]["oil_bbl"] * 0.99
    assert "OKJ-01 well work" in w["detail"]
    # a trip opens with the engineer queue (machine_trip's role), so escalating goes past it to the ops lead
    assert o["escalate"]["label"] == "Escalate to the ops lead"
    assert out["recommended"] == "dispatch"
    assert "bbl" in out["reason"] and "$" in out["reason"]


def test_a_condition_costing_nothing_rides_along_with_the_visit(conn):
    from fuzer.core import exceptions as port
    from fuzer.core.exceptions import TYPES, ExceptionType

    TYPES["walkdown_check"] = ExceptionType("walkdown_check", "Walkdown check", audience=("ioc", "field"))
    try:
        e = port.raise_exception(conn, "walkdown_check", "GLM gauge", "Gauge on OKJ01-GLM reads low", "olga",
                                 refs=["allocation_node:OKJ01-GLM"], priority=0.3)
        conn.commit()
        visit(conn, 2, refs=["allocation_node:OKJ01-GLM"])
        out = options.for_exception(conn, e["id"])
        assert out["applies"] is True
        assert out["recommended"] == "window", out["reason"]
        assert "visit" in out["reason"].lower()
    finally:
        conn.rollback()
        TYPES.pop("walkdown_check", None)
        conn.execute("update exception_record set state = 'closed' where type = 'walkdown_check'")
        conn.commit()


def test_a_visit_rides_along_only_on_the_same_equipment_not_anywhere_in_the_area(conn):
    """#1358: every field report in Okan's compression area (half its tags) was offered the one gas turbine PM there.
    A visit is offered on the asset itself or what it is part of (a temperature on its exchanger), not elsewhere in
    the area."""
    from fuzer.core import exceptions as port
    from fuzer.core.exceptions import TYPES, ExceptionType

    TYPES["walkdown_check"] = ExceptionType("walkdown_check", "Walkdown check", audience=("ioc", "field"))
    try:
        e = port.raise_exception(conn, "walkdown_check", "TT weeping", "Weeping fitting at 20-TT-2250B", "olga",
                                 refs=["tag:20-TT-2250B"], priority=0.3)
        conn.commit()
        visit(conn, 2, title="Preventive maintenance gas turbine driver", refs=["tag:20-GT-2000A"])
        w = by_key(options.for_exception(conn, e["id"]))["window"]
        assert w["available"] is False and "nothing scheduled on its equipment" in w["unavailable"]
        visit(conn, 3, title="Clean 20-E-2250B", refs=["tag:20-E-2250B"])
        w = by_key(options.for_exception(conn, e["id"]))["window"]
        assert w["available"] is True and "Clean 20-E-2250B" in w["detail"]
    finally:
        conn.rollback()
        TYPES.pop("walkdown_check", None)
        conn.execute("update exception_record set state = 'closed' where type = 'walkdown_check'")
        conn.commit()


def test_no_visit_on_the_plan_says_so_and_desk_work_has_no_options(conn):
    e = trip(conn)
    out = options.for_exception(conn, e["id"])
    w = by_key(out)["window"]
    assert w["available"] is False and "nothing scheduled" in w["unavailable"]
    desk = conn.execute("select id from exception_record where type = 'criticality_not_assessed' "
                        "and state = 'open' limit 1").fetchone()
    if desk:
        assert options.for_exception(conn, desk["id"])["applies"] is False


def test_choosing_dispatch_sends_the_ask_and_counts_the_recommendation(conn):
    e = trip(conn, "K-2300B trip 2")
    out = options.choose(conn, e["id"], "riley", "dispatch", to="sam", ask="Restart checks on the four wells")
    conn.commit()
    assert out["choice"]["accepted"] is True and out["event"]["stage"] == "field"
    item = out["event"]["item_id"]
    said = [r["body"] for r in conn.execute(
        "select data->>'text' as body from work_event where item_id = %s and kind = 'commented' order by id", (item,))]
    assert any(s.startswith("Chose dispatch now") and "as recommended" in s for s in said), said
    sent = conn.execute("select data->>'reason' as r from work_event where item_id = %s and kind in "
                        "('handed_off', 'created') order by id desc limit 1", (item,)).fetchone()["r"]
    assert sent == "Restart checks on the four wells"
    assert options.for_case(conn, item)["applies"] is False  # the field has it now
    stats = options.acceptance(conn)
    assert stats["decided"] >= 1 and next(t for t in stats["types"] if t["type"] == "machine_trip")["rate"] is not None


def test_a_one_tap_dispatch_asks_the_field_to_look_not_the_cost_sum(conn):
    e = trip(conn, "K-2300B trip 4")
    out = options.choose(conn, e["id"], "riley", "dispatch", to="sam")
    conn.commit()
    sent = conn.execute("select data->>'reason' as r from work_event where item_id = %s and kind in "
                        "('handed_off', 'created') order by id desc limit 1", (out["event"]["item_id"],)).fetchone()["r"]
    assert sent == options.ASK

def test_the_field_gets_only_what_to_do_and_the_why_stays_on_the_case(conn):
    e = trip(conn, "K-2300B trip 5")
    out = options.choose(conn, e["id"], "riley", "dispatch", "the shelf costs more than the trip", to="sam",
                         ask="Check the seal for weeping")
    conn.commit()
    item = out["event"]["item_id"]
    sent = conn.execute("select data->>'reason' as r from work_event where item_id = %s and kind in "
                        "('handed_off', 'created') order by id desc limit 1", (item,)).fetchone()["r"]
    assert sent == "Check the seal for weeping"
    said = [r["body"] for r in conn.execute(
        "select data->>'text' as body from work_event where item_id = %s and kind = 'commented'", (item,))]
    assert any("the shelf costs more than the trip" in s for s in said), said


def test_any_option_takes_an_optional_why_and_is_counted_as_not_taken(conn):
    e = trip(conn, "K-2300B trip 3")
    visit(conn, 3)
    out = options.choose(conn, e["id"], "riley", "window", reason="crew already on OKJ-01 that day")
    conn.commit()
    assert out["choice"]["accepted"] is False and out["choice"]["recommended"] == "dispatch"
    on = conn.execute("select start_at, notes from plan_item where exception_id = %s and status = 'scheduled'",
                      (e["id"],)).fetchone()
    assert on and "crew already on OKJ-01 that day" in on["notes"] and "OKJ-01 well work" in on["notes"]
    assert options.decisions(conn, e["id"])[0]["reason"] == "crew already on OKJ-01 that day"
    conn.execute("update exception_record set state = 'closed' where id = %s", (e["id"],))  # the next trip is its own
    # no why: still carried out (#1347), and the recommendation's reason is not passed off as this choice's
    e2 = trip(conn, "K-2300B trip 5")
    out = options.choose(conn, e2["id"], "riley", "shelve")
    conn.commit()
    assert out["choice"]["accepted"] is False and out["choice"]["reason"] is None
    said = [r["t"] for r in conn.execute("select data->>'text' as t from work_event where item_id = %s and kind = "
                                         "'commented'", (out["event"]["item_id"],))]
    assert any(s.startswith("Chose shelve") and s.endswith("Why: none given") for s in said), said


def test_once_the_field_reports_it_done_the_options_go(conn):
    e = trip(conn, "K-2300B trip 6")
    options.choose(conn, e["id"], "riley", "dispatch", to="sam")
    conn.commit()
    ex.report(conn, e["id"], "sam", "on_site")
    ex.report(conn, e["id"], "sam", "done", "reset the trip and restarted, running")
    conn.commit()
    out = options.for_exception(conn, e["id"])
    assert out["applies"] is False and out["options"] == [], out


def test_the_api_reads_and_chooses(conn):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from fuzer.api import ops as ops_api

    app = FastAPI()
    app.include_router(ops_api.router)
    app.dependency_overrides[ops_api.get_conn] = lambda: conn
    client = TestClient(app)
    e = trip(conn, "K-2300B trip 4")
    r = client.get(f"/exceptions/{e['id']}/options")
    assert r.status_code == 200 and r.json()["recommended"] == "dispatch"
    r = client.post(f"/exceptions/{e['id']}/options/choose",
                    json={"by": "riley", "option": "escalate", "reason": "repeat trip: engineer to look first"})
    assert r.status_code == 200, r.text
    assert client.get("/options/acceptance").json()["decided"] >= 1
    assert client.get("/exceptions/999999/options").status_code == 404


@pytest.fixture
def report_type(conn):
    """A field-work type of its own that never joins another case, so two reports on one asset stay two."""
    from fuzer.core.exceptions import TYPES, ExceptionType

    TYPES["walkdown_check"] = ExceptionType("walkdown_check", "Walkdown check", audience=("ioc", "field"), joins=False)
    yield "walkdown_check"
    TYPES.pop("walkdown_check", None)
    conn.rollback()  # closed again, so a report left in the field is no clash for the next test's trip
    conn.execute("update exception_record set state = 'closed' where state = 'open' and type = 'walkdown_check'")
    conn.commit()


def raise_report(conn, key: str, title: str, refs: list[str]) -> dict:
    from fuzer.core import exceptions as port

    e = port.raise_exception(conn, "walkdown_check", key, title, "olga", refs=refs, priority=0.3)
    conn.commit()
    return e


def test_a_gas_smell_never_reads_low_and_a_report_is_not_costed_as_lost_production(conn, report_type):
    """#1365: a gas smell at a wellhead read "Risk low", and a drip was costed at the well's share of its test rate."""
    e = raise_report(conn, "gas smell", f"Gas smell at the wellhead of {WELLS[0]}", [f"well:{WELLS[0]}"])
    out = options.for_exception(conn, e["id"])
    assert out["applies"] is True
    risks = {o["key"]: o["risk"] for o in out["options"] if o.get("available")}
    assert all(r and r["level"] != "Low" for r in risks.values()), risks
    assert risks["dispatch"]["level"] == "Medium" and "gas smell reported" in risks["dispatch"]["why"]
    assert risks["shelve"]["level"] == "High" and out["recommended"] != "shelve"
    # nothing measured links a smell to lost production: what is behind it, not a loss
    assert out["loss"] is None and all(not o.get("deferred") for o in out["options"])
    assert out["if_it_trips"]["oil_bbld"] > 0
    assert any("nothing measured links it to a loss of production" in u for u in out["unknown"])


def test_no_criticality_and_no_hazard_gives_no_risk_word_and_no_shelf(conn, report_type):
    e = raise_report(conn, "glm gauge", "Gauge on OKJ01-GLM reads low", ["allocation_node:OKJ01-GLM"])
    out = options.for_exception(conn, e["id"])
    if out["criticality"]:
        pytest.skip("this asset has a criticality on record")
    assert all(o.get("risk") is None for o in out["options"] if o.get("available"))
    assert "criticality: not assessed on its assets" in out["unknown"]
    assert out["recommended"] != "shelve", out["reason"]


def test_a_field_report_naming_a_gas_test_and_permit_holds_the_dispatch(conn, report_type):
    """#1365: after "gas detector in alarm, needs gas test and permit", Options said "Dispatch now… ready"."""
    e = raise_report(conn, "gas alarm", f"Gas smell at the wellhead of {WELLS[1]}", [f"well:{WELLS[1]}"])
    options.choose(conn, e["id"], "riley", "dispatch", to="sam")
    ex.report(conn, e["id"], "sam", "could_not_do", "Gas detector in alarm, needs gas test and permit")
    conn.commit()
    out = options.for_exception(conn, e["id"])
    d = by_key(out)["dispatch"]
    assert d["ready"] is False
    assert d["blockers"][0].startswith("Field: waits on a gas test and a permit, from sam's report")
    assert out["recommended"] != "dispatch", out["reason"]
    # a later report that names nothing lets it go again
    ex.report(conn, e["id"], "sam", "note", "Gas test clear, permit issued")
    conn.commit()
    assert by_key(options.for_exception(conn, e["id"]))["dispatch"]["blockers"][:1] != d["blockers"][:1]


def test_an_operator_on_site_at_the_same_asset_is_the_ride_along_not_a_clash(conn, report_type):
    """#1365: case 1155 recommended a 30-day shelf and flagged a clash with sam, on site at the same transmitter."""
    first = raise_report(conn, "gland", "Loose cable gland on 20-TT-2250B", ["tag:20-TT-2250B"])
    options.choose(conn, first["id"], "riley", "dispatch", to="sam")
    ex.report(conn, first["id"], "sam", "on_site")
    conn.commit()
    e = raise_report(conn, "erratic", "20-TT-2250B reading erratic", ["tag:20-TT-2250B"])
    out = options.for_exception(conn, e["id"])
    o = by_key(out)
    w = o["window"]
    assert w["available"] is True and w["person"] == "sam" and "on site" in w["detail"], w
    assert not any("20-TT-2250B" in c and "at the same time" in c for c in o["dispatch"]["clashes"]), o["dispatch"]
    assert out["recommended"] == "window" and out["reason"].startswith("sam is already there"), out["reason"]
    chosen = options.choose(conn, e["id"], "riley", "window")
    conn.commit()
    held = conn.execute("select assignee from work_item where id = %s", (chosen["event"]["item_id"],)).fetchone()
    assert held["assignee"] == "sam" and chosen["choice"]["accepted"] is True


def message(conn, exception_id: int, text: str) -> None:
    """The message an exception came from, as the message reader keeps it."""
    conn.execute("insert into message (id, channel, sender, text, received_at, outcome, exception_id) "
                 "values (%s, 'radio', 'CRO', %s, now(), 'case', %s)", (f"m-{exception_id}", text, exception_id))
    conn.commit()


def test_a_limit_or_a_leak_with_production_behind_it_is_never_shelved(conn, report_type):
    """#1381: Shelve 30 days was recommended for a pump at 193 °F against a 185 °F limit with a seal weep and 60,000
    bbl/d behind it, and for a falling lift gas header with 177 wells behind it."""
    hot = raise_report(conn, "hot", f"{WELLS[3]} flowline temperature: 193 °F, limits 32 to 185 °F",
                       [f"well:{WELLS[3]}"])
    out = options.for_exception(conn, hot["id"])
    assert out["if_it_trips"]["oil_bbld"] > 0
    s = by_key(out)["shelve"]
    # the reason in words, never a word quoted from the title (tour E, E6: "“limits” with about 210 bbl/d ...")
    assert s["available"] is False and s["unavailable"].startswith("it is past or heading past a limit with about"), s
    assert s["unavailable"].endswith("bbl/d behind it") and "“" not in s["unavailable"]
    assert out["recommended"] != "shelve"
    weep = raise_report(conn, "weep", f"Seal weeping on the {WELLS[3]} flowline pump", [f"well:{WELLS[3]}"])
    assert by_key(options.for_exception(conn, weep["id"]))["shelve"]["unavailable"].startswith("it leaks with about")
    # what the message said counts as the title does
    header = raise_report(conn, "header", "Lift gas on the pad", [f"well:{WELLS[2]}"])
    message(conn, header["id"], "Lift gas header pressure dropping, down to 1150 psig from 1276.")
    assert by_key(options.for_exception(conn, header["id"]))["shelve"]["available"] is False
    # nothing past a limit or leaking: a shelf is still offered
    tidy = raise_report(conn, "sign", f"Faded sign at the wellhead of {WELLS[2]}", [f"well:{WELLS[2]}"])
    assert by_key(options.for_exception(conn, tidy["id"]))["shelve"]["available"] is True


def test_a_gas_test_and_permit_goes_to_the_ops_lead_and_never_back_to_whoever_reads_it(conn, report_type):
    """#1381: Escalate to the engineer was offered for a job waiting on a gas test and a permit, and "Escalate to the
    ops lead, Recommended" was shown to the ops lead."""
    e = raise_report(conn, "gas test", f"Gas smell at the wellhead of {WELLS[1]}", [f"well:{WELLS[1]}"])
    options.choose(conn, e["id"], "riley", "dispatch", to="sam")
    ex.report(conn, e["id"], "sam", "could_not_do", "Gas detector in alarm, needs a gas test and a permit")
    conn.commit()
    out = options.for_exception(conn, e["id"])
    assert by_key(out)["escalate"]["label"] == "Escalate to the ops lead"
    assert out["recommended"] == "escalate" and "the ops lead decides" in out["reason"], out["reason"]
    # read by the ops lead: no escalating to themselves, nothing recommended, and the blocker is theirs
    mine = options.for_exception(conn, e["id"], viewer=["ops_lead"])
    esc = by_key(mine)["escalate"]
    assert esc["available"] is False and esc["unavailable"] == "it would come back to you"
    assert mine["recommended"] is None and mine["reason"].endswith("clear that, then dispatch it."), mine["reason"]
    # read by the engineer, a case with no permit work escalates past the engineer
    other = raise_report(conn, "rattle", f"Rattle at the wellhead of {WELLS[0]}", [f"well:{WELLS[0]}"])
    assert by_key(options.for_exception(conn, other["id"]))["escalate"]["label"] == "Escalate to the engineer"
    assert by_key(options.for_exception(conn, other["id"], viewer=["engineer"]))["escalate"]["label"] == \
        "Escalate to the ops lead"
    # someone holding every role narrows nothing
    every = [r["name"] for r in conn.execute("select name from work_role where kind = 'person'")]
    assert by_key(options.for_exception(conn, e["id"], viewer=every))["escalate"]["available"] is True


def test_a_ride_along_waits_on_what_the_field_said_and_never_rides_a_visit_that_came_back(conn, report_type):
    """#1381: a ride-along was recommended for a job the field said needs depressurising, onto a visit that had itself
    come back Could not do."""
    first = raise_report(conn, "gland 2", "Loose cable gland on 20-TT-2250B", ["tag:20-TT-2250B"])
    options.choose(conn, first["id"], "riley", "dispatch", to="sam")
    ex.report(conn, first["id"], "sam", "on_site")
    conn.commit()
    e = raise_report(conn, "flange", "Flange bolts loose on 20-TT-2250B", ["tag:20-TT-2250B"])
    options.choose(conn, e["id"], "riley", "dispatch", to="sam")
    ex.report(conn, e["id"], "sam", "could_not_do", "Line still live, needs depressurising before the bolts")
    conn.commit()
    w = by_key(options.for_exception(conn, e["id"]))["window"]
    assert w["available"] and w["person"] == "sam"
    assert w["ready"] is False and w["blockers"][0].startswith("Field: waits on depressurising"), w
    # the visit itself comes back: nobody there to ride along with, even after a note
    ex.report(conn, first["id"], "sam", "could_not_do", "No access, scaffold down")
    ex.report(conn, first["id"], "sam", "note", "Will try again tomorrow")
    conn.commit()
    w = by_key(options.for_exception(conn, e["id"]))["window"]
    assert w["available"] is False, w


def test_a_message_that_says_it_is_over_is_not_dispatched(conn, report_type):
    """#1381 (B-N10): "Compression bypass opened during the train A restart, closed again at 09:10" asks for nothing,
    yet got "Dispatch now, Recommended"."""
    from fuzer.ops import messages

    e = raise_report(conn, "bypass", "Compression bypass opened during the train A restart", ["tag:20-TT-2250B"])
    message(conn, e["id"], "Compression bypass opened during the train A restart, closed again at 09:10.")
    out = options.for_exception(conn, e["id"])
    assert out["applies"] is False and out["why_not"].startswith("the message says what happened and that it is over")
    assert not messages.info_only("Bypass closed again at 09:10, can someone check the valve?")
    assert not messages.info_only("Seal leak on 10-P-1001A, cleared the drain but still weeping")


def test_a_ride_along_and_a_dispatch_carry_the_message_as_the_ask(conn, report_type):
    """#1381 (C5): a one-tap ride-along sent "go and have a look"; the field never saw "PT-2001 reading erratic"."""
    first = raise_report(conn, "gland 3", "Loose cable gland on 20-TT-2250B", ["tag:20-TT-2250B"])
    options.choose(conn, first["id"], "riley", "dispatch", to="sam")
    ex.report(conn, first["id"], "sam", "on_site")
    conn.commit()
    e = raise_report(conn, "erratic 2", "20-TT-2250B reading erratic", ["tag:20-TT-2250B"])
    message(conn, e["id"], "20-TT-2250B reading erratic, jumping 73 degF")
    out = options.choose(conn, e["id"], "riley", "window")
    conn.commit()
    sent = conn.execute("select data->>'reason' as r from work_event where item_id = %s and kind in "
                        "('handed_off', 'created') order by id desc limit 1", (out["event"]["item_id"],)).fetchone()["r"]
    assert sent == "While you are there: 20-TT-2250B reading erratic, jumping 73 degF"
    d = raise_report(conn, "erratic 3", "Gauge on OKJ01-GLM reads low", ["allocation_node:OKJ01-GLM"])
    message(conn, d["id"], "Gauge on OKJ01-GLM reads low since the night shift")
    out = options.choose(conn, d["id"], "riley", "dispatch", to="sam")
    conn.commit()
    sent = conn.execute("select data->>'reason' as r from work_event where item_id = %s and kind in "
                        "('handed_off', 'created') order by id desc limit 1", (out["event"]["item_id"],)).fetchone()["r"]
    assert sent == "Gauge on OKJ01-GLM reads low since the night shift"


def _queued(conn, person: str) -> set[int]:
    from fuzer.workflow import assignment

    got = assignment.my_work(conn, person)
    return {r["item_id"] for r in got["mine"] + got["queue"]}


def test_shelve_takes_the_case_off_every_queue_until_its_date(conn, report_type):
    """#1390 (D1): Shelve on case 221 said "Shelved, as recommended", yet the case stayed in My work, overdue, with Shelve
    recommended again; only a comment was written."""
    e = raise_report(conn, "sign 2", f"Faded sign at the wellhead of {WELLS[2]}", [f"well:{WELLS[2]}"])
    options.choose(conn, e["id"], "riley", "escalate")  # in a desk queue of riley's, as 221 was in the ops lead's
    conn.commit()
    item = conn.execute("select item_id from exception_record where id = %s", (e["id"],)).fetchone()["item_id"]
    person = "riley"
    assert item in _queued(conn, person)
    shelf = by_key(options.for_exception(conn, e["id"]))
    assert "shelve" in shelf and shelf["shelve"]["available"], shelf["shelve"]
    options.choose(conn, e["id"], "riley", "shelve", reason="sign only, next turnaround")
    conn.commit()
    assert item not in _queued(conn, person), "a shelved case is off the queue until its date"
    out = options.for_exception(conn, e["id"])
    assert out["applies"] is False and out["why_not"].startswith("it is shelved until"), out["why_not"]
    ex.unshelve(conn, e["id"], "riley")
    conn.commit()
    assert item in _queued(conn, person) and options.for_exception(conn, e["id"])["applies"] is True


def test_a_ride_along_adds_a_field_step_for_the_visits_operator_with_the_message(conn, report_type):
    """#1390 (D2, C5): "Add to the visit" on 1168 moved the due date only: no field step, no ask, nothing on sam's
    visit, and the case stayed with the ops lead."""
    e = raise_report(conn, "erratic 4", "20-TT-2250B reading erratic", ["tag:20-TT-2250B"])
    message(conn, e["id"], "20-TT-2250B reading erratic, jumping 73 degF")
    start = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    plan.propose(conn, "maintenance", "Clean 20-E-2250B", "planner", start, start + timedelta(hours=8),
                 refs=["tag:20-E-2250B"], status="scheduled", crew="sam")
    conn.commit()
    w = by_key(options.for_exception(conn, e["id"]))["window"]
    assert w["available"] and w["operator"] == "sam" and "Clean 20-E-2250B" in w["detail"], w
    out = options.choose(conn, e["id"], "riley", "window")
    conn.commit()
    item = out["event"]["item_id"]
    assert out["event"]["stage"] == "field", out["event"]
    held = conn.execute("select assignee from work_item where id = %s", (item,)).fetchone()["assignee"]
    step = conn.execute("select assignee from work_token where item_id = %s and state = 'waiting' and kind = 'user'",
                        (item,)).fetchone()
    assert "sam" in (held, step and step["assignee"]), (held, step)
    sent = conn.execute("select data->>'reason' as r from work_event where item_id = %s and kind in "
                        "('handed_off', 'created') order by id desc limit 1", (item,)).fetchone()["r"]
    assert sent == "While you are there: 20-TT-2250B reading erratic, jumping 73 degF"
    bar = conn.execute("select start_at, crew from plan_item where exception_id = %s and status = 'scheduled'",
                       (e["id"],)).fetchone()
    assert bar["crew"] == "sam" and bar["start_at"] == start, bar


def _opt(key: str, usd: float, hours: float, risk: str | None, ready: bool = True, **more) -> dict:
    return {"key": key, "available": True, "ready": ready, "hours": hours, "deferred": {"usd": usd, "oil_bbl": 0},
            "risk": {"level": risk, "why": ""} if risk else None, "blockers": [] if ready else ["Spares: none"],
            "clashes": [], "label": {"escalate": "Escalate to the engineer"}.get(key, key), "detail": "x, Wed 25 Nov",
            **more}


def test_options_never_recommend_shelve(conn, report_type):
    """#1390 (D4): Shelve was recommended for a loose coupling guard on a running export pump and a missing Ex cover
    bolt. A person may still choose it; the rule never recommends it."""
    cheap_shelf = [_opt("dispatch", 0, 4, "Low"), _opt("shelve", 0, 700, "Low"), _opt("escalate", 0, 28, "Low")]
    assert options.recommend(cheap_shelf, None, 5000)[0] == "dispatch"
    blocked = [_opt("dispatch", 0, 4, "Low", ready=False), _opt("shelve", 0, 700, "Low"), _opt("escalate", 0, 28, "Low")]
    assert options.recommend(blocked, None, 5000)[0] == "escalate"
    no_basis = [_opt("dispatch", 0, 4, None), _opt("shelve", 0, 700, None), _opt("escalate", 0, 28, None)]
    assert options.recommend(no_basis, None, 5000)[0] == "dispatch"
    # on Okan: a tidy-up with no limit or leak in it still offers a shelf, and never recommends it
    tidy = raise_report(conn, "guard", f"Loose coupling guard at the wellhead of {WELLS[2]}", [f"well:{WELLS[2]}"])
    out = options.for_exception(conn, tidy["id"])
    assert by_key(out)["shelve"]["available"] is True and out["recommended"] != "shelve", out["reason"]


def test_no_risk_word_beside_a_risk_that_is_not_known(conn):
    """#1390 (B-N4): "Risk low · ready" stood beside "The risk of waiting is not known" when no wait was open."""
    key, reason, _ = options.recommend([_opt("dispatch", 0, 4, "Low"), _opt("escalate", 0, 28, "Medium")], None, 5000)
    assert key == "dispatch" and "not known" not in reason, reason
    key, reason, _ = options.recommend([_opt("dispatch", 0, 4, None), _opt("escalate", 0, 28, None)], None, 5000)
    assert key == "dispatch" and "The risk of waiting is not known" in reason, reason


def test_a_question_or_a_bulletin_gets_no_options(conn, report_type):
    """#1390 (B-N10): "Anyone know why train C tripped?" and a vendor's service bulletin got full options with Escalate
    recommended; neither asks for field work."""
    from fuzer.ops import messages

    q = raise_report(conn, "train c", "Anyone know why train C tripped", ["tag:20-TT-2250B"])
    message(conn, q["id"], "Anyone know why train C tripped last night?")
    out = options.for_exception(conn, q["id"])
    assert out["applies"] is False and out["why_not"].startswith("the message asks a question"), out["why_not"]
    b = raise_report(conn, "bulletin", "Service bulletin for the fuel nozzles", ["tag:20-TT-2250B"])
    message(conn, b["id"], "Service bulletin attached for the Taurus 60 fuel nozzles. It applies to all three drivers.")
    out = options.for_exception(conn, b["id"])
    assert out["applies"] is False and out["why_not"].startswith("the message passes on a notice"), out["why_not"]
    # a question that asks for work, or a bulletin with something wrong, still gets them
    assert not messages.info_only("Anyone know why 20-K-2300A tripped? Can someone check the discharge temp?")
    assert not messages.info_only("Service bulletin: fuel nozzles on train B leaking, please check")


def test_a_look_is_not_held_on_a_maintenance_crew_it_does_not_use(conn, report_type):
    """Tour E, E2: Dispatch now (to a field operator) read "not ready: Crew: Mechanical maintenance crew (MECH): 8 of 6
    booked today" on simple looks (1203, 1233, 1215, 1216), so Escalate was recommended. A look is the field
    operator's: the fitters' crew is not checked for it, and sending one books none of it."""
    from fuzer.ops import readiness, schedule

    e = raise_report(conn, "seal vent", "20-K-2300B dry gas seal vent past its alarm", ["tag:20-K-2300B"])
    subjects = list(conn.execute("select subjects from exception_record where id = %s", (e["id"],)).fetchone()["subjects"])
    centers = [c["label"] for c in readiness.crew(conn, subjects)["centers"]]
    assert centers, "the compressor's work orders name a work center"
    schedule.set_resource(conn, "FULL", "planner", name="Fitters", kind="crew", capacity=1, work_center=centers[0])
    start = datetime.now(UTC) - timedelta(hours=1)
    job = plan.propose(conn, "maintenance", "Seal change elsewhere", "planner", start, start + timedelta(hours=8),
                       status="scheduled")
    plan.book(conn, job["id"], "planner", "FULL", 1)
    conn.commit()
    try:
        assert not readiness.crew(conn, subjects, e["id"])["ready"]  # the crew is full today
        out = options.for_exception(conn, e["id"])
        d = by_key(out)["dispatch"]
        assert not any(b.startswith("Crew") for b in d["blockers"]), d["blockers"]
        assert "Crew" not in out["reason"] and out["recommended"] != "escalate", out["reason"]
        options.choose(conn, e["id"], "riley", "dispatch")
        conn.commit()
        bar = conn.execute("select id from plan_item where exception_id = %s", (e["id"],)).fetchone()
        assert conn.execute("select 1 from plan_demand where item_id = %s", (bar["id"],)).fetchone() is None
    finally:
        conn.rollback()
        conn.execute("delete from plan_resource where code = 'FULL'")
        conn.commit()


def test_a_message_about_a_tag_the_field_has_a_case_on_links_it_and_is_no_clash(conn, report_type):
    """Tour E, E14: a message repeating a fault already with the field (1204 on 25-XV-4021) showed a simultaneous
    operations clash and recommended Escalate. The field is already on it: Options says so and links that case."""
    first = raise_report(conn, "xv flicker", "25-XV-4021 position indicator flickers", ["tag:25-XV-4021"])
    options.choose(conn, first["id"], "riley", "dispatch")  # to the operator queue: nobody on site yet
    conn.commit()
    again = raise_report(conn, "xv flicker 2", "25-XV-4021 position indicator flickers between open and closed",
                         ["tag:25-XV-4021"])
    message(conn, again["id"], "25-XV-4021 position indicator flickers between open and closed.")
    out = options.for_exception(conn, again["id"])
    held = conn.execute("select item_id from exception_record where id = %s", (first["id"],)).fetchone()["item_id"]
    assert out["field_case"] == {"exception": first["id"], "item_id": held,
                                 "title": "25-XV-4021 position indicator flickers"}, out["field_case"]
    d = by_key(out)["dispatch"]
    assert d["clashes"] == [] and d["ready"] is True, d
    assert out["recommended"] != "escalate", out["reason"]


def test_a_case_never_clashes_with_itself_or_the_visit_it_rides_along_with(conn, report_type):
    """Tour E, D16: 1168's ride along read "clash Sam is also booked on “30-V-2001 level gauge glass is fogged inside”"
    (its own bar), and on Plan the visit it rode along with showed as its clash, twice."""
    e = raise_report(conn, "fogged", "20-TT-2250B local display fogged inside", ["tag:20-TT-2250B"])
    start = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    v = plan.propose(conn, "maintenance", "Clean 20-E-2250B", "planner", start, start + timedelta(hours=8),
                     refs=["tag:20-E-2250B"], status="scheduled", crew="sam")
    plan.plan_exception(conn, e["id"], "planner", start, start + timedelta(hours=4), crew="sam")  # its own bar, sam's
    conn.commit()
    w = by_key(options.for_exception(conn, e["id"]))["window"]
    assert w["available"] and w["plan_item"] == v["id"], w
    assert not any("fogged" in c for c in w["clashes"]), w["clashes"]
    options.choose(conn, e["id"], "riley", "window")
    conn.commit()
    bar = conn.execute("select * from plan_item where exception_id = %s and status = 'scheduled'", (e["id"],)).fetchone()
    assert bar["rides_with"] == v["id"]
    rows = {i["id"]: i for i in plan.items(conn, start - timedelta(days=1), start + timedelta(days=1))}
    assert not [c for c in rows[bar["id"]]["clashes"] if c["with"] == v["id"]], rows[bar["id"]]["clashes"]
    assert not [c for c in rows[v["id"]]["clashes"] if c["with"] == bar["id"]], rows[v["id"]]["clashes"]
    clash = next(p for p in plan.prerequisites(conn, bar["id"])["prerequisites"] if p["kind"] == "clash")
    assert clash["satisfied"], clash


def _api(conn):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from fuzer.api import ops as ops_api
    from fuzer.api import workflow as workflow_api

    app = FastAPI()
    app.include_router(ops_api.router)
    app.include_router(workflow_api.router)
    app.dependency_overrides[ops_api.get_conn] = lambda: conn
    return TestClient(app)


def _waiting_on(item: dict) -> list[tuple]:
    return [(s["name"], s["role"], s["assignee"]) for p in item["processes"] for s in p["open"] if s["kind"] == "user"]


def test_an_action_answers_with_the_case_as_it_left_it(conn, report_type):
    """#1403 (B-N15): after Choose who, Send to someone else and Dispatch the case still said "With ops lead queue"
    until a reload. The action's answer carries the case, which the page shows with the confirmation."""
    from fuzer.core import exceptions as port

    e = port.raise_exception(conn, "walkdown_check", "answer 1", "Loose guard on 20-TT-2250B", "olga",
                             refs=["tag:20-TT-2250B"], priority=0.3, role="role:ops_lead")
    conn.commit()
    client = _api(conn)
    before = client.get(f"/work/items/{e['item_id']}").json()
    assert [(r, a) for _, r, a in _waiting_on(before)] == [("ops_lead", None)], _waiting_on(before)
    r = client.post(f"/exceptions/{e['id']}/options/choose", json={"by": "riley", "option": "dispatch", "to": "sam"})
    assert r.status_code == 200, r.text
    now = r.json()["item"]
    assert now["id"] == e["item_id"] and _waiting_on(now) == _waiting_on(client.get(f"/work/items/{e['item_id']}").json())
    assert [a for _, _, a in _waiting_on(now)] == ["sam"], _waiting_on(now)


def test_send_to_someone_else_sends_a_case_waiting_at_to_dispatch(conn, report_type):
    """#1403 (E5): Send to someone else on message case 1203 set the case's queue only. It stayed at To dispatch with
    "No step is open" and its options, and nothing said who had it. It goes out as the event's dispatch now: a step in
    the planner's queue, off To dispatch."""
    from fuzer.core import work
    from fuzer.ops import routing

    work.add_person(conn, "pat", ["maintenance_planner"], "test")
    e = raise_report(conn, "puddle", "20-TT-2250B lube oil console has a small oil puddle", ["tag:20-TT-2250B"])
    message(conn, e["id"], "Small oil puddle under the filter of the lube oil console by 20-TT-2250B")
    item = conn.execute("select item_id from exception_record where id = %s", (e["id"],)).fetchone()["item_id"]
    assert routing.waiting_case(conn, item) and options.for_case(conn, item)["stage"] == "dispatch"
    conn.commit()
    r = _api(conn).post(f"/work/items/{item}/reassign", json={"by": "riley", "to": "role:maintenance_planner",
                                                             "reason": "planner to book it"})
    assert r.status_code == 200, r.text
    assert _waiting_on(r.json()["item"]) == [("Work the case", "maintenance_planner", None)], r.json()["item"]
    assert not routing.waiting_case(conn, item)
    assert options.for_case(conn, item)["stage"] == "desk"
    sent = conn.execute("select data->>'reason' as r from work_event where item_id = %s and kind = 'handed_off' "
                        "order by id desc limit 1", (item,)).fetchone()["r"]
    assert sent == "planner to book it"
    # a case already sent is handed on as before
    r = _api(conn).post(f"/work/items/{item}/reassign", json={"by": "riley", "to": "pat"})
    assert r.status_code == 200 and [a for _, _, a in _waiting_on(r.json()["item"])] == ["pat"], r.text
