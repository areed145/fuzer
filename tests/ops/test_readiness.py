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


def test_okan_spares_come_from_its_own_records(okan_db):
    conn = okan_db.conn
    short = conn.execute(
        "select b.src from edge b join golden s on s.fuzer_id = b.dst and s.field = 'stock' "
        "where b.rel = 'uses_part' and (s.value #>> '{}')::numeric = 0 limit 1").fetchone()["src"]
    out = readiness.check(conn, [short])
    assert out["ready"] is False and out["checks"][0]["checked"]
    ordered = conn.execute("select count(*) as n from entity where kind = 'purchase_order'").fetchone()["n"]
    assert ordered > 0
