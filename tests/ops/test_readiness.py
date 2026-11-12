"""Ready to dispatch (P5.5a, #681): a job's spares from its equipment's BOM, stock and purchase orders, on Kestrel
and Okan, read from the records and never from the equipment type."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs
from fuzer.ops import readiness

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_readiness"


@pytest.fixture(scope="module")
def kestrel():
    from fuzer.ops import pipeline
    from fuzer.sources import ingest
    from fuzer.workflow import engine

    mp = pytest.MonkeyPatch()
    with db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres")) as a:
        a.execute(f'drop database if exists "{NAME}" with (force)')
        a.execute(f'create database "{NAME}"')
    url = make_conninfo(db.database_url(), dbname=NAME)
    mp.setenv("FUZER_DATABASE_URL", url)
    mp.delenv("FUZER_PROJECT", raising=False)
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()
    with db.connect(url=url) as conn:
        db.init_schema(conn)
        first = bundles.init(conn, KESTREL, by="test")
        mp.setenv("FUZER_DATA_DIR", str(first["data"]))
        pack = packs.current_pack(conn)
        ingest.ingest(conn, Path(first["data"]))
        pipeline.rebuild(conn, pack)
        engine.load_defaults(conn)
        conn.commit()
        yield conn, pack
    mp.undo()
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def test_a_compressor_short_of_valves_waits_on_its_order_and_its_packing_has_none(kestrel):
    from fuzer.graph import queries

    conn, pack = kestrel
    k2001 = queries.resolve_ref(conn, pack, "B1-K-2001")
    out = readiness.check(conn, [k2001])
    assert out["ready"] is False
    spares = out["checks"][0]
    valves = next(p for p in spares["parts"] if p["label"] == "MAT-K2001-VLV")
    assert (valves["needed"], valves["stock"], valves["short"]) == (4, 2, 2)
    assert valves["orders"] == [{"po": "4500007101", "quantity": 2, "due": (date.today() + timedelta(days=3)).isoformat()}]
    rings = next(p for p in spares["parts"] if p["label"] == "MAT-K2001-RNG")
    assert rings["short"] == 0  # 8 in stock, 8 needed
    assert any("Compressor valve assembly" in b and "on order (4500007101)" in b for b in out["blockers"])
    assert any("Rod packing set" in b and "nothing on order" in b for b in out["blockers"])
    assert len(spares["parts"]) == 3


def test_equipment_with_its_parts_in_stock_is_ready_and_one_with_no_bom_says_so(kestrel):
    from fuzer.graph import queries

    conn, pack = kestrel
    fan = queries.resolve_ref(conn, pack, "B1-E-2010")  # 2 belts needed, 3 in stock
    assert readiness.check(conn, [fan])["ready"] is True
    out = readiness.check(conn, ["fz-nothing-here"])
    assert out["ready"] is True and out["checks"][0]["checked"] is False
    assert out["checks"][0]["note"].startswith("No BOM")


@pytest.fixture
def client(kestrel, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from fuzer.api import ops as ops_api
    from fuzer.core.exceptions import TYPES, ExceptionType

    monkeypatch.setitem(TYPES, "walkdown_check", ExceptionType("walkdown_check", "Walkdown check",
                                                               audience=("ioc", "field")))
    app = FastAPI()
    app.include_router(ops_api.router)
    app.dependency_overrides[ops_api.get_conn] = lambda: kestrel[0]
    return TestClient(app)


def job(conn, key: str, subject: str) -> int:
    from fuzer.core.exceptions import raise_exception

    return raise_exception(conn, "walkdown_check", key, f"Look at {key}", "olga", refs=[subject])["id"]


def test_a_blocked_job_goes_only_with_a_reason_and_the_reason_is_on_its_case(kestrel, client):
    from fuzer.graph import queries

    conn, pack = kestrel
    e = job(conn, "K-2001 valves", queries.resolve_ref(conn, pack, "B1-K-2001"))
    ready = client.get(f"/exceptions/{e}/readiness").json()
    assert ready["ready"] is False and any("on order (4500007101)" in b for b in ready["blockers"])
    r = client.post(f"/exceptions/{e}/dispatch", json={"by": "ioc-ann", "to": "fred"})
    assert r.status_code == 409 and "Not ready to dispatch: Spares: " in r.text and "Say why" in r.text
    r = client.post(f"/exceptions/{e}/dispatch", json={"by": "ioc-ann", "to": "fred",
                                                     "override": "inspect only, no parts needed today"})
    assert r.status_code == 200, r.text
    item = r.json()["item_id"]
    said = [c["body"] for c in conn.execute(
        "select data->>'text' as body from work_event where item_id = %s and kind = 'commented'", (item,))]
    assert any(s.startswith("Sent though not ready (Spares: ") and s.endswith("Why: inspect only, no parts needed today")
               for s in said), said
    fan = job(conn, "E-2010 belts", queries.resolve_ref(conn, pack, "B1-E-2010"))
    assert client.post(f"/exceptions/{fan}/dispatch", json={"by": "ioc-ann", "to": "fred"}).status_code == 200


def test_okan_spares_come_from_its_own_records(okan_db):
    conn = okan_db.conn
    short = conn.execute(
        "select b.src from edge b join golden s on s.fuzer_id = b.dst and s.field = 'stock' "
        "where b.rel = 'uses_part' and (s.value #>> '{}')::numeric = 0 limit 1").fetchone()["src"]
    out = readiness.check(conn, [short])
    assert out["ready"] is False and out["checks"][0]["checked"]
    ordered = conn.execute("select count(*) as n from entity where kind = 'purchase_order'").fetchone()["n"]
    assert ordered > 0
