"""Daily operations report (#98): facts from the day's records, each cited; a trip day leads with the trip;
late records make a new version with what changed; Claude's draft (a recorded one here) is checked for
uncited lines and numbers not in the records; the reporter step and the tools."""

import re
from datetime import date, timedelta

import pytest

from fuzer.agents import mcp_server, worker
from fuzer.core.pack import load_pack
from fuzer.graph import operations
from fuzer.ops import daily_report
from fuzer.workflow import engine

PACK = load_pack("okan")
pytestmark = pytest.mark.sequence  # (#137) days closed, then late records make a new version, then the draft and the reporter


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    engine.load_defaults(c)
    yield c


def test_last_week_every_line_cited_and_matching(conn):
    last = date.fromisoformat(operations.as_of(conn))
    assert last.isoformat() == "2025-12-30"
    assert daily_report.recheck_days(last) == [last - timedelta(days=i) for i in range(7)]
    for day in daily_report.recheck_days(last):
        day = day.isoformat()
        out = daily_report.close_day(conn, PACK, day)
        assert out["version"] == 1 and out["item_id"], out
        r = daily_report.get(conn, day)
        assert r["checks"]["ok"], r["checks"]
        assert r["facts"]["day"] == day
        assert "Headline" in r["facts"]["sections"]
        rec = daily_report._day001(conn, day)
        assert f"Oil {float(rec['oil_sm3']):,.0f} Sm3" in r["markdown"] and rec["id"] in r["markdown"]
        assert [line for line in r["markdown"].splitlines() if line.startswith("## ")] == \
            [f"## {n}. {s}" for n, s in enumerate(daily_report.OUTLINE, 1)]
    assert daily_report.close_day(conn, PACK, last.isoformat())["changed"] is False  # nothing new: no new version


def test_a_trip_day_leads_with_the_trip(conn):
    daily_report.close_day(conn, PACK, "2024-11-05")
    report = daily_report.get(conn, "2024-11-05")
    head = report["markdown"].split("## 2.")[0]
    headline = report["facts"]["sections"]["Headline"]
    trip = conn.execute("select fuzer_id from golden where field = 'start' and value #>> '{}' = '2024-11-05T05:30'").fetchone()
    assert "Biggest loss: Process trip, 146 Sm3 oil, back at 10:30" in head and trip["fuzer_id"] in head
    assert any("Biggest loss: Process trip, 146 Sm3 oil" in fact["text"] for fact in headline)
    assert any("7-day average" in fact["text"] for fact in report["facts"]["sections"]["Production and deferment"])


def test_late_records_make_a_new_version(conn):
    day = "2025-12-29"
    rec = daily_report._day001(conn, day)
    conn.execute("update golden set value = '7000.0'::jsonb where fuzer_id = %s and field = 'oil_sm3'", (rec["id"],))
    out = daily_report.close_day(conn, PACK, day)
    assert out["version"] == 2 and "Production and deferment" in out["changes"] and "Headline" in out["changes"]
    assert any("Oil 7,000 Sm3" in a for a in out["changes"]["Headline"]["added"])
    r = daily_report.get(conn, day)
    assert r["versions"] == [1, 2] and daily_report.get(conn, day, 1)["version"] == 1
    conn.rollback()


GOOD = """# Daily operations report 2024-11-05

## 1. Headline

- Train C tripped at 05:30 on high scrubber level, losing 146 Sm3 of oil; it was back at 10:30. [{trip}]

## 2. Production and deferment

- Oil was 7,098 Sm3 against a potential of 7,244 Sm3. [{day}]

## 3. Upsets and events

Nothing to report.
"""


def test_claude_draft_is_checked(conn):
    trip = conn.execute("select fuzer_id from golden where field = 'start' and value #>> '{}' = '2024-11-05T05:30'").fetchone()
    day = daily_report._day001(conn, "2024-11-05")["id"]
    good = GOOD.format(trip=trip["fuzer_id"], day=day)
    seen = {}

    def ask(system, user):
        seen["system"], seen["user"] = system, user
        return {"answer": good, "usage": {"cost_usd": 0.05, "input_tokens": 9000, "output_tokens": 600}}

    out = daily_report.redraft(conn, "2024-11-05", ask=ask, comments="lead with the trip")
    assert out["checks"]["ok"], out["checks"]
    assert "square brackets" in seen["system"] and "lead with the trip" in seen["user"] and trip["fuzer_id"] in seen["user"]
    r = daily_report.get(conn, "2024-11-05")
    assert r["drafted_by"] == "claude" and r["usage"]["cost_usd"] == 0.05 and r["seconds"] is not None

    bad = good + "\n- Production will recover to 7,600 Sm3 tomorrow.\n- Oil was 6,123 Sm3. [fz-made-up-0123456789]\n"
    c = daily_report.check(bad, r and daily_report.latest(conn, "2024-11-05")["facts"])
    assert c["uncited_lines"] == ["- Production will recover to 7,600 Sm3 tomorrow."]
    assert c["unknown_citations"] == ["fz-made-up-0123456789"] and set(c["unknown_numbers"]) == {"7,600", "6,123"}
    bold = re.sub(r"(?m)^## (\d+\. .+)$", r"**\1**", good)  # headings in bold, as Claude sometimes writes them
    assert bold != good and daily_report.check(bold, daily_report.latest(conn, "2024-11-05")["facts"])["ok"]
    assert daily_report.check("**Oil was fine**", daily_report.latest(conn, "2024-11-05")["facts"])["uncited_lines"]


def test_reporter_step_and_tools(conn, monkeypatch):
    day = "2025-12-28"
    item = daily_report.get(conn, day)["item_id"]
    real = daily_report.draft
    monkeypatch.setattr(daily_report, "draft", lambda f, ask=None, comments=None, model=None: real(
        f, lambda s, u: {"answer": daily_report.render(f), "usage": {"cost_usd": 0.01}}, comments))
    conn.commit()
    ran = 0
    while worker.run_once(conn, {"reporter": daily_report.reporter_agent}) and ran < 20:
        ran += 1
    assert ran >= 7, "the reporter drafted each day's report"
    proc = conn.execute("select data from work_process where item_id = %s", (item,)).fetchone()
    tasks = conn.execute("select t.element from work_token t join work_process p on p.id = t.process_id "
                         "where p.item_id = %s and t.state = 'waiting'", (item,)).fetchall()
    assert "draft" in proc["data"] or any(t["element"] == "review" for t in tasks)
    got = mcp_server.get_daily_ops_report(day)
    assert got["day"] == day and got["markdown"].startswith(f"# Daily operations report {day}")
    assert mcp_server.get_daily_ops_report()["day"] == "2025-12-30"
    from fastapi.testclient import TestClient

    from fuzer import api
    client = TestClient(api.app)
    assert client.get("/reports/daily").json()[0]["day"] == "2025-12-30"
    assert client.get("/reports/daily/1999-01-01").status_code == 404

    # #242: the Reports page shows where each day's review stands and reviews it in place
    statuses = {d["day"]: d["status"] for d in client.get("/reports/daily").json()}
    assert statuses[day] == "awaiting review"
    rv = client.get(f"/reports/daily/{day}").json()["review"]
    assert rv["status"] == "awaiting review" and rv["step"]["element"] == "review" and rv["step"]["kind"] == "user"
    task = rv["step"]["id"]
    assert client.post(f"/work/tasks/{task}/claim", json={"by": "olga"}).status_code == 200
    r = client.post(f"/work/tasks/{task}/complete", json={"by": "olga", "data": {
        "review_decision": "revise", "review_comments": "lead with the trip"}})
    assert r.status_code == 200, r.text
    rv = client.get(f"/reports/daily/{day}").json()["review"]
    assert rv["status"] == "sent back" and rv["comments"] == "lead with the trip" and rv["step"]["kind"] == "agent"
    while worker.run_once(conn, {"reporter": daily_report.reporter_agent}):
        pass
    rv = client.get(f"/reports/daily/{day}").json()["review"]
    assert rv["status"] == "awaiting review"
    task = rv["step"]["id"]
    client.post(f"/work/tasks/{task}/claim", json={"by": "olga"}).raise_for_status()
    client.post(f"/work/tasks/{task}/complete", json={"by": "olga", "data": {"review_decision": "publish"}}).raise_for_status()
    rv = client.get(f"/reports/daily/{day}").json()["review"]
    assert rv["status"] == "published" and rv["step"] is None
    assert {d["day"]: d["status"] for d in client.get("/reports/daily").json()}[day] == "published"


def test_comparisons_never_read_as_production_values():
    """A change from yesterday or the 7-day average is phrased as above, below or unchanged (#390)."""
    assert daily_report._vs("yesterday's {prev} Sm3", 0.0) == "Oil unchanged from yesterday's {prev} Sm3"
    up = daily_report._f(daily_report._vs("the 7-day average of {avg} Sm3", 20), [], change=20, avg=7452)
    assert up["text"] == "Oil 20 Sm3 above the 7-day average of 7,452 Sm3"
    down = daily_report._f(daily_report._vs("yesterday's {prev} Sm3", -35.5), [], change=35.5, prev=7472.0)
    assert down["text"] == "Oil 35.5 Sm3 below yesterday's 7,472 Sm3"
