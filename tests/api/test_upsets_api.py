from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.api import entities
from fuzer.ops import sre


def test_upsets_defaults_to_detected_process_upsets_and_caps_limit(monkeypatch):
    seen = {}

    def upsets(conn, **kwargs):
        seen.update(kwargs)
        return [{"id": 7, "status": "detected"}]

    monkeypatch.setattr(sre, "upsets", upsets)
    app = FastAPI()
    app.include_router(entities.router)
    app.dependency_overrides[entities.get_conn] = lambda: None

    response = TestClient(app).get("/upsets?limit=2000")

    assert response.status_code == 200
    assert response.json() == [{"id": 7, "status": "detected"}]
    assert seen == {"status": "detected", "kind": "process", "limit": 1000}
