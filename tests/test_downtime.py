"""EC downtime / deferment and DAY001 daily reports."""

import os
import tempfile

import pytest

from fuzer import db, fakedata, ingest, pipeline, queries
from fuzer.pack import load_pack

PACK = load_pack()


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    fakedata.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_downtime_links_wells_facility_and_causes(conn):
    well = queries.downtime(conn, fid(conn, "A-11"))
    assert well["events"] >= 1 and all(r["affects"][0]["label"] == "A-11" for r in well["recent"])
    k = queries.downtime(conn, fid(conn, "23-K-1001A"))  # named in the planted comment
    assert any("23-K-1001A" in r["comment"] for r in k["recent"])
    facility = queries.downtime(conn, fid(conn, "OSB"))
    assert facility["lost_oil_sm3"] > k["lost_oil_sm3"] > 0


def test_rollup_to_class(conn):
    compressors = queries.downtime(conn, fid(conn, "ISO14224:CO"))
    assert compressors["events"] >= queries.downtime(conn, fid(conn, "23-K-1001A"))["events"]


def test_rankings(conn):
    by_reason = {r["label"]: r for r in queries.downtime_ranking(conn, "reason")}
    assert {"Planned maintenance", "Weather", "Well intervention"} <= set(by_reason)
    by_tag = queries.downtime_ranking(conn, "tag")
    assert by_tag and by_tag[0]["lost_oil_sm3"] >= by_tag[-1]["lost_oil_sm3"]
    by_class = queries.downtime_ranking(conn, "class")
    assert {r["label"].split(".")[0] for r in by_class} <= {"ISO14224:CO", "ISO14224:PU"}


def test_reason_code_mismatch_flagged(conn):
    checks = queries.downtime_reason_checks(conn)
    assert any("23-K-1001A" in c["issue"] and "weather" in c["issue"] for c in checks)
    # A well intervention that names only the well is not a mismatch.
    assert not any("A-11" in c["issue"] for c in checks)


def test_since_filter(conn):
    all_ = queries.downtime(conn)
    recent = queries.downtime(conn, since="2024-01-01")
    assert 0 < recent["events"] < all_["events"]


def test_daily_reports(conn):
    e = queries.entity(conn, fid(conn, "2024-02-03"), pack=PACK)
    assert e["kind"] == "daily_report" and e["golden"]["oil_sm3"]["value"] > 0
    linked = {x["other_label"] for x in e["edges"] if x["rel"] == "mentions"}
    assert "23-K-1001A" in linked
    assert any(x["rel"] == "reports_on" and x["other_label"] == "OSB" for x in e["edges"])


def test_api(conn):
    from fastapi.testclient import TestClient
    from fuzer.api import app
    client = TestClient(app)
    assert client.get("/downtime/ranking", params={"by": "reason"}).status_code == 200
    assert client.get("/downtime", params={"limit": 3}).json()["recent"][0]["start"]
    assert client.get("/downtime/ranking", params={"by": "nonsense"}).status_code == 422
