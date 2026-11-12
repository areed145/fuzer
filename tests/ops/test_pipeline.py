"""End-to-end: Okan data -> embedded Postgres -> pipeline -> API."""

import os
import tempfile
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

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
        from fuzer.core import projects

        projects.save(c, "okan", {"pack": "okan"}, "tests")
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


def test_review_accept_match_survives_rebuild(conn, api):
    well = fid(conn, "well", "OK-J07-03")
    records = conn.execute(
        "select source, record_type, source_key from crosswalk where fuzer_id = %s and source in ('sap', 'ec')",
        (well,),
    ).fetchall()
    refs = {r["source"]: f"{r['source']}:{r['record_type']}:{r['source_key']}" for r in records}
    assert {"sap", "ec"} <= refs.keys()
    left, right = refs["sap"], refs["ec"]
    item = conn.execute(
        "insert into review_item (kind, subject, payload) values ('match', %s, %s) returning id",
        (f"{left}|{right}", Jsonb({"left": {"ref": left}, "right": {"ref": right}})),
    ).fetchone()

    response = api.post(f"/review/{item['id']}", json={"decision": "accept", "by": "tester"})
    assert response.status_code == 200
    from fuzer.api import rebuilder

    rebuilder.wait()
    assert "error" not in rebuilder.last
    with db.connect() as c:
        pipeline.rebuild(c, PACK)
        pinned = c.execute(
            "select rule, decided_by from pinned_link where left_ref = %s and right_ref = %s",
            (left, right),
        ).fetchone()
        assert pinned == {"rule": "must", "decided_by": "tester"}
        owners = {
            r["fuzer_id"]
            for r in c.execute(
                "select fuzer_id from crosswalk where (source, record_type, source_key) in "
                "((%s, %s, %s), (%s, %s, %s))",
                (*left.split(":", 2), *right.split(":", 2)),
            )
        }
        assert len(owners) == 1


def test_review_choose_conflict_value(conn, api):
    field = conn.execute(
        # not the plant's one Baker Hughes machine, which the search test looks for (an unordered pick sometimes was)
        "select fuzer_id, field, value from golden where field = 'manufacturer' "
        "and value #>> '{}' not ilike 'baker%%hughes' order by fuzer_id limit 1"
    ).fetchone()
    item = conn.execute(
        "insert into review_item (kind, subject, payload) values ('conflict', %s, %s) returning id",
        (f"{field['fuzer_id']}:{field['field']}",
         Jsonb({"fuzer_id": field["fuzer_id"], "field": field["field"], "chosen": field["value"]})),
    ).fetchone()

    response = api.post(
        f"/review/{item['id']}", json={"decision": "choose", "value": "Reviewed manufacturer", "by": "tester"}
    )
    assert response.status_code == 200
    assert conn.execute(
        "select value, source from golden where fuzer_id = %s and field = %s",
        (field["fuzer_id"], field["field"]),
    ).fetchone() == {"value": "Reviewed manufacturer", "source": "review"}


def test_api_search_and_entity(api):
    hits = api.get("/entities", params={"q": "20-PT-3003"}).json()
    assert any(h["label"] == "20-PT-3003" for h in hits)
    detail = api.get(f"/entities/{hits[0]['fuzer_id']}").json()
    assert detail["golden"]["description"]["source"] == "sap"


def test_search_covers_all_fields(api):
    maker = api.get("/entities", params={"q": "Baker Hughes", "kind": "equipment"}).json()
    assert maker and all(hit["kind"] == "equipment" for hit in maker)
    exact_first = api.get("/entities", params={"q": "20-V-2001"}).json()
    assert exact_first[0]["label"] == "20-V-2001"


def test_api_lookup_text(api):
    out = api.get("/lookup", params={"text": "Train B down on OK_GGCP, lift gas short to OKA_ST2"}).json()
    assert {"OKAN-20", "OKAN-40"} <= {m["key"] for m in out}
    assert all(m["fuzer_id"] for m in out)


def test_text_links_aliases_to_tags_and_area(conn):
    entry = conn.execute(
        "select fuzer_id from golden where field = 'text' and value::text like %s limit 1",
        ("%GGCP inlet separator hunting%",),
    ).fetchone()["fuzer_id"]
    linked = {
        r["label"]
        for r in conn.execute(
            "select l.label from edge e join entity_label l on l.fuzer_id = e.dst where e.src = %s",
            (entry,),
        )
    }
    assert {"20-LT-2001", "20-V-2001", "OKAN-20"} <= linked


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


def test_graph_honors_dated_installations(api, conn):
    old = conn.execute(
        "select e.dst, e.valid_to, l.label from edge e join entity_label l on l.fuzer_id = e.src "
        "where e.rel = 'installed_at' and e.valid_to is not null limit 1"
    ).fetchone()
    new = conn.execute(
        "select e.valid_from, l.label from edge e join entity_label l on l.fuzer_id = e.src "
        "where e.rel = 'installed_at' and e.dst = %s and e.valid_to is null",
        (old["dst"],),
    ).fetchone()
    before = api.get(
        f"/graph/{old['dst']}", params={"as_of": (old["valid_to"] - timedelta(days=1)).isoformat()}
    ).json()
    after = api.get(f"/graph/{old['dst']}", params={"as_of": old["valid_to"].isoformat()}).json()
    before_labels = {node["label"] for node in before["nodes"]}
    after_labels = {node["label"] for node in after["nodes"]}
    assert old["label"] in before_labels and new["label"] not in before_labels
    assert new["label"] in after_labels and old["label"] not in after_labels


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
