"""The SRE hand-off (#96c) and the work board (#99) on the upsets of Okan's 5 November 2024 scan: each test
sets up what it needs through fixtures, so it passes on its own and in any order (#137)."""

import pytest

from fuzer.agents import mcp_server
from fuzer.core.pack import load_pack
from fuzer.ops import pipeline, sre

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    """Scanned from midnight to 18:00 on 5 November 2024 (train C's trip), and rebuilt so the trip's
    downtime lands on the detected upset's incident."""
    from fuzer.workflow import engine

    c = okan_db.conn
    engine.load_defaults(c)
    sre.scan(c, PACK, "2024-11-05T00:00", "2024-11-05T18:00")
    pipeline.rebuild(c, PACK)
    return c


@pytest.fixture(scope="module")
def handed_off(conn):
    """The upsets handed off once, with a recorded diagnosis for the top one: (candidates, result, the
    questions asked, the fake Claude)."""
    from fuzer.ops import sre_handoff

    asked = []

    def fake_ask(q):
        asked.append(q)
        return {"answer": ANSWER, "usage": {"cost_usd": 0.4}}

    todo = sre_handoff.candidates(conn)
    return todo, sre_handoff.handoff(conn, PACK, ask=fake_ask, max_diagnoses=1), asked, fake_ask


ANSWER = """Most likely liquid carryover from the inlet separator into train C's 1st stage scrubber.

```json
{"causes": [{"cause": "Liquid carryover from 20-V-2001 (level transmitter hunting, real level high)",
             "confidence": 0.8, "evidence": ["ec:downtime:OKDT-00008"], "confirm_by": "compare 20-LT-2001 with a sight glass"},
            {"cause": "Scrubber level control fault", "confidence": 0.2, "evidence": [], "confirm_by": "stroke the dump valve"}],
 "actions": [{"action": "Raise a notification to check 20-LT-2001", "source": "fz-tag-0123456789ab"}]}
```"""


def test_handoff_prioritises_diagnoses_and_opens_work(conn, handed_off):
    """Each upset of note gets a #100 priority, the troubleshooter's diagnosis (a recorded answer here,
    within the cap) and an `upset` work item; a false alarm stops its pattern."""
    from fuzer.ops import sre_handoff
    from fuzer.workflow import engine

    todo, out, asked, fake_ask = handed_off
    assert todo and all(u["detections"] >= sre_handoff.MIN_DETECTIONS for u in todo)
    assert out["handed_off"] == len(todo) and out["diagnosed"] == 1 and len(asked) == 1 and out["claude_usd"] == 0.4
    assert "likely first cause" in asked[0].lower() and "```json" in asked[0]
    top = max(out["items"], key=lambda i: i["value_usd"])
    assert top["diagnosed"] and out["items"][0] == top  # the highest priority gets the diagnosis
    row = conn.execute("select * from upset where id = %s", (top["upset"],)).fetchone()
    assert row["work_item_id"] == top["item_id"] and row["priority_usd"] == top["value_usd"]
    causes = row["diagnosis"]["causes"]
    assert [c["cited"] for c in causes] == [True, False] and row["diagnosis"]["actions"]
    item = engine.item(conn, top["item_id"])
    data = conn.execute("select data from work_process where item_id = %s", (top["item_id"],)).fetchone()["data"]
    assert item["type"] == "upset" and data["priority"]["components"] and data["pre_diagnosis"]["causes"]
    assert mcp_server.upset_detail(top["upset"])["diagnosis"]["causes"][0]["confidence"] == 0.8
    assert sre_handoff.handoff(conn, PACK, ask=fake_ask)["candidates"] == 0  # handed off once

    # an engineer triages one as a false alarm: its pattern isn't handed off again
    other = out["items"][-1]
    tok = conn.execute("select t.id from work_token t join work_process p on p.id = t.process_id "
                       "where p.item_id = %s and t.element = 'triage' and t.state = 'waiting'", (other["item_id"],)).fetchone()
    engine.set_person(conn, "alice", ["engineer"])
    engine.complete(conn, tok["id"], "alice", {"triage": "false_alarm", "triage_note": "valve stroke test"})
    conn.commit()
    assert sre_handoff.sync_feedback(conn)["false_alarms"] == 1
    conn.execute("update upset set status = 'detected', work_item_id = null where id = %s", (other["upset"],))
    conn.commit()
    assert other["upset"] not in [u["id"] for u in sre_handoff.candidates(conn)]


def test_board_ranks_everything_once(conn, handed_off):
    """The work board (#99) after the hand-off: SAP backlog, plans, incidents, upsets, work items and a
    project together, ranked; an upset, its incident and its work item one card; a pin survives a
    rebuild."""
    from fuzer.ops import board

    board.add_project(conn, "Replace 20-LT-2001 with a guided-wave radar", "alice", benefit_usd=250000.0, owner="alice")
    out = board.refresh(conn, PACK)
    kinds = set(out["by_kind"])
    assert {"work_order", "maintenance_plan", "incident", "project"} <= kinds, out
    page = board.cards(conn, per_page=500)
    assert [c["rank"] for c in page["cards"]] == list(range(1, len(page["cards"]) + 1))
    trip = conn.execute("select id, work_item_id from upset where work_item_id is not null order by priority_usd desc "
                        "limit 1").fetchone()
    holding = [c for c in page["cards"] if f"upset:{trip['id']}" in c["records"]]
    assert len(holding) == 1 and f"work_item:{trip['work_item_id']}" in holding[0]["records"]
    inc = conn.execute("select fuzer_id from golden where field = 'upsets' and value @> %s::jsonb",
                       (f"[{trip['id']}]",)).fetchone()  # its incident is older than #100's lookback here,
    keys = {f"incident:{inc['fuzer_id']}", f"upset:{trip['id']}"}  # so check the link that joins them
    assert board._links(conn, keys) == [(f"incident:{inc['fuzer_id']}", f"upset:{trip['id']}")]
    valued = [c["effective_usd"] for c in page["cards"] if c["effective_usd"] is not None]
    assert valued == sorted(valued, reverse=True)
    wo = next(c for c in board.cards(conn, kind="work_order", per_page=200)["cards"]
              if c["text"] and (w := c["text"].split()[-1]).lower() not in c["title"].lower() and len(w) > 3)
    word = wo["text"].split()[-1]  # in the description only, not the title
    assert wo["key"] in {c["key"] for c in board.cards(conn, text=word, per_page=200)["cards"]}

    low = page["cards"][len(valued) - 1]
    board.pin(conn, low["records"][-1], "bob", "the ops lead wants this first", rank=1)
    pipeline.rebuild(conn, PACK)
    board.refresh(conn, PACK)
    top = board.cards(conn, per_page=3)["cards"][0]
    assert top["key"] == low["key"] and top["pinned"] and top["pin_reason"] == "the ops lead wants this first"
    assert top["moved"].startswith("up") and board.history(conn, top["key"])[-1]["rank"] == 1
    with pytest.raises(ValueError):
        board.pin(conn, low["key"], "bob", "", rank=2)

    f = board.cards(conn, facets=True, per_page=5)["facets"]
    assert sum(f["kind"].values()) == page["total"] and "unassigned" in f["owner"] and f["area"]
    owner = next(k for k in f["owner"] if k != "unassigned")
    mine = board.cards(conn, owner=owner, per_page=500)
    assert mine["total"] == f["owner"][owner] and all(c["owner"] == owner for c in mine["cards"])
    from fastapi.testclient import TestClient

    from fuzer import api
    client = TestClient(api.app)
    got = client.get("/board", params={"kind": "project", "facets": "true"}).json()
    assert got["cards"][0]["title"].startswith("Replace 20-LT-2001") and "facets" in got
    assert client.post("/board/pins", json={"key": "project:1", "rank": 2}).status_code == 400  # a pin needs a reason

    train = mcp_server.board(unit="20-V-2001", limit=5)
    assert train["cards"] and all(c["unit"] == "20-V-2001" for c in train["cards"])
    steps = mcp_server.next_work("troubleshooter")
    assert [s["board_rank"] for s in steps["steps"]] == sorted(s["board_rank"] or 10 ** 9 for s in steps["steps"])
