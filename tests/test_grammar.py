import pytest

from fuzer.pack import load_pack
from fuzer.extract import norm_serial
from fuzer.nel import find_mentions

PACK = load_pack()


@pytest.mark.parametrize("text,area,expected", [
    ("OSB-20-PT1001A", None, "20-PT-1001A"),
    ("OSB.20PT1001A.PV", None, "20-PT-1001A"),
    ("OSB:23-PT-1001A.DACA.PV", None, "23-PT-1001A"),
    ("27PT1001A_PV", None, "27-PT-1001A"),
    ("OIL EXPORT PUMP - 27-P1001-A", None, "27-P-1001A"),
    ("OSB/20/P-1001A", "20", "20-P-1001A"),
    ("PT\n1001A", "27", "27-PT-1001A"),
    ("44P1001B", None, "44-P-1001B"),
    ("20-V-2001", None, "20-V-2001"),
    ("PT-1001A", None, None),          # no area and no context
    ("OSB.A07.WHP", None, None),        # a well point, not a tag
])
def test_tag_parse(text, area, expected):
    assert PACK.tags.parse(text, default_area=area) == expected


def test_suffix_does_not_eat_next_word():
    found = PACK.tags.find_all("PT\n1001\nTT\n1002")
    assert [t.short_key for t in found] == ["PT-1001", "TT-1002"]


@pytest.mark.parametrize("text,expected", [
    ("A07", "A-07"), ("A-7", "A-07"), ("OSB_A-07", "A-07"), ("OSB-WELL-A07A", "A-07 ST1"),
    ("OSB_A-07 ST1", "A-07 ST1"), ("OSB.A07ST1.WHP", "A-07 ST1"), ("20-PT-1001A", None),
])
def test_well_parse(text, expected):
    assert PACK.wells.parse(text) == expected


def test_serial_normalization():
    assert norm_serial("SN40123") == norm_serial("40123") == norm_serial("sn-40123") == "40123"


def test_mentions_prefer_longest_alias():
    ms = find_mentions("Lined up A-03 to test separator, LT-2002 hunting", PACK)
    assert [(m.kind, m.key) for m in ms] == [
        ("well", "A-03"), ("tag", "20-V-2002"), ("tag_noarea", "LT-2002")]
