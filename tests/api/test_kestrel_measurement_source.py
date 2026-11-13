"""Kestrel's gas measurement system, added from the Sources page alone (#457 follow-up): a source type Fuzer has no
built-in mapping for. The Add a source calls, with the suggested mapping accepted as offered, land each meter's daily
volume, energy and heating value on the plant tag or well it measures, and yesterday's compressor trip shows as a
short day on the sales meter."""

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, clock, db, settings
from fuzer.core import pack as packs

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_measurement"


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_gas_measurement_is_onboarded_from_the_ui(monkeypatch):
    from fuzer import api
    from fuzer.ops import pipeline
    from fuzer.sources import ingest

    with db.connect(autocommit=True, url=db.database_url()) as a:
        a.execute(f'drop database if exists "{NAME}" with (force)')
        a.execute(f'create database "{NAME}"')
    url = make_conninfo(db.database_url(), dbname=NAME)
    monkeypatch.setenv("FUZER_DATABASE_URL", url)
    monkeypatch.delenv("FUZER_PROJECT", raising=False)
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            first = bundles.init(conn, KESTREL, by="test")
            pack = packs.current_pack(conn)
            ingest.ingest(conn, Path(first["data"]))
            pipeline.rebuild(conn, pack)
            conn.commit()

            with TestClient(api.app) as client:
                ok(client.post("/sources/systems", json={"name": "measurement", "values": {"owner": "ana"}, "by": "ana"}))
                c = ok(client.post("/sources/systems/measurement/connections", json={
                    "name": "export", "type": "folder", "config": {"path": str(KESTREL / "measurement")}, "by": "ana"}))
                assert ok(client.post(f"/sources/connections/{c['id']}/test"))["ok"]
                p = ok(client.get(f"/sources/connections/{c['id']}/tables/daily_volumes"))
                assert p["date_hint"] is None and "shape: measurements" in p["suggested"]["yaml"]
                form = ok(client.post("/sources/mapper/form", json={"yaml": p["suggested"]["yaml"]}))
                assert form["warnings"] == [], form["warnings"]
                out = ok(client.post(f"/sources/connections/{c['id']}/tables/daily_volumes",
                                     json={"yaml": p["suggested"]["yaml"], "values": {}, "by": "ana"}))
                assert out["load"]["loaded"] == ["daily_volumes"] and out["load"]["inserted"] == 300

            pipeline.rebuild(conn, pack)
            got = {(r["key"], r["quantity"]): r["n"] for r in conn.execute(
                """
                select k.key, m.quantity, count(*) as n from measurement m
                join entity_key k on k.fuzer_id = m.fuzer_id and k.kind in ('tag', 'well')
                where m.quantity in ('volume', 'energy', 'hhv', 'flow_hours') group by 1, 2
                """)}
            for thing in ("A1-FT-1002", "C1-FT-3005", "H1-FT-8010", "KR 12-03H", "KR 12-04H"):
                assert got[(thing, "volume")] == 60 and got[(thing, "hhv")] == 60
            yesterday, before = (clock.today() - timedelta(days=d) for d in (1, 2))
            sales = {r["at"].date(): r["value"] for r in conn.execute(
                """
                select m.at, m.value from measurement m join entity_key k on k.fuzer_id = m.fuzer_id
                where k.key = 'H1-FT-8010' and m.quantity = 'volume' and m.at::date >= %s
                """, (before,))}
            assert sales[yesterday] < 0.9 * sales[before]  # the trip's lost sales gas, in the measurement system too
    finally:
        packs.clear_cache()
        settings._cache.clear()
        settings._first.clear()
        with db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres")) as a:
            a.execute(f'drop database if exists "{NAME}" with (force)')
