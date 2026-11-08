"""Matching the legacy registers (JDE, Maximo) to SAP, and the match review queue, on the Okan plant.

The generator writes each register's copies of SAP items and answer_keys/matches.json, naming which SAP item
each record really is and how it was planted (fakeplant.registers): with a serial, without one but scoring high
enough to merge, without serial or maker (review), at a tag with two SAP items (ambiguous), or with the right
serial and another maker (a value conflict). A scrapped JDE pump with its own serial must stay apart."""

import json

import pytest
from fastapi.testclient import TestClient

from fuzer.core.model import Candidate
from fuzer.core.pack import load_pack
from fuzer.evals import evaluate
from fuzer.graph.resolve import score_equipment
from fuzer.ops import pipeline

PACK = load_pack("okan")

pytestmark = pytest.mark.sequence  # the review steps change the queue the later steps read


@pytest.fixture(scope="module")
def key(okan_db):
    return json.loads((okan_db.data / "answer_keys" / "matches.json").read_text())


def cases(key, case, source="jde"):
    return [k for k in key if k["source"] == source and k["case"] == case]


def owner(conn, source, key):
    row = conn.execute("select fuzer_id, method from crosswalk where source = %s and source_key = %s",
                       (source, key)).fetchone()
    return row and (row["fuzer_id"], row["method"])


def match_items(conn, ref, status="open"):
    return conn.execute("select id, subject, payload from review_item where kind = 'match' and status = %s "
                        "and subject like %s order by subject", (status, f"%{ref}%")).fetchall()


def jde_ref(k):
    return f"jde:asset:{k['key']}"


def test_registers_are_loaded_and_keyed(okan_db, key):
    loaded = okan_db.built.loaded
    gen = okan_db.built.generated
    assert gen["jde_assets"] > 100 and gen["maximo_assets"] > 80 and gen["match_pairs"] == len(key)
    n = {r["source"]: r["n"] for r in okan_db.conn.execute(
        "select source, count(*) as n from crosswalk where source in ('jde', 'maximo') group by source")}
    assert n == {"jde": gen["jde_assets"], "maximo": gen["maximo_assets"]}, loaded
    assert {k["case"] for k in key} == {"serial", "score", "review", "ambiguous", "conflict"}


def test_answer_key_scores(okan_db, key):
    m = evaluate.matches(okan_db.conn, PACK, key)
    waiting = len(cases(key, "review")) + len(cases(key, "ambiguous"))
    assert m["pairs"] == len(key) and m["wrong_merges"] == 0 and m["precision"] == 1.0
    assert m["not_merged"] == waiting and m["recall"] == round((len(key) - waiting) / len(key), 3)
    assert m["recall"] >= 0.9  # the rest wait in the review queue


def test_each_case_takes_its_path(okan_db, key):
    c = okan_db.conn
    for k in key:
        mine, sap = owner(c, k["source"], k["key"]), owner(c, "sap", k["same_as"]["key"])
        if k["case"] in ("serial", "conflict"):
            assert mine[0] == sap[0] and mine[1] == "key", k
        elif k["case"] == "score":  # no serial: tag, maker (misspelt or not) and description
            assert mine[0] == sap[0] and mine[1] == "score", k
        else:
            assert mine[0] != sap[0] and mine[1] == "singleton", k


def test_spaced_maker_spellings_score_as_equal():
    def item(source, maker):
        return Candidate(source=source, record_type="x", source_key="1", kind="equipment", key=None,
                         block="72-HCV-1002", fields={"manufacturer": maker, "description": "Production Choke"})

    assert score_equipment(item("sap", "Master Flo"), item("jde", "MasterFlo"))[0] == 1.0
    assert score_equipment(item("sap", "Master Flo"), item("jde", ""))[0] == 0.7  # unknown maker is neutral
    assert score_equipment(item("sap", "Flowserve"), item("jde", "Sulzer"))[0] < PACK.matching["equipment"]["auto_accept"]


def test_review_cases_are_queued_with_reasons(okan_db, key):
    c = okan_db.conn
    for k in cases(key, "review"):
        items = match_items(c, jde_ref(k))
        assert len(items) == 1 and items[0]["payload"]["score"] == 0.7, k
        assert "manufacturer unknown" in items[0]["payload"]["why"]
    (amb,) = cases(key, "ambiguous")
    items = match_items(c, jde_ref(amb))
    assert len(items) == 2  # the choke replaced in 2025 and the one before it
    assert all("more than one possible match" in i["payload"]["why"] for i in items)
    sides = {i["payload"][s]["ref"].split(":", 2)[2] for i in items for s in ("left", "right")} - {amb["key"]}
    assert amb["same_as"]["key"] in sides and len(sides) == 2
    assert okan_db.built.stats["match_reviews"] == len(cases(key, "review")) + 2


def test_conflicting_maker_goes_to_review(okan_db, key):
    c = okan_db.conn
    (k,) = cases(key, "conflict")
    fid = owner(c, "sap", k["same_as"]["key"])[0]
    item = c.execute("select payload from review_item where kind = 'conflict' and status = 'open' and subject = %s",
                     (f"{fid}:manufacturer",)).fetchone()
    assert item, "the JDE maker differs from SAP's"
    assert {v["source"] for v in item["payload"]["values"]} >= {"sap", "jde"}
    sap_maker = c.execute("select value from golden where fuzer_id = %s and field = 'manufacturer'", (fid,)).fetchone()
    assert sap_maker["value"] == item["payload"]["chosen"]  # SAP still wins until someone chooses


def test_scrapped_item_with_its_own_serial_stays_apart(okan_db):
    c = okan_db.conn
    scrapped = c.execute(
        "select source_key from source_record where source = 'jde' and payload->>'serial' = '69001'"
    ).fetchone()["source_key"]
    fid, method = owner(c, "jde", scrapped)
    assert method == "singleton"
    others = c.execute("select source from crosswalk where fuzer_id = %s", (fid,)).fetchall()
    assert [r["source"] for r in others] == ["jde"]
    assert not match_items(c, f"jde:asset:{scrapped}")  # different serials never reach the queue


@pytest.fixture(scope="module")
def api(okan_db):
    from fuzer.api import app

    return TestClient(app)


def _decide(api, item_id, decision):
    from fuzer.api import rebuilder

    r = api.post(f"/review/{item_id}", json={"decision": decision, "by": "tester"})
    assert r.status_code == 200, r.text
    rebuilder.wait()
    assert "error" not in rebuilder.last, rebuilder.last


def test_accepting_a_match_merges_and_survives_rebuild(okan_db, key, api):
    c = okan_db.conn
    k = cases(key, "review")[0]
    (item,) = match_items(c, jde_ref(k))
    _decide(api, item["id"], "accept")
    assert owner(c, "jde", k["key"]) == (owner(c, "sap", k["same_as"]["key"])[0], "pinned")
    pipeline.rebuild(c, PACK)
    c.commit()
    assert owner(c, "jde", k["key"])[0] == owner(c, "sap", k["same_as"]["key"])[0]
    assert not match_items(c, jde_ref(k))  # decided, not asked again
    assert evaluate.matches(c, PACK, key)["not_merged"] == len(cases(key, "review")) + len(cases(key, "ambiguous")) - 1


def test_rejecting_one_side_of_an_ambiguous_match_lets_the_other_merge(okan_db, key, api):
    c = okan_db.conn
    (amb,) = cases(key, "ambiguous")
    right = amb["same_as"]["key"]
    wrong = next(i for i in match_items(c, jde_ref(amb))
                 if not any(i["payload"][s]["ref"] == f"sap:equipment:{right}" for s in ("left", "right")))
    _decide(api, wrong["id"], "reject")
    pinned = c.execute("select rule from pinned_link where %s in (left_ref, right_ref) ", (jde_ref(amb),)).fetchall()
    assert [p["rule"] for p in pinned] == ["cannot"]
    # With the replacement ruled out (its SAP and Maximo records alike), the old choke is the only candidate
    # left, and it scores high enough to merge.
    assert owner(c, "jde", amb["key"]) == (owner(c, "sap", right)[0], "score")
    assert not match_items(c, jde_ref(amb))
    m = evaluate.matches(c, PACK, key)
    assert m["wrong_merges"] == 0 and m["not_merged"] == len(cases(key, "review")) - 1


def test_rejecting_a_match_keeps_records_apart(okan_db, key, api):
    c = okan_db.conn
    k = cases(key, "review")[1]
    (item,) = match_items(c, jde_ref(k))
    _decide(api, item["id"], "reject")
    assert owner(c, "jde", k["key"])[0] != owner(c, "sap", k["same_as"]["key"])[0]
    pipeline.rebuild(c, PACK)
    c.commit()
    assert owner(c, "jde", k["key"])[0] != owner(c, "sap", k["same_as"]["key"])[0]
    assert not match_items(c, jde_ref(k))
    decided = match_items(c, jde_ref(k), status="decided")
    assert len(decided) == 1 and decided[0]["id"] == item["id"]
