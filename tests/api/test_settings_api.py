"""The Settings page's API (#131): list for a project, set with a reason, reset, history; values checked."""

import os
import tempfile

import pytest

from fuzer.core import db, settings


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    mp = pytest.MonkeyPatch()
    mp.delenv("FUZER_DATABASE_URL", raising=False)
    mp.setenv("FUZER_PGDATA", os.environ.get("FUZER_PGDATA") or tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
    from fuzer import api

    with TestClient(api.app) as cl:
        yield cl
    mp.undo()


def test_set_reset_and_history(client):
    key, url = "alarms.flood_per_10min", "/settings/alarms.flood_per_10min"
    rows = {r["key"]: r for r in client.get("/settings", params={"project": "okan"}).json()["settings"]}
    assert rows[key]["value"] == 10 and rows[key]["from"] == "default" and rows[key]["group"] == "Alarms"
    groups = list(dict.fromkeys(r["group"] for r in rows.values()))
    assert groups[0] == "LLM"  # declared order, not alphabetical

    assert client.put(url, json={"value": "12", "scope": "global", "by": "ana", "reason": "site standard"}).status_code == 200
    assert client.put(url, json={"value": 15, "project": "okan", "by": "ana", "reason": "okan floods often"}).status_code == 200
    r = {x["key"]: x for x in client.get("/settings", params={"project": "okan"}).json()["settings"]}[key]
    assert (r["value"], r["from"], r["global"], r["updated_by"]) == (15, "project", 12, "ana")

    bad = client.put(url, json={"value": 1000, "project": "okan", "by": "ana", "reason": "too many"})
    assert bad.status_code == 400 and "at most" in bad.json()["detail"]
    assert client.put(url, json={"value": 11, "project": "okan", "by": "ana", "reason": ""}).status_code == 422

    reset = client.post(f"{url}/reset", json={"project": "okan", "by": "ana", "reason": "back to the site value"}).json()
    assert reset["now"] == 12
    h = client.get(f"{url}/history").json()
    assert [(x["new"], x["reason"]) for x in h][:2] == [(None, "back to the site value"), (15, "okan floods often")]
    assert client.get("/settings/no.such/history").status_code == 404
    settings._cache.clear()


def test_readiness_uses_effective_values_and_exposes_only_secret_references(client, monkeypatch):
    for variable in ("FUZER_MICROSOFT_CLIENT_ID", "FUZER_MICROSOFT_CLIENT_SECRET", "FUZER_PUBLIC_URL"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("FUZER_AGENT_MODEL", "environment-model")
    monkeypatch.setenv("READINESS_TEST_KEY", "never-return-this-secret")
    monkeypatch.setenv("FUZER_OWNER_BOOTSTRAP_TOKEN", "never-return-bootstrap")
    settings._cache.clear()
    response = client.get("/settings", params={"project": "okan"})
    assert response.status_code == 200
    result = response.json()
    model = next(s for s in result["settings"] if s["key"] == "agents.model")
    assert (model["value"], model["from"], model["environment"], model["restart"]) == (
        "environment-model", "environment", "FUZER_AGENT_MODEL", True)
    assert result["deployment"] == {"mode": "local", "configured": False, "bootstrap_present": True}
    for key, value in (("agents.model", "stored-model"), ("agents.api_key", "env:READINESS_TEST_KEY")):
        assert client.put(f"/settings/{key}", json={
            "value": value, "project": "okan", "reason": "readiness verification",
        }).status_code == 200
    response = client.get("/settings", params={"project": "okan"})
    rows = {r["key"]: r for r in response.json()["settings"]}
    assert rows["agents.model"]["value"] == settings.get(None, "agents.model", "okan") == "stored-model"
    assert rows["agents.model"]["from"] == "project"
    assert rows["agents.api_key"]["value"] == "env:READINESS_TEST_KEY"
    assert rows["agents.api_key"]["resolves"] is True
    assert "never-return" not in response.text
    for key in ("agents.model", "agents.api_key"):
        assert client.post(f"/settings/{key}/reset", json={
            "project": "okan", "reason": "restore after verification",
        }).status_code == 200
    settings._cache.clear()


def test_demo_is_enabled_by_default_and_only_global_with_recorded_changes(client):
    assert client.get("/demo/config").json() == {"enabled": True}
    url = "/settings/demo.enabled"
    row = next(s for s in client.get("/settings").json()["settings"] if s["key"] == "demo.enabled")
    assert row["scopes"] == ["global"] and row["restart"] is False
    assert client.put(url, json={"value": False, "project": "okan", "reason": "hide onboarding"}).status_code == 400
    try:
        response = client.put(url, json={"value": False, "scope": "global", "reason": "onboarding complete"})
        assert response.status_code == 200
        assert client.get("/demo/config").json() == {"enabled": False}
        assert client.get(f"{url}/history").json()[0]["reason"] == "onboarding complete"
    finally:
        assert client.post(f"{url}/reset", json={"scope": "global", "reason": "restore onboarding"}).status_code == 200
    assert client.get("/demo/config").json() == {"enabled": True}
