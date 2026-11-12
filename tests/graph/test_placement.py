"""P2.1d: where a new item sits, and which drawing and documents are about it, as ranked proposals with a confidence
and their evidence. A placement two kinds of evidence agree on applies at rebuild; the rest wait for a steward, whose
answer every later rebuild keeps."""

import pytest
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.core import pack as packs
from fuzer.graph import placement

pytestmark = pytest.mark.sequence


def _fid(conn, label: str) -> str:
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def _unplace(conn, fid: str) -> str:
    """Take an item out of its area (in this transaction only), returning the area it was in."""
    area = conn.execute("select p.dst from edge p join entity a on a.fuzer_id = p.dst and a.kind = 'area' "
                        "where p.src = %s and p.rel = 'part_of'", (fid,)).fetchone()["dst"]
    conn.execute("delete from edge where src = %s and rel in ('part_of', 'located_at')", (fid,))
    return area


def _run(conn, piped: str, lone: str) -> None:
    pack = packs.current_pack(conn)
    try:
        k, v = _fid(conn, piped), _fid(conn, lone)
        area, lone_area = _unplace(conn, k), _unplace(conn, v)
        assert {k, v} <= set(placement.unplaced(conn))
        got = placement.parents(conn, pack, [k, v])
        best = got[k][0]
        assert best["target"] == area and best["confidence"] > 0.5, got[k]
        assert {"grammar", "neighbour"} <= set(best["kinds"]) and best["basis"]
        assert placement._confident(best, got[k])

        # The equipment two kinds of evidence agree on is placed at rebuild; one with only its tag's name waits.
        conn.execute("delete from edge where src = any(%s) and rel = 'pipes_to' or dst = any(%s) and rel = 'pipes_to'",
                     ([v], [v]))
        out = placement.apply(conn, pack)
        row = conn.execute("select sources, props from edge where src = %s and rel = 'part_of' and dst = %s",
                           (k, area)).fetchone()
        assert row and row["sources"] == [placement.SOURCE] and row["props"]["basis"]
        lone_best = placement.parents(conn, pack, [v])[v]
        assert out["to_confirm"] >= 1 and lone_best and lone_best[0]["target"] == lone_area
        assert not placement._confident(lone_best[0], lone_best)

        # A steward's answer is kept: rejected is never applied, accepted is applied by every rebuild.
        placement.decide(conn, k, "part_of", area, False, "ana", "it moved")
        assert not conn.execute("select 1 from edge where src = %s and rel = 'part_of'", (k,)).fetchone()
        placement.apply(conn, pack)
        assert not conn.execute("select 1 from edge where src = %s and rel = 'part_of' and dst = %s",
                                (k, area)).fetchone()
        placement.decide(conn, v, "part_of", lone_area, True, "ana")
        conn.execute("delete from edge where src = %s and rel = 'part_of'", (v,))  # as a rebuild would
        placement.apply(conn, pack)
        assert conn.execute("select 1 from edge where src = %s and rel = 'part_of' and dst = %s",
                            (v, lone_area)).fetchone()
    finally:
        conn.rollback()


def test_a_new_item_is_placed_on_okan(okan_db):
    _run(okan_db.conn, "20-K-2200B", "20-V-2002")


def test_a_new_item_is_placed_on_kestrel(request):
    _run(request.getfixturevalue("kestrel"), "B1-K-2001", "B1-V-2012")


def _drawing(conn, label: str) -> None:
    """An asset taken off its sheet: the sheets that show what it is piped to are offered, never applied."""
    try:
        k = _fid(conn, label)
        sheets = {r["src"] for r in conn.execute(
            "select src from edge where dst = %s and exists (select 1 from unnest(sources) s where s like 'pid%%')", (k,))}
        conn.execute("delete from edge where dst = %s and rel in ('mentions', 'applies_to') and src = any(%s)",
                     (k, list(sheets)))
        got = placement.documents(conn, [k], want=("drawing",))[k]
        assert got and got[0]["rel"] == "shown_on" and got[0]["target"] in sheets, got
        assert all(p["confidence"] < placement.APPLY and p["basis"] for p in got)
        placement.decide(conn, k, "shown_on", got[0]["target"], True, "ana")
        assert conn.execute("select 1 from edge where src = %s and dst = %s and %s = any(sources)",
                            (got[0]["target"], k, placement.DRAWN)).fetchone()
    finally:
        conn.rollback()


def test_a_drawing_is_suggested_on_okan(okan_db):
    _drawing(okan_db.conn, "10-V-2002")


def test_a_drawing_is_suggested_on_kestrel(request):
    _drawing(request.getfixturevalue("kestrel"), "B1-K-2001")


def test_the_asset_panel_carries_suggestions_and_takes_answers(okan_db):
    from fastapi.testclient import TestClient

    from fuzer import api

    conn = okan_db.conn
    k = _fid(conn, "20-K-2200B")
    with TestClient(api.app) as client:
        r = client.get(f"/entities/{k}/context")
        assert r.status_code == 200 and isinstance(r.json()["suggestions"], list), r.text
        bad = client.post(f"/entities/{k}/placement", json={"rel": "flows_to", "target": k, "accept": True, "by": "ana"})
        assert bad.status_code == 400
        gone = client.post(f"/entities/{k}/placement", json={"rel": "applies_to", "target": "fz-nothing", "accept": True,
                                                            "by": "ana"})
        assert gone.status_code == 404
