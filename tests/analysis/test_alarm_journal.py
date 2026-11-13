"""The DCS alarm and event journal (#859): fakeplant logs what the alarm summary would have shown, and the alarm
analyses read it where it covers the window, else derive alarms from the limits as before."""

import csv
from datetime import date, datetime
from pathlib import Path

import pytest

from fuzer.analysis import alarm_journal, alarms
from fuzer.core.pack import load_pack
from fuzer.sources import relative_dates

PACK = load_pack("okan")
ROOT = Path(__file__).resolve().parents[2]


def _journal(bundle: str) -> list[dict]:
    rows = list(csv.DictReader((ROOT / "fakeplant" / "bundles" / bundle / "exports" / "dcs_alarm_journal.csv").open()))
    return [{**r, "timestamp": relative_dates.resolve(r["timestamp"], date(2026, 11, 12))} for r in rows]


@pytest.mark.unit
def test_kestrels_journal_logs_yesterdays_trip_with_its_first_out():
    rows = _journal("kestrel")
    assert all(r["timestamp"] < "2026-11-13" for r in rows)  # written relative to the day it is read
    trip = [r for r in rows if r["event"] == "TRIPPED" and r["timestamp"].startswith("2026-11-11")]
    assert len(trip) == 1 and trip[0]["tag"] == "B1K2001" and trip[0]["comment"] == "First out: B1VT2003 HIHI"
    around = [r for r in rows if "2026-11-11T03:00" <= r["timestamp"].replace(" ", "T") <= "2026-11-11T04:00"
              and r["event"] == "ALARM"]
    assert {"H1FT8010", "B1PT2001"} <= {r["tag"] for r in around}  # sales gas and discharge pressure fall with it


@pytest.mark.unit
@pytest.mark.parametrize("bundle", ["kestrel", "harrier"])
def test_every_alarm_event_follows_its_alarm(bundle):
    on: dict[tuple, bool] = {}
    rows = sorted(_journal(bundle), key=lambda r: (r["timestamp"], r["event_id"]))
    for r in rows:
        k = (r["tag"], r["alarm"])
        if r["event"] == "ALARM":
            on[k] = True
        elif r["event"] == "RTN":
            on[k] = False
        elif r["event"] == "ACK":
            assert k in on or r["timestamp"] < rows[0]["timestamp"][:10] + "T23", r  # after it rang (ISA-18.2
            # allows acknowledging one that has already returned)
        elif r["event"] == "SHELVE":
            assert on.get(k, True), r  # shelved only while on (or on before the journal starts)
    assert sum(r["event"] == "SHELVE" for r in _journal(bundle))  # standing alarms get shelved


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


def test_the_analysis_reads_the_journal_where_it_covers_the_window(conn):
    first, last = alarm_journal.coverage(conn)
    assert first < datetime(2025, 9, 1, 1) and last > datetime(2025, 12, 31)
    a = alarms.alarm_analysis(conn, PACK, None, "2025-09-08T00:00", "2025-09-10T00:00")
    assert a["alarm_source"] == "journal" and "journal" in a["basis"]
    # the export surge drum's Lo set inside its normal range chatters, and is the worst bad actor by far
    assert any(c["point"].endswith("10LT2004.PV") for c in a["chattering"])
    assert a["bad_actors"][0]["point"].endswith("10LT2004.PV") and a["bad_actors"][0]["share"] > 0.5
    before = alarms.alarm_analysis(conn, PACK, None, "2025-06-23T00:00", "2025-06-25T00:00")
    assert before["alarm_source"] == "limits" and "no alarm journal" in before["basis"]
    conn.rollback()
