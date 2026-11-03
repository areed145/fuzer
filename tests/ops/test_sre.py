"""Agentic SRE detection (#96): the detectors replayed on Okan's injected upsets (#103), the scheduled
scan's watermark and dedupe, and the daily checks on the slow stories."""

from datetime import datetime, timedelta

import pytest

from fuzer.agents import mcp_server
from fuzer.core.pack import load_pack
from fuzer.evals import replay
from fuzer.ops import pipeline, sre
from fuzer.sources import timeseries

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def data(okan_data):
    return okan_data


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    yield c


@pytest.mark.e2e  # a week replayed: about 2.5 minutes
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


def test_alarms_already_on_at_the_start_are_standing():
    """#142: detections already on at the first instant scanned group as usual, but a group of nothing else
    is `standing`, not an upset; one a new detection joins is an upset, and may start from it (an upset
    already under way)."""
    t0 = datetime(2024, 6, 10)

    def det(point, unit, h, key="alarm:hi"):
        return {"point": point, "unit_id": unit, "detector": "excursions", "key": key, "start": t0 + timedelta(hours=h),
                "end": None}

    whp, other, step = det("OK_J21_08.WHP", "w1", 0), det("OK_J02_01.WHP", "w2", 0), det("OK_J21_08.FLOW", "w1", 1, "step")
    groups = sre.group_detections([whp, other, step], {}, standing_before=t0)
    assert sorted(g["kind"] for g in groups) == ["process", "standing"]
    process = next(g for g in groups if g["kind"] == "process")
    assert process["detections"] == [whp, step] and process["first"] is whp
    fresh = [{**whp, "standing": False}, {**other, "standing": False}]
    assert [g["kind"] for g in sre.group_detections(fresh, {})] == ["process", "process"]  # no scan start: as before
