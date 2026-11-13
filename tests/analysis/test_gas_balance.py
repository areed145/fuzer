"""The daily gas balance (#369): gas handled against export, lift gas, flare, fuel and vent on Okan, with the closure
gap always shown, and the stretch when the fuel gas meter read zero standing out."""

import json

import pytest

from fuzer.analysis import gas_balance as gb


def test_the_balance_closes_to_the_meters_except_while_the_fuel_meter_read_zero(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "gas_balance.json").read_text())
    start, end = key["fuel_meter_failed"]
    b = gb.daily(conn, days=730)
    assert not b["missing_terms"] and b["verdict"] == "closes_with_episodes"
    assert abs(b["summary"]["median_gap_pct"]) < 1 and len(b["days"]) == 730
    assert all(r["gap_pct"] is not None for r in b["days"])  # the gap is shown every day, never hidden
    fuel = max(b["episodes"], key=lambda e: e["days"])
    assert (fuel["start"], fuel["end"]) == (start, end) and fuel["direction"] == "unaccounted"
    assert fuel["terms_reading_zero"] == ["fuel"] and fuel["median_gap_pct"] > gb.GAP_PCT
    outside = [r for r in b["days"] if not start <= r["date"] <= end]
    assert sum(r["open"] for r in outside) <= 3  # the meters' scatter rarely opens it on its own
    conn.rollback()


def test_a_report_without_fuel_or_vent_says_the_gap_includes_them(okan_db, monkeypatch):
    conn = okan_db.conn
    real = gb.ops._rows

    def no_fuel(*a, **k):
        return [{**r, "f": {k2: v for k2, v in r["f"].items() if k2 not in ("fuel_gas_ksm3", "vent_ksm3")}}
                for r in real(*a, **k)]
    monkeypatch.setattr(gb.ops, "_rows", no_fuel)
    b = gb.daily(conn, since="2025-12-01", until="2025-12-31")
    assert b["missing_terms"] == ["fuel", "vent"] and "includes it" in b["note"]
    assert b["verdict"] == "open" and all(r["gap_pct"] > gb.GAP_PCT for r in b["days"])  # shown, not dropped
    conn.rollback()


def test_the_charts_and_the_card_reach_people_and_agents(okan_db):
    from fastapi.testclient import TestClient

    from fuzer import api
    from fuzer.plots import ChartError, general, production

    conn = okan_db.conn
    fig = production.gas_balance(conn, since="2025-09-01", until="2025-11-01")
    assert fig.summary["episodes"] and any(t["name"].startswith("gap") for t in fig.data)
    fl = general.flare(conn, "2025-09-01", "2025-11-01")
    assert {"fuel (kSm3/d)", "vent (kSm3/d)"} <= {t["name"] for t in fl.data}
    assert fl.summary["fuel_ksm3"] > 0 and "fuel gas and venting aren't" not in fl.summary["note"]
    with pytest.raises(ChartError):
        production.gas_balance(conn, since="2020-01-01", until="2020-02-01")
    r = TestClient(api.app).get("/plant/gas-balance")
    assert r.status_code == 200 and r.json()["summary"]["days"] > 300
    conn.rollback()
