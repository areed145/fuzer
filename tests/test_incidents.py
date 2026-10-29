"""Incidents: events, downtime and notifications on one unit grouped into one upset."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

from fuzer import db, fakedata, incidents, ingest, llm_links, pipeline, queries
from fuzer.pack import load_pack

sys.path.insert(0, str(Path(__file__).parent))
from test_llm_links import FakeClaude  # noqa: E402

PACK = load_pack()


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    fakedata.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_downtime_alone_makes_an_incident(conn):
    found = queries.incidents(conn, fid(conn, "23-K-1001A"))["items"]
    assert any(i["start"] == "2024-02-03" and i["downtime_records"] >= 1 for i in found)


def test_events_and_notifications_group_on_one_unit(conn):
    llm_links.run(conn, PACK, client=FakeClaude(), workers=1, sources=["oplog"])
    pipeline.rebuild(conn, PACK)
    pump = queries.incidents(conn, fid(conn, "20-P-1001B"))["items"]
    jan = next(i for i in pump if i["start"] == "2024-01-01")
    assert jan["events"] >= 1 and jan["notifications"] >= 1 and "VIB" in jan["failure_modes"]
    timeline = queries.incident_timeline(conn, jan["id"])
    assert [t["kind"] for t in timeline][:2] == ["event", "notification"]  # trip logged, notification next day
    assert timeline == sorted(timeline, key=lambda t: (t["at"], t["kind"]))


def test_instrument_signals_roll_up_to_their_equipment(conn):
    pt, vessel = fid(conn, "20-PT-2001"), fid(conn, "20-V-2001")
    units = {s["unit"] for s in incidents._signals(conn)
             if s["kind"] == "event" and s["text"] and "20-PT-2001" in s["text"]}
    assert units == {vessel} and pt not in units


def test_incidents_by_equipment_model_and_class(conn):
    tag = fid(conn, "20-P-1001B")
    pump = conn.execute("select src from edge where dst = %s and rel = 'installed_at' and valid_to is null",
                        (tag,)).fetchone()["src"]
    by_tag = queries.incidents(conn, tag)["total"]
    assert queries.incidents(conn, pump)["total"] == by_tag
    assert queries.incidents(conn, fid(conn, "ISO14224:PU"))["total"] >= by_tag > 0


def test_similar_incidents(conn):
    jan = next(i for i in queries.incidents(conn, fid(conn, "20-P-1001B"))["items"] if i["start"] == "2024-01-01")
    similar = queries.similar_incidents(conn, jan["id"])
    assert similar and jan["id"] not in {s["id"] for s in similar}
    assert similar[0]["score"] >= similar[-1]["score"]


def test_incident_ids_are_stable(conn):
    before = {i["start"] + i["unit"]: i["id"] for i in queries.incidents(conn, limit=1000)["items"]}
    pipeline.rebuild(conn, PACK)
    after = {i["start"] + i["unit"]: i["id"] for i in queries.incidents(conn, limit=1000)["items"]}
    assert before == after


def test_lone_notifications_are_not_incidents(conn):
    total = queries.incidents(conn, limit=1)["total"]
    notifications = conn.execute("select count(*) as n from entity where kind = 'notification'").fetchone()["n"]
    assert 0 < total < notifications
