"""Static equipment and integrity checks (P4.2, pack 4) on Kestrel and Okan. Kestrel's inspection system records each
vessel CML's minimum required thickness: the amine contactor's second CML has lost about a millimetre in four and a half
years toward a tmin close under it, so its five-yearly internal inspection is longer than its wall supports; the inlet
separator has decades left. Okan's records hold no tmin, so its vessels' supported interval can't be worked out, and its
carryover separator's internal inspection is overdue."""

import pytest
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.analysis import calcs

pytestmark = pytest.mark.sequence


def _fid(conn, label: str) -> str:
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def _run(conn, check: str, label: str, **kw) -> dict:
    out = calcs.run(conn, check, _fid(conn, label), **kw)
    assert out["method"]["id"] == check and out["sources"] and out["basis"]
    return out


def test_the_amine_contactor_plan_outlasts_its_wall(request):
    conn = request.getfixturevalue("kestrel")
    wall = _run(conn, "corrosion_circuit", "D1-V-4001")
    me = wall["line"]
    assert me["retirement_basis"] == "recorded minimum thickness" and me["governing_cml"] == "CML-V2"
    assert me["retirement_mm"] == 23.4 and 3 < me["remaining_years"] < 5 and wall["flag"] == "retire_soon"
    assert "as the inspection system records it" in wall["basis"]
    plan = _run(conn, "inspection_plan", "D1-V-4001")
    assert plan["flag"] == "interval_too_long" and plan["value"] == 5.0 and 1.5 < plan["expected"] < 2.5
    assert plan["plan"]["needs_shutdown"] and plan["rate_unchecked"] is None
    assert plan["supported_due"] > plan["plan"]["next_due"]  # the coming inspection is in time; the one after is not
    # the inlet separator's wall supports the ten years the standards allow, longer than its plan
    sep = _run(conn, "inspection_plan", "A1-V-1001")
    assert sep["flag"] is None and sep["expected"] == 10.0 and sep["value"] == 5.0


def test_without_a_recorded_tmin_the_interval_is_not_checked_and_overdue_is_still_said(okan_db):
    conn = okan_db.conn
    out = _run(conn, "inspection_plan", "20-V-2001")
    assert out["flag"] == "inspection_overdue" and out["overdue_days"] > 0 and out["plan"]["last_done"] == "2021-09-30"
    assert out["expected"] is None and "retirement thickness" in out["rate_unchecked"]
    with pytest.raises(calcs.CalcError, match="no condition inspection plan"):
        _run(conn, "inspection_plan", "20-K-2200B")
