"""P2.1: a source Fuzer has never seen, added from the UI alone, contextualizes itself on both bundles: its records
match the register on their own with under 10% left for a steward, what matched nothing is one steward case that one
answer settles, every automatic match is listed with its confidence, and reversing one is remembered by the next
rebuilds."""

import csv
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from test_add_source_ui_only import add_source
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.core import pack as packs
from fuzer.graph import contextualized
from fuzer.ops import pipeline

pytestmark = pytest.mark.sequence


def _style(tag: str, i: int) -> str:
    """An inspection contractor's way of writing a tag: as the register does, in lower case, or spaced."""
    return (tag, tag.lower(), tag.replace("-", " "))[i % 3]


def _inspections(conn, folder: Path, n: int = 60) -> list[str]:
    """An inspection contractor's export of the register's top-level items, plus one item the register lacks."""
    rows = conn.execute(
        """
        select l.label as tag, g.value #>> '{}' as descr from entity e join entity_label l using (fuzer_id)
        left join golden g on g.fuzer_id = e.fuzer_id and g.field = 'description'
        where e.kind = 'tag' and e.merged_into is null and l.label ~ '-' and not exists (
            select 1 from edge p join entity a on a.fuzer_id = p.dst and a.kind = 'tag'
            where p.src = e.fuzer_id and p.rel = 'part_of')
        order by md5(l.label) limit %s
        """, (n,)).fetchall()
    with (folder / "inspections.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Equipment Tag", "Description", "Inspected", "Result"])
        w.writeheader()
        for i, r in enumerate(rows):
            w.writerow({"Equipment Tag": _style(r["tag"], i), "Description": r["descr"] or "",
                        "Inspected": f"2026-10-{1 + i % 28:02d}", "Result": "pass" if i % 5 else "follow up"})
        w.writerow({"Equipment Tag": "99-X-9999", "Description": "Temporary skid", "Inspected": "2026-10-01",
                    "Result": "pass"})
    return [r["tag"] for r in rows]


def _run(conn, client) -> None:
    folder = Path(tempfile.mkdtemp(prefix="fzinsp"))
    tags = _inspections(conn, folder)
    _, out, _ = add_source(client, "inspect", folder, "inspections")
    assert out["load"]["inserted"] == len(tags) + 1
    pipeline.rebuild(conn, packs.current_pack(conn))
    conn.commit()

    r = client.get("/sources/systems/inspect/context").json()
    assert r["records"] == len(tags) + 1 and r["expects_match"] == ["tag"]
    assert r["share"] < contextualized.TARGET, r  # under 10% needs a steward
    assert r["decided"]["key"] + r["decided"]["score"] >= 0.9 * len(tags)
    assert [u["label"] for u in r["unmatched"]] == ["99-X-9999"]  # the one the register lacks, named as written
    assert all(a["confidence"] > 0 and a["basis"] and a["with"] for a in r["automatic"])

    # What matched nothing is one case for the steward, whichever many records it is, and one answer settles it.
    case = conn.execute("select x.title, x.item_id, w.role from exception_record x join work_item w on w.id = x.item_id "
                        "where x.type = 'unmatched_records' and x.key = 'inspect' and x.state = 'open'").fetchone()
    assert case and case["title"] == "1 INSPECT record matches nothing in the register"
    assert "data_steward" in (case["role"] or "")
    done = client.post("/sources/systems/inspect/context/new", json={"by": "ana", "reason": "a temporary skid"})
    assert done.status_code == 200 and done.json()["accepted"] == 1, done.text
    assert client.get("/sources/systems/inspect/context").json()["steward"]["unmatched"] == 0
    assert conn.execute("select state from exception_record where type = 'unmatched_records' and key = 'inspect' "
                        "order by id desc limit 1").fetchone()["state"] != "open"

    # Not this record: the reversal is a pinned decision, kept by every rebuild after it.
    a = next(a for a in r["automatic"] if a["method"] == "key")
    done = client.post("/stewardship/split", json={"fuzer_id": a["fuzer_id"], "ref": a["ref"], "by": "ana",
                                                   "reason": "the contractor inspected the spare"})
    assert done.status_code == 200, done.text
    for _ in range(2):
        pipeline.rebuild(conn, packs.current_pack(conn))
        conn.commit()
        again = client.get("/sources/systems/inspect/context").json()
        assert a["ref"] not in {x["ref"] for x in again["automatic"]}
        row = conn.execute("select fuzer_id from crosswalk where source = 'inspect' and source_key = %s",
                           (a["source_key"],)).fetchone()
        assert row["fuzer_id"] != a["fuzer_id"]
        assert again["steward"]["unmatched"] == 0 or [u["source_key"] for u in again["unmatched"]] == [a["source_key"]]


def test_a_new_source_contextualizes_itself_on_okan(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        _run(okan_db.conn, client)


def test_a_new_source_contextualizes_itself_on_kestrel(request):
    from fuzer import api

    conn = request.getfixturevalue("kestrel")
    with TestClient(api.app) as client:
        _run(conn, client)


def _unlink(conn, client) -> None:
    """Not this link: one link a source alone states is left out of every later build, and comes back when taken back."""
    e = conn.execute("select src, rel, dst, sources[1] as source from edge where cardinality(sources) = 1 "
                     "and rel = 'has_timeseries' and valid_to is null order by src, dst limit 1").fetchone()
    listed = client.get(f"/sources/systems/{e['source']}/context").json()["links"]
    assert listed["listed"] and all(x["basis"] and x["confidence"] > 0 for x in listed["listed"])
    body = {"src": e["src"], "rel": e["rel"], "dst": e["dst"], "by": "ana", "reason": "that point is on the spare"}
    assert client.post(f"/sources/systems/{e['source']}/links/unlink", json=body).status_code == 200
    gone = "select 1 from edge where src = %s and rel = %s and dst = %s"
    for _ in range(2):
        pipeline.rebuild(conn, packs.current_pack(conn))
        conn.commit()
        assert conn.execute(gone, (e["src"], e["rel"], e["dst"])).fetchone() is None
    after = client.get(f"/sources/systems/{e['source']}/context").json()["links"]
    assert [(u["src"], u["dst"]) for u in after["unlinked"]] == [(e["src"], e["dst"])]
    assert client.post(f"/sources/systems/{e['source']}/links/relink", json=body).status_code == 200
    pipeline.rebuild(conn, packs.current_pack(conn))
    conn.commit()
    assert conn.execute(gone, (e["src"], e["rel"], e["dst"])).fetchone()


def test_not_this_link_is_remembered_on_okan(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        _unlink(okan_db.conn, client)


def test_not_this_link_is_remembered_on_kestrel(request):
    from fuzer import api

    conn = request.getfixturevalue("kestrel")
    with TestClient(api.app) as client:
        _unlink(conn, client)


def _links_route(conn) -> None:
    """A missing parent has a route: each source and link type whose values named nothing on the last rebuild is one
    open case for the data steward, and it clears once they all resolve."""
    from fuzer.graph.contextualized import LINKS_TYPE, sync_links

    pipeline.rebuild(conn, packs.current_pack(conn))
    conn.commit()
    want = {f"{r['source']}:{rel}" for r in conn.execute(
        "select distinct on (source) source, stats->'coverage'->'unresolved' as u from source_match_stat "
        "order by source, id desc").fetchall() for rel, v in (r["u"] or {}).items() if v.get("rows")}
    open_ = "select key, title, item_id from exception_record where type = %s and state = 'open'"
    assert {r["key"] for r in conn.execute(open_, (LINKS_TYPE.name,))} == want

    # A source whose next build names a parent the register lacks: one case, saying how many rows and which value.
    src = conn.execute("select source from source_match_stat order by id desc limit 1").fetchone()["source"]
    stats = conn.execute("select stats from source_match_stat where source = %s order by id desc limit 1",
                         (src,)).fetchone()["stats"]
    bad = {**stats, "coverage": {**(stats.get("coverage") or {}), "unresolved": {
        "part_of": {"rows": 3, "values": [{"value": "NO-SUCH-PARENT", "rows": 3}]}}}}
    clean = {**stats, "coverage": {**(stats.get("coverage") or {}), "unresolved": {}}}
    added = []
    try:
        added.append(conn.execute("insert into source_match_stat (source, stats) values (%s, %s) returning id",
                                  (src, Jsonb(bad))).fetchone()["id"])
        sync_links(conn)
        case = conn.execute(open_ + " and key = %s", (LINKS_TYPE.name, f"{src}:part_of")).fetchone()
        assert case and case["title"] == f"3 {src.upper()} rows name a parent the register doesn't have"
        role = conn.execute("select role from work_item where id = %s", (case["item_id"],)).fetchone()["role"]
        assert "data_steward" in (role or "")

        added.append(conn.execute("insert into source_match_stat (source, stats) values (%s, %s) returning id",
                                  (src, Jsonb(clean))).fetchone()["id"])
        assert sync_links(conn)["cleared"] >= 1
        assert not conn.execute(open_ + " and key like %s", (LINKS_TYPE.name, f"{src}:%")).fetchone()
    finally:
        conn.execute("delete from source_match_stat where id = any(%s)", (added,))
        sync_links(conn)
        conn.commit()


def test_a_missing_parent_is_one_steward_case_on_okan(okan_db):
    _links_route(okan_db.conn)


def test_a_missing_parent_is_one_steward_case_on_kestrel(request):
    _links_route(request.getfixturevalue("kestrel"))
