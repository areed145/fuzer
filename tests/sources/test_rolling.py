"""A generated facility that rolls forward (#1085): its project names the day its exports were written as today
(`rolls_from`), and every whole date read from them moves on by the days since, so Okan's latest data day is always
yesterday. Identifiers and free text stay as written, a re-dated record keeps no daily history, and the work that
follows the register (the daily report, the plan's SAP maintenance plans) treats the move as a move, not new work."""

import csv
import json
import shutil
from datetime import date, datetime, timedelta

import pytest

from fuzer.core.pack import load_pack
from fuzer.ops import daily_report, pipeline
from fuzer.sources import ingest, instruments, process_model, relative_dates
from fuzer.sources.relative_dates import place, roll

PACK = load_pack("okan")
ANCHOR = date(2025, 12, 31)  # Okan's AS_OF: the day its exports were written as today
TODAY = date(2026, 11, 14)
DAYS = (TODAY - ANCHOR).days  # 318

pytestmark = pytest.mark.sequence  # one copy of Okan, read on one day and then the next


def on(monkeypatch, today: date, anchor: date | None = ANCHOR) -> None:
    monkeypatch.setattr(relative_dates, "rolls_from", lambda: anchor)
    monkeypatch.setattr(relative_dates, "plant_today", lambda: today)


def test_only_whole_dates_and_times_move():
    assert roll("2025-12-30", DAYS) == "2026-11-13"
    assert roll("2025-12-30T14:00", DAYS) == "2026-11-13T14:00"
    assert roll("2025-12-30 14:00:05.123", DAYS) == "2026-11-13 14:00:05.123"
    assert roll("2025-12-30T14:00:00+00:00", DAYS) == "2026-11-13T14:00:00+00:00"
    assert roll(date(2025, 12, 30), DAYS) == date(2026, 11, 13)
    assert roll(datetime(2025, 12, 30, 6), DAYS) == datetime(2026, 11, 13, 6)
    for kept in ("OKLOG-2024-01-01-00295", "WT-OK-J01-01-2024-01-19", "set during the 2025-04-15 shut-in", "2025",
                 "20250415", "today-1d", 2025, None, ""):
        assert roll(kept, DAYS) == kept
    assert roll("2025-12-30", 0) == "2025-12-30"


def test_a_facility_that_does_not_roll_reads_as_written(monkeypatch):
    on(monkeypatch, TODAY, anchor=None)
    assert relative_dates.offset() == 0
    assert place({"since": "2024-03-01", "at": "today-1d"}) == {"since": "2024-03-01", "at": "2026-11-13"}


def test_hand_written_settings_move_with_the_data(monkeypatch):
    on(monkeypatch, TODAY)
    assert relative_dates.offset() == DAYS
    assert place({"since": "2024-03-01", "until": "2024-04-01T00:00", "at": "today-1d"}) == {
        "since": "2025-01-13", "until": "2025-02-13T00:00", "at": "2026-11-13"}  # a relative date isn't moved twice


def test_only_a_rolling_facility_closes_just_its_latest_day():
    last = date(2026, 11, 13)
    assert daily_report.recheck_days(last, rolling=True) == [last]
    assert len(daily_report.recheck_days(last)) == 7


def test_the_process_model_and_instrument_faults_move_on(monkeypatch, okan_data):
    as_written = process_model.read(okan_data)
    on(monkeypatch, TODAY)
    rolled = process_model.read(okan_data)
    e0, e1 = as_written["events"][0], rolled["events"][0]
    assert datetime.fromisoformat(e1["start"]) - datetime.fromisoformat(e0["start"]) == timedelta(days=DAYS)
    faults = json.loads((okan_data / "instrument_faults.json").read_text())["faults"]
    point = faults[0]["point"]
    a = datetime.fromisoformat(faults[0]["start"]) + timedelta(days=DAYS)
    assert instruments.load(okan_data).touches(point, a, a + timedelta(minutes=10))


@pytest.fixture(scope="module")
def okan(okan_db, tmp_path_factory):
    """Its own copy of the exports, so the fingerprints it records are its own."""
    data = tmp_path_factory.mktemp("rolling") / "data"
    shutil.copytree(okan_db.data, data)
    return okan_db.conn, data


def _latest_day(conn) -> str:
    return conn.execute("select max(payload->>'report_date') as d from source_record "
                        "where source = 'ec' and record_type = 'daily_report'").fetchone()["d"]


def test_read_today_the_latest_data_day_is_yesterday(okan, monkeypatch):
    conn, data = okan
    first_log = next(csv.DictReader((data / "oplogs.csv").open()))
    on(monkeypatch, TODAY)
    ingest.ingest(conn, data)
    conn.execute("delete from source_record_history")
    conn.commit()
    assert _latest_day(conn) == "2026-11-13"
    log = conn.execute("select payload from source_record where source = 'oplog' and source_key = %s",
                       (first_log["entry_id"],)).fetchone()["payload"]  # its key is as written...
    assert log["timestamp"] == roll(first_log["timestamp"], DAYS) and log["text"] == first_log["text"]  # ...its text too
    assert ingest.ingest(conn, data)["updated"] == 0  # read again the same day: nothing to do
    pipeline.rebuild(conn, PACK)
    pipeline.follow_register(conn, PACK)
    conn.commit()


def test_read_the_next_day_every_date_moves_on_without_daily_history(okan, monkeypatch):
    conn, data = okan
    plans = conn.execute("select count(*) as n from plan_item where source = 'sap_plan'").fetchone()["n"]
    on(monkeypatch, TODAY + timedelta(days=1))
    out = ingest.ingest(conn, data)
    assert out["updated"] > 10_000 and out["inserted"] == out["removed"] == 1  # a day on, a day off the start
    assert _latest_day(conn) == "2026-11-14"
    history = conn.execute("select change, count(*) as n from source_record_history group by 1").fetchall()
    assert [(r["change"], r["n"]) for r in history] == [("removed", 1)]
    pipeline.rebuild(conn, PACK)
    planned = pipeline.follow_register(conn, PACK)["plan"]["maintenance_plans"]
    assert planned["added"] == planned["rolled_forward"] == 0  # a plan's due date moving with the data isn't new work
    assert conn.execute("select count(*) as n from plan_item where source = 'sap_plan'").fetchone()["n"] == plans
    conn.commit()
