"""Upsets that belong together: related upsets, the triage view, merging cases and recurring cascades, on Okan's
5 November 2024 scan (train C's trip)."""

import pytest

from fuzer.core.pack import load_pack
from fuzer.ops import cascades, sre

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    from fuzer.ops import sre_handoff
    from fuzer.workflow import engine

    c = okan_db.conn
    engine.load_defaults(c)
    sre.scan(c, PACK, "2024-11-05T00:00", "2024-11-05T18:00")
    sre_handoff.handoff(c, PACK, ask=lambda q: {"answer": "no diagnosis"}, max_diagnoses=0)
    return c


def _case(conn, upset_id):
    return conn.execute("select work_item_id from upset where id = %s", (upset_id,)).fetchone()["work_item_id"]


def _by_first(conn, unit, detector):
    return next(u["id"] for u in sre.upsets(conn, kind="process", status=None, limit=100)
                if u["first_unit"] == unit and u["first_detector"] == detector and _case(conn, u["id"]))


def test_related_upsets_are_scored_by_time_links_and_history(conn):
    """The lift gas excursion on 67-FT-3001 at 11:40 and the step on the wells it feeds at 12:00 arrive as two upsets
    and two cases: the step is its likely knock-on; an export pump stopping two hours on is connected only far
    down the oil path, so it ranks well below the lift gas excursion."""
    gas = _by_first(conn, "67-FT-3001", "excursions")
    wells = _by_first(conn, "OK-J12-01", "steps")
    out = cascades.related(conn, gas)
    [step] = [r for r in out["related"] if r["id"] == wells]
    assert step["role"] == "likely_knock_on" and step["lag_minutes"] == 20 and step["hops"] is not None
    assert step["work_item_id"]
    assert out["related"] == sorted(out["related"], key=lambda r: -r["score"])
    assert all(r["role"] in cascades.ROLE_WORDS for r in out["related"])
    from_wells = cascades.related(conn, wells, 24)["related"]
    pump = next(r for r in from_wells if r["signature"] == "a stop on 10-P-1001C")
    lift_gas = next(r for r in from_wells if r["id"] == gas)
    assert pump["hops"] >= 6 and pump["score"] < lift_gas["score"] / 2
    back = cascades.related(conn, wells)
    assert next(r for r in back["related"] if r["id"] == gas)["role"] == "likely_cause"


def test_the_triage_view_shows_the_timeline_and_how_it_spread(conn):
    gas = _by_first(conn, "67-FT-3001", "excursions")
    v = cascades.view(conn, gas)
    assert v["signature"] == "an excursion on 67-FT-3001"
    assert v["timeline"] and v["timeline"] == sorted(v["timeline"], key=lambda d: d["start"])
    assert all(d["finding"] for d in v["timeline"])
    assert v["spread"]["spec"]["data"] and v["spread"]["summary"]["first_cause"] == "67-FT-3001"
    assert v["related"]["related"] and isinstance(v["merged"], list)  # the merge test may have run first
    with pytest.raises(cascades.CascadeError, match="no upset"):
        cascades.view(conn, 999999)


def test_the_ioc_merges_a_knock_on_into_one_case(conn):
    """One cascade, one case: the knock-on's case joins the cause's, its own is cancelled (process and all), both
    histories say so, and it can't be merged twice."""
    gas = _by_first(conn, "67-FT-3001", "excursions")
    wells = _by_first(conn, "OK-J12-01", "steps")
    host, old = _case(conn, gas), _case(conn, wells)
    out = cascades.merge(conn, wells, gas, "ioc-ann", reason="lift gas loss reached the wells")
    assert out["item_id"] == host and out["cancelled_case"] == old
    assert conn.execute("select state from work_item where id = %s", (old,)).fetchone()["state"] == "cancelled"
    assert not conn.execute("select 1 from work_process where item_id = %s and state = 'running'", (old,)).fetchone()
    assert not conn.execute("select 1 from work_token where item_id = %s and state = 'waiting'", (old,)).fetchone()
    assert conn.execute("select state from work_item where id = %s", (host,)).fetchone()["state"] not in ("done",
                                                                                                        "cancelled")
    note = conn.execute("select data->>'text' as t from work_event where item_id = %s and kind = 'commented' "
                        "order by id desc limit 1", (host,)).fetchone()["t"]
    assert note.startswith("Merged in: ") and "lift gas loss reached the wells" in note and f"was case {old}" in note
    mine = conn.execute("select * from exception_record where key = %s", (f"upset:{wells}",)).fetchone()
    theirs = conn.execute("select * from exception_record where key = %s", (f"upset:{gas}",)).fetchone()
    assert mine["joined_to"] == theirs["id"] and mine["item_id"] == host
    assert _case(conn, wells) == host
    assert [m["id"] for m in cascades.view(conn, gas)["merged"]] == [wells]
    assert next(r for r in cascades.related(conn, gas)["related"] if r["id"] == wells)["same_case"]
    from fuzer.core.exceptions import ExceptionError
    with pytest.raises(ExceptionError, match="already on case"):
        cascades.merge(conn, wells, gas, "ioc-ann")
    conn.rollback()
    with pytest.raises(cascades.CascadeError, match="into itself"):
        cascades.merge(conn, gas, gas, "ioc-ann")
    lone = next(u["id"] for u in sre.upsets(conn, kind="process", status=None, limit=100) if not _case(conn, u["id"]))
    with pytest.raises(cascades.CascadeError, match="no case to merge into"):
        cascades.merge(conn, wells, lone, "ioc-ann")


def test_recurring_cascades_are_counted_with_their_lags(conn):
    out = cascades.patterns(conn, window_minutes=720, min_support=2)
    assert out["value"] == len(out["patterns"]) or out["value"] > len(out["patterns"])
    top = out["patterns"][0]
    assert top["support"] >= 2 and 0 < top["confidence"] <= 1 and top["hops"] is not None
    assert top["lag_range_minutes"][0] <= top["median_lag_minutes"] <= top["lag_range_minutes"][1]
    assert top["first"] != top["then"] and len(top["examples"]) == top["support"]
    assert cascades.patterns(conn, window_minutes=10, min_support=2)["value"] < out["value"]
    loose = cascades.patterns(conn, window_minutes=720, min_support=2, connected_only=False)
    assert loose["value"] >= out["value"]


def test_a_cascades_study_is_kept_with_a_summary(conn):
    from fuzer.ops import study

    s = study.start(conn, PACK, "cascades", {"window_minutes": 720}, "ann")
    assert s["status"] == "done" and "recurring cascades in" in s["summary"]
