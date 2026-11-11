"""Reciprocating compressor checks on Kestrel's B1-K-2001 (Ariel JGK/4, two stages, four throws): a discharge valve
on cylinder 3 (2nd stage) has leaked for three weeks, so that cylinder runs hot, the 2nd stage loses capacity, the
interstage pressure climbs and the 1st stage throws carry rod load past the frame's rating. Frame vibration is judged
against the maker's alarm and trip, not the centrifugal zones. Yesterday's trip on high vibration is the train's
second in 45 days (the first on discharge temperature), so it asks reliability to review it."""

from pathlib import Path

import pytest
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_recip"


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
        mp.setenv("FUZER_DATA_DIR", str(first["data"]))  # its synthetic historian reads the bundle's process model
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


def k2001(conn, pack):
    from fuzer.graph import queries

    return queries.resolve_ref(conn, pack, "B1-K-2001")


def test_a_leaking_second_stage_valve_pushes_first_stage_rod_load_past_the_rating(kestrel):
    from fuzer.analysis import calcs

    conn, pack = kestrel
    r = calcs.run(conn, "rod_load", k2001(conn, pack))
    assert r["flag"] == "rod_load_high" and r["expected"] == 95 and r["value"] > 95 and r["unit"] == "kN"
    by = {t["throw"]: t for t in r["throws"]}
    assert r["worst_throw"] in (1, 2) and by[1]["stage"] == 1 and by[3]["stage"] == 2
    assert by[1]["daily"][0]["kn"] < 90 < by[1]["daily"][-1]["kn"]  # it climbed with the leak
    assert all(t["reversal_ratio"] > 0.05 for t in r["throws"]) and r["hours_over_rating"] > 0
    assert "API 618" in r["basis"] and "inertia" in r["basis"]


def test_the_leaking_cylinder_runs_hot_and_the_stage_ratios_shift(kestrel):
    from fuzer.analysis import recip

    conn, pack = kestrel
    v = recip.valve_health(conn, k2001(conn, pack))
    assert v["flag"] == "valve_leak" and v["hottest_cylinder"] == 3 and v["hottest_stage"] == 2
    cyl = {c["cylinder"]: c for c in v["cylinders"]}
    assert cyl[3]["above_stage_c"] >= 10 and abs(cyl[3]["at_start_c"]) < 5 and abs(cyl[1]["above_stage_c"]) < 5
    assert v["stage_ratio"]["change"] > 0.04 and v["weak_stage"] == 2


def test_recip_vibration_uses_the_makers_limits_and_health_says_act(kestrel):
    from fuzer.analysis import rotating

    conn, pack = kestrel
    vib = rotating.vibration(conn, k2001(conn, pack))
    assert vib["group"] == "reciprocating" and vib["zones"] == {"A/B": 3.55, "B/C": 7.1, "C/D": 11.2}
    assert "ISO 20816-8" in vib["basis"] and vib["zone"] in ("A", "B")
    h = rotating.health(conn, k2001(conn, pack))
    by = {f["check"]: f for f in h["findings"]}
    assert h["value"] == "act" and by["rod_load"]["flag"] == "rod_load_high" and by["valve_health"]["flag"] == "valve_leak"
    assert "surge" not in by and "compressor_performance" not in by  # centrifugal checks don't apply
    assert any("rod load" in r for r in h["recommendations"])
    w = rotating.watch(conn)
    assert any(x["machine"] == "B1-K-2001" and x["status"] == "act" for x in w["raised"])


def test_yesterdays_vibration_trip_and_the_one_five_weeks_ago(kestrel):
    from fuzer.analysis import trips

    conn, pack = kestrel
    d = trips.diagnose(conn, k2001(conn, pack), pack=pack)
    assert d["value"] == "vibration" and d["first_out"]["point"] == "KRGP:B1.VT2003.PV"
    assert any(x["point"] == "KRGP:B1.VT2003.PV" and x["trip_setting"] == 11.2 for x in d["tripped_on"])
    assert d["verdict"] == "review" and d["prior_trips"][0]["family"] == "discharge_temperature"
    assert 33 <= d["prior_trips"][0]["days_before"] <= 36 and "reliability" in d["checklist"][-1]
    # the utility power dip three months ago had no warning in the data
    from datetime import date, timedelta
    dip = (date.today() - timedelta(days=96)).isoformat() + "T14:20"
    p = trips.diagnose(conn, k2001(conn, pack), dip, pack=pack, history=False)
    assert p["value"] == "none" and p["first_out"] is None and "first-out panel" in p["checklist"][0]
