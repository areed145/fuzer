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


def test_no_visit_on_the_plan_says_so_and_desk_work_has_no_options(conn):
    e = trip(conn)
    out = options.for_exception(conn, e["id"])
    w = by_key(out)["window"]
    assert w["available"] is False and "nothing scheduled" in w["unavailable"]
    desk = conn.execute("select id from exception_record where type = 'criticality_not_assessed' "
                        "and state = 'open' limit 1").fetchone()
    if desk:
        assert options.for_exception(conn, desk["id"])["applies"] is False


def test_choosing_dispatch_sends_it_with_why_now_and_counts_the_recommendation(conn):
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
    assert sent and sent.startswith("Restart checks") and "Why now:" not in sent  # no reason given: the ask alone
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
