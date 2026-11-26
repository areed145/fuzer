"""A report's AI note: written once per report, scope, window and day from that report's own panel results, checked like
the daily report, read by everyone, and never written by a read."""

import json
import re
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
    # a list panel says it is the first rows of how many (#1396): "all six cases in the field" was 6 of 41
    lists = [p for p in facts["panels"] if p["type"] == "cases"]
    assert lists and all(re.fullmatch(r"\d+ of \d+ shown", p["result"]["shown"]) for p in lists), lists
    assert all("total" not in p["result"] for p in lists)
    assert "6 of 41 shown" in rs.SYSTEM
    fake = FakeNote()
    assert rs.latest(conn, "work", None, "7d", DAY) is None
    out = rs.write(conn, pack, "work", None, "7d", DAY, "user:test", client=fake)
    assert out["headline"].startswith("Work and dispatch") and out["checks"]["ok"], out["checks"]
    assert fake.requests[0]["model"] == "claude-sonnet-5-5" and fake.requests[0]["output_config"]["effort"] == "medium"
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


def test_a_measure_key_reads_as_its_label():
    """P5.26e, #1367: a note never shows a measure key; its cites carry the labels the page shows them by."""
    from fuzer.core.pack import load_pack

    note = {"headline": "work.backlog is high", "points": [{"text": "See work.backlog and e.g. the queue.",
                                                            "cites": ["work.backlog", "case:3"]}]}
    out = rs.worded(note, load_pack("okan"))
    label = rs.metrics.registry()["work.backlog"].label
    assert out["headline"] == f"{label} is high" and "work.backlog" not in out["points"][0]["text"]
    assert "e.g." in out["points"][0]["text"] and out["labels"] == {"work.backlog": label}


def test_the_note_talks_about_the_stats_it_cites_or_whose_value_it_says():
    f = {"panels": [*FACTS["panels"], {"id": "closed", "type": "stat", "measures": ["work.closed"],
                                        "result": {"value": 318}},
                    {"id": "late", "type": "stat", "measures": ["work.sla_misses"], "result": {"value": 7}}]}
    note = {"headline": "Closed 318 cases", "points": [{"text": "Backlog high.", "cites": ["work.backlog"]}]}
    assert [p["id"] for p in rs.said(note, f)] == ["backlog", "closed"]


@pytest.mark.integration
def test_a_note_whose_figures_moved_is_not_shown(okan_db):
    """P5.26e, #1367: a kept note shows only while the stats it talks about read as they did when it was written; a
    stale one is hidden (a read writes nothing) and asking writes a new one."""
    from fuzer.core.pack import current_pack
    from fuzer.ops import report_pages

    conn, pack = okan_db.conn, current_pack()
    day = date.today()  # cases have history from the day the test database is built
    sc = report_pages.panel_data(conn, pack, "work", "summary", None, "7d", day)["scope"]["id"]  # the page's scope
    f = rs.facts(conn, pack, "work", sc, "7d", day)
    backlog = next(p for p in f["panels"] if p["id"] == "backlog")
    fake = FakeNote({"headline": "The week in work", "points": [
        {"text": "Open cases are on the page.", "cites": ["work.backlog"]}]})
    rs.write(conn, pack, "work", sc, "7d", day, "user:test", client=fake)
    assert rs.current(conn, pack, "work", sc, "7d", day)["headline"] == "The week in work"
    assert report_pages.panel_data(conn, pack, "work", "summary", sc, "7d", day)["summary"] is not None

    def kept_with(panel_id, value):  # the facts the note was written from, with one stat reading otherwise
        moved = {**f, "panels": [{**p, "result": {**p["result"], "value": value}} if p["id"] == panel_id else p
                                 for p in f["panels"]]}
        conn.execute("update report_summary set facts = %s where report_key = 'work' and at = %s",
                     (rs.Jsonb(moved), day))
        conn.commit()

    kept_with("backlog", (backlog["result"].get("value") or 0) + 1)
    assert rs.stale(conn, pack, rs.latest(conn, "work", sc, "7d", day), sc, "7d", day) == ["backlog"]
    assert rs.current(conn, pack, "work", sc, "7d", day) is None
    r = report_pages.panel_data(conn, pack, "work", "summary", sc, "7d", day)
    assert r["summary"] is None and r["due"] is False, "a stale note is never rewritten by a read"
    # a stat the note neither cites nor quotes may move without making it stale
    other = next(p for p in f["panels"] if p["type"] == "stat" and p["id"] != "backlog")
    kept_with(other["id"], 123456)
    assert rs.current(conn, pack, "work", sc, "7d", day) is not None
