"""What a live facility's systems record about its live events (sources/live_rows): rows in the export's own columns,
each only once its time has come, a downtime row with the volume lost, notifications that are completed days later,
and a connector whose fingerprint moves when a new row falls due."""

import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from fuzer.sources import connectors, live_events, live_rows, process_model, relative_dates

KESTREL = Path(__file__).parents[2] / "fakeplant" / "bundles" / "kestrel" / "exports"


@pytest.fixture(autouse=True)
def live(monkeypatch):
    monkeypatch.delenv(live_events.ENV, raising=False)


def _trip():
    spec = process_model.read(KESTREL)
    trips = [e for e in spec["events"] if e.get("live") and e["kind"] == "trip"]
    return spec, trips[-1]


def test_a_trip_is_logged_raised_and_deferred_as_its_time_comes():
    spec, trip = _trip()
    start, end = datetime.fromisoformat(trip["start"]), datetime.fromisoformat(trip["end"])
    n = lambda name, at: [r for r in live_rows.rows(name, KESTREL, now=at, spec=spec)
                          if trip["live"] in r.get("comment", "") or relative_dates.resolve(
                              r.get("start") or r.get("timestamp") or r.get("created") or "")[:10] == start.date().isoformat()]
    before = start - timedelta(minutes=1)
    assert not [r for r in live_rows.rows("oplogs", KESTREL, now=before, spec=spec) if "tripped" in r["text"]
                and relative_dates.resolve(r["timestamp"]) >= start.isoformat(sep=" ", timespec="minutes")]
    logs = [r for r in n("oplogs", end + timedelta(hours=1)) if "tripped" in r["text"] or "restarted" in r["text"]]
    assert logs and set(logs[0]) == {"entry_id", "timestamp", "facility", "author", "text"}
    down = [r for r in n("ec_downtime", end + timedelta(hours=3)) if r["event_id"].startswith("KRDT-LIVE-")]
    assert down and float(down[-1]["lost_gas_mscf"]) > 0 and down[-1]["object_code"] == "KRGP"
    assert not [r for r in n("ec_downtime", end) if r["event_id"] == down[-1]["event_id"]]  # EC has it after the lag
    notes = live_rows.rows("sap_notifications", KESTREL, now=end + timedelta(hours=3), spec=spec)
    assert any(r["notif_type"] == "M2" and r["floc"].startswith("KRGP-") and r["equnr"] for r in notes)
    old = live_rows.rows("sap_notifications", KESTREL, now=end + timedelta(days=11), spec=spec)
    assert {r["status"] for r in old if r["qmnum"] in {x["qmnum"] for x in notes}} == {"NOCO"}  # completed since


def test_the_connector_fingerprint_moves_when_a_row_falls_due(tmp_path, monkeypatch):
    folder = tmp_path / "exports"
    shutil.copytree(KESTREL, folder, ignore=shutil.ignore_patterns("pid", "documents", "well_logs"))
    c = connectors.CsvTable(folder / "oplogs.csv")
    _, trip = _trip()
    real = live_rows.rows
    at = {"now": datetime.fromisoformat(trip["start"]) - timedelta(minutes=1)}
    monkeypatch.setattr(live_rows, "rows", lambda name, d, now=None, spec=None: real(name, d, now=at["now"]))
    one = live_rows.wrap("oplogs", c)
    at["now"] = datetime.fromisoformat(trip["start"]) + timedelta(minutes=10)
    two = live_rows.wrap("oplogs", c)
    assert one.fingerprint() != two.fingerprint()
    assert sum(1 for _ in two.rows()) == sum(1 for _ in c.rows()) + two.live_rows
    monkeypatch.setenv(live_events.ENV, "off")
    assert live_rows.wrap("oplogs", c) is c  # off: the export as it is


def test_only_a_live_facilitys_datasets_get_rows():
    assert live_rows.rows("sap_equipment", KESTREL) == []
    assert live_rows.wrap("sap_equipment", object()) is not None


def test_a_live_facilitys_rows_land_through_ingest_beside_its_own(okan_db):
    """The live rows go through the same load as the export's own: keyed, mapped, versioned, and rolled dates left
    alone (they are relative)."""
    from fuzer.sources import ingest

    conn = okan_db.conn
    report = ingest.ingest(conn, okan_db.data, ["oplogs", "ec_downtime", "sap_notifications"], trigger="test")
    conn.commit()
    assert set(report["loaded"]) == {"oplogs", "ec_downtime", "sap_notifications"}
    got = {r["record_type"]: r["n"] for r in conn.execute(
        "select record_type, count(*) as n from source_record where source_key like '%LIVE-%' or "
        "(record_type = 'notification' and source_key like '9%%') group by 1")}
    assert got.get("downtime") and sum(got.values()) > got["downtime"], got
    latest = conn.execute("select max(payload->>'start') as s from source_record where source_key like 'OKDT-LIVE-%'"
                          ).fetchone()["s"]
    today = relative_dates.plant_today()
    assert latest and today - timedelta(days=live_events.LOOKBACK_DAYS) <= datetime.fromisoformat(latest).date() <= today
