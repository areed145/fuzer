"""Operator rounds (P5.4, #716) on Kestrel: the checklist points come from the rounds export, one run of each round
opens per shift for the operator role, and a run whose shift is over closes as done, partly done or missed."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs
from fuzer.ops import rounds

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_rounds"


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
    conn.execute("delete from round_reading")
    conn.execute("delete from round_run")
    conn.commit()


def test_the_rounds_come_from_the_export_in_order_with_their_equipment_and_limits(kestrel):
    from fuzer.graph import queries

    conn, pack = kestrel
    by = rounds.rounds(conn)
    assert sorted(by) == ["Compression rounds", "Dehydration rounds", "Utilities rounds"]
    comp = by["Compression rounds"]
    assert [p["point_id"] for p in comp] == ["R-B1-01", "R-B1-02", "R-B1-03", "R-B1-04"]
    lube = comp[0]
    assert (lube["what"], lube["kind"], lube["unit"], lube["low"], lube["high"]) == (
        "Lube oil pressure", "reading", "barg", 3.0, 5.5)
    assert lube["asset"] == queries.resolve_ref(conn, pack, "B1-K-2001")
    assert comp[3]["kind"] == "check" and comp[3]["low"] is None and comp[3]["unit"] is None


def test_each_round_opens_once_a_shift_for_operators_and_last_shifts_close(kestrel):
    conn, _ = kestrel
    now = datetime.now(UTC)
    first = rounds.open_shift(conn, now)
    assert first["opened"] == 3 and first["shift_start"] <= now < first["shift_end"]
    assert timedelta(hours=11) <= first["shift_end"] - first["shift_start"] <= timedelta(hours=13)  # 06:00, 18:00
    assert rounds.open_shift(conn, now)["opened"] == 0  # once per shift
    runs = rounds.runs(conn, now)
    assert [(r["round"], r["role"], r["points"], r["taken"], r["state"]) for r in runs] == [
        ("Compression rounds", "operator", 4, 0, "open"), ("Dehydration rounds", "operator", 3, 0, "open"),
        ("Utilities rounds", "operator", 2, 0, "open")]
    assert runs[0]["area"] == "B1"
    one = rounds.run(conn, runs[0]["id"])
    assert [p["point_id"] for p in one["points"]] == ["R-B1-01", "R-B1-02", "R-B1-03", "R-B1-04"]
    assert all(p["taken"] is None for p in one["points"])
    # the next shift: these close as missed (nothing taken) and three new runs open
    later = rounds.open_shift(conn, first["shift_end"] + timedelta(minutes=5))
    assert later["closed"] == 3 and later["opened"] == 3
    states = {r["state"] for r in conn.execute("select state from round_run where shift_start = %s",
                                                (first["shift_start"],))}
    assert states == {"missed"}
