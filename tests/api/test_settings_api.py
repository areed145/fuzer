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
    assert groups[0] == "Claude"  # declared order, not alphabetical

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
