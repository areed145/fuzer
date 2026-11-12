"""P4.3: the owning specialist's brief on a case, or the reason it has none."""

import os
import tempfile

import pytest

from fuzer.agents import brief
from fuzer.core import db, detectors, settings

BRIEFED = """The compressor is near surge.

```json
{"what_happened": "Stage 1 ran within 4 % of its surge line for most of the week.",
 "evidence": [{"ref": "tag:K-1", "says": "suction flow fell 12 %"}],
 "analysis": [{"method": "surge", "says": "5th percentile margin 3.8 %, near_surge"}],
 "similar": [{"case": "fz-incident-1", "fixed_by": "anti-surge valve recalibrated"}],
 "proposal": {"disposition": "plan an anti-surge valve check", "confidence": 1.4, "why": "margin under 5 %"},
 "could_not_check": ["no OEM map revision date"]}
```"""


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        yield c


def _case(conn, type_: str, subject: str) -> int:
    with conn.transaction():
        return conn.execute("insert into work_item (type, title, subjects, created_by) values (%s, %s, %s, 'test') "
                            "returning id", (type_, f"{type_} on {subject}", [subject])).fetchone()["id"]


@pytest.fixture
def classes(monkeypatch):
    known = {"unit-co": "CO", "unit-pu": "PU"}
    monkeypatch.setattr(detectors, "classes_of", lambda conn, units: {u: known[u] for u in units if u in known})


def test_the_owning_pack_briefs_a_new_case_with_its_ledger_and_cost(conn, classes):
    item = _case(conn, "machine_health", "unit-co")
    assert item in brief.pending(conn)
    asked = []

    def ask(q):
        asked.append(q)
        return {"answer": BRIEFED, "usage": {"cost_usd": 0.31, "models": {"m": {}}, "model_calls": 2},
                "specialists": ["maintenance-integrity"], "tools": [{"agent": "maintenance-integrity", "tool": "calc",
                                                                     "args": {"check": "surge"}}]}

    out = brief.prepare(conn, item, ask)
    assert "`compression` skill" in asked[0] and "could_not_check" in asked[0]
    assert out["pack"] == "maintenance-integrity/compression" and not out["over_budget"]
    b = out["brief"]
    assert b["proposal"]["confidence"] == 1.0  # clamped: a confidence is a fraction
    assert b["similar"][0]["fixed_by"] and b["could_not_check"] == ["no OEM map revision date"]
    assert out["ledger"]["cost_usd"] == 0.31 and "maintenance-integrity/compression" in out["ledger"]["skills"]
    assert item not in brief.pending(conn)
    assert brief.prepare(conn, item, lambda q: pytest.fail("asked twice")) == brief.latest(conn, item)
    from fuzer.agents import ledger
    assert ledger.case_spend(conn, item) == 0.31  # the brief counts toward the case's agent spend


def test_a_missing_brief_says_why(conn, classes):
    pump = _case(conn, "machine_health", "unit-pu")  # no shipped pack covers pumps yet
    assert "no specialist pack briefs a machine_health case" in brief.prepare(conn, pump, ask=None)["missing"]

    down = _case(conn, "machine_trip", "unit-co")

    def unreachable(q):
        raise RuntimeError("no API key")
    assert "could not be reached: no API key" in brief.prepare(conn, down, unreachable)["missing"]

    capped = _case(conn, "machine_health", "unit-co")
    settings.set(conn, "agents.case_cap_usd", 0.2, by="test")
    try:
        with conn.transaction():
            conn.execute("insert into work_event (item_id, kind, actor, element, data) values (%s, 'agent_result', "
                         "'agent:x', 's', %s)", (capped, '{"data": {"s": {"ledger": {"cost_usd": 0.25}}}}'))
        assert "reached its cap: $0.25 of $0.20" in brief.prepare(conn, capped, lambda q: pytest.fail("ran"))["missing"]
    finally:
        settings.reset(conn, "agents.case_cap_usd", by="test")


def test_a_brief_over_its_budget_says_so_and_types_no_pack_briefs_are_left_alone(conn, classes):
    item = _case(conn, "machine_health", "unit-co")
    out = brief.prepare(conn, item, lambda q: {"answer": "no json here", "usage": {"cost_usd": 0.9}})
    assert out["over_budget"] and out["budget_usd"] == 0.5
    assert "the brief has no proposal" in out["brief"]["could_not_check"]
    other = _case(conn, "source_issue", "unit-co")
    assert other not in brief.pending(conn)


def test_a_brief_is_due_within_the_cadence_of_what_raises_its_type():
    from datetime import timedelta
    assert brief.tier("machine_health") == timedelta(days=1)  # the daily machine health screen
    assert brief.tier("no_such_type") == brief.DEFAULT_TIER
