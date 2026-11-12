"""One case per event (P3.5, #691) on Kestrel, replayed in production order: the SRE scan and its hand-off, the trip
and machine health detectors, then the SAP sync. Its own module, as Kestrel's detectors must read Kestrel's model
(see test_loop_kestrel)."""

from fuzer.evals import loop
from fuzer.ops import around, exceptions
from fuzer.workflow import engine


def test_the_compressor_trip_is_one_case_with_its_alarm_log_detection_and_notification(kestrel_db):
    conn = kestrel_db
    engine.load_defaults(conn)
    loop._people(conn)
    conn.commit()
    arrive = loop.arrivals(conn)
    for kind in ("upset", "machine_trip", "machine_health", "sap_notification"):
        loop._bring(conn, arrive[kind])
        conn.commit()
    trip = conn.execute("select * from exception_record where type = 'machine_trip' and key like 'KRGP-B1@%'").fetchone()
    assert trip and trip["item_id"], "the trip on train B1 opened no case"
    on = exceptions.on_case(conn, trip["item_id"])
    assert {"machine_trip", "sap_notification"} <= {x["type"] for x in on}, on
    assert any("High vibration trip" in x["title"] for x in on if x["type"] == "sap_notification"), on

    ctx = around.context(conn, trip["item_id"])
    assert any(a["point"].endswith("VT2003.PV") and a["type"] in ("hi", "hihi") for a in ctx["alarms"]), ctx["alarms"]
    texts = [x["text"] for x in ctx["log"]]
    assert any("B1-K-2001 tripped on high vibration" in t for t in texts), texts
    assert not any("glycol circulation pump" in t.lower() for t in texts), texts  # another asset's entries stay off
