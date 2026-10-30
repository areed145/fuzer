"""Process values around an event through the (synthetic) time-series provider."""

import os
import tempfile
from datetime import datetime

import pytest

from fakeplant import offshore
from fuzer import db, ingest, pipeline, queries, timeseries
from fuzer.pack import load_pack

PACK = load_pack()


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    offshore.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c


def test_synthetic_values_are_deterministic_and_show_upsets():
    p = timeseries.SyntheticProvider()
    start, end, upset = datetime(2024, 2, 1), datetime(2024, 2, 4), datetime(2024, 2, 3, 14)
    a = p.values("OSB.23TT1001A.PV", "degC", start, end, 30, [upset])
    assert a == p.values("OSB.23TT1001A.PV", "degC", start, end, 30, [upset])
    quiet = [v for t, v in a if t < "2024-02-02"]
    peak = max(v for t, v in a if "2024-02-03T10" <= t <= "2024-02-03T14")
    assert peak > max(quiet) * 1.2  # temperature climbs into the trip


def test_values_around_an_incident(conn):
    inc = next(i for i in queries.incidents(conn, queries.resolve_ref(conn, PACK, "23-K-1001A"))["items"]
               if i["start"] == "2024-02-03")
    out = timeseries.around(conn, inc["id"])
    assert out["upsets"] and out["series"]
    points = {s["point"] for s in out["series"]}
    assert "OSB:23-TT-1001A.DACA.PV" in points  # the unit's own instruments
    assert all(len(s["values"]) == 145 for s in out["series"])  # 72 h at 30 min


def test_unknown_or_unconfigured_provider_is_explicit(monkeypatch):
    monkeypatch.setenv("FUZER_TIMESERIES", "osisoft")
    with pytest.raises(ValueError, match="synthetic, pi or adx"):
        timeseries.provider()
    monkeypatch.setenv("FUZER_TIMESERIES", "pi")
    monkeypatch.delenv("FUZER_PI_WEB_API_URL", raising=False)
    with pytest.raises(ValueError, match="FUZER_PI_WEB_API_URL"):
        timeseries.provider()


def test_values_of_one_timeseries(conn):
    ts = conn.execute("select fuzer_id from entity_key where kind = 'timeseries' and key = 'OSB:23-TT-1001A.DACA.PV'"
                      ).fetchone()["fuzer_id"]
    out = timeseries.around(conn, ts)
    assert [s["point"] for s in out["series"]] == ["OSB:23-TT-1001A.DACA.PV"]
    # centred on the latest incident on the compressor it measures, shaded
    latest = queries.incidents(conn, queries.resolve_ref(conn, PACK, "23-K-1001A"))["items"][0]
    assert out["centre"][:10] == latest["start"] and out["centre"] in out["upsets"]
    week = timeseries.around(conn, ts, "2024-02-03T14:00", hours_before=144, hours_after=24)
    assert week["step_minutes"] == 120 and len(week["series"][0]["values"]) == 85


def test_auto_step():
    assert [timeseries.auto_step(h) for h in (24, 72, 168, 720)] == [10, 30, 120, 360]
