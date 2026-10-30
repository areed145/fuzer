"""PI Web API and ADX connectors against the fake historian: the point list and AF tree load without
files and match the file exports, the ADX catalog puts a pointer on each timeseries entity, and process
values around an incident come from PI or ADX and show the lead-in."""

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fakeplant import historian, offshore
from fuzer import adx, assistant, connectors, db, ingest, jobs, pi_web_api, pipeline, queries, timeseries
from fuzer.pack import load_pack

PACK = load_pack()


@pytest.fixture(scope="module")
def data():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    out = tempfile.mkdtemp(prefix="fzdata")
    offshore.generate(out)
    return out


@pytest.fixture(scope="module")
def server(data):
    return TestClient(historian.app(data))


@pytest.fixture
def pi(server):
    return pi_web_api.Client("http://testserver/piwebapi", http=server)


@pytest.fixture
def kusto(server):
    return adx.Client("http://testserver", "OSB", http=server)


@pytest.fixture
def live(monkeypatch, server):
    """Configure both live systems, talking to the fake historian."""
    monkeypatch.setenv("FUZER_PI_WEB_API_URL", "http://testserver/piwebapi")
    monkeypatch.setenv("FUZER_ADX_URL", "http://testserver")
    monkeypatch.setenv("FUZER_ADX_DATABASE", "OSB")
    real_pi, real_adx = pi_web_api.Client, adx.Client
    monkeypatch.setattr(pi_web_api, "Client", lambda *a, **k: real_pi(*a, **(k | {"http": server})))
    monkeypatch.setattr(adx, "Client", lambda *a, **k: real_adx(*a, **(k | {"http": server})))


def test_pi_points_match_the_file_export(data, pi):
    assert list(pi_web_api.PiPoints(pi).rows()) == list(connectors.find(Path(data), "pi_points").rows())


def test_pi_af_matches_the_file_export(data, pi):
    file = ingest.dataset(Path(data), "pi_af")
    by_key = lambda recs: {k: p for _, _, k, p in recs}
    assert by_key(pi_web_api.PiAf(pi).records()) == by_key(file.records())


def test_points_page_and_batch(pi, monkeypatch):
    monkeypatch.setattr(pi_web_api, "PAGE", 10)
    monkeypatch.setattr(pi_web_api, "BATCH", 7)
    rows = list(pi_web_api.PiPoints(pi).rows())
    assert len(rows) == 88 and len({r["tag"] for r in rows}) == 88
    assert all(r["pointsource"] == "OPC" for r in rows)


def test_adx_catalog_and_kql(kusto):
    cat = list(adx.SeriesCatalog(kusto).rows())
    vib = next(r for r in cat if r["pi_point"] == "OSB.20VT1001B.PV")
    assert vib["values_table"] == "PiValues" and vib["cluster"] == "http://testserver"
    rows = kusto.query(f"PiValues | where SeriesId == {adx.quote(vib['series_id'])} and Timestamp between "
                       "(datetime(2024-01-01T00:00) .. datetime(2024-01-01T02:00)) "
                       "| summarize Value = avg(Value), n = count() by bin(Timestamp, 30m) | order by Timestamp asc")
    assert [r["Timestamp"] for r in rows][:2] == ["2024-01-01T00:00:00Z", "2024-01-01T00:30:00Z"]
    assert rows[0]["n"] == 6  # 5-minute raw samples
    with pytest.raises(RuntimeError, match="need SeriesId"):
        kusto.query("PiValues | take 10")  # no full scans
    with pytest.raises(RuntimeError, match="unsupported operator"):
        kusto.query("SeriesCatalog | extend x = 1")


def test_live_ingest_loads_pi_and_adx_and_values_come_from_them(data, live):
    with db.connect() as conn:
        db.reset(conn)
        files = {n: c for n in ingest.export_names() if (c := ingest.dataset(Path(data), n))}
        assert files["pi_points"].kind == "pi_web_api" and files["pi_af"].kind == "pi_web_api"
        assert files["adx_series"].kind == "adx"
        stats = ingest.ingest(conn, data)
        assert {"pi_points", "pi_af", "adx_series"} <= set(stats["loaded"]) and not stats["held"]
        pipeline.rebuild(conn, PACK)

        # one timeseries entity per PI point, with the ADX pointer merged onto it
        vib = conn.execute("select fuzer_id from entity_key where kind = 'timeseries' and key = 'OSB.20VT1001B.PV'"
                           ).fetchone()["fuzer_id"]
        ent = queries.entity(conn, vib)
        assert ent["golden"]["uom"]["value"] == "mm/s"
        assert ent["golden"]["adx_table"]["value"] == "PiValues" and ent["golden"]["adx_series_id"]["value"]
        assert conn.execute("select count(*) as n from entity where kind = 'timeseries' and retired_at is null "
                            "and merged_into is null").fetchone()["n"] == 88

        # a second load from the same server changes nothing
        again = ingest.ingest(conn, data, ["pi_points", "pi_af", "adx_series"])
        assert again["unchanged"] == ["pi_points", "pi_af", "adx_series"]

        inc = next(i for i in queries.incidents(conn, queries.resolve_ref(conn, PACK, "23-K-1001A"))["items"]
                   if i["start"] == "2024-02-03")
        for name in ("adx", "pi"):
            os.environ["FUZER_TIMESERIES"] = name
            try:
                out = timeseries.around(conn, inc["id"])
            finally:
                os.environ.pop("FUZER_TIMESERIES")
            assert out["provider"] == name and not out.get("errors")
            assert out["centre"] == "2024-02-03T14:00"  # the trip's downtime start, not midnight
            lead = {s["point"]: s["lead_in"]["change_pct"] for s in out["series"]}
            assert all(len(s["values"]) == 145 for s in out["series"])
            assert lead["OSB:23-TT-1001A.DACA.PV"] > 30  # discharge temperature climbs into the trip
            assert lead["OSB:23-PT-1001A.DACA.PV"] < -20  # and pressure falls away

        os.environ["FUZER_TIMESERIES"] = "adx"
        try:
            one = timeseries.around(conn, vib)  # a timeseries entity's own page
            brief = assistant.brief(conn, PACK, inc["id"])
        finally:
            os.environ.pop("FUZER_TIMESERIES")
        assert [s["point"] for s in one["series"]] == ["OSB.20VT1001B.PV"] and one["provider"] == "adx"
        pv = brief["process_values"]
        assert pv["provider"] == "adx" and pv["note"] is None
        assert any(s["lead_in"]["change_pct"] > 30 for s in pv["series"] if "lead_in" in s)


def test_live_datasets_skip_the_file_sensor(data, live):
    assert jobs.export_signature(Path(data), "pi_points") is None
    assert jobs.export_signature(Path(data), "sap_equipment")


def test_unreachable_point_is_reported_not_fatal(data):
    class Broken:
        name = "pi"

        def series(self, s, start, end, step):
            if "VT" in s["point"]:
                raise RuntimeError("PI Point not found")
            return timeseries.SyntheticProvider().series(s, start, end, step)

    with db.connect() as conn:
        db.reset(conn)
        ingest.ingest(conn, data)
        pipeline.rebuild(conn, PACK)
        unit = queries.resolve_ref(conn, PACK, "20-P-1001B")
        out = timeseries.around(conn, unit, "2024-01-01T18:00", src=Broken())
    assert out["series"] and out["errors"] and all("VT" not in s["point"] for s in out["series"])
