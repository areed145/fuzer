"""End-to-end: Okan data -> embedded Postgres -> pipeline -> API."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from fakeplant import okan
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.ops import pipeline
from fuzer.sources import ingest

PACK = load_pack("okan")

pytestmark = pytest.mark.sequence


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ["FUZER_PGDATA"] = tempfile.mkdtemp(prefix="fzpg")
    data = tempfile.mkdtemp(prefix="fzdata")
    okan.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        c.data = data
    with db.connect(autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def api(conn):
    from fuzer.api import app

    return TestClient(app)


def fid(conn, kind, key):
    row = conn.execute("select fuzer_id from entity_key where kind = %s and key = %s", (kind, key)).fetchone()
    return row["fuzer_id"]


def test_well_identity_merges_across_sources(conn):
    well = fid(conn, "well", "OK-J07-03")
    sources = {r["source"] for r in conn.execute("select source from crosswalk where fuzer_id = %s", (well,))}
    assert {"sap", "ec"} <= sources


def test_ids_stable_across_rebuild(conn):
    before = {(r["source"], r["source_key"]): r["fuzer_id"] for r in conn.execute("select * from crosswalk")}
    with db.connect() as c:
        pipeline.rebuild(c, PACK)
    after = {(r["source"], r["source_key"]): r["fuzer_id"] for r in conn.execute("select * from crosswalk")}
    assert before == after


def test_api_search_and_entity(api):
    hits = api.get("/entities", params={"q": "20-PT-3003"}).json()
    assert any(h["label"] == "20-PT-3003" for h in hits)
    detail = api.get(f"/entities/{hits[0]['fuzer_id']}").json()
    assert detail["golden"]["description"]["source"] == "sap"


def test_api_lookup_text(api):
    out = api.get("/lookup", params={"text": "Train B down on OK_GGCP, lift gas short to OKA_ST2"}).json()
    assert {"OKAN-20", "OKAN-40"} <= {m["key"] for m in out}
    assert all(m["fuzer_id"] for m in out)


def test_api_ontology(api):
    ontology = api.get("/ontology").json()
    kinds = {item["kind"]: item for item in ontology["kinds"]}
    assert {"tag", "equipment", "well", "timeseries"} <= set(kinds)
    assert "sap" in {source["source"] for source in kinds["equipment"]["sources"]}
    rels = {(r["src_kind"], r["rel"], r["dst_kind"]): r for r in ontology["relations"]}
    assert ("well", "flows_to", "allocation_node") in rels


def test_api_neighborhood_and_network(api, conn):
    well = fid(conn, "well", "OK-NWP-G2")
    graph = api.get(f"/graph/{well}").json()
    assert graph["root"] == well and graph["nodes"]
    ids = {node["id"] for node in graph["nodes"]}
    assert all(edge["src"] in ids and edge["dst"] in ids for edge in graph["edges"])
    network = api.get("/network").json()
    assert network["nodes"] and network["edges"]


def test_snapshot_ingest_removes_and_retires(conn, tmp_path):
    data = tmp_path / "okan"
    okan.generate(data)
    lines = (data / "oplogs.csv").read_text().splitlines()
    (data / "oplogs.csv").write_text("\n".join(lines[:-1]) + "\n")
    with db.connect() as c:
        db.init_schema(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        assert c.execute("select count(*) as n from source_record where source = 'oplog'").fetchone()["n"] == len(lines) - 2
        assert c.execute("select count(*) as n from entity where kind = 'log_entry' and retired_at is not null").fetchone()["n"] >= 1
