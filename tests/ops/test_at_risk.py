"""Production at risk from planned shutdowns (P5.9a, #337) on Kestrel and Okan: the wells behind each unit going down,
at their latest test rate, counted once per window of overlapping outages, with the durations used said."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs
from fuzer.ops import at_risk, plan

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_at_risk"


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


@pytest.fixture(autouse=True)
def fresh(kestrel):
    yield
    conn = kestrel[0]
    conn.rollback()
    conn.execute("update plan_item set status = 'cancelled'")
    conn.commit()



def test_wells_behind_overlapping_shutdowns_are_counted_once_with_the_durations_said(kestrel):
    from fuzer.graph import queries

    conn, pack = kestrel
    t0 = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(days=10)
    comp = queries.resolve_ref(conn, pack, "B1-K-2001")
    inlet = queries.resolve_ref(conn, pack, "A1-V-1001")
    plan.propose(conn, "shutdown", "Compressor valve change", "test", t0, t0 + timedelta(hours=48), refs=[comp],
                 needs_shutdown=True)
    plan.propose(conn, "maintenance", "Inlet separator inspection", "test", t0 + timedelta(hours=24),
                 t0 + timedelta(hours=72), refs=[inlet], needs_shutdown=True)
    later = t0 + timedelta(days=30)
    plan.propose(conn, "maintenance", "Aftercooler fan belts", "test", later, later + timedelta(hours=12),
                 refs=[queries.resolve_ref(conn, pack, "B1-E-2010")], needs_shutdown=True)
    out = at_risk.windows(conn, datetime.now(UTC), days=60)
    first, second = out["windows"]
    assert [i["title"] for i in first["items"]] == ["Compressor valve change", "Inlet separator inspection"]
    assert [i["hours"] for i in first["items"]] == [48.0, 48.0]
    wells = first["wells"]
    assert wells and all(w["hours"] == 72.0 for w in wells)  # 0-48 h and 24-72 h overlap: 72 h, not 96
    gas = next(r for r in first["at_risk"] if r["quantity"] == "gas_rate")
    expected = sum(w["rates"]["gas_rate"]["value"] for w in wells if "gas_rate" in w["rates"]) * 3
    assert abs(gas["value"] - round(expected, 1)) < 0.2 and gas["unit"] == "kSm3"
    # the aftercooler isn't on the flow network: its train's units are, so its wells are at risk too
    assert [u["label"] for u in second["items"][0]["units"]] and second["wells"]
    assert all(w["hours"] == 12.0 for w in second["wells"])
    assert "counted once" in out["basis"]
    conn.commit()


def test_a_due_date_without_an_outage_length_is_said_to_be_assumed(kestrel):
    from fuzer.graph import queries

    conn, pack = kestrel
    t0 = datetime.now(UTC) + timedelta(days=5)
    conn.execute(
        "insert into plan_item (kind, title, source, source_key, subjects, start_at, end_at, status, needs_shutdown, "
        "created_by, updated_by) values ('maintenance', 'Compressor 4000-hour service', 'sap_plan', 'PM-X:1', %s, %s, %s, "
        "'proposed', true, 'sap', 'sap')", ([queries.resolve_ref(conn, pack, "B1-K-2001")], t0, t0 + timedelta(days=1)))
    [w] = at_risk.windows(conn, datetime.now(UTC), days=30)["windows"]
    assert w["assumptions"] == [at_risk.ASSUMED["sap_plan"]] and w["items"][0]["assumed"]
    conn.commit()


def test_okan_oil_at_risk_behind_a_unit(okan_db):
    conn = okan_db.conn
    unit = conn.execute(
        "select e.dst as id from edge e join entity w on w.fuzer_id = e.src and w.kind = 'well' "
        "where e.rel = 'flows_to' group by e.dst order by count(*) desc limit 1").fetchone()["id"]
    t0 = datetime.now(UTC) + timedelta(days=3)
    plan.propose(conn, "shutdown", "Manifold repair", "test", t0, t0 + timedelta(hours=24), refs=[unit],
                 needs_shutdown=True)
    [w] = [x for x in at_risk.windows(conn, datetime.now(UTC), days=30)["windows"]
           if any(i["title"] == "Manifold repair" for i in x["items"])]
    oil = next(r for r in w["at_risk"] if r["quantity"] == "oil_rate")
    assert oil["value"] > 0 and oil["unit"] == "Sm3" and len(w["wells"]) >= 2
    conn.rollback()
