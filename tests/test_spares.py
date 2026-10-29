"""Spare parts: SAP materials and equipment BOMs, linked to the manuals that name them."""

import os
import tempfile

import pytest

from fakeplant import offshore
from fuzer import db, ingest, pipeline, queries
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


def test_spares_for_a_tag_and_its_model(conn):
    tag = queries.resolve_ref(conn, PACK, "20-P-1001B")
    parts = queries.spares(conn, tag)
    assert len(parts) == 3 and {p["mpn"].rsplit("-", 1)[1] for p in parts} == {"SK1", "BS2", "GS3"}
    pump = conn.execute("select src from edge where dst = %s and rel = 'installed_at' and valid_to is null",
                        (tag,)).fetchone()["src"]
    model = conn.execute("select dst from edge where src = %s and rel = 'instance_of'", (pump,)).fetchone()["dst"]
    by_model = queries.spares(conn, model)
    assert {p["fuzer_id"] for p in by_model} == {p["fuzer_id"] for p in parts}
    assert max(p["used_by"] for p in by_model) >= 1


def test_manual_sections_link_part_numbers(conn):
    parts = queries.spares(conn, queries.resolve_ref(conn, PACK, "20-P-1001B"))
    assert all(p["documents"] for p in parts)  # the manual's Spare parts section names each one


def test_out_of_stock_is_visible(conn):
    stocks = [r["v"] for r in conn.execute(
        "select (g.value #>> '{}')::numeric as v from golden g join entity e using (fuzer_id) "
        "where e.kind = 'material' and g.field = 'stock'")]
    assert 0 in stocks and any(s > 0 for s in stocks)
