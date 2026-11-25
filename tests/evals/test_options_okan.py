"""P4.8 (#1260): options weighed the same way on every case, scored over a set of Okan cases with no model call. A
trip that defers production, a report with a visit to ride along with, a gas smell and a gauge with no criticality on
record each get options with the same fields, one recommended with the factor that decided it, and what is not known
named. Two specialists' recorded answers on one case either recommend the same option or say why not."""

from datetime import UTC, datetime, timedelta

import pytest

from fuzer.evals import options_eval
from fuzer.ops import exceptions as ex
from fuzer.ops import options, plan

WELLS = ["OK-J01-01", "OK-J01-02", "OK-J01-03", "OK-J01-04"]


@pytest.fixture(scope="module")
def conn(okan_db):
    from fuzer.analysis import trips  # noqa: F401  declares machine_trip
    from fuzer.core import work
    from fuzer.core.exceptions import TYPES, ExceptionType
    from fuzer.workflow import engine

    c = okan_db.conn
    engine.load_defaults(c)
    work.add_person(c, "sam", ["operator"], "test")
    work.add_person(c, "riley", ["ops_lead", "engineer"], "test")
    c.commit()
    TYPES["walkdown_check"] = ExceptionType("walkdown_check", "Walkdown check", audience=("ioc", "field"), joins=False)
    yield c
    TYPES.pop("walkdown_check", None)
    c.rollback()
    c.execute("update exception_record set state = 'closed' where state = 'open' "
              "and type in ('machine_trip', 'walkdown_check')")
    c.execute("update plan_item set status = 'cancelled' where status in ('proposed', 'scheduled', 'in_progress')")
    c.commit()


@pytest.fixture(scope="module")
def cases(conn) -> dict[str, int]:
    from fuzer.core import exceptions as port

    def report(key: str, title: str, refs: list[str]) -> int:
        return port.raise_exception(conn, "walkdown_check", key, title, "olga", refs=refs, priority=0.3)["id"]

    trip = ex.raise_(conn, "machine_trip", "K-2300B trip", "20-K-2300B tripped: four lifted wells on OKJ-01 down",
                     "agent:sre", refs=[f"well:{w}" for w in WELLS], priority=0.9, role=None)["id"]
    out = {"trip": trip,
           "gauge": report("glm gauge", "Gauge on OKJ01-GLM reads low", ["allocation_node:OKJ01-GLM"]),
           "gas": report("gas smell", f"Gas smell at the wellhead of {WELLS[2]}", [f"well:{WELLS[2]}"]),
           "nowhere": report("drip", "Drip under a flange somewhere on the pad", [])}
    start = datetime.now(UTC) + timedelta(days=3)
    plan.propose(conn, "maintenance", "OKJ-01 well work", "planner", start, start + timedelta(hours=8),
                 refs=[f"well:{WELLS[0]}", "allocation_node:OKJ01-GLM"], status="scheduled", crew="role:operator")
    conn.commit()
    return out


def test_every_case_gets_options_with_the_same_fields_and_a_reasoned_recommendation(conn, cases):
    """Done when: on a set of Okan cases, options with the same fields and a reasoned recommendation, scored."""
    res = options_eval.run(conn, list(cases.values()))
    assert res["cases"] == len(cases), res["skipped"]
    assert options_eval.gate(res) == [], res["by_case"]
    assert {c["deciding"] for c in res["by_case"]} <= set(options.FACTORS)
    by = {c["exception_id"]: c for c in res["by_case"]}
    assert by[cases["trip"]]["recommended"] == "dispatch" and by[cases["trip"]]["deciding"] == "deferred"
    # a case read on another scale fails the gate
    worse = {**res, "same_fields": 0.75}
    assert options_eval.gate(worse) == ["same_fields 0.75 < 1.0"]


def test_unknown_inputs_are_named_not_guessed(conn, cases):
    """Done when: unknown inputs are named, not guessed."""
    gauge = options.for_exception(conn, cases["gauge"])
    if gauge["criticality"] is None:  # no criticality on record: no risk word, and the gap named
        assert "criticality: not assessed on its assets" in gauge["unknown"]
        assert all(o["risk"] is None for o in gauge["options"] if o.get("available"))
    nowhere = options.for_exception(conn, cases["nowhere"])
    assert nowhere["loss"] is None and any(u.startswith("what it defers") for u in nowhere["unknown"])
    assert all(o["deferred"] is None for o in nowhere["options"] if o.get("available"))
    assert len(nowhere["undetermined"]) >= len(nowhere["unknown"])  # each kept for what would have answered it (P6.6)
    assert options_eval.case(nowhere)["unknowns_named"]
    # an answer that leaves an unknown out is caught
    assert options_eval.answer("Dispatch now: nothing blocks it.", nowhere)["unnamed"]


# Recorded answers from two specialists on the trip, given the same case_options output (no model call).
MAINTENANCE = ("Dispatch now. Waiting for the visit on OKJ-01 defers more production than a separate trip costs; "
               "nothing blocks it. Criticality: not assessed on its assets, so the risk of waiting is not known.")
WORK = ("Ride along with the next visit, instead of Dispatch now: the crew on the OKJ-01 well work is already "
        "permitted for the pad, and work order 4000123 holds the parts. Criticality is not assessed on its assets.")
GUESS = "Shelve for 30 days: the risk is low."


def test_two_specialists_on_one_case_recommend_the_same_option_or_say_why_not(conn, cases):
    """Done when: two specialists given the same case recommend the same option, or say why not. Both read the one
    tool; their recorded answers are read against its output."""
    from fuzer.agents.capabilities import tools_by_agent

    tools = tools_by_agent()
    assert all("case_options" in tools[a] for a in ("maintenance-integrity", "work-management-supply-chain",
                                                    "orchestrator"))
    offered = options.for_exception(conn, cases["trip"])
    a, b = options_eval.answer(MAINTENANCE, offered), options_eval.answer(WORK, offered)
    assert a["same"] and a["chose"] == "dispatch", a
    assert not b["same"] and b["says_why"] and "readiness" in b["factors"], b
    assert a["unknowns_named"] and b["unknowns_named"], (offered["unknown"], a, b)
    bad = options_eval.answer(GUESS, offered)
    assert not bad["agrees_or_says_why"]


def test_the_acceptance_rate_of_the_shared_skill_shows_where_its_shortfalls_do(conn, cases):
    """Done when: acceptance rate is visible per skill. A choice other than the one recommended is a shortfall of the
    shared skill, and the share of recommendations taken shows on the skills page beside it."""
    from fuzer.agents import shortfalls, skill_book
    from fuzer.core import exceptions as port

    def counted() -> dict:
        return next(r for r in skill_book.listing(conn)["skills"] if r["skill"] == shortfalls.OPTIONS)

    before = counted()["taken"] or {"decided": 0, "accepted": 0}
    # on assets none of the other cases is on, so a dispatch here is no clash for them
    one = port.raise_exception(conn, "walkdown_check", "weep a", "Weeping fitting at 20-TT-2250B", "olga",
                               refs=["tag:20-TT-2250B"], priority=0.3)["id"]
    two = port.raise_exception(conn, "walkdown_check", "weep b", "Weeping flange at 20-E-2250B", "olga",
                               refs=["tag:20-E-2250B"], priority=0.3)["id"]
    conn.commit()
    options.choose(conn, one, "riley", options.for_exception(conn, one)["recommended"])
    other = next(k for k in ("escalate", "dispatch") if k != options.for_exception(conn, two)["recommended"])
    options.choose(conn, two, "riley", other, reason="the pad is shut in for a tie-in")
    conn.commit()
    row = counted()
    assert row["taken"]["decided"] == before["decided"] + 2 and row["taken"]["accepted"] == before["accepted"] + 1
    assert row["shortfalls"] >= 1
    d = skill_book.detail(conn, shortfalls.OPTIONS)
    assert d["taken"] == row["taken"]
    fell = next(x for x in d["shortfalls"] if x["kind"] == "option" and x["note"] == "the pad is shut in for a tie-in")
    assert fell["chosen"] == other and fell["case"]
