"""The register's containment as a tree (Explore's entity pages): up to the facility, down two levels, work rolled up."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.api import entities
from fuzer.graph import hierarchy

pytestmark = pytest.mark.integration


def _id(conn, label: str) -> str:
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def test_a_facility_holds_its_areas_and_an_area_its_tags(okan_db):
    conn = okan_db.conn
    okan = _id(conn, "OKAN")
    out = hierarchy.beneath(conn, okan)
    kinds = {c["kind"] for c in out["children"]}
    assert "area" in kinds
    ggcp = next(c for c in out["children"] if c["label"] == "OKAN-20")
    assert ggcp["children"], "two levels: the area's own children are listed"
    assert ggcp["beneath"] >= len(ggcp["children"])
    assert out["total"] >= sum(1 + c["beneath"] for c in out["children"])
    # Places first, then machines, then instruments.
    ranks = [hierarchy.KIND_RANK.get(c["kind"], 50) for c in out["children"]]
    assert ranks == sorted(ranks)
    # No schedule activities or document sections in the asset tree.
    assert not set(out["kinds"]) & set(hierarchy.OFF_TREE)


def test_up_walks_to_the_facility(okan_db):
    conn = okan_db.conn
    chain = hierarchy.up(conn, _id(conn, "20-K-2100A"))
    assert [c["kind"] for c in chain][-1] == "facility"
    assert any(c["label"] == "OKAN-20" for c in chain)


def test_the_api_rolls_open_work_up_to_the_area(okan_db):
    app = FastAPI()
    app.include_router(entities.router)
    client = TestClient(app)
    conn = okan_db.conn
    r = client.get(f"/entities/{_id(conn, 'OKAN')}/hierarchy")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["up"] == []
    for c in data["children"]:
        assert c["open_work"] <= data["open_work"]
        assert all(g["open_work"] <= c["open_work"] for g in c["children"]), "a child's work is inside its parent's"
    assert client.get("/entities/fz-nothing/hierarchy").status_code == 404
