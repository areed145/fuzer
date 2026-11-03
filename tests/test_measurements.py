"""Measurements (#62): well tests, lab results and wall thickness readings land in the measurement
table against the wells, tags and pipelines they were taken on, in stored units, and show up next to
process values (API, troubleshooting brief, MCP)."""

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fakeplant import okan
from fuzer import mcp_server
from fuzer.core import db, units
from fuzer.core.pack import load_pack
from fuzer.graph import measurements, pipeline, queries
from fuzer.sources import ingest, mappings

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzokan"))
    okan.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        c.stats = pipeline.rebuild(c, PACK)
        yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_units_from_reference_data():
    assert units.normalise(0.5, "in") == (12.7, "mm")
    assert units.normalise(100, "psig")[1] == "barg" and round(units.normalise(100, "psig")[0], 2) == 6.89
    assert units.normalise(862, "kg/m3") == (0.862, "g/cm3")
    assert round(units.normalise(14, "degF")[0], 1) == -10.0
    assert units.normalise(3, "% vol") == (3, "%") and units.normalise(3, "furlongs") == (3, "furlongs")
    assert units.reading("<5") == (5.0, "<") and units.reading("1,250") == (1250.0, None)
    assert units.reading("n/a") is None and units.reading("") is None


def test_mapping_reads_one_row_into_several_quantities():
    m = mappings.load()[("ec", "well_test")]
    out = m.measure("WT-1", {"well": "OK-J03-02", "test_date": "2025-06-12", "oil_sm3d": "41.5",
                             "water_cut_pct": "72", "gor_sm3_sm3": "120", "lift_gas_ksm3d": "", "whp_barg": "18"}, PACK)
    assert [(x.quantity, x.value, x.unit) for x in out] == [
        ("oil_rate", 41.5, "Sm3/d"), ("water_cut", 72.0, "%"), ("gor", 120.0, "Sm3/Sm3"), ("wellhead_pressure", 18.0, "barg")]
    assert out[0].of == [("well", "OK-J03-02")] and out[0].method == "well test" and out[0].record == "ec:well_test:WT-1"

    lab = mappings.load()[("lims", "result")].measure("S1>oil_in_water", {
        "tag": "10-V-2001", "well": "", "sample_point": "water outlet", "sampled_at": "2025-07-01T09:00:00",
        "analyte": "oil_in_water", "result": "<5", "unit": "mg/l", "method": "IR"}, PACK)
    assert [(x.quantity, x.value, x.qualifier, x.unit, x.location) for x in lab] == [
        ("oil_in_water", 5.0, "<", "mg/L", "water outlet")]
    assert lab[0].of[0][0] == "tag"


def test_measurements_land_on_wells_tags_and_pipelines(conn):
    assert conn.stats["measurements"] > 20000 and conn.stats["unresolved_measurements"] == {}
    kinds = {r["kind"] for r in conn.execute("select distinct e.kind from measurement m join entity e using (fuzer_id)")}
    assert kinds == {"well", "tag", "pipeline"}
    # Older surveys reported in inches are stored in mm, with what the source said kept.
    old = conn.execute("select value, unit, reported from measurement where quantity = 'wall_thickness' "
                       "and at < '2021-01-01' limit 1").fetchone()
    assert old["unit"] == "mm" and old["reported"].endswith(" in") and 5 < old["value"] < 30


def test_well_test_history_and_the_planted_stories(conn):
    well = fid(conn, "OK-J03-02")
    out = measurements.for_entity(conn, well)
    by = {s["quantity"]: s for s in out["series"]}
    assert {"oil_rate", "water_cut", "chloride"} <= set(by)
    assert by["oil_rate"]["count"] == 24 and by["oil_rate"]["of"]["id"] == well
    chloride = [v["value"] for v in by["chloride"]["values"]]
    assert sum(chloride[-5:]) / 5 > 1.5 * sum(chloride[:5]) / 5  # formation water after the breakthrough

    # J03's flowline loses wall much faster than the others.
    rates = {}
    for r in conn.execute("select distinct fuzer_id from measurement m join entity e using (fuzer_id) "
                          "where e.kind = 'pipeline'"):
        s = measurements.for_entity(conn, r["fuzer_id"], "wall_thickness")["series"]
        rates[r["fuzer_id"]] = min(x["per_year"] for x in s)
    worst = min(rates, key=rates.get)
    assert rates[worst] < -0.35 and sorted(rates.values())[1] > -0.2
    assert "production flowline" in queries.entity(conn, worst, per_group=1)["golden"]["description"]["value"]


def test_tag_page_brief_and_mcp(conn):
    sep = fid(conn, "10-V-2001")
    series = measurements.for_entity(conn, sep)["series"]
    assert {(s["quantity"], s["location"]) for s in series} >= {
        ("oil_in_water", "HP separator water outlet"), ("bsw", "HP separator oil outlet"), ("wall_thickness", "CML-01")}
    oiw = next(s for s in series if s["quantity"] == "oil_in_water")
    assert oiw["latest"]["value"] > 30  # up since the breakthrough

    from fuzer import api

    client = TestClient(api.app)
    r = client.get(f"/entities/{sep}/measurements", params={"quantity": "oil_in_water", "since": "2025-01-01"})
    assert r.status_code == 200 and {s["quantity"] for s in r.json()["series"]} == {"oil_in_water"}
    assert all(v["at"] >= "2025-01-01" for v in r.json()["series"][0]["values"])

    from fuzer import assistant

    brief = assistant.brief(conn, PACK, sep)
    assert any(s["quantity"] == "oil_in_water" and len(s["recent"]) == 3 for s in brief["measurements"])
    assert "values" not in brief["measurements"][0]

    got = mcp_server.measurements("OK-J03-02", quantity="water_cut")
    assert got["series"][0]["quantity"] == "water_cut" and got["series"][0]["unit"] == "%"
