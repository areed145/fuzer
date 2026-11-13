"""P2.5: each asset's context is scored against what its class usually has; the gaps that hold back the most checks,
weighted by criticality, are the data steward's open cases, and a case clears when its gap is filled."""

import pytest
from fastapi.testclient import TestClient
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.analysis import completeness

pytestmark = pytest.mark.sequence


def _open(conn) -> dict[str, dict]:
    return {r["key"]: r for r in conn.execute(
        "select x.key, x.title, x.item_id, w.role, w.assignee from exception_record x "
        "left join work_item w on w.id = x.item_id where x.type = 'context_gap' and x.state = 'open'")}


def test_okan_assets_are_scored_by_class_and_the_gap_that_holds_back_most_is_a_case(okan_db):
    conn = okan_db.conn
    rows = completeness.assets(conn)
    assert len(rows) > 100 and all(set(r["has"]) == set(completeness.BY_KEY) for r in rows)
    for r in rows:  # a gap is something the asset's class usually has
        assert set(r["missing"]) <= set(r["expected"]) and all(r["share"][k] >= completeness.EXPECT for k in r["expected"])
    worth = [r for r in rows if r["value"] > 0]
    assert worth and set(_open(conn)) == {r["fuzer_id"] for r in worth[:completeness.TOP]}  # synced by the rebuild
    top = worth[0]
    case = _open(conn)[top["fuzer_id"]]
    assert "fz-" not in case["title"] and "can't run" in case["title"]
    assert "data_steward" in (case["role"] or case["assignee"] or "")
    assert completeness.sync(conn)["raised"] == 0  # no duplicates

    # Filling the gap clears the case: give one of its points a limit. (An unmeasured asset, a water injector with no
    # historian points, needs more than a limit.)
    top = next(r for r in worth[:completeness.TOP] if "limits" in r["missing"] and "measured" not in r["missing"])
    point = conn.execute("select dst from edge where rel = 'has_timeseries' and src = %s limit 1",
                         (top["fuzer_id"],)).fetchone()
    if point is None:  # the points hang on a tag under it
        point = conn.execute("select h.dst from edge p join edge h on h.src = p.src and h.rel = 'has_timeseries' "
                             "where p.dst = %s and p.rel = 'part_of' limit 1", (top["fuzer_id"],)).fetchone()
    conn.execute("insert into point_limit (record, limit_type, point_id, source, basis, value) "
                 "values ('test:limit:1', 'hi', %s, 'dcs', 'alarm', 1)", (point["dst"],))
    assert completeness.sync(conn)["cleared"] >= 1 and top["fuzer_id"] not in _open(conn)
    conn.execute("delete from point_limit where record = 'test:limit:1'")  # the work engine commits: put it back
    assert completeness.sync(conn)["raised"] == 1
    conn.commit()


def test_the_asset_page_and_master_data_show_completeness(okan_db):
    from fuzer import api

    conn = okan_db.conn
    top = next(r for r in completeness.assets(conn) if r["value"] > 0)
    under = conn.execute("select src from edge where rel = 'part_of' and dst = %s limit 1",
                         (top["fuzer_id"],)).fetchone()
    conn.rollback()
    with TestClient(api.app) as client:
        a = client.get(f"/entities/{top['fuzer_id']}/context").json()
        assert a["fuzer_id"] == top["fuzer_id"] and a["of"] is None and a["item_id"]
        assert {k["check"] for k in a["known"]} == set(completeness.BY_KEY)
        assert all(g["detectors"] or g["prevents"] for g in a["gaps"])
        if under:  # a tag under the asset shows the asset's context
            assert client.get(f"/entities/{under['src']}/context").json()["fuzer_id"] == top["fuzer_id"]
        s = client.get("/stewardship/context").json()
        assert s["areas"] and s["classes"] and s["top"][0]["fuzer_id"] == top["fuzer_id"] and s["top"][0]["item_id"]
        doc = conn.execute("select fuzer_id from entity where kind = 'document' limit 1").fetchone()
        assert client.get(f"/entities/{doc['fuzer_id']}/context").json() is None  # not part of an asset


def test_kestrel_ranks_an_unmeasured_critical_exchanger_first(request):
    conn = request.getfixturevalue("kestrel")
    worth = [r for r in completeness.assets(conn) if r["value"] > 0]
    top = worth[0]
    assert top["criticality"] == "A" and {"measured", "limits"} <= set(top["missing"])
    assert top["blocked"] and set(_open(conn)) == {r["fuzer_id"] for r in worth[:completeness.TOP]}
    small = [c for c in completeness.summary(conn)["classes"] if c["assets"] < completeness.MIN_CLASS]
    assert small and all(c["score"] is None for c in small)  # too few to say what the class usually has
