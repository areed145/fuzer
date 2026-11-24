"""A report's AI note: written once per report, scope, window and day from that report's own panel results, checked like
the daily report, read by everyone, and never written by a read."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.agents import report_summary as rs
from fuzer.api import commentary

DAY = date(2026, 11, 24)


class FakeNote:
    def __init__(self, out=None):
        self.requests, self.out = [], out
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        facts = json.loads(kw["messages"][0]["content"])
        first = facts["panels"][0]
        out = self.out or {"headline": f"{facts['title']}: the week in figures",
                           "points": [{"text": f"{first['title']} is on the page.", "cites": [first["id"]]}]}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(out))],
                               usage=SimpleNamespace(input_tokens=900, output_tokens=60, cache_read_input_tokens=0))


FACTS = {"report": "work", "title": "Work and dispatch", "window": "7 days to 24 Nov 2026", "at": "2026-11-24",
         "scope": {"id": "fz-area-000000000001", "label": "OKAN-20", "kind": "area"},
         "panels": [{"id": "backlog", "type": "stat", "measures": ["work.backlog"],
                     "result": {"value": 42, "unit": "cases", "previous": 37.5, "refs": ["case:12", "fz-tag-0123"]}}]}


def test_the_checks_flag_numbers_and_citations_not_in_the_facts():
    good = {"headline": "Backlog 42 cases over 7 days", "points": [
        {"text": "Up from 37.5 cases the week before.", "cites": ["work.backlog", "case:12"]}]}
    assert rs.check(good, FACTS)["ok"]
    bad = {"headline": "Backlog rose 12%", "points": [
        {"text": "Mostly on one pump.", "cites": []},
        {"text": "See the queue.", "cites": ["case:99", "backlog"]}]}
    c = rs.check(bad, FACTS)
    assert not c["ok"] and c["unknown_numbers"] == ["12"] and c["unknown_citations"] == ["case:99"]
    assert c["uncited_points"] == ["Mostly on one pump."]


def test_a_long_series_is_cut_to_its_shape():
    xs = [{"at": f"2026-11-{d:02d}", "value": v} for d, v in zip(range(1, 21), [5] * 9 + [1] + [5] * 5 + [9] + [5] * 4)]
    cut = rs._small({"history": xs})["history"]
    assert [p["value"] for p in cut] == [5, 1, 9, 5] and cut[0]["at"] == "2026-11-01" and cut[-1]["at"] == "2026-11-20"


@pytest.mark.integration
def test_a_note_is_written_from_the_panels_and_kept_for_its_period(okan_db):
    from fuzer.core.pack import current_pack

    conn, pack = okan_db.conn, current_pack()
    facts = rs.facts(conn, pack, "work", None, "7d", DAY)
    assert facts["panels"] and all(p["type"] not in ("note", "summary") for p in facts["panels"])
    assert len(json.dumps(facts, default=str)) <= rs.FACTS_CAP
    fake = FakeNote()
    assert rs.latest(conn, "work", None, "7d", DAY) is None
    out = rs.write(conn, pack, "work", None, "7d", DAY, "user:test", client=fake)
    assert out["headline"].startswith("Work and dispatch") and out["checks"]["ok"], out["checks"]
    assert fake.requests[0]["model"] == "claude-haiku-5-5" and fake.requests[0]["output_config"]["effort"] == "low"
    assert rs.latest(conn, "work", None, "7d", DAY)["headline"] == out["headline"]
    assert rs.latest(conn, "work", None, "30d", DAY) is None, "another window is another note"
    assert rs.facts(conn, pack, "no-such-report", None, "7d", DAY) is None


@pytest.mark.integration
def test_reading_never_calls_a_model_and_a_kept_note_is_not_rewritten(okan_db, monkeypatch):
    from fuzer.core.pack import current_pack
    from fuzer.ops import report_pages

    conn, pack = okan_db.conn, current_pack()
    monkeypatch.setattr(rs.llm, "client", lambda *a, **k: pytest.fail("reading called a model"))
    defn = rs.reports.get(pack, "work")
    sid = next(p["id"] for p in rs.reports.panels(defn) if p["type"] == "summary") \
        if any(p["type"] == "summary" for p in rs.reports.panels(defn)) else None
    if sid:
        r = report_pages.panel_data(conn, pack, "work", sid, None, "7d", date(2026, 11, 3))
        assert r["summary"] is None and r["due"] is False
    rs.write(conn, pack, "work", None, "7d", date(2026, 11, 3), "user:test", client=FakeNote())
    app = FastAPI()
    app.include_router(commentary.router)
    r = TestClient(app).post("/reports/work/summary/generate?window=7d&at=2026-11-03").json()
    assert r["written"] is False and r["summary"]["headline"].startswith("Work and dispatch")
