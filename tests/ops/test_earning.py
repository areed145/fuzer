"""How detection and agents are performing (P7.4, #955): per detector, its cases and close-outs, hours from what it
found to the case, the value it confirmed and how its confirmed cases were found (P7.4a)."""

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from fuzer.core import db, work
from fuzer.core.pack import load_pack
from fuzer.ops import disposition, earning, tuning

PACK = load_pack("okan")
NOW = datetime.now(UTC)


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


def _case(conn, key: str, finding: str, closed_as: str | None = None, *, origin: str | None = "deterministic",
          hours_before: float = 2, value: float | None = None) -> int:
    item = work.open_case(conn, "manual", f"{finding} ({key})", by="fuzer")["id"]
    rec = {"detector": finding, "start": (NOW - timedelta(hours=hours_before)).isoformat()}
    if origin:
        rec["origin"] = origin
    conn.execute("insert into exception_record (type, key, title, evidence, item_id, value_usd) values "
                 "('upset', %s, %s, %s, %s, %s)", (key, key, Jsonb({"finding": rec}), item, value))
    if closed_as:
        disposition.record(conn, PACK, item, "ana", closed_as,
                           **({"duplicate_of": item - 1} if closed_as == "duplicate" else {}),
                           **({"failure_mode": "VIB", "cause": "3", "activity": "1"} if closed_as == "real" else {}))
    return item


@pytest.mark.integration
def test_each_detector_shows_whether_it_earns_its_keep(conn):
    try:
        for i, (origin, value) in enumerate([("deterministic", 120000.0), ("deterministic", 30000.0),
                                             ("deterministic", None), ("deterministic", None), ("agentic", None)]):
            _case(conn, f"s{i}", "sre.steps", "real", origin=origin, value=value, hours_before=1 + i)
        _case(conn, "s5", "sre.steps", "false_alarm")
        for i, how in enumerate(["false_alarm", "false_alarm", "false_alarm", "duplicate", "duplicate", "real"]):
            _case(conn, f"d{i}", "sre.drifts", how, origin=None if how == "real" else "deterministic")
        _case(conn, "x0", "sre.excursions", "real", origin="external")
        _case(conn, "x1", "sre.excursions")  # still open
        tune = work.open_case(conn, "manual", "Tune drifts", by="fuzer")["id"]  # a tuning proposal for drifts (P6.3b)
        conn.execute("insert into exception_record (type, key, title, evidence, item_id) values (%s, 'drifts', 'x', "
                     "%s, %s)", (tuning.TUNING, Jsonb({"detector": "drifts"}), tune))
        conn.commit()

        got = earning.detectors(conn)
        by = {r["detector"]: r for r in got["detectors"]}
        steps, drifts, exc = by["steps"], by["drifts"], by["excursions"]
        assert [r["detector"] for r in got["detectors"]] == ["drifts", "steps", "excursions"]  # the most cases first
        assert (steps["cases"], steps["closed"], steps["real"], steps["verdict"]) == (6, 6, 5, "earning")
        assert steps["value_confirmed_usd"] == 150000.0 and steps["valued"] == 2  # the false alarm's value isn't counted
        assert steps["by_origin"] == {"deterministic": 4, "agentic": 1, "external": 0, "unknown": 0}
        assert 2.5 <= steps["hours_to_case"] <= 3.6, steps["hours_to_case"]  # the median of 1 to 5 hours, and the 2
        assert steps["title"] and steps["kind"] == "statistical" and len(steps["item_ids"]) == 6
        assert steps["tuning_case"] is None

        assert (drifts["noise"], drifts["closed"], drifts["verdict"]) == (5, 6, "needs_tuning")
        assert drifts["tuning_case"] == tune
        assert drifts["by_origin"]["unknown"] == 1 and drifts["value_confirmed_usd"] is None
        assert (exc["cases"], exc["closed"], exc["verdict"]) == (2, 1, "too_few")

        assert got["confirmed"] == 7
        assert got["by_origin"]["deterministic"] == {"cases": 4, "share": round(4 / 7, 3)}
        assert got["by_origin"]["agentic"]["cases"] == got["by_origin"]["external"]["cases"] == 1
    finally:
        conn.rollback()


def _brief(conn, pack: str, usd: float, followed: str | None) -> int:
    item = work.open_case(conn, "manual", f"briefed by {pack}", by="fuzer")["id"]
    entry = {"pack": pack, "brief": {"proposal": {"disposition": "real"}}, "ledger": {"agent": "x", "cost_usd": usd}}
    conn.execute("insert into work_event (item_id, kind, actor, element, data) values (%s, 'agent_result', 'agent:x', "
                 "'brief', %s)", (item, Jsonb({"data": {"brief": entry}, "step": "brief"})))
    if followed:
        conn.execute("insert into work_event (item_id, kind, actor, element, data) values (%s, 'brief_verdict', 'ana', "
                     "'brief', %s)", (item, Jsonb({"pack": pack, "followed": followed, "disposition": "real"})))
    return item


@pytest.mark.integration
def test_each_skill_shows_its_briefs_how_far_they_were_followed_its_ratings_and_edits(conn):
    from fuzer.agents import earning as agent_earning
    from fuzer.ops import skill_review

    comp, pumps = "maintenance-integrity/compression", "maintenance-integrity/pumps-and-drivers"
    try:
        for i, how in enumerate(["no", "no", "partly", "yes", None]):
            _brief(conn, comp, 0.2 + i * 0.1, how)
        for how in ["yes", "yes", "yes", "yes", "partly"]:
            _brief(conn, pumps, 0.1, how)
        for i, useful in enumerate([False, False, True]):  # answers the integrity specialist worked on
            conn.execute("insert into assistant_answer (id, conversation_id, question, specialists) values "
                         "(%s, 'c', 'why?', '{maintenance-integrity}')", (f"a{i}",))
            conn.execute("insert into feedback (target, useful) values (%s, %s)", (f"answer:a{i}", useful))
        conn.execute("insert into skill_version (skill, version, playbook, status, created_by, published_by, "
                     "published_at) values (%s, 1, 'x', 'published', 'ana', 'ana', now()), "
                     "(%s, 2, 'y', 'draft', 'agent:maintenance-integrity', null, null)", (comp, comp))
        review = skill_review.open_review(conn, comp, 2, "Compression: briefs not followed", "agent:maintenance-integrity")
        conn.commit()

        got = agent_earning.skills(conn)
        by = {r["skill"]: r for r in got["skills"]}
        c, p = by[comp], by[pumps]
        assert (c["briefs"], c["brief_cases"], c["owner"]) == (5, 5, "maintenance-integrity")
        assert c["brief_usd"] == 2.0 and c["brief_cost_per_case"] == 0.4
        assert c["followed"] == {"yes": 1, "partly": 1, "no": 2} and c["followed_rate"] == 0.25
        assert c["rated"] == {"useful": 1, "not_useful": 2}  # every integrity skill shares the specialist's answers
        assert (c["verdicts"], c["short"], c["verdict"]) == (7, 5, "needs_change")
        assert c["edits"] == {"published": 1, "live_version": 1, "live_since": c["edits"]["live_since"], "waiting": 2,
                              "review_case": review["item_id"]}
        assert (p["verdicts"], p["short"], p["verdict"]) == (8, 3, "mixed")
        assert by["wells-subsurface/well-review"]["verdict"] == "too_few"
        assert [r["skill"] for r in got["skills"][:2]] == [pumps, comp]  # the most briefs and verdicts first
        assert all(not s.startswith("shared/") for s in by)  # nothing counts against a shared skill
    finally:
        conn.rollback()
