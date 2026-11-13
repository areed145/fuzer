"""Messy maintenance text on Okan (#890): the record checks find the duplicate and misfiled notifications and the
orders closed without saying what was done, against the generator's answer key."""

import json

import pytest

from fakeplant import text
from fuzer.analysis import record_quality


@pytest.fixture(scope="module")
def truth(okan_db):
    return json.loads((okan_db.data / "answer_keys" / "records_text.json").read_text())


@pytest.fixture(scope="module")
def found(okan_db):
    return record_quality.check(okan_db.conn)


def test_every_duplicate_is_found_against_the_one_it_repeats_and_nothing_else(truth, found):
    assert len(truth["duplicates"]) >= 5
    assert {(d["notification"], d["duplicate_of"]) for d in found["duplicates"]} == \
        {(d["qmnum"], d["duplicate_of"]) for d in truth["duplicates"]}


def test_every_misfiled_notification_is_found_and_names_where_it_belongs(truth, found):
    assert {m["kind"] for m in truth["misfiled"]} == {"area", "twin"}
    got = {m["notification"]: m for m in found["misfiled"]}
    assert set(got) == {m["qmnum"] for m in truth["misfiled"]}
    for m in truth["misfiled"]:
        real = m["true_floc"].split("-", 1)[1]  # OKAN-20-K2300B -> 20-K2300B
        assert got[m["qmnum"]]["kind"] == m["kind"]
        assert any(t.replace("-", "") == real.replace("-", "") for t in got[m["qmnum"]]["text_names"]), m


def test_orders_closed_without_confirmation_are_the_ones_planted(truth, found):
    assert {o["order"] for o in found["closed_without_confirmation"]} == {o["aufnr"] for o in truth["no_confirmation"]}
    assert "" in {o["confirmation"] for o in found["closed_without_confirmation"]}
    assert found["counts"]["closed_orders"] > len(found["closed_without_confirmation"])


def test_authors_write_their_own_way_but_never_touch_tags_or_stop_words():
    line = "Train B compressor tripped, 20-PT-3003 pressure normal and checked."
    assert text.styled(line, "full") == line
    assert text.styled(line, "terse") == "Train B comp tripped, 20-PT-3003 press nml & chkd"
    assert text.styled(line, "caps") == "TRAIN B COMP TRIPPED, 20-PT-3003 PRESS NML & CHKD"
