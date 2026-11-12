"""P2.1: a source Fuzer has never seen, added from the UI alone, contextualizes itself on both bundles: its records
match the register on their own with under 10% left for a steward, every automatic match is listed with its
confidence, and reversing one is remembered by the next rebuilds."""

import csv
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
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


def test_a_new_source_contextualizes_itself_on_okan(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        _run(okan_db.conn, client)


def test_a_new_source_contextualizes_itself_on_kestrel(request):
    from fuzer import api

    conn = request.getfixturevalue("kestrel")
    with TestClient(api.app) as client:
        _run(conn, client)
