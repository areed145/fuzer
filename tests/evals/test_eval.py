"""Answer-key scoring of search, document links, section failure modes and log reading."""


import pytest

from fuzer.core.pack import load_pack
from fuzer.evals import evaluate

PACK = load_pack()


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


def test_okan_has_no_cross_register_matches(scores):
    # Okan has one equipment register (SAP), so no record pairs to resolve; a real asset with a second
    # register supplies matches.json, scored as in test_matches_scoring.
    assert "matches" not in scores


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
