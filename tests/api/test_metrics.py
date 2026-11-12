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
