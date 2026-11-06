"""Incidents: events, downtime and notifications on one unit grouped into one upset."""


import pytest
from test_llm_links import (
    FakeClaude,  # a test module: tests/conftest.py puts every test folder on the path
)

from fuzer.core.pack import load_pack
from fuzer.graph import incidents, llm_links, queries
from fuzer.ops import pipeline

PACK = load_pack()


@pytest.fixture(scope="module")
def conn(okan_db):
    """With the operator logs read (by a fake Claude) into events, so incidents have them."""
    c = okan_db.conn
    llm_links.run(c, PACK, client=FakeClaude(), workers=1, sources=["oplog"])
    pipeline.rebuild(c, PACK)
    yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_downtime_alone_makes_an_incident(conn):
    found = queries.incidents(conn, fid(conn, "20-K-2200B"))["items"]
    assert any(i["downtime_records"] >= 1 for i in found)


def test_events_and_notifications_group_on_one_unit(conn):
    compressor = queries.incidents(conn, fid(conn, "20-K-2200B"))["items"]
    march = next(i for i in compressor if i["start"].startswith("2024-03"))
    assert march["events"] >= 1 and march["notifications"] >= 1 and "UST" in march["failure_modes"]
    timeline = queries.incident_timeline(conn, march["id"])
    assert any(t["kind"] == "notification" for t in timeline)
    assert timeline == sorted(timeline, key=lambda t: (t["at"], t["kind"]))


def test_instrument_signals_roll_up_to_their_equipment(conn):
    compressor = fid(conn, "20-K-2200B")
    units = {s["unit"] for s in incidents._signals(conn)
             if s["kind"] == "event" and s["text"] and "20-K-2200B" in s["text"]}
    assert compressor in units


def test_incidents_by_equipment_model_and_class(conn):
    tag = fid(conn, "20-K-2200B")
    pump = conn.execute("select src from edge where dst = %s and rel = 'installed_at' and valid_to is null",
                        (tag,)).fetchone()["src"]
    by_tag = queries.incidents(conn, tag)["total"]
    assert queries.incidents(conn, pump)["total"] == by_tag
    assert queries.incidents(conn, fid(conn, "ISO14224:CO"))["total"] >= by_tag > 0


def test_similar_incidents(conn):
    march = next(i for i in queries.incidents(conn, fid(conn, "20-K-2200B"))["items"]
                 if i["start"].startswith("2024-03"))
    similar = queries.similar_incidents(conn, march["id"])
    assert march["id"] not in {s["id"] for s in similar}
    assert not similar or similar[0]["score"] >= similar[-1]["score"]


def test_incident_ids_are_stable(conn):
    before = {i["start"] + i["unit"]: i["id"] for i in queries.incidents(conn, limit=1000)["items"]}
    pipeline.rebuild(conn, PACK)
    after = {i["start"] + i["unit"]: i["id"] for i in queries.incidents(conn, limit=1000)["items"]}
    assert before == after


def test_lone_notifications_are_not_incidents(conn):
    total = queries.incidents(conn, limit=1)["total"]
    notifications = conn.execute("select count(*) as n from entity where kind = 'notification'").fetchone()["n"]
    assert 0 < total < notifications


def test_upset_workflow_on_a_fake_incident(conn):
    """#97: an upset on the fake asset goes detected -> agent diagnosis -> engineer approval -> resolved,
    each step in its history; a second detection of the same incident adds evidence to it."""
    from fuzer.agents import worker
    from fuzer.workflow import engine
    engine.load_defaults(conn)
    engine.set_person(conn, "alice", ["engineer"])
    engine.set_person(conn, "pat", ["maintenance_planner"])
    inc = queries.incidents(conn, fid(conn, "20-K-2200B"))["items"][0]
    unit = conn.execute("select dst from edge where src = %s and rel = 'about'", (inc["id"],)).fetchone()["dst"]
    start = {"incident_id": inc["id"], "title": f"Upset on 20-K-2200B from {inc['start']}", "subjects": [unit]}
    out = engine.start_or_correlate(conn, "upset", start, "agent:sre")
    again = engine.start_or_correlate(conn, "upset", {**start, "note": "seen again"}, "agent:sre")
    assert not again["started"] and again["process_id"] == out["process_id"]

    def open_task(element):
        return engine.item(conn, out["item_id"])["processes"][0]["open"][0] | {"element_expected": element}

    def diagnose(call):  # stands in for the troubleshooter agent: reads the incident's signals from Fuzer
        signals = queries.incident_timeline(conn, call.data["incident_id"])
        return {"diagnosis": f"{len(signals)} signals", "proposed_action": "inspect 20-K-2200B"}

    engine.complete(conn, open_task("triage")["id"], "alice", {"triage": "real"})
    assert worker.run_once(conn, {"troubleshooter": diagnose}, "w")
    approve = open_task("approve")
    assert approve["element"] == "approve"
    with pytest.raises(engine.Forbidden):
        engine.complete(conn, approve["id"], "agent:troubleshooter", {"approval": "approved"})
    engine.complete(conn, approve["id"], "alice", {"approval": "approved"})
    assert worker.run_once(conn, {"maintenance": lambda call: {"notification_draft": "inspect"}}, "w")
    engine.complete(conn, open_task("raise_notification")["id"], "pat", {})
    from datetime import timedelta
    engine.tick(conn, now=engine.utcnow() + timedelta(hours=7))
    engine.complete(conn, open_task("confirm")["id"], "alice", {"outcome": "resolved"})
    detail = engine.item(conn, out["item_id"])
    assert detail["state"] == "done" and detail["outcome"] == "Resolved" and detail["subjects"] == [unit]
    assert [s["element"] for s in detail["processes"][0]["path"] if s["kind"] in ("user", "agent")] == \
        ["triage", "diagnose", "approve", "draft_notification", "raise_notification", "confirm"]
    assert detail["processes"][0]["data"]["messages"][0]["payload"]["note"] == "seen again"
