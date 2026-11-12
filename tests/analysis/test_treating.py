"""Gas treating and dehydration checks (P4.2, pack 3) on Kestrel, read through measurement roles and the site's IOW
register: the amine contactor foamed after yesterday's restart (differential pressure up two and a half times within
the hour, sweet gas CO2 up toward its limit); the TEG reboiler has cooled about 10 degC over six weeks toward its low
limit and the dry gas water content has crossed its 112 mg/Sm3 limit; the propane chiller holds the dew point."""

import pytest
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.analysis import calcs

pytestmark = pytest.mark.sequence


@pytest.fixture
def conn(request):
    return request.getfixturevalue("kestrel")


def _run(conn, check: str, label: str) -> dict:
    fid = conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]
    out = calcs.run(conn, check, fid)
    assert out["method"]["id"] == check and out["sources"] and out["basis"]
    return out


def test_the_amine_contactor_foamed_after_the_restart(conn):
    out = _run(conn, "contactor_foaming", "D1-V-4001")
    assert out["flag"] == "foaming" and out["times_clean"] >= 2 and out["expected"] == pytest.approx(0.32, abs=0.02)
    gas = out["treated_gas"]
    assert gas["description"] == "Sweet Gas CO2" and gas["limit"] == 2.0 and 1.5 < gas["peak"] < 2.0
    assert gas["limit_basis"] == "integrity operating window IOW-KR-007" and "iow:window:IOW-KR-007>A" in out["sources"]
    assert gas["peak_at"][:13] == out["peak_at"][:13]  # the gas went off as the column foamed
    # the sweet gas itself is inside its window on the day: foaming is the finding, not a CO2 problem
    assert _run(conn, "treated_gas", "D1-V-4001")["flag"] is None


def test_the_glycol_unit_lost_purity_as_its_reboiler_cooled(conn):
    gas = _run(conn, "treated_gas", "C1-V-3001")
    assert gas["flag"] == "off_spec" and gas["value"] > 112 and gas["expected"] == 112.0
    assert gas["description"] == "Dry Gas Water Content" and gas["trend"]["hi"]["per_day"] > 1
    reb = _run(conn, "regenerator", "C1-H-3008")
    assert reb["flag"] == "reboiler_falling" and reb["expected"] == 190.0 and 190 < reb["value"] < 197
    assert reb["window"]["basis"] == "integrity operating window IOW-KR-004" and reb["trend"]["lo"]["reaches"]
    # the amine reboiler holds its window; the stabilizer's is judged against its alarm settings
    assert _run(conn, "regenerator", "D1-E-4006")["flag"] is None
    assert _run(conn, "regenerator", "F1-E-6003")["window"]["basis"] == "alarm settings"


def test_the_chiller_holds_the_dew_point(conn):
    out = _run(conn, "chiller", "E1-V-5003")
    assert out["flag"] is None and out["value"] < out["expected"] == -15.0


def test_a_unit_without_the_measurement_refuses_by_name(conn):
    fid = conn.execute("select fuzer_id from entity_label where label = 'C1-V-3001'").fetchone()["fuzer_id"]
    with pytest.raises(calcs.CalcError, match="no differential pressure point") as e:
        calcs.run(conn, "contactor_foaming", fid)
    assert e.value.name == "missing_input"
