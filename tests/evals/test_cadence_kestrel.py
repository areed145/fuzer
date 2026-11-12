"""Cadence by criticality (P3.2, #757) on Kestrel: compressor trips run in the 5-minute tier, each run from where the
last one ended, so yesterday's trip on B1-K-2001 reaches the IOC's list on the first run after its data arrives,
while the drift check still runs daily. Its own module, as Kestrel's detectors read Kestrel's model."""

from datetime import datetime, timedelta

from fuzer.core import detectors, settings
from fuzer.core.pack import resolve
from fuzer.evals import loop
from fuzer.ops import cadence
from fuzer.sources.relative_dates import resolve_all
from fuzer.workflow import engine


def test_a_trip_reaches_the_list_within_ten_minutes_and_drifts_still_run_daily(kestrel_db):
    conn = kestrel_db
    engine.load_defaults(conn)
    loop._people(conn)
    conn.commit()
    tripped = datetime.fromisoformat(resolve_all({"t": "today-1d 03:10"})["t"])
    assert "compressor_trips" in [d.name for d in cadence.members(conn, "5min")]
    # the stop shows in the data from 03:20 (10-minute steps); the 5-minute tier's run that read to then saw only
    # the running before it, and the next run, at 03:25, reads on from there
    detectors.record(conn, "compressor_trips", detectors.RAN, found=0, since=tripped - timedelta(minutes=20),
                     until=tripped + timedelta(minutes=10))
    conn.commit()
    out = cadence.run_tier(conn, resolve(conn), "5min", now=tripped + timedelta(minutes=15))
    assert not out["failed"], out
    trip = conn.execute("select * from exception_record where type = 'machine_trip' and state = 'open'").fetchone()
    assert trip and trip["item_id"], out  # on the IOC's list, with its case, five minutes after the data shows it
    last = conn.execute("select since, until from detector_run where detector = 'compressor_trips' order by id desc "
                        "limit 1").fetchone()
    assert last["until"] == tripped + timedelta(minutes=15) and last["since"] == tripped - timedelta(minutes=20)
    cadence.run_tier(conn, resolve(conn), "5min", now=tripped + timedelta(minutes=20))  # read again: raised once
    assert conn.execute("select count(*) as n from exception_record where type = 'machine_trip'").fetchone()["n"] == 1

    drifts = detectors.get("drifts")
    assert drifts.window and cadence.tier(conn, drifts) == "daily"  # the SRE scan runs it, once a day
    assert "drifts" not in [d.name for tier in cadence.TIERS for d in cadence.members(conn, tier)]

    settings.set(conn, "detectors.cadence@records_gaps", "hourly", by="ana", reason="records come in all day")
    assert "records_gaps" in [d.name for d in cadence.members(conn, "hourly")]
    assert "records_gaps" not in [d.name for d in cadence.members(conn, "daily")]
    five = next(t for t in cadence.summary(conn) if t["tier"] == "5min")
    assert five["runs"] == 2 and five["found"] >= 1 and five["detectors"][0]["name"] == "compressor_trips"
    conn.commit()
