"""Event lanes (P5.13a): what happened on a unit in a window, one lane per kind, each event naming its record."""

import pytest

from fuzer.core.exceptions import raise_exception
from fuzer.core.findings import Finding
from fuzer.ops import timeline


@pytest.fixture(scope="module")
def okan(okan_db):
    return okan_db.conn


def _trip(conn):
    """A downtime record a compressor caused: found by its links and class, not by name."""
    return conn.execute(
        """
        select d.fuzer_id as id, c.dst as unit, s.value #>> '{}' as start, e.value #>> '{}' as "end"
        from entity d join edge c on c.src = d.fuzer_id and c.rel = 'caused_by'
        join edge k on k.src = c.dst and k.rel = 'classified_as' join entity_label kl on kl.fuzer_id = k.dst
        join golden s on s.fuzer_id = d.fuzer_id and s.field = 'start'
        join golden e on e.fuzer_id = d.fuzer_id and e.field = 'end'
        where d.kind = 'downtime' and kl.label like 'ISO14224:CO%%' and s.value #>> '{}' like '%%T%%'
        order by 3 limit 1
        """).fetchone()


def _shift(iso, hours):
    from datetime import datetime, timedelta
    return (datetime.fromisoformat(iso) + timedelta(hours=hours)).isoformat(timespec="minutes")


def test_a_compressor_trip_shows_in_its_lanes(okan):
    trip = _trip(okan)
    assert trip, "Okan has a timed compressor downtime"
    from fuzer.analysis import trips

    case = raise_exception(okan, trips.TYPE, "TIMELINE-TRIP", "Compressor trip", "test", refs=[trip["unit"]],
                           finding=Finding("test", "deterministic", start=trip["start"]))
    out = timeline.lanes(okan, trip["unit"], _shift(trip["start"], -24), _shift(trip["start"], 24))
    lanes = {lane["key"]: lane["events"] for lane in out["lanes"]}
    assert [lane["key"] for lane in out["lanes"]] == ["alarms", "log", "cases", "maintenance", "downtime"]
    assert trip["id"] in [e["id"] for e in lanes["downtime"]]
    assert case["item_id"] in [e["item_id"] for e in lanes["cases"]]
    assert lanes["log"], "the trip was logged"
    for events in lanes.values():
        assert [e["start"] for e in events] == sorted(e["start"] for e in events)
        for e in events:
            assert e["kind"] and e["start"] and (e["id"] or e.get("item_id"))
    okan.rollback()


def test_records_outside_the_window_are_left_out(okan):
    trip = _trip(okan)
    out = timeline.lanes(okan, trip["unit"], _shift(trip["start"], -96), _shift(trip["start"], -72))
    assert trip["id"] not in [e["id"] for lane in out["lanes"] for e in lane["events"]]


def test_an_instrument_reads_on_its_equipment(okan):
    trip = _trip(okan)
    instrument = okan.execute("select src from edge where rel = 'part_of' and dst = %s order by 1 limit 1",
                              (trip["unit"],)).fetchone()["src"]
    out = timeline.lanes(okan, instrument, _shift(trip["start"], -24), _shift(trip["start"], 24))
    assert out["unit"] == trip["unit"]


def test_unknown_entity_and_a_backwards_window(okan):
    assert timeline.lanes(okan, "fz-nope", "2025-01-01", "2025-01-02") == {"error": "unknown entity"}
    trip = _trip(okan)
    assert "error" in timeline.lanes(okan, trip["unit"], "2025-01-02", "2025-01-01")
