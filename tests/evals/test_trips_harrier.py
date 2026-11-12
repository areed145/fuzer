"""Gate C (P8.4): machine trips beyond compressors. Harrier's mainline pump A shook for hours and tripped on
vibration yesterday; the pump trip detector diagnoses it from the pump's own points, as a compressor's is, and raises
it as a machine trip for the IOC."""

from datetime import datetime, timedelta

from fuzer.analysis import trips
from fuzer.core.pack import current_pack


def test_a_mainline_pump_trip_reaches_the_ioc_with_its_cause(harrier_db):
    conn, pack = harrier_db, current_pack(harrier_db)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    out = trips.pump_scan(conn, pack, (today - timedelta(days=2)).isoformat(), today.isoformat(), by="test")
    assert out["trains"] >= 8 and out["trips"] == 1 and not out["errors"], out
    raised = out["raised"][0]
    assert "A1-P-1003A" in raised["title"] and "high vibration" in raised["title"]
    row = conn.execute("select type from exception_record where id = %s", (raised["exception_id"],)).fetchone()
    assert row["type"] == "machine_trip"
    assert trips.compressor_trains(conn) and not set(trips.compressor_trains(conn)) & set(trips.pump_trains(conn))
