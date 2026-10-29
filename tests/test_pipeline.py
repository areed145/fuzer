"""End-to-end: fake data -> embedded Postgres -> pipeline -> API."""

import os
import tempfile
from datetime import date

import pytest
from fastapi.testclient import TestClient

from fuzer import db, fakedata, graph, ingest, pipeline
from fuzer.pack import load_pack

PACK = load_pack()


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ["FUZER_PGDATA"] = tempfile.mkdtemp(prefix="fzpg")  # short path: socket length limit
    data = tempfile.mkdtemp(prefix="fzdata")
    fakedata.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
    with db.connect(autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def api(conn):
    from fuzer.api import app
    return TestClient(app)


def fid(conn, kind, key):
    return conn.execute("select fuzer_id from entity_key where kind = %s and key = %s", (kind, key)).fetchone()["fuzer_id"]


def records(conn, fuzer_id):
    return {(r["source"], r["source_key"]) for r in conn.execute(
        "select source, source_key from crosswalk where fuzer_id = %s", (fuzer_id,))}


def test_tag_merges_across_sources(conn):
    srcs = {s for s, _ in records(conn, fid(conn, "tag", "20-V-2001"))}
    assert srcs == {"sap", "pi_af", "ec"}


def test_pi_point_linked_to_instrument_tag(conn):
    tag = fid(conn, "tag", "23-PT-1001A")
    ts = fid(conn, "timeseries", "OSB:23-PT-1001A.DACA.PV")
    assert conn.execute("select 1 from edge where src = %s and rel = 'has_timeseries' and dst = %s",
                        (tag, ts)).fetchone()


def test_swapped_pump_is_two_items_at_one_tag(conn):
    tag = fid(conn, "tag", "20-P-1001B")
    rows = conn.execute("select src, valid_from, valid_to from edge where dst = %s and rel = 'installed_at' "
                        "order by valid_from", (tag,)).fetchall()
    assert [(r["valid_from"], r["valid_to"]) for r in rows] == [
        (date(2018, 1, 1), date(2023, 6, 1)), (date(2023, 6, 1), None)]
    old_sources = {s for s, _ in records(conn, rows[0]["src"])}
    assert old_sources == {"sap", "jde"}  # stale JDE record matched the retired pump


def test_review_queue_has_planted_cases(conn):
    items = conn.execute("select kind, payload from review_item where status = 'open'").fetchall()
    matches = [i["payload"] for i in items if i["kind"] == "match"]
    conflicts = [i["payload"] for i in items if i["kind"] == "conflict"]
    assert any(m["left"]["block"] == "23-K-1001B" for m in matches)
    assert any({v["value"] for v in c["values"]} == {"Sulzer", "Flowserve"} for c in conflicts)
    assert not any("K.S.B." in {v["value"] for v in c["values"]} for c in conflicts)


def test_trace_respects_dates(conn):
    sep = fid(conn, "tag", "20-V-2001")
    before = {r["label"] for r in graph.trace(conn, sep, as_of=date(2024, 1, 1))}
    after = {r["label"] for r in graph.trace(conn, sep, as_of=date(2024, 6, 1))}
    assert "A-03" not in before and "A-03" in after
    assert "A-07" not in after and "A-07 ST1" in after


def test_log_mentions_link_aliases_and_short_tags(conn):
    entry = conn.execute("select fuzer_id from golden where field = 'text' and value::text like %s limit 1",
                         ("%lt-2002%",)).fetchone()["fuzer_id"]
    linked = {r["label"] for r in conn.execute(
        "select l.label from edge e join entity_label l on l.fuzer_id = e.dst where e.src = %s", (entry,))}
    assert linked == {"20-LT-2002", "20-V-2002"}


def test_ids_stable_across_rebuild(conn):
    before = {(r["source"], r["source_key"]): r["fuzer_id"] for r in conn.execute("select * from crosswalk")}
    with db.connect() as c:
        pipeline.rebuild(c, PACK)
    after = {(r["source"], r["source_key"]): r["fuzer_id"] for r in conn.execute("select * from crosswalk")}
    assert before == after


def test_api_search_and_entity(api):
    hits = api.get("/entities", params={"q": "OSB.20PT2001.PV"}).json()
    assert any(h["label"] == "20-PT-2001" for h in hits)
    detail = api.get(f"/entities/{hits[0]['fuzer_id']}").json()
    assert detail["golden"]["description"]["source"] == "sap"


def test_api_lookup_text(api):
    out = api.get("/lookup", params={"text": "Lined up A-03 to test sep"}).json()
    assert [m["key"] for m in out] == ["A-03", "20-V-2002"]
    assert all(m["fuzer_id"] for m in out)


def test_review_accept_match_survives_rebuild(conn, api):
    item = next(i for i in api.get("/review", params={"kind": "match"}).json()
                if i["payload"]["left"]["block"] == "23-K-1001B")
    left, right = item["payload"]["left"]["ref"], item["payload"]["right"]["ref"]
    api.post(f"/review/{item['id']}", json={"decision": "accept", "by": "tester"}).raise_for_status()
    with db.connect() as c:
        pipeline.rebuild(c, PACK)

    def owner(ref):
        s, rt, k = ref.split(":", 2)
        return conn.execute("select fuzer_id from crosswalk where source = %s and record_type = %s "
                            "and source_key = %s", (s, rt, k)).fetchone()["fuzer_id"]
    assert owner(left) == owner(right)


def test_review_choose_value_wins(conn, api):
    item = api.get("/review", params={"kind": "conflict"}).json()[0]
    fz, field = item["payload"]["fuzer_id"], item["payload"]["field"]
    api.post(f"/review/{item['id']}", json={"decision": "choose", "value": "Flowserve", "by": "tester"}).raise_for_status()
    g = conn.execute("select value, source from golden where fuzer_id = %s and field = %s", (fz, field)).fetchone()
    assert (g["value"], g["source"]) == ("Flowserve", "review")


def test_api_ontology(api):
    o = api.get("/ontology").json()
    kinds = {k["kind"]: k for k in o["kinds"]}
    assert {"tag", "equipment", "well", "timeseries"} <= set(kinds)
    assert {s["source"] for s in kinds["equipment"]["sources"]} >= {"sap", "jde"}
    rels = {(r["src_kind"], r["rel"], r["dst_kind"]): r for r in o["relations"]}
    assert rels[("equipment", "installed_at", "tag")]["dated"] == rels[("equipment", "installed_at", "tag")]["n"]
    assert ("well", "flows_to", "allocation_node") in rels


def test_api_neighborhood_and_network(api, conn):
    sep = fid(conn, "tag", "20-V-2001")
    one = api.get(f"/graph/{sep}").json()
    two = api.get(f"/graph/{sep}", params={"depth": 2}).json()
    assert one["root"] == sep and len(two["nodes"]) > len(one["nodes"])
    ids = {n["id"] for n in two["nodes"]}
    assert all(e["src"] in ids and e["dst"] in ids for e in two["edges"])
    flow_only = api.get(f"/graph/{sep}", params={"rels": "flows_to"}).json()
    assert {e["rel"] for e in flow_only["edges"]} == {"flows_to"}

    labels = lambda g: {n["label"] for n in g["nodes"]}
    everything = api.get("/network").json()
    on_date = api.get("/network", params={"as_of": "2024-01-01"}).json()
    assert "A-07" in labels(everything) and "A-07" not in labels(on_date)
