"""Three proposed windows for an outage on Harrier (P5.22c, #1253), approved through a plan change that drafts the
crews' and the customers' notices, from its bundle alone. One story: ask, pick, approve, send, revert."""

from itertools import pairwise

import pytest

from fuzer.core import work
from fuzer.core.plan import PlanError
from fuzer.ops import outages, pipeline, replan

pytestmark = pytest.mark.sequence
PIG = "HR-PIG-2027"


@pytest.fixture(scope="module")
def c(harrier_db):
    pipeline.sync_plan(harrier_db)
    work.add_person(harrier_db, "pat", ["maintenance_planner"], "test")
    harrier_db.commit()
    return harrier_db


@pytest.fixture(scope="module")
def asked(c):
    pc = replan.outage_windows(c, PIG, "pat")
    c.commit()
    return pc


def test_an_outage_gets_three_proposed_windows_with_reasons_and_dollar_impact(c, asked):
    p = asked["proposal"]
    assert asked["open"] and asked["say"].startswith("Harrier mainline outage")
    assert len(p["windows"]) == 3 and p["recommended"] == 0 and p["picked"] == 0
    first, *rest = p["windows"]
    # the cheapest window takes the line down with the origin's relief valve test, so both shippers lose those
    # hours once and Prairie Basin is no longer hit twice
    assert first["alongside"] == ["Relief valve bench test"] and first["usd"] < min(w["usd"] for w in rest)
    assert "Prairie Basin Producers" not in {x["customer"] for x in first["customers"] if x["flagged"]}
    assert any(r.startswith("shares its hours with Relief valve bench test") for r in first["reasons"])
    assert p["recommend"].startswith("Costs the customers least ($")
    # each window costs what its contracts lose, and none overlaps another or puts a crew over capacity
    for w in p["windows"]:
        assert w["usd"] > 0 and w["volume_bbl"] > 0 and w["hits"]
        assert "crews within capacity, no rule broken" in w["reasons"]
    spans = sorted((w["start"], w["end"]) for w in p["windows"])
    assert all(a[1] <= b[0] for a, b in pairwise(spans))
    assert rest[0]["reasons"][0] == "as planned"  # among equals, the one nearest the plan
    # the duration read against similar past work: the last planned line shutdown
    assert p["past"] and p["past"][0]["key"] == "HRDT-0001" and p["past"][0]["hours"] == 36
    # Fuzer worked it out, a person asked: who proposed each is on its history
    assert [h["by"] for h in asked["history"]] == ["pat", "agent:planner"]
    assert p["moves"] and all(m["project"] == PIG for m in p["moves"])


def test_another_window_is_picked_only_with_a_reason(c, asked):
    eid = asked["exception_id"]
    with pytest.raises(PlanError, match="say why window 2"):
        replan.pick_window(c, eid, 1, "pat")
    c.rollback()
    got = replan.pick_window(c, eid, 1, "pat", "the ILI contractor is booked elsewhere that week")
    assert got["proposal"]["picked"] == 1
    assert any(h["by"] == "pat" and h["did"].startswith("Picked window 2: the ILI") for h in got["history"])
    got = replan.pick_window(c, eid, 0, "pat")  # back to the recommendation, no reason needed
    c.commit()
    assert got["proposal"]["picked"] == 0


def test_approving_a_window_moves_the_outage_and_drafts_the_crews_and_customers_notices(c, asked):
    eid = asked["exception_id"]
    window = asked["proposal"]["windows"][0]
    got = replan.approve(c, eid, "pat")
    c.commit()
    assert got["state"] == "approved"
    start = c.execute("select start_at from plan_project where code = %s", (PIG,)).fetchone()["start_at"]
    assert start.isoformat() == window["start"]
    row = c.execute("select min(start_at) as a from plan_item where project = %s", (PIG,)).fetchone()
    assert row["a"].isoformat() == window["start"]
    customers = {n["name"]: n for n in got["notices"] if n["audience"] == "customer"}
    assert set(customers) == {"Northgate Crude Marketing", "Prairie Basin Producers"}
    assert "HR-TSA-102 (transportation) at A1-FT-1010" in customers["Prairie Basin Producers"]["text"]
    assert {n["to"] for n in got["notices"] if n["audience"] == "crew"} >= {"MECH", "ILI", "OPS"}
    assert all(n["sent_at"] is None for n in got["notices"])  # drafts, for a person to send
    assert got["history"][-1]["did"].startswith("Approved window 1 as recommended: 14 jobs moved")
    choice = c.execute("select chosen, recommended, accepted, by from option_choice where exception_id = %s",
                       (eid,)).fetchone()
    assert dict(choice) == {"chosen": "window 1", "recommended": "window 1", "accepted": True, "by": "pat"}
    # Prairie Basin is now hit once: the mainline outage shares its hours with the relief valve test
    prairie = next(x for x in outages.outages(c)["by_customer"] if x["customer"] == "Prairie Basin Producers")
    assert prairie["times"] == 1 and not prairie["flagged"]
    sent = replan.send_notice(c, eid, customers["Prairie Basin Producers"]["n"], "pat")
    c.commit()
    assert sent["notices"][customers["Prairie Basin Producers"]["n"]]["sent_by"] == "pat"


def test_revert_puts_the_outage_back_where_it_was_planned(c, asked):
    eid = asked["exception_id"]
    got = replan.revert(c, eid, "pat")
    c.commit()
    assert got["state"] == "reverted"
    start = c.execute("select min(start_at) as a from plan_item where project = %s", (PIG,)).fetchone()["a"]
    assert start.isoformat() == asked["proposal"]["outage"]["start"]
