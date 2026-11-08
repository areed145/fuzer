"""Process values around an event through the (synthetic) time-series provider."""

from datetime import datetime

import pytest

from fuzer.analysis import values
from fuzer.core.pack import load_pack
from fuzer.graph import queries
from fuzer.sources import timeseries

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    yield c


def test_synthetic_values_are_deterministic_and_show_upsets(okan_data):
    p = timeseries.SyntheticProvider(okan_data)
    start, end = datetime(2024, 1, 20), datetime(2024, 3, 9)
    point = "OK_GGCP:20-VT-2200B.PV"
    a = p.values(point, "mm/s", start, end, 30, [])
    assert a == p.values(point, "mm/s", start, end, 30, [])
    quiet = [v for t, v in a if t < "2024-02-20"]
    peak = max(v for t, v in a if "2024-03-08T10" <= t <= "2024-03-08T14")
    assert peak > max(quiet) * 1.2  # vibration climbs into the trip


def test_values_around_an_incident(conn):
    inc = next(i for i in queries.incidents(conn, queries.resolve_ref(conn, PACK, "20-K-2200B"))["items"]
               if i["start"] == "2024-03-14")
    out = values.around(conn, inc["id"])
    assert out["upsets"] and out["series"]
    points = {s["point"] for s in out["series"]}
    assert "OK_GGCP:20-VT-2200B.PV" in points  # the unit's own instruments
    assert all(len(s["values"]) == 145 for s in out["series"])  # 72 h at 30 min


def test_unknown_or_unconfigured_provider_is_explicit(monkeypatch):
    monkeypatch.setenv("FUZER_TIMESERIES", "osisoft")
    with pytest.raises(ValueError, match="synthetic, pi, adx or none"):
        timeseries.provider()
    monkeypatch.setenv("FUZER_TIMESERIES", "pi")
    monkeypatch.delenv("FUZER_PI_WEB_API_URL", raising=False)
    with pytest.raises(ValueError, match="pi_web_api connection"):
        timeseries.provider()


def test_values_of_one_timeseries(conn):
    ts = conn.execute("select fuzer_id from entity_key where kind = 'timeseries' and key = 'OK_GGCP:20-VT-2200B.PV'"
                      ).fetchone()["fuzer_id"]
    out = values.around(conn, ts)
    assert [s["point"] for s in out["series"]] == ["OK_GGCP:20-VT-2200B.PV"]
    # centred on the latest incident on the compressor it measures, shaded
    latest = queries.incidents(conn, queries.resolve_ref(conn, PACK, "20-K-2200B"))["items"][0]
    assert out["centre"][:10] == latest["start"] and out["centre"] in out["upsets"]
    week = values.around(conn, ts, "2024-02-03T14:00", hours_before=144, hours_after=24)
    assert week["step_minutes"] == 120 and len(week["series"][0]["values"]) == 85


def test_auto_step():
    assert [timeseries.auto_step(h) for h in (24, 72, 168, 720)] == [10, 30, 120, 360]


def test_direct_points_of_a_tag(conn):
    unit = queries.resolve_ref(conn, PACK, "20-K-2200B")
    vt = queries.resolve_ref(conn, PACK, "20-VT-2200B")
    whole = values.around(conn, unit)
    own = values.around(conn, vt, direct=True)
    assert {s["point"] for s in own["series"]} == {"OK_GGCP:20-VT-2200B.PV"}
    assert len(whole["series"]) > 1
    assert own["upsets"] and own["centre"] in own["upsets"]  # the latest incident on the compressor
