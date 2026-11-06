import pytest

from fuzer.core.nel import find_mentions
from fuzer.core.pack import load_pack
from fuzer.sources.extract import norm_serial

PACK = load_pack()


@pytest.mark.parametrize("text,area,expected", [
    ("OK_GGCP:20-PT-3003.PV", None, "20-PT-3003"),
    ("OKA_PP.10PT3001.PV", None, "10-PT-3001"),
    ("OKA_ST1:30-FT-3002.PV", None, "30-FT-3002"),
    ("20PT3003", None, "20-PT-3003"),
    ("20-PT-3003", None, "20-PT-3003"),
    ("PT3003", "20", "20-PT-3003"),
    ("PT\n3003", "20", "20-PT-3003"),
    ("PT-1001A", None, None),          # no area and no context
    ("OK_J07_03.WHP", None, None),        # a well point, not a tag
])
def test_tag_parse(text, area, expected):
    assert PACK.tags.parse(text, default_area=area) == expected


def test_suffix_does_not_eat_next_word():
    found = PACK.tags.find_all("PT\n1001\nTT\n1002")
    assert [t.short_key for t in found] == ["PT-1001", "TT-1002"]


@pytest.mark.parametrize("text,expected", [
    ("OK-J07-03", "OK-J07-03"), ("OKAN_J07_03", "OK-J07-03"),
    ("OKAN-WELL-OKJ07-03", "OK-J07-03"), ("OK-NWP-G2", "OK-NWP-G2"),
    ("20-PT-3003", None),
])
def test_well_parse(text, expected):
    assert PACK.wells.parse(text) == expected


def test_serial_normalization():
    assert norm_serial("SN40123") == norm_serial("40123") == norm_serial("sn-40123") == "40123"


def test_mentions_prefer_longest_alias():
    ms = find_mentions("Train B down on OK_GGCP, lift gas short to OKA_ST2", PACK)
    assert {"OKAN-20", "OKAN-40"} <= {m.key for m in ms}
