"""Answer-key scoring of search, document links, section failure modes and log reading."""


import json

import pytest

from fuzer.core.pack import load_pack
from fuzer.evals import evaluate

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def scores(okan_db):
    c = okan_db.conn
    data = okan_db.data
    return evaluate.run(c, PACK, data / "answer_keys")


def test_search_quality(scores):
    s = scores["search"]
    assert s["questions"] >= 20 and s["hit_at_3"] >= 0.9 and s["mrr"] >= 0.8


def test_document_links(scores):
    assert scores["linking"]["precision"] == 1.0 and scores["linking"]["recall"] == 1.0


def test_section_failure_modes(scores):
    assert scores["section_modes"]["recall"] >= 0.9 and scores["section_modes"]["precision"] >= 0.7


def test_log_reading_rules_baseline(scores):
    m = scores["logs"]["mentions"]
    assert m["precision"] >= 0.95 and 0.8 <= m["recall"] < 1.0  # short tags in several areas need Claude
    assert scores["logs"]["events"].startswith("no events yet")


def test_equipment_matching(scores):
    # JDE and Maximo copies of SAP items (fakeplant.registers); the rest wait in the review queue.
    m = scores["matches"]
    assert m["pairs"] > 200 and m["wrong_merges"] == 0 and m["precision"] == 1.0 and m["recall"] >= 0.9
    assert "not_scored" not in scores  # every answer key is written, so nothing is left out


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_):
        return self.rows


@pytest.mark.unit
def test_matches_scoring():
    owner = lambda source, key, fid: {"source": source, "source_key": key, "fuzer_id": fid}  # noqa: E731
    conn = _Rows([owner("sap", "1", "a"), owner("jde", "J1", "a"),  # merged with its twin
                  owner("sap", "2", "b"), owner("jde", "J2", "c"),  # still apart (in review)
                  owner("sap", "3", "d"), owner("jde", "J3", "e"), owner("jde", "J9", "d")])  # merged with the wrong one
    pair = lambda k, j: {"source": "sap", "key": k, "same_as": {"source": "jde", "key": j}}  # noqa: E731
    m = evaluate.matches(conn, PACK, [pair("1", "J1"), pair("2", "J2"), pair("3", "J3")])
    assert (m["pairs"], m["wrong_merges"], m["not_merged"]) == (3, 1, 1)
    assert m["precision"] == 0.5 and m["recall"] == round(1 / 3, 3)


def test_assistant_keys_name_the_wet_line_and_accept_synonyms(okan_db):
    keys = json.loads((okan_db.data / "answer_keys" / "assistant.json").read_text())
    walls = next(k for k in keys if "wall thickness" in k["question"])
    line = walls["must_mention"][2][0]
    assert line.startswith("OK-PL-") and "{wet_line}" not in json.dumps(keys) and "{j1202_choke}" not in json.dumps(keys)
    readings = (okan_db.data / "inspection_readings.csv").read_text()
    assert f"TM-{line}-01-202511" in readings  # the follow-up survey is on that line
    bad_actors = next(k for k in keys if k["domain"] == "reliability")
    answer = {"answer": "20-K-2200B fails every 41 days (mean time between failures).", "tools": [], "specialists": [],
              "seconds": 1, "usage": {"cost_usd": 0}}
    assert evaluate.score_answer(bad_actors, answer)["mentions"] == 1.0
