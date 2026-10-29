"""Answer-key scoring of search, document links, section failure modes and log reading."""

import os
import tempfile
from pathlib import Path

import pytest

from fuzer import db, evaluate, fakedata, ingest, pipeline
from fuzer.pack import load_pack

PACK = load_pack()


@pytest.fixture(scope="module")
def scores():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzdata"))
    fakedata.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
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
    m = scores["matches"]
    assert m["pairs"] > 20 and m["wrong_merges"] == 0 and m["precision"] == 1.0
    assert m["recall"] >= 0.85  # the rest wait in the review queue (no serial, no maker)
