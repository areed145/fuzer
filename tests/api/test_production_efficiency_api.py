from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.analysis import production_efficiency as pe
from fuzer.api import ops


def client(monkeypatch, resolve=lambda *args: "fz-facility"):
    from fuzer.api import deps
    from fuzer.core.pack import load_pack

    monkeypatch.setattr(ops.queries, "resolve_ref", resolve)
    monkeypatch.setattr(deps, "pack", lambda: load_pack("okan"))  # no database here: the routes' pack is given
    app = FastAPI()
    app.include_router(ops.router)
    app.dependency_overrides[ops.get_conn] = lambda: None
    return TestClient(app)


def test_default_and_explicit_date_and_facility(monkeypatch):
    calls = []

    def compute(conn, facility, end):
        calls.append((facility, end))
        return pe.calculate([], [], end or date(2026, 11, 5))

    monkeypatch.setattr(pe, "production_efficiency", compute)
    c = client(monkeypatch)
    response = c.get("/analytics/production-efficiency")
    assert response.status_code == 200
    assert len(response.json()["points"]) == 14
    assert calls == [("fz-facility", None)]
    assert c.get("/analytics/production-efficiency?end=2024-02-29&ref=facility").status_code == 200
    assert calls[-1] == ("fz-facility", date(2024, 2, 29))
    assert c.get("/analytics/production-efficiency?end=0001-01-01").status_code == 400
    assert c.get("/analytics/production-efficiency?end=9999-12-31").status_code == 400


def test_invalid_date_unknown_ref_and_wrong_kind(monkeypatch):
    c = client(monkeypatch, resolve=lambda *args: None)
    assert c.get("/analytics/production-efficiency?end=bad").status_code == 422
    assert c.get("/analytics/production-efficiency?ref=unknown").status_code == 404
    c = client(monkeypatch)

    def wrong_kind(*args):
        raise ValueError("ref must resolve to a live facility")

    monkeypatch.setattr(pe, "production_efficiency", wrong_kind)
    response = c.get("/analytics/production-efficiency?ref=well")
    assert response.status_code == 400
    assert "live facility" in response.json()["detail"]


def test_ambiguous_facility_is_a_conflict_not_server_error(monkeypatch):
    def ambiguous(*args):
        raise ops.queries.AmbiguousRef("facility", [{"id": "one"}, {"id": "two"}])

    c = client(monkeypatch, resolve=ambiguous)
    assert c.get("/analytics/production-efficiency?ref=facility").status_code == 409


def test_flow_capacity_is_a_read_of_the_resolved_facility(monkeypatch):
    from fuzer.analysis import flow_capacity as fc

    calls = []
    monkeypatch.setattr(fc, "flow_capacity", lambda conn, facility: calls.append(facility) or {"stages": []})
    assert client(monkeypatch).get("/analytics/flow-capacity").json() == {"stages": []}
    assert calls == ["fz-facility"]


def test_flow_capacity_errors(monkeypatch):
    from fuzer.analysis import flow_capacity as fc

    assert client(monkeypatch, resolve=lambda *args: None).get("/analytics/flow-capacity?ref=x").status_code == 404

    def fail(conn, facility):
        raise ValueError("facility has no production report")

    monkeypatch.setattr(fc, "flow_capacity", fail)
    assert client(monkeypatch).get("/analytics/flow-capacity").status_code == 400


def test_live_figure_routes(monkeypatch):
    from fuzer.plots import analytics

    calls = []
    monkeypatch.setattr(analytics, "live", lambda conn, pack, kind, facility: calls.append((kind, facility)) or {"kind": kind})
    assert client(monkeypatch).get("/analytics/figures/flow-capacity").json() == {"kind": "flow-capacity"}
    assert calls == [("flow-capacity", "fz-facility")]
    monkeypatch.undo()
    assert client(monkeypatch).get("/analytics/figures/nope").status_code == 404
