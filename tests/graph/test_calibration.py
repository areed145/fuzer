"""P2.2: calibrated match confidence. Straight-through matches are at least 99% correct against the answer keys on
both bundles, before and after each source's thresholds are set from measured precision, and the Rules page shows
the thresholds with what was measured at them."""

import json
from pathlib import Path

import pytest

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs_
from fuzer.evals import evaluate
from fuzer.graph import calibration
from fuzer.ops import pipeline
from fuzer.sources import ingest

SHIPPED = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles"
KESTREL = SHIPPED / "kestrel" / "exports"


# ---- the measures ------------------------------------------------------------------------------

def test_isotonic_is_monotone_and_pools_violators():
    curve = calibration.isotonic([(0.5, False), (0.6, True), (0.7, False), (0.9, True), (1.0, True)])
    assert [s["p"] for s in curve] == sorted(s["p"] for s in curve)
    assert curve[0] == {"upto": 0.5, "p": 0.0, "n": 1} and curve[-1]["p"] == 1.0
    assert calibration.probability(curve, 0.65) == 0.5  # 0.6 and 0.7 pooled: one of two was the same item


def test_wilson_lower_bound_says_ten_right_is_not_ninety_nine_percent():
    assert calibration.wilson_lower(10, 10) < 0.75
    assert calibration.wilson_lower(400, 400) > 0.99
    assert calibration.wilson_lower(0, 0) is None


def test_a_looser_threshold_needs_enough_pairs_and_a_stricter_one_does_not():
    base = {"auto_accept": 0.9, "review_min": 0.6}
    few = {"auto_accept": {"threshold": 0.7, "pairs": 10}, "review_min": {"threshold": 0.5}, "same": 10}
    assert calibration.recommended(few, base) == {"auto_accept": 0.9, "review_min": 0.5}  # lower floor: cautious
    many = {"auto_accept": {"threshold": 0.7, "pairs": 40}, "review_min": {"threshold": 0.65}, "same": 40}
    assert calibration.recommended(many, base) == {"auto_accept": 0.7, "review_min": 0.65}
    stricter = {"auto_accept": {"threshold": 0.95, "pairs": 3}, "review_min": {"threshold": 0.95}, "same": 3}
    assert calibration.recommended(stricter, base) == {"auto_accept": 0.95, "review_min": 0.6}
    assert calibration.recommended({"same": 0}, base) is None  # nothing reached 99%: the kind's thresholds stand


# ---- both bundles ------------------------------------------------------------------------------

def _straight_through(conn, keys: Path) -> dict:
    return evaluate.matches(conn, None, json.loads((keys / "matches.json").read_text()))


def _calibrate_and_adopt(conn, keys: Path) -> tuple[dict, dict, dict, dict]:
    before = _straight_through(conn, keys)
    measured = calibration.measure_from_keys(conn, keys, "test")
    adopted = calibration.adopt(conn, "test")
    pipeline.rebuild(conn, packs_.current_pack(conn))
    return before, measured, adopted, _straight_through(conn, keys)


def test_okan_straight_through_stays_99_percent_correct_when_calibrated(okan_db):
    conn, keys = okan_db.conn, okan_db.data / "answer_keys"
    before, measured, adopted, after = _calibrate_and_adopt(conn, keys)
    conn.commit()
    assert before["precision"] >= 0.99 and after["precision"] >= 0.99, (before, after)
    assert after["recall"] >= before["recall"]
    # JDE's legacy register is the one scored (no serials); the ambiguous choke is counted, never measured.
    assert measured["jde"]["pairs"] >= calibration.MIN_PAIRS and measured["jde"]["ambiguous"] == 2
    assert measured["jde"]["auto_accept"]["precision"] >= 0.99 and "sap" not in measured
    assert adopted["jde"] is not None
    rule = conn.execute("select config from source_rule where scope = 'match' and name = 'equipment/jde'").fetchone()
    assert rule["config"] == adopted["jde"]
    # The choke a replacement left behind still waits for a person.
    assert conn.execute("select count(*) as n from match_score where outcome = 'ambiguous'").fetchone()["n"] == 2


@pytest.fixture
def kestrel(monkeypatch):
    from psycopg.conninfo import make_conninfo

    with db.connect(autocommit=True, url=db.database_url()) as a:
        a.execute('drop database if exists "calibration_kestrel" with (force)')
        a.execute('create database "calibration_kestrel"')
    url = make_conninfo(db.database_url(), dbname="calibration_kestrel")
    monkeypatch.setenv("FUZER_DATABASE_URL", url)
    monkeypatch.delenv("FUZER_PROJECT", raising=False)
    monkeypatch.delenv("FUZER_DATA_DIR", raising=False)
    packs_.clear_cache()
    settings._cache.clear()
    settings._first.clear()
    with db.connect(url=url) as conn:
        db.init_schema(conn)
        bundles.init(conn, SHIPPED / "kestrel", by="test")
        ingest.ingest(conn, KESTREL)
        pipeline.rebuild(conn, packs_.current_pack(conn))
        conn.commit()
        yield conn
    packs_.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def test_kestrel_planted_maximo_records_take_every_path_and_stay_99_percent_correct(kestrel):
    conn = kestrel
    outcome = {r["left_ref"].split(":")[-1] if r["left_ref"].startswith("maximo") else r["right_ref"].split(":")[-1]:
               (r["outcome"], r["score"]) for r in conn.execute("select * from match_score")}
    assert outcome["MX-2010"][0] == "merged"  # no serial, same maker and item
    assert outcome["MX-6003"][0] == "review"  # no serial and no maker: a person decides
    assert outcome["MX-1009"][0] == "rejected"  # the pump's motor at the pump's tag is not the pump
    # The same maker and item on an A-criticality reboiler: the human gate (P5.10) always asks a person there.
    gate = {r["subject"].split("|")[0].split(":")[-1]: r["payload"]["gate"] for r in conn.execute(
        "select subject, payload from review_item where kind = 'match' and payload ? 'gate'")}
    assert outcome["MX-4006"] == ("review", 1.0) and gate["MX-4006"]["criticality"] == "A"
    # The glycol pump's driver: same maker, a close name, the pump's tag. The pack's 0.9 would take it straight
    # through, into the pump; the gate holds it, since an unmeasured score of 0.90 is short of the 99% it needs.
    assert outcome["MX-3010"][0] == "review" and outcome["MX-3010"][1] >= 0.9
    assert gate["MX-3010"]["confidence"] == outcome["MX-3010"][1] and gate["MX-3010"]["threshold"] == 0.99

    before, measured, adopted, after = _calibrate_and_adopt(conn, KESTREL / "answer_keys")
    conn.commit()
    assert before["merged_unlisted"] == [] and before["precision"] >= 0.99
    maximo = measured["maximo"]
    assert maximo["pairs"] == 5 and maximo["same"] == 3  # the two negatives are measured too
    assert maximo["auto_accept"]["threshold"] > outcome["MX-3010"][1]  # set above the driver
    assert maximo["auto_accept"]["precision"] == 1.0 and maximo["auto_accept"]["lower"] < 0.99  # and honest about n
    # Stricter is the cautious direction, so it's adopted on a handful of pairs; now the measured threshold itself
    # sends the driver to a person, before the gate is asked, and straight-through stays right.
    assert adopted["maximo"]["auto_accept"] > 0.9
    assert after["precision"] >= 0.99 and after["merged_unlisted"] == [], after
    assert conn.execute("select outcome from match_score where 'maximo:asset:MX-3010' in (left_ref, right_ref)"
                        ).fetchone()["outcome"] == "review"
    assert not conn.execute("select 1 from review_item where kind = 'match' and status = 'open' "
                            "and subject like 'maximo:asset:MX-3010|%%' and payload ? 'gate'").fetchone()

    from fastapi.testclient import TestClient

    from fuzer import api

    with TestClient(api.app) as client:
        rules = client.get("/sources/rules").json()
    row = next(r for r in rules["calibration"] if r["source"] == "maximo")
    assert row["in_use"] == adopted["maximo"] | {"auto_accept": row["in_use"]["auto_accept"]}
    assert row["at_auto_accept"]["precision"] == 1.0 and row["pairs"] == 5 and row["labels"].startswith("answer key")
