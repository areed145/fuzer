"""What an asset is, for the top of its page: the unit installed at a position, its maker, model and serial, class,
criticality, location and manual."""

import pytest

from fuzer.core.pack import load_pack
from fuzer.graph import queries

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    yield okan_db.conn


def test_a_position_shows_the_unit_installed_there(conn):
    p = queries.profile(conn, queries.resolve_ref(conn, PACK, "20-K-2300A"))
    assert (p["manufacturer"], p["model"]) == ("Solar Turbines", "C40")
    assert p["serial"] and p["installed"]["label"] and p["installed"]["since"]
    assert p["equipment_model"]["label"] == "SOLAR TURBINES C40"
    assert p["equipment_class"]["description"] == "Centrifugal compressors"
    assert [x["kind"] for x in p["location"]] == ["area", "facility"]
    assert any("C40" in (d["title"] or "") for d in p["manuals"])


def test_a_unit_shows_where_it_is_installed(conn):
    tag = queries.resolve_ref(conn, PACK, "20-K-2300A")
    unit = conn.execute("select src from edge where dst = %s and rel = 'installed_at' and valid_to is null",
                        (tag,)).fetchone()["src"]
    p = queries.profile(conn, unit)
    assert p["position"]["id"] == tag and p["model"] == "C40" and p["installed"] is None
    assert p["location"][0]["kind"] == "area"


def test_an_instrument_sits_under_its_unit(conn):
    p = queries.profile(conn, queries.resolve_ref(conn, PACK, "20-PT-2300A"))
    assert p["location"][0]["label"] == "20-K-2300A"
    assert p["manufacturer"] is None or isinstance(p["manufacturer"], str)  # not stated, not guessed
