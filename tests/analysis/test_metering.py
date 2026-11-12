"""Metering and balances (P4.2, pack 8) on Kestrel, read from the allocation network and the well tests: the sales
custody meter H1-FT-8010 has read about 4 % low for the last nine days (the process model's `bias` event), the dry
gas check meter C1-FT-3005 upstream of it still reads true. On Okan, the oil fiscal meter has no other meter on its
stream, and its allocation factor sits inside the band."""

from datetime import datetime, timedelta

import pytest
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.analysis import calcs

pytestmark = pytest.mark.sequence


@pytest.fixture
def conn(request):
    return request.getfixturevalue("kestrel")


def _fid(conn, label: str) -> str:
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def _run(conn, check: str, label: str, **kw) -> dict:
    out = calcs.run(conn, check, _fid(conn, label), **kw)
    assert out["method"]["id"] == check and out["sources"] and out["basis"]
    return out


def test_the_sales_meter_allocates_low_once_it_drifts(conn):
    month = _run(conn, "allocation_factor", "H1-FT-8010")
    sales = _run(conn, "allocation_factor", "H1-FT-8010", days=7)  # the last week alone: out of it
    assert sales["flag"] == "af_low" and 0.85 < sales["value"] < 0.9 and sales["phase"] == "gas"
    assert sales["wells"] == 2 and sales["unit"] == "kSm3/d" and "lift gas left out" in sales["basis"]
    dry = _run(conn, "allocation_factor", "C1-FT-3005")  # upstream of the drift: inside the band and steady
    assert dry["flag"] is None and 0.92 < dry["value"] < 0.97 and abs(dry["value"] - dry["previous"]) < 0.05
    before = _run(conn, "allocation_factor", "H1-FT-8010", days=7, until=month["window"][0])  # a month earlier
    assert before["flag"] is None and 0.9 < before["value"] < 0.94


def test_the_check_meter_sees_the_sales_meter_step(conn):
    out = _run(conn, "meter_agreement", "H1-FT-8010")
    assert out["flag"] == "meter_drift" and out["against"] == "C1-FT-3005" and -5 < out["change_pct"] < -2.5
    assert "which one moved" in out["which"]  # two meters can't tell which: the result says what can
    start = datetime.fromisoformat(out["window"][0])
    calm = _run(conn, "meter_agreement", "H1-FT-8010", until=(start - timedelta(days=6)).isoformat())
    assert calm["flag"] is None  # the week before the drift began: the two agree


def test_a_meter_with_no_wells_or_partner_is_refused(conn):
    with pytest.raises(calcs.CalcError, match="no flow point"):
        calcs.run(conn, "allocation_factor", _fid(conn, "A1-V-1001"))
