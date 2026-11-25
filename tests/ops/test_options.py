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
