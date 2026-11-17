"""EC downtime / deferment and DAY001 daily reports."""


import pytest

from fuzer.core.pack import load_pack
from fuzer.graph import queries

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_downtime_links_wells_facility_and_causes(conn):
    well = queries.downtime(conn, fid(conn, "OK-J07-03"))
    assert well["events"] >= 1 and all(r["affects"] for r in well["recent"])
    compressor = queries.downtime(conn, fid(conn, "20-K-2200B"))
    facility = queries.downtime(conn, fid(conn, "OKAN"))
    assert facility["lost_oil_bbl"] > compressor["lost_oil_bbl"] > 0


def test_rollup_to_class(conn):
    compressors = queries.downtime(conn, fid(conn, "ISO14224:CO"))
    assert compressors["events"] >= queries.downtime(conn, fid(conn, "20-K-2200B"))["events"] > 0


def test_rankings(conn):
    by_reason = {r["label"]: r for r in queries.downtime_ranking(conn, "reason")}
    assert by_reason and all(r["lost_oil_bbl"] > 0 for r in by_reason.values())  # no "Hydrates 0" (#403)
    by_tag = queries.downtime_ranking(conn, "tag")
    tags = [r for r in by_tag if not r.get("unattributed")]
    assert tags and tags[0]["lost_oil_bbl"] >= tags[-1]["lost_oil_bbl"]
    by_class = queries.downtime_ranking(conn, "class")
    assert {r["label"].split(".")[0] for r in by_class if not r.get("unattributed")} <= {"ISO14224:CO", "ISO14224:VE"}


def test_rankings_account_for_the_whole_total(conn):
    """Loss that names no equipment shows as its own row, so the cards add up to the header (#403)."""
    total = queries.downtime(conn)["lost_oil_bbl"]
    by_reason = queries.downtime_ranking(conn, "reason", limit=200)
    assert abs(sum(r["lost_oil_bbl"] for r in by_reason) - total) < 1
    by_tag = queries.downtime_ranking(conn, "tag", limit=200)
    rest = [r for r in by_tag if r.get("unattributed")]
    assert len(rest) == 1 and by_tag[-1] is rest[0] and rest[0]["fuzer_id"] is None
    unattributed = sum(r["lost_oil_bbl"] for r in queries.downtime(conn, limit=1000)["recent"]
                       if not r["caused_by"] and r["lost_oil_bbl"])
    assert abs(rest[0]["lost_oil_bbl"] - unattributed) < 1


def test_reason_code_mismatch_flagged(conn):
    checks = queries.downtime_reason_checks(conn)
    assert isinstance(checks, list)


def test_since_filter(conn):
    all_ = queries.downtime(conn)
    recent = queries.downtime(conn, since="2024-04-01")
    assert 0 <= recent["events"] < all_["events"]


def test_daily_reports(conn):
    e = queries.entity(conn, fid(conn, "2024-03-14"), pack=PACK)
    assert e["kind"] == "production_day" and e["golden"]["oil_bbl"]["value"] > 0
    assert any(x["rel"] == "reports_on" and x["other_label"] == "OKAN" for x in e["edges"])


def test_api(conn):
    from fastapi.testclient import TestClient

    from fuzer.api import app
    client = TestClient(app)
    assert client.get("/downtime/ranking", params={"by": "reason"}).status_code == 200
    assert client.get("/downtime", params={"limit": 3}).json()["recent"][0]["start"]
    assert client.get("/downtime/ranking", params={"by": "nonsense"}).status_code == 422


def test_status_gives_the_newest_downtime_incident_and_well_test(conn):
    """The entity page's status line: a well's latest test and downtime, a compressor's newest incident."""
    well = queries.status(conn, fid(conn, "OK-J07-03"))
    newest = queries.downtime(conn, fid(conn, "OK-J07-03"), limit=1)["recent"][0]
    assert well["downtime"]["id"] == newest["id"] and well["downtime"]["open"] == (not newest["end"])
    assert well["well_test"]["at"] and {v["quantity"] for v in well["well_test"]["values"]} >= {"oil_rate"}
    compressor = queries.status(conn, fid(conn, "20-K-2200B"))
    assert compressor["well_test"] is None
    assert compressor["incident"]["total"] == queries.incidents(conn, fid(conn, "20-K-2200B"))["total"] > 0


def test_planned_apart_from_unplanned(conn):
    """#1078: the planned shutdowns are counted apart, the rankings can leave them out, and by month adds up."""
    total = queries.downtime(conn)
    planned = total["planned"]
    assert 0 < planned["events"] < total["events"]
    unplanned = sum(r["lost_oil_bbl"] for r in queries.downtime_ranking(conn, "reason", planned=False))
    only = sum(r["lost_oil_bbl"] for r in queries.downtime_ranking(conn, "reason", planned=True))
    assert unplanned + only == pytest.approx(total["lost_oil_bbl"], abs=1)
    assert only == pytest.approx(planned["lost_oil_bbl"], abs=1)
    months = queries.downtime_monthly(conn)
    assert [m["month"] for m in months] == sorted(m["month"] for m in months)
    assert sum(m["planned_events"] + m["unplanned_events"] for m in months) == total["events"]
    assert sum(m["planned_bbl"] for m in months) == pytest.approx(planned["lost_oil_bbl"], abs=1)
