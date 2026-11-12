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
    conn.execute("update exception_record set state = 'cleared' where type = 'round_finding'")
    conn.execute("delete from measurement where method = 'Operator round'")
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


def test_a_reading_outside_its_limits_raises_one_routed_case_and_is_kept_on_the_asset(kestrel):
    from fuzer.graph import measurements
    from fuzer.ops import pipeline

    conn, pack = kestrel
    comp = next(r for r in rounds.runs(conn) if r["round"] == "Compression rounds")
    ok = rounds.take(conn, comp["id"], "R-B1-01", "olu", value=4.2)
    assert ok["taken"]["passed"] is True and ok["exception"] is None
    low = rounds.take(conn, comp["id"], "R-B1-01", "olu", value=2.1, note="gauge steady")  # taken again: corrected
    assert low["taken"]["passed"] is False and low["exception"]["raised"] is True
    e = conn.execute("select * from exception_record where id = %s", (low["exception"]["id"],)).fetchone()
    assert e["type"] == "round_finding" and e["subjects"] == [low["asset"]] and e["item_id"]
    assert e["title"] == "Lube oil pressure on B1-K-2001: 2.1 barg, limits 3 to 5.5 barg"
    case = conn.execute("select role from work_item where id = %s", (e["item_id"],)).fetchone()
    assert case["role"] == "ops_lead" or conn.execute(  # the queue's own process holds the role on its task
        "select 1 from work_task where item_id = %s and role = 'ops_lead'", (e["item_id"],)).fetchone()
    items = {i["label"]: i for i in e["evidence"]["finding"]["items"]}
    assert (items["Reading"]["value"], items["Reading"]["unit"], items["Limits"]["value"], items["Outside"]["value"]) == (
        2.1, "barg", "3 to 5.5 barg", "low")
    assert e["evidence"]["finding"]["origin"] == "deterministic"
    # a second bad reading on a later run adds to the same case, never a second one
    conn.execute("update round_run set state = 'missed' where id = %s", (comp["id"],))
    again = rounds.take(conn, conn.execute(
        "insert into round_run (round, shift_start, shift_end, points) values ('Compression rounds', now(), "
        "now() + interval '12 hours', 4) returning id").fetchone()["id"], "R-B1-01", "olu", value=2.0)
    assert again["exception"] == {"id": e["id"], "raised": False}
    assert conn.execute("select count(*) as n from exception_record where type = 'round_finding' and state = 'open'"
                        ).fetchone()["n"] == 1
    with pytest.raises(Exception, match="closed"):
        rounds.take(conn, comp["id"], "R-B1-02", "olu", value=1)
    conn.commit()
    # the reading is a measurement on the compressor, and a rebuild keeps it there
    series = {s["quantity"]: s for s in measurements.for_entity(conn, low["asset"])["series"]}
    assert series["lube_oil_pressure"]["latest"]["method"] == "Operator round"
    assert sorted(v["value"] for v in series["lube_oil_pressure"]["values"]) == [2.0, 2.1]
    pipeline.rebuild(conn, pack)
    conn.commit()
    series = {s["quantity"]: s for s in measurements.for_entity(conn, low["asset"])["series"]}
    assert sorted(v["value"] for v in series["lube_oil_pressure"]["values"]) == [2.0, 2.1]


def test_a_failed_check_on_the_phone_needs_what_was_found_and_raises_a_finding(kestrel):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from fuzer.api import ops as ops_api

    conn, _ = kestrel
    app = FastAPI()
    app.include_router(ops_api.router)
    app.dependency_overrides[ops_api.get_conn] = lambda: conn
    api = TestClient(app)
    comp = next(r for r in api.get("/rounds").json() if r["round"] == "Compression rounds")
    check = api.get(f"/rounds/{comp['id']}").json()["points"][3]
    url = f"/rounds/{comp['id']}/readings"
    refused = api.post(url, json={"point": check["id"], "by": "olu", "passed": False})
    assert refused.status_code == 400 and "say what you found" in refused.json()["detail"]
    assert "enter the reading" in api.post(url, json={"point": "R-B1-01", "by": "olu"}).json()["detail"]
    out = api.post(url, json={"point": check["id"], "by": "olu", "passed": False,
                              "note": "oil weeping at the drain valve"}).json()
    e = conn.execute("select * from exception_record where id = %s", (out["exception"]["id"],)).fetchone()
    assert e["title"].endswith("failed: oil weeping at the drain valve")
    assert e["evidence"]["finding"]["origin"] == "external"
    assert [r["found"] for r in rounds.runs(conn) if r["id"] == comp["id"]] == [1]


def _inside(p: dict) -> float:
    return ((p["low"] or 0) + (p["high"] if p["high"] is not None else (p["low"] or 0) + 2)) / 2


def test_rounds_done_partly_done_and_missed_are_counted_per_shift_and_in_the_handover(kestrel):
    from fuzer.ops import handover, performance

    conn, _ = kestrel
    now = datetime.now(UTC)
    first = rounds.open_shift(conn, now)
    by = {r["round"]: r for r in rounds.runs(conn, now)}
    comp, dehy = by["Compression rounds"], by["Dehydration rounds"]
    for p in rounds.rounds(conn)["Compression rounds"]:  # every point: done
        rounds.take(conn, comp["id"], p["point_id"], "olu", value=_inside(p) if p["kind"] == "reading" else None,
                    passed=True if p["kind"] == "check" else None)
    rounds.take(conn, dehy["id"], rounds.rounds(conn)["Dehydration rounds"][0]["point_id"], "olu", value=1e6)  # 1 of 3
    later = first["shift_end"] + timedelta(minutes=5)
    rounds.open_shift(conn, later)
    [this, last] = rounds.per_shift(conn, now=later)[:2]
    assert this["open"] == 3 and this["shift_start"] == first["shift_end"]
    assert (last["rounds"], last["done"], last["partly_done"], last["missed"], last["not_taken"], last["found"]) == (
        3, 1, 1, 1, 2 + 2, 1)  # dehydration left 2 of 3, utilities all 2
    lines = [x["text"] for x in handover.facts(conn, first["shift_start"], first["shift_end"])["sections"]["Rounds"]]
    assert lines == ["Compression rounds: done", "Dehydration rounds: partly done, 2 of 3 checks not taken; "
                     "1 found something", "Utilities rounds: missed"]
    shown = {r["shift_start"]: r for r in performance.measures(conn)["rounds"]}
    assert shown[first["shift_start"].isoformat()]["missed"] == 1
