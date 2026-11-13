"""P2.7: plant structure from the register alone. A train is the piped equipment of an area once its headers are left
out, plus what drives it; a compressor stage's order, scrubber, cooler and its counterparts on parallel trains come
from piping, never from tag names, and where the data can't say, the answer says why."""

import pytest
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.graph import structure

pytestmark = pytest.mark.sequence


def _fid(conn, label: str) -> str:
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def _labels(p, fids) -> list:
    return [p.label(f) for f in fids]


def test_okan_compression_trains_from_its_line_list(okan_db):
    conn = okan_db.conn
    p = structure.load(conn)
    k = _fid(conn, "20-K-2200B")
    t = structure.train(conn, k, p)
    assert t["known"] and _labels(p, t["members"]) == [
        "20-V-2101B", "20-K-2100B", "20-E-2150B", "20-V-2201B", "20-K-2200B", "20-E-2250B", "20-V-2301B",
        "20-K-2300B", "20-E-2350B", "20-GT-2000B"]
    assert _labels(p, t["shared"]) == ["20-FT-3001", "20-V-2001"] and _labels(p, t["machines"]) == [
        "20-K-2100B", "20-K-2200B", "20-K-2300B"]
    assert structure.train(conn, _fid(conn, "20-GT-2000B"), p)["key"] == t["key"]  # a driver is in what it drives
    assert not structure.train(conn, _fid(conn, "20-V-2001"), p)["known"]  # the header every train shares
    assert not structure.train(conn, _fid(conn, "20-FT-3001"), p)["known"]  # and the export meter they all feed

    s = structure.stage(conn, k, p)
    assert s["order"] == 2 and p.label(s["scrubber"]) == "20-V-2201B" and p.label(s["cooler_before"]) == "20-E-2150B"
    assert _labels(p, s["siblings"]) == ["20-K-2200A", "20-K-2200C"]  # by train key, every run the same
    first = structure.stage(conn, _fid(conn, "20-K-2100A"), p)
    assert first["order"] == 1 and p.label(first["scrubber"]) == "20-V-2101A" and first["cooler_before"] is None
    assert p.label(structure.driver(conn, k, p)) == "20-GT-2000B"
    assert p.label(structure.upstream(conn, k, "HE", p)) == "20-E-2150B"
    assert p.label(structure.downstream(conn, k, "HE", p)) == "20-E-2250B"


def test_names_play_no_part(okan_db):
    """Renaming every tag (letters dropped and all) changes no train, stage or sibling."""
    conn = okan_db.conn
    before = structure.load(conn)
    k = _fid(conn, "20-K-2300C")
    want = structure.stage(conn, k, before)
    try:
        conn.execute("update entity_label set label = 'X' || md5(label) where label like '20-%%'")
        after = structure.load(conn)
        got = structure.stage(conn, k, after)
        assert (got["order"], got["scrubber"], got["cooler_before"]) == (want["order"], want["scrubber"],
                                                                         want["cooler_before"])
        assert sorted(got["siblings"]) == sorted(want["siblings"])
        assert set(structure.train(conn, k, after)["members"]) == set(want["train"]["members"])
    finally:
        conn.rollback()


def test_kestrel_compression_train_from_its_pids(request):
    conn = request.getfixturevalue("kestrel")
    p = structure.load(conn)
    k = _fid(conn, "B1-K-2001")
    t = structure.train(conn, k, p)
    assert t["known"] and _labels(p, t["members"]) == ["B1-V-2005", "B1-K-2001", "B1-E-2010", "B1-V-2012"]
    s = structure.stage(conn, k, p)
    assert s["order"] == 1 and p.label(s["scrubber"]) == "B1-V-2005" and s["siblings"] == []
    assert structure.driver(conn, k, p) is None  # no source names what turns it: not guessed
    # the spared instrument air compressors run in parallel off one receiver: each the other's counterpart
    a, b = _fid(conn, "G1-K-7004A"), _fid(conn, "G1-K-7004B")
    assert structure.stage(conn, a, p)["siblings"] == [b]


def test_what_the_data_cant_say_says_why(okan_db):
    conn = okan_db.conn
    p = structure.load(conn)
    loose = next(f for f in sorted(p.classes) if f not in p.trains and f not in p.headers and f in p.area
                 and not any(d == f for d in p.drives.values()))
    t = structure.train(conn, loose, p)
    assert not t["known"] and t["basis"]
    assert structure.stage(conn, loose, p)["known"] is False
