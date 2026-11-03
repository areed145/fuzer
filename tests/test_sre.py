"""Agentic SRE detection (#96): the detectors replayed on Okan's injected upsets (#103), the scheduled
scan's watermark and dedupe, and the daily checks on the slow stories."""

import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from fakeplant import okan
from fuzer import mcp_server, replay
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.graph import pipeline
from fuzer.ops import sre
from fuzer.sources import ingest, timeseries

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def data():
    out = Path(tempfile.mkdtemp(prefix="fzokan"))
    okan.generate(out)
    return out


@pytest.fixture(scope="module")
def conn(data):
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATA_DIR", str(data))
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c
    mp.undo()


def test_replay_finds_the_november_upsets(conn, data):
    """Train C's two trips, the inlet separator's level transmitter hunting and the high levels that
    carried over: all found, the trips within the hour, with no false alarms."""
    res = replay.run(conn, PACK, "sre", "2024-11-04T00:00", "2024-11-10T00:00", data_dir=data)
    assert res["recall"] == 1.0, res["by_kind"]
    assert res["by_kind"]["trip"]["median_hours_to_detect"] <= 1
    assert res["false_alarms_per_week"] <= 2, res["false_alarm_examples"]
    g = res["grouping"]  # each trip is one upset, starting from its first cause
    assert g["groups_per_trip"] == 1 and g["first_cause_right"] == 1.0 and g["false_upsets_per_week"] <= 2, g
    assert {d.split(":")[0] for d in replay.DETECTORS} == {"limits", "sre"}


def test_daily_checks_find_the_slow_stories(conn, data):
    src = timeseries.SyntheticProvider(data)
    assert sre._daily(datetime(2025, 8, 1), datetime(2025, 8, 1, 6)) and not sre._daily(datetime(2025, 8, 1, 6),
                                                                                       datetime(2025, 8, 1, 12))
    pump = sre.models(conn, PACK, datetime(2025, 8, 1), datetime(2025, 8, 1, 6), src)
    assert any(d["key"] == "pump_curve:head_loss" and d["point"].startswith("OKA_PP.10") and "1001A" in d["point"]
               for d in pump), pump
    foul = sre.drifts(conn, PACK, datetime(2024, 7, 1), datetime(2024, 7, 1, 6), src)
    assert any(d["point"] == "OK_GGCP:20-TT-2350A.PV" and d["key"] == "drift_up" and d["magnitude"] > 0 for d in foul)
    assert sre.drifts(conn, PACK, datetime(2024, 7, 1, 6), datetime(2024, 7, 1, 12), src) == []  # once a day


def test_scan_carries_on_from_the_last_run(conn):
    first = sre.scan(conn, PACK, "2024-11-05T00:00", "2024-11-05T12:00")
    assert first["windows"] == 2 and first["new"] > 0
    stop = conn.execute("select d.*, l.label from detection d join entity_label l on l.fuzer_id = d.point_id "
                        "where d.detector = 'stops' and d.start >= '2024-11-05'").fetchall()
    assert any(s["start"] == datetime(2024, 11, 5, 5, 30) or abs((s["start"] - datetime(2024, 11, 5, 5, 30)).total_seconds())
               <= 3600 for s in stop), [(s["label"], s["start"]) for s in stop]  # train C tripped at 05:30

    second = sre.scan(conn, PACK, until="2024-11-05T18:00")  # from the watermark, less an hour
    assert second["since"] == "2024-11-05T11:00" and second["windows"] == 2 and second["extended"] > 0
    dupes = conn.execute("select point_id, detector, key, start, count(*) from detection "
                         "group by 1, 2, 3, 4 having count(*) > 1").fetchall()
    assert dupes == []  # still-open detections were extended, not raised again
    assert sre.scan(conn, PACK, until="2024-11-05T18:00")["windows"] == 1  # the overlap hour only
    assert [r["status"] for r in conn.execute("select status from sre_run order by id")] == ["done"] * 3

    trip = [u for u in sre.upsets(conn, "2024-11-05T00:00") if u["start"] <= "2024-11-05T05:30"
            and "20-K-2100C" in (u["units"] or [])]
    assert len(trip) == 1 and trip[0]["first_unit"] == "20-V-2001", trip  # the carryover, before the trip
    assert len({tuple(u["units"] or []) for u in sre.upsets(conn) if "20-K-2100C" in (u["units"] or [])}) == 1
    detail = mcp_server.upset_detail(trip[0]["id"])
    first = next(x for x in detail["timeline"] if x["detector"] != "multivariate")  # an SPE finding isn't a cause
    assert first["unit"] == "20-V-2001" and detail["by_unit"]["20-K-2100C"] >= 1

    pipeline.rebuild(conn, PACK)  # the train C trip downtime lands on the detected upset's incident
    inc = conn.execute("select f.value as f from entity_label l join golden f on f.fuzer_id = l.fuzer_id "
                       "and f.field = 'upsets' where l.label = '20-V-2001@2024-11-05'").fetchone()
    assert inc and trip[0]["id"] in inc["f"]

    got = mcp_server.detections("2024-11-05T00:00", "stops")
    n = conn.execute("select count(*) as n from detection where detector = 'stops'").fetchone()["n"]
    assert got["count"] == n and got["detections"][0]["unit"] and got["detections"][0]["point"]


ANSWER = """Most likely liquid carryover from the inlet separator into train C's 1st stage scrubber.

```json
{"causes": [{"cause": "Liquid carryover from 20-V-2001 (level transmitter hunting, real level high)",
             "confidence": 0.8, "evidence": ["ec:downtime:OKDT-00008"], "confirm_by": "compare 20-LT-2001 with a sight glass"},
            {"cause": "Scrubber level control fault", "confidence": 0.2, "evidence": [], "confirm_by": "stroke the dump valve"}],
 "actions": [{"action": "Raise a notification to check 20-LT-2001", "source": "fz-tag-0123456789ab"}]}
```"""


def test_handoff_prioritises_diagnoses_and_opens_work(conn):
    """After the scans above: each upset of note gets a #100 priority, the troubleshooter's diagnosis (a
    recorded answer here, within the cap) and an `upset` work item; a false alarm stops its pattern."""
    from fuzer.ops import sre_handoff
    from fuzer.workflow import engine

    engine.load_defaults(conn)
    asked = []

    def fake_ask(q):
        asked.append(q)
        return {"answer": ANSWER, "usage": {"cost_usd": 0.4}}

    todo = sre_handoff.candidates(conn)
    assert todo and all(u["detections"] >= sre_handoff.MIN_DETECTIONS for u in todo)
    out = sre_handoff.handoff(conn, PACK, ask=fake_ask, max_diagnoses=1)
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
    assert other["upset"] not in [u["id"] for u in sre_handoff.candidates(conn)]


def test_board_ranks_everything_once(conn):
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
