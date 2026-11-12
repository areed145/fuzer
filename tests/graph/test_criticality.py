"""An asset with no criticality is the data steward's case, one per asset, cleared once it is rated; values that
read criticality say "not assessed" instead of counting it as the lowest class."""

import pytest
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.graph import criticality, gaps

pytestmark = pytest.mark.sequence


def _open(conn) -> dict[str, dict]:
    return {r["key"]: r for r in conn.execute(
        "select x.key, x.title, x.evidence, w.role, w.assignee from exception_record x "
        "left join work_item w on w.id = x.item_id where x.type = 'criticality_not_assessed' and x.state = 'open'")}


def test_each_asset_without_criticality_is_one_steward_case_until_it_is_rated(okan_db):
    conn = okan_db.conn
    missing = criticality.missing(conn)
    assert missing, "some Okan assets have no criticality"
    rated = conn.execute("select count(*) as n from golden where field = 'criticality'").fetchone()["n"]
    assert rated > len(missing)
    cases = _open(conn)
    assert set(cases) == {a["fuzer_id"] for a in missing}  # synced by the rebuild, one per asset
    for x in cases.values():
        assert x["title"].endswith("criticality not assessed") and "fz-" not in x["title"]
        assert "data_steward" in (x["role"] or x["assignee"] or "")
    assert criticality.sync(conn) == {"raised": 0, "refreshed": len(missing), "cleared": 0}  # no duplicates

    a = missing[0]
    gaps.set_values(conn, "criticality", "B", [a["fuzer_id"]], "ana", reason="rated at the walkdown")
    out = criticality.sync(conn)
    assert out["cleared"] == 1 and a["fuzer_id"] not in _open(conn)
    row = conn.execute("select state, evidence from exception_record where type = 'criticality_not_assessed' "
                       "and key = %s", (a["fuzer_id"],)).fetchone()
    assert row["state"] != "open"
    conn.rollback()


def test_a_register_that_rates_nothing_raises_nothing(okan_db):
    conn = okan_db.conn
    conn.execute("delete from golden where field = 'criticality'")
    assert criticality.missing(conn) == []
    conn.rollback()


def test_kestrel_wells_have_no_criticality_and_are_cases(request):
    conn = request.getfixturevalue("kestrel")
    missing = criticality.missing(conn)
    assert {a["kind"] for a in missing} == {"well"} and len(missing) == 2  # every Kestrel tag is rated
    assert set(_open(conn)) == {a["fuzer_id"] for a in missing}
    assert criticality.label(None) == "not assessed" and criticality.label("a") == "A"
