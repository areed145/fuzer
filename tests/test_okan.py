"""The Okan field demo: every source loads with the okan pack and the stories hang together."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from fakeplant import okan
from fuzer import db, graph, ingest, nel, pipeline, queries
from fuzer.pack import load_pack

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def setup():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzokan"))
    stats = okan.generate(data)
    with db.connect() as c:
        db.reset(c)
        loaded = ingest.ingest(c, data)
        rebuilt = pipeline.rebuild(c, PACK)
        yield c, data, stats, loaded, rebuilt


def fid(conn, kind, key):
    r = conn.execute("select fuzer_id from entity_key where kind = %s and key = %s", (kind, key)).fetchone()
    return r["fuzer_id"] if r else None


def test_every_source_loads(setup):
    conn, data, stats, loaded, _ = setup
    assert loaded["unmapped"] == [] and loaded["held"] == []
    assert {"sap_floc", "sap_equipment", "pi_points", "oplogs", "ec_downtime", "ec_day001", "gis_pipelines",
            "gis_facilities", "pi_af", "ec_network", "pid_sheets", "documents"} <= set(loaded["loaded"])
    unread = conn.execute(
        "select source, record_type, count(*) as n from source_record r where not exists "
        "(select 1 from crosswalk c where c.source = r.source and c.record_type = r.record_type "
        "and c.source_key = r.source_key) and r.record_type not in ('edge', 'bom') group by 1, 2").fetchall()
    assert unread == [], unread  # every record became (part of) an entity


def test_wells_and_facilities(setup):
    conn, data, stats, _, _ = setup
    wells = json.loads((data / "wells.json").read_text())
    keys = {r["key"] for r in conn.execute("select key from entity_key where kind = 'well'")}
    assert {w["name"] for w in wells} == keys and len(keys) == stats["wells"]
    assert {"OK-NWP-G1", "OK-NWP-G2", "OK-NWP-G3"} <= keys
    # A well's EC node, PI points and SAP location are one entity.
    w = fid(conn, "well", "OK-J07-03")
    sources = {r["source"] for r in conn.execute("select source from crosswalk where fuzer_id = %s", (w,))}
    assert {"sap", "ec"} <= sources
    assert conn.execute("select count(*) as n from edge where src = %s and rel = 'has_timeseries'", (w,)).fetchone()["n"] >= 3
    assert {m.key for m in nel.find_mentions("Train B down on OK_GGCP, lift gas short to OKA_ST2", PACK)} >= {
        "OKAN-20", "OKAN-40"}


def test_gas_lift_and_export_network(setup):
    conn, *_ = setup
    # Gas producers flow to GGCP inlet separation, compression and EGP.
    down = {r["fuzer_id"] for r in graph.trace(conn, fid(conn, "well", "OK-NWP-G2"), direction="down")}
    assert {fid(conn, "tag", "20-V-2001"), fid(conn, "tag", "20-K-2300B")} <= down
    egp = fid(conn, "allocation_node", "EGP")
    assert egp in down
    # Lift gas is its own network: production traces never enter it.
    assert fid(conn, "tag", "20-FT-3002") not in down
    lift = {r["fuzer_id"] for r in graph.trace(conn, fid(conn, "tag", "20-K-2300B"), "lift_gas_to", "down", depth=12)}
    assert {fid(conn, "tag", "20-FT-3002"), fid(conn, "tag", "30-FT-3002"), fid(conn, "tag", "40-FT-3002"),
            fid(conn, "well", "OK-J07-05")} <= lift
    # Jacket oil reaches PP and the crude terminal.
    oil = {r["fuzer_id"] for r in graph.trace(conn, fid(conn, "well", "OK-J18-02"), direction="down")}
    assert {fid(conn, "tag", "40-V-2002"), fid(conn, "tag", "10-V-2001"), fid(conn, "allocation_node", "ESCRAVOS-TERM")} <= oil


def test_gis_pipelines_connect_facilities(setup):
    conn, _, stats, _, _ = setup
    n = conn.execute("select count(*) as n from entity where kind = 'pipeline' and retired_at is null").fetchone()["n"]
    assert n == stats["pipelines"]
    pp = fid(conn, "area", "OKAN-10")
    ends = conn.execute("select count(*) as n from edge where dst = %s and rel = 'ends_at'", (pp,)).fetchone()["n"]
    assert ends >= 6  # the PP-area jackets piped straight to PP, and the satellite trunklines
    egp = fid(conn, "facility", "EGP")
    assert egp and conn.execute("select count(*) as n from edge where dst = %s and rel = 'ends_at'", (egp,)).fetchone()["n"] == 2
    geom = conn.execute("select g.value from golden g join entity e using (fuzer_id) where e.kind = 'pipeline' "
                        "and g.field = 'geometry' limit 1").fetchone()["value"]
    assert geom["type"] == "LineString" and len(geom["coordinates"]) >= 2


def test_train_b_story(setup):
    conn, *_ = setup
    k = fid(conn, "tag", "20-K-2200B")
    incidents = queries.incidents(conn, k, limit=20)["items"]
    march = [i for i in incidents if str(i.get("start", "")).startswith("2024-03")]
    assert march, incidents
    m = queries.maintenance(conn, k, limit=20)
    assert any("bearing" in (w["text"] or "").lower() for r in m["recent"] for w in r["work_orders"])


def test_drawings_and_guides(setup):
    conn, data, stats, _, _ = setup
    truth = json.loads((data / "pid_truth.json").read_text())
    assert len(truth) == stats["pid_sheets"] == 9
    assert any(t["key"] == "20-GT-2000B" and t["kind"] == "equipment" for t in truth["OKAN-20-PID-005"]["tags"])
    guide = conn.execute("select c.fuzer_id from crosswalk c where c.source = 'edms' and c.source_key = 'TG-20L-001 rev A'").fetchone()
    assert guide
    applies = {r["label"] for r in conn.execute(
        "select l.label from edge e join entity_label l on l.fuzer_id = e.dst where e.src = %s and e.rel = 'applies_to'",
        (guide["fuzer_id"],))}
    assert "20-PT-3003" in applies


def test_spatial_links_and_map(setup):
    conn, data, stats, _, rebuilt = setup
    from fastapi.testclient import TestClient

    from fuzer import evaluate
    from fuzer.api import app

    assert rebuilt["located"] == stats["gis_facilities"] + stats["pipelines"] + stats["gis_wells"]
    truth = json.loads((data / "answer_keys" / "spatial.json").read_text())
    s = evaluate.spatial(conn, PACK, truth)
    assert s["precision"] == 1.0 and s["recall"] == 1.0 and s["links"] == stats["gis_wells"] + stats["unlabelled_pipeline_ends"]
    # A legacy flowline without asset IDs now starts at its jacket and ends at its host, found by distance.
    legacy = next(t for t in truth if t["rel"] == "starts_at")
    e = conn.execute("select e.confidence, e.props from edge e join entity_label l on l.fuzer_id = e.src "
                     "where l.label = %s and e.rel = 'starts_at'", (legacy["src"],)).fetchone()
    assert e["props"]["method"] == "spatial" and e["props"]["distance_m"] < 30 and e["confidence"] > 0.9

    client = TestClient(app)
    everything = client.get("/map").json()
    assert len(everything["features"]) == rebuilt["located"]
    kinds = {f["properties"]["kind"] for f in everything["features"]}
    assert kinds == {"area", "facility", "pipeline", "well"}
    line = next(f for f in everything["features"] if f["properties"]["kind"] == "pipeline")
    assert line["geometry"]["type"] == "LineString" and line["properties"]["service"]
    near_pp = client.get("/map", params={"bbox": "4.97,5.41,5.0,5.43"}).json()["features"]
    assert 0 < len(near_pp) < len(everything["features"])

    # A compressor has no location of its own: its map shows the platform it is on, and the lines there.
    k = fid(conn, "tag", "20-K-2200B")
    m = client.get(f"/entities/{k}/map").json()
    assert m["focus"] == fid(conn, "area", "OKAN-20")
    assert any(f["properties"].get("service") == "gas export" for f in m["features"])
    # A jacket's map has its wells and its flowline and gas lift line.
    j = client.get(f"/entities/{fid(conn, 'area', 'OKAN-67')}/map").json()
    services = {f["properties"].get("service") for f in j["features"]}
    assert {"production flowline", "gas lift"} <= services
    assert sum(f["properties"]["kind"] == "well" for f in j["features"]) >= 5


def test_lift_gas_network_layout(setup):
    conn, data, *_ = setup
    net = json.loads((data / "ec_network.json").read_text())
    lift = [e for e in net["edges"] if e.get("service") == "lift_gas"]
    headers = {e["to"] for e in lift if e["to"].startswith("LG-HDR")} | {"LG-HDR-GGCP"}
    assert headers == {"LG-HDR-GGCP", "LG-HDR-ST1", "LG-HDR-ST2"}
    fed = {e["to"]: e["from"] for e in lift if e["to"].endswith("-GLM")}
    assert len(fed) == 25  # 24 jackets and NWP
    assert {fed[j] for j in fed if j.startswith("OKJ")} >= {"LG-HDR-GGCP", "LG-HDR-ST1", "LG-HDR-ST2"}
    chained = [j for j, src in fed.items() if src.endswith("-GLM")]
    assert len(chained) >= 3  # some jackets get lift gas through another jacket's manifold
    prod = {e["from"]: e["to"] for e in net["edges"] if e["from"].endswith("-PM") and not e.get("service")}
    assert all(prod[j.replace("GLM", "PM")].endswith("-PM") for j in chained)  # and send oil back the same way

    # In the graph: every gas lifted well has lift gas from a manifold that sits on its jacket.
    rows = conn.execute(
        """
        select w.fuzer_id, l.label, (select e.src from edge e where e.dst = w.fuzer_id and e.rel = 'lift_gas_to') as glm
        from entity w join golden g on g.fuzer_id = w.fuzer_id and g.field = 'lift_type' and g.value #>> '{}' = 'GL'
        left join entity_label l on l.fuzer_id = w.fuzer_id
        where w.kind = 'well'
        """).fetchall()
    assert rows and all(r["glm"] for r in rows)
    r = next(r for r in rows if r["label"] == "OK-J07-05")
    at = conn.execute("select l.label from edge e join entity_label l on l.fuzer_id = e.dst "
                      "where e.src = %s and e.rel = 'located_at'", (r["glm"],)).fetchone()["label"]
    assert at == "OKAN-67"
