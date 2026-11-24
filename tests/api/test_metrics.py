"""P7.1: every KPI is declared once in the metric registry, and the API, an agent and the page read the same value."""

import pytest
from fastapi.testclient import TestClient
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.core import metrics

pytestmark = pytest.mark.sequence


def test_every_metric_is_declared_with_what_it_means():
    reg = metrics.registry()
    assert len(reg) >= 10
    for m in reg.values():
        d = m.describe()
        assert d["definition"] and d["formula"] and d["unit"] and d["records"] and d["group"], d
        assert set(d["grains"]) <= set(metrics.GRAINS)


def _same(conn) -> dict:
    from fuzer import api
    from fuzer.agents import mcp_server

    seen = {}
    with TestClient(api.app) as client:
        listed = client.get("/metrics").json()
        assert [m["key"] for m in listed] == sorted(metrics.registry())
        for key, m in metrics.registry().items():
            for grain in (None, *m.grains):
                r = client.get(f"/metrics/{key}", params={"grain": grain} if grain else None)
                assert r.status_code == 200, r.text
                via_api = r.json()
                via_agent = mcp_server.get_metric(key, grain)
                assert via_agent["value"] == via_api["value"], key  # the agent quotes what the page shows
                assert via_agent["rows"] == via_api["rows"], (key, grain)
                if grain:
                    assert all(set(x) == {"key", "label", "value", "refs"} for x in via_api["rows"])
                seen[key] = via_api
        assert client.get("/metrics/no.such").status_code == 404
        assert client.get("/metrics/maintenance.urgent_overdue", params={"grain": "area"}).status_code == 400
        assert client.get("/metrics/maintenance.urgent_overdue", params={"at": "2025-12-20"}).status_code == 400
        for key in (k for k, m in metrics.registry().items() if m.dated):  # a dated metric, for a past period
            past = client.get(f"/metrics/{key}", params={"at": "2025-12-20"}).json()
            assert mcp_server.get_metric(key, at="2025-12-20")["value"] == past["value"], key
    return seen


def test_the_api_and_an_agent_agree_on_okan(okan_db):
    seen = _same(okan_db.conn)
    assert seen["production.efficiency"]["value"] is not None
    assert seen["context.completeness"]["value"] is not None and seen["sources.straight_through"]["value"] > 50


def test_the_api_and_an_agent_agree_on_kestrel(request):
    seen = _same(request.getfixturevalue("kestrel"))
    assert seen["context.completeness"]["value"] is not None


def _okan_facility(conn) -> str:
    return conn.execute("select distinct dst from edge where rel = 'reports_on'").fetchone()["dst"]


def test_a_scope_rolls_up_what_is_beneath_it_and_a_window_ends_on_its_day(okan_db):
    from fuzer.core import pack as packs

    conn, pack = okan_db.conn, packs.current_pack(okan_db.conn)
    fac = _okan_facility(conn)
    whole = metrics.value(conn, pack, "downtime.events", window="ytd", history=True)
    assert whole["window"] == "ytd" and whole["scope"] is None and whole["value"] > 0
    assert whole["as_of"] == "2025-12-30"  # the last production day: its cut-off
    assert len(whole["history"]) == metrics.HISTORY_WEEKS and whole["previous"] is not None
    on_fac = metrics.value(conn, pack, "downtime.events", scope=fac, window="ytd")
    assert on_fac["scope"] == fac and on_fac["value"] == whole["value"]  # every stop is on Okan's register
    by_cause = metrics.value(conn, pack, "production.deferred", window="ytd", grain="cause")
    assert round(sum(r["value"] for r in by_cause["rows"]), 2) == by_cause["value"]
    by_well = metrics.value(conn, pack, "downtime.events", window="ytd", grain="well")
    well = by_well["rows"][0]
    one = metrics.value(conn, pack, "downtime.events", scope=well["key"], window="ytd")
    assert one["value"] == well["value"] and set(one["refs"]) == set(well["refs"])
    # a fixed day reads the same later
    day = metrics.value(conn, pack, "production.oil", scope=fac, window="7d", at="2025-06-30")
    assert day["value"] == metrics.value(conn, pack, "production.oil", window="7d", at="2025-06-30")["value"] > 0
    assert day["span"] == {"start": "2025-06-24T00:00", "end": "2025-07-01T00:00", "at": "2025-06-30", "days": 7}
    # the 14 days the daily report reads are its own period, as before
    eff = metrics.value(conn, pack, "production.efficiency", at="2025-12-30")
    assert eff["window"] == "14d" and eff["value"] is not None
    # an entity with nothing of the kind beneath it says so, and one not cut by scope says it is for the whole register
    assert metrics.value(conn, pack, "production.oil", scope=well["key"], window="30d")["note"]
    plain = metrics.value(conn, pack, "maintenance.urgent_overdue", scope=fac)
    assert plain["scope"] is None and "whole register" in plain["note"]
    with pytest.raises(metrics.MetricError, match="no entity"):
        metrics.value(conn, pack, "work.backlog", scope="fz-nothing")


def test_row_sets_and_measure_trends_read_the_same_model(okan_db):
    from fastapi.testclient import TestClient

    from fuzer import api
    from fuzer.analysis import trend_spec
    from fuzer.core import pack as packs
    from fuzer.core import rowsets

    conn, pack = okan_db.conn, packs.current_pack(okan_db.conn)
    fac = _okan_facility(conn)
    stops = rowsets.read(conn, pack, "downtime.events", scope=fac, window="ytd", limit=5)
    assert stops["total"] == metrics.value(conn, pack, "downtime.events", scope=fac, window="ytd")["value"]
    assert len(stops["rows"]) == 5 and stops["rows"][0]["href"].startswith("/e/fz-downtime-")
    assert stops["rows"][0]["cells"]["oil"] >= stops["rows"][1]["cells"]["oil"]  # the largest loss first
    assert {c["key"] for c in stops["columns"]} == set(stops["rows"][0]["cells"])
    open_cases = rowsets.read(conn, pack, "cases.open", limit=3)
    assert open_cases["total"] == metrics.value(conn, pack, "work.backlog", window="1d")["value"]
    assert open_cases["rows"][0]["href"].startswith("/cases/")
    spec = trend_spec.from_measures(conn, pack, ["production.oil", "production.efficiency"], fac, "30d", "2025-12-30")
    keys = [s["key"] for s in spec["series"]]
    assert keys == ["measure:production.oil", "measure:production.efficiency", "target:production.efficiency"]
    assert len(spec["series"][0]["values"]) == 30 and len(spec["panels"]) == 2 and not spec["errors"]
    day = spec["series"][0]["values"][-1]
    assert day[1] == metrics.value(conn, pack, "production.oil", window="1d", at=day[0])["value"]
    with TestClient(api.app) as client:
        r = client.get("/metrics/downtime.events", params={"scope": fac, "window": "ytd", "grain": "cause"}).json()
        assert r["scope"] == fac and r["rows"]
        assert client.get("/metrics/downtime.events", params={"window": "2w"}).status_code == 400
        assert {x["key"] for x in client.get("/row-sets").json()} >= {"downtime.events", "cases.open", "plan.lines"}
        got = client.get("/row-sets/downtime.events", params={"scope": fac, "window": "ytd", "limit": 2}).json()
        assert got["total"] == stops["total"] and len(got["rows"]) == 2
        assert client.get("/row-sets/no.such").status_code == 404
