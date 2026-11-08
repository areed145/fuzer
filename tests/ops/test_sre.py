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


class Counting(timeseries.SyntheticProvider):
    """Counts the series read and the values in them, as a historian would bill them."""
    reads = read_values = 0

    def series(self, p, start, end, step):
        out = list(super().series(p, start, end, step))
        self.reads, self.read_values = self.reads + 1, self.read_values + len(out)
        return out


def test_instruments_read_their_baseline_once_a_day(conn, data, monkeypatch):
    """#203: a point's usual spread is read from the 2 days before the window's day on its first window of
    the day and kept; later windows read only themselves. It finds what reading the baseline every window
    (ending at the window) found, and a fault that starts during the day stays a fault (a baseline read up to
    each window soaks it up)."""
    conn.execute("delete from point_baseline")  # another test's scan may have kept the day's already
    day = datetime(2024, 11, 5)
    windows = [(day + timedelta(hours=h), day + timedelta(hours=h + 6)) for h in (0, 6, 12, 18)]
    pts = len([p for p in sre._points(conn) if not any(x.search(p["point"]) or x.search(p["description"] or "")
                                                       for x in sre._set_by_hand(PACK))])
    src, reads, new = Counting(data), [], []
    for a, b in windows:
        r0 = src.reads
        new += sre.instruments(conn, PACK, a, b, src)
        reads.append(src.reads - r0)
    modes = reads[1] - pts  # the run-signal reads every window makes for its units
    assert reads[0] == modes + 2 * pts and reads[1:] == [modes + pts] * 3, (reads, pts)
    assert conn.execute("select count(*) as n from point_baseline where day = %s", (day.date(),)).fetchone()["n"] == pts

    def every_window(conn, src, pts, at, step, days):  # as before #203
        from fuzer.analysis import signals
        return {p["id"]: signals.usual_spread(signals._read(src, p, at - timedelta(days=days), at - timedelta(minutes=step),
                                                            step), step) for p in pts}

    monkeypatch.setattr(sre, "baselines", every_window)
    old = [d for a, b in windows for d in sre.instruments(conn, PACK, a, b, timeseries.SyntheticProvider(data))]
    found = lambda ds: {(d["point"], d["key"]) for d in ds}
    assert found(new) <= found(old) and len(found(new)) >= 0.99 * len(found(old))  # all but a stretch or two at 60 min
    hunting = [d["start"].hour for d in new if d["point"] == "OK_GGCP:20-LT-2001.PV" and d["key"] == "instrument:noisy"]
    old_hunting = [d["start"].hour for d in old if d["point"] == "OK_GGCP:20-LT-2001.PV" and d["key"] == "instrument:noisy"]
    assert max(hunting) >= 18 and max(old_hunting) < 6  # read up to each window, the hunting became its own usual
    conn.commit()  # keep the day's baselines for the module's other scans, without holding their locks


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


def test_bad_instruments_roll_into_one_summary_a_week():
    """#141: instrument faults on any unit in the same week are one instrument-health summary, not an upset
    each; the next week starts another, and they never join a process upset."""
    t0 = datetime(2024, 6, 10)  # a Monday

    def det(point, unit, h, detector="instruments", key="instrument:frozen"):
        return {"point": point, "unit_id": unit, "detector": detector, "key": key, "start": t0 + timedelta(hours=h),
                "end": None}

    found = [det("A.PV", "u1", 1), det("B.PV", "u2", 30, key="instrument:noisy"), det("C.PV", "u3", 100),
             det("D.PV", "u4", 24 * 7 + 2), det("A.PV", "u1", 2, "excursions", "alarm:hi")]
    groups = sre.group_detections(found, {})
    assert sorted(g["kind"] for g in groups) == ["instrument", "instrument", "process"]
    week = next(g for g in groups if g["kind"] == "instrument")
    assert [d["point"] for d in week["detections"]] == ["A.PV", "B.PV", "C.PV"] and week["units"] == ["u1", "u2", "u3"]
