"""Agentic SRE detection (#96): the detectors replayed on Okan's injected upsets (#103), the scheduled
scan's watermark and dedupe, and the daily checks on the slow stories."""

import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from fakeplant import okan
from fuzer import db, ingest, mcp_server, pipeline, replay, sre, timeseries
from fuzer.pack import load_pack

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

    got = mcp_server.detections("2024-11-05T00:00", "stops")
    n = conn.execute("select count(*) as n from detection where detector = 'stops'").fetchone()["n"]
    assert got["count"] == n and got["detections"][0]["unit"] and got["detections"][0]["point"]
