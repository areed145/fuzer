import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.api import investigation
from fuzer.core.pack import load_pack
from fuzer.graph.investigation import InvestigationUnavailable, okan_investigation


@pytest.fixture
def client(okan_db):
    app = FastAPI()
    app.include_router(investigation.router)
    return TestClient(app)


def test_seeded_journey_has_cited_evidence_without_ai_or_mutations(okan_db, client):
    conn = okan_db.conn
    before = conn.execute("select count(*) as n from source_record").fetchone()["n"]
    response = client.get("/demo/okan/investigation")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["asset_label"] == "20-K-2200B"
    assert data["since"] == "2024-03-01" and data["until"] == "2024-04-01"
    evidence = {e["id"]: e for e in data["evidence"]}
    downtime = [e for e in evidence.values() if e["kind"] == "downtime"]
    assert len(downtime) == 2
    assert sum(e["lost_oil_sm3"] for e in downtime) == 1088
    assert all(e["records"] for e in evidence.values())
    assert {"notification", "downtime", "log_entry", "work_order"} <= {e["kind"] for e in evidence.values()}
    assert any("bearing" in e["text"].lower() for e in evidence.values() if e["kind"] == "work_order")
    for finding in data["findings"]:
        assert finding["evidence_ids"]
        assert set(finding["evidence_ids"]) <= evidence.keys()
    assert any("1,088 Sm3" in f["text"] for f in data["findings"])
    maintenance = next(f for f in data["findings"] if "April maintenance response" in f["text"])
    assert {"notification", "work_order"} <= {evidence[id_]["kind"] for id_ in maintenance["evidence_ids"]}
    assert data["asset_records"] and data["asset_fields"]
    assert all(r["observed_at"] for e in evidence.values() for r in e["records"])
    assert conn.execute("select count(*) as n from source_record").fetchone()["n"] == before


def test_http_adapter_enforces_read_only_transaction(okan_db, client, monkeypatch):
    import psycopg

    def accidental_write(conn, pack):
        assert conn.execute("show transaction_read_only").fetchone()["transaction_read_only"] == "on"
        conn.commit()
        conn.execute("update entity set kind = kind")

    monkeypatch.setattr(investigation, "okan_investigation", accidental_write)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        client.get("/demo/okan/investigation")
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/demo/okan/investigation").status_code == 405


def test_missing_records_are_explicit_not_success_shaped(okan_db, client, monkeypatch):
    def unavailable(conn, pack):
        raise InvestigationUnavailable("The March 2024 Okan incidents are not available in this register.")

    monkeypatch.setattr(investigation, "okan_investigation", unavailable)
    response = client.get("/demo/okan/investigation")
    assert response.status_code == 404
    assert "not available" in response.json()["detail"]


def test_partial_evidence_does_not_invent_production_impact(okan_db, monkeypatch):
    from fuzer.graph import queries

    monkeypatch.setattr(queries, "incident_timeline", lambda *_: [])
    monkeypatch.setattr(queries, "maintenance", lambda *_, **kw: {"recent": []})
    result = okan_investigation(okan_db.conn, load_pack("okan"))
    assert result.evidence  # raw operator logs still work without model enrichment
    assert not any("Sm3" in f.text for f in result.findings)
    assert any("production impact is not established" in text for text in result.limitations)


def test_missing_volume_is_unknown_not_zero_and_shared_evidence_is_counted_once(okan_db, monkeypatch):
    from fuzer.graph import queries

    original = queries.incident_timeline

    def incomplete(conn, id_):
        rows = original(conn, id_)
        return [{**r, "lost_oil_sm3": None} for r in rows] * 2

    monkeypatch.setattr(queries, "incident_timeline", incomplete)
    result = okan_investigation(okan_db.conn, load_pack("okan"))
    assert len({e.id for e in result.evidence}) == len(result.evidence)
    assert not any("Sm3" in f.text for f in result.findings)
    assert any("complete total cannot be reported" in text for text in result.limitations)


def test_missing_asset_and_incidents_have_no_synthetic_fallback(okan_db, monkeypatch):
    from fuzer.graph import queries

    monkeypatch.setattr(queries, "incidents", lambda *_, **kw: {"items": []})
    with pytest.raises(InvestigationUnavailable, match="incidents are not available"):
        okan_investigation(okan_db.conn, load_pack("okan"))
    monkeypatch.setattr(queries, "resolve_ref", lambda *_: None)
    with pytest.raises(InvestigationUnavailable, match="asset is not loaded"):
        okan_investigation(okan_db.conn, load_pack("okan"))
