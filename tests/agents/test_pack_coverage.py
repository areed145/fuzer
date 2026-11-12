"""P4.1b: each specialist pack against the shipped bundles' registers. A pack covers measured assets of its classes
on Okan and Kestrel, and where a measurement one of its methods needs is not on an asset, it says what it cannot run
there."""

from pathlib import Path

import pytest
from psycopg.conninfo import make_conninfo

from fuzer.agents import packs
from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs_
from fuzer.ops import pipeline
from fuzer.sources import ingest

pytestmark = pytest.mark.integration

COMPRESSION, PUMPS = "maintenance-integrity/compression", "maintenance-integrity/pumps-and-drivers"
ALARMS = "field-operations-facilities/alarms-and-instruments"
TREATING = "field-operations-facilities/gas-treating"
KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"


@pytest.fixture(scope="module")
def kestrel():
    """The Kestrel bundle, built once for this module in a database of its own."""
    with db.connect(autocommit=True, url=db.database_url()) as a:
        a.execute('drop database if exists "packs_kestrel" with (force)')
        a.execute('create database "packs_kestrel"')
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATABASE_URL", make_conninfo(db.database_url(), dbname="packs_kestrel"))
    mp.delenv("FUZER_PROJECT", raising=False)
    mp.delenv("FUZER_DATA_DIR", raising=False)
    packs_.clear_cache()
    settings._cache.clear()
    settings._first.clear()
    try:
        with db.connect() as conn:
            db.init_schema(conn)
            bundles.init(conn, KESTREL, by="test")
            ingest.ingest(conn, KESTREL / "exports")
            pipeline.rebuild(conn, packs_.current_pack(conn))
            conn.commit()
            yield conn
            conn.rollback()
    finally:
        mp.undo()
        packs_.clear_cache()
        settings._cache.clear()
        settings._first.clear()
        with db.connect(autocommit=True, url=db.database_url()) as a:
            a.execute('drop database if exists "packs_kestrel" with (force)')


def _by_label(site: dict) -> dict:
    return {a["label"]: a for a in site["assets"]}


@pytest.mark.parametrize("path", [COMPRESSION, PUMPS, ALARMS])
def test_each_pack_works_on_okan(okan_db, path):
    site = packs.on_site(okan_db.conn, path)
    assert site["problems"] == [] and site["assets"]


def test_on_okan_the_centrifugal_compressors_run_their_checks_and_the_recip_checks_do_not(okan_db):
    k = _by_label(packs.on_site(okan_db.conn, COMPRESSION))["20-K-2200B"]
    assert {"compressor_performance", "surge", "seal_gas", "rotating_health"} <= set(k["can"])
    assert {c["method"] for c in k["cannot"]} == {"rod_load", "valve_health"}  # a centrifugal has no cylinders


def test_on_okan_the_pumps_have_what_their_curve_needs(okan_db):
    site = _by_label(packs.on_site(okan_db.conn, PUMPS))
    assert {"pump_curve", "pump_bep", "vibration"} <= set(site["10-P-1001A"]["can"])
    assert "heat_rate" in site["20-GT-2000B"]["can"]


def test_on_okan_the_treating_pack_says_its_glycol_contactor_is_not_instrumented_for_it(okan_db):
    site = packs.on_site(okan_db.conn, TREATING)  # no differential pressure or analyser on any vessel: its lab samples are the evidence
    assert site["problems"] == ["none of its methods can run on any asset here"]
    assert {c["method"] for c in _by_label(site)["20-V-2400"]["cannot"]} == {"contactor_foaming", "treated_gas"}


@pytest.mark.parametrize("path", [COMPRESSION, PUMPS, ALARMS, TREATING])
def test_each_pack_works_on_kestrel(kestrel, path):
    site = packs.on_site(kestrel, path)
    assert site["problems"] == [] and site["assets"]


def test_on_kestrel_a_pack_says_what_it_cannot_run_where_measurements_are_missing(kestrel):
    site = _by_label(packs.on_site(kestrel, COMPRESSION))
    assert {"rod_load", "valve_health", "compressor_performance"} <= set(site["B1-K-2001"]["can"])
    assert "surge" in {c["method"] for c in site["B1-K-2001"]["cannot"]}  # a recip has no anti-surge valve
    assert site["E1-K-5004"]["can"] == []  # only its suction pressure is measured

    pump = kestrel.execute("select fuzer_id from entity_label where label = 'A1-P-1007A'").fetchone()["fuzer_id"]
    lines = packs.cannot_do(kestrel, packs.load(PUMPS), [pump])
    assert "pump_curve on A1-P-1007A: no suction pressure or discharge pressure measured there" in lines
    assert not any(line.startswith("pump_bep ") for line in lines)  # its flow is measured
