"""Change control through the API (P8.3), as the Standards page drives it: propose a change, test-run it, review it
(not by its author), publish it, roll it out to an asset and roll it back. A project's own difference is promoted."""

import pytest


@pytest.fixture(scope="module")
def client(okan_db):
    from fastapi.testclient import TestClient

    from fuzer import api
    from fuzer.workflow import engine

    conn = okan_db.conn
    engine.set_person(conn, "ana", ["engineer"])
    engine.set_person(conn, "olga", ["ops_lead", "workflow_admin"])
    engine.load_defaults(conn)
    conn.commit()
    with TestClient(api.app) as cl:
        yield cl


def test_a_change_goes_from_proposal_to_an_asset_and_back(client):
    v = client.get("/standards/versions").json()
    assert v["latest"] == 1 and [x["version"] for x in v["versions"]] == [1] and v["rollouts"] == []

    nothing = client.post("/standards/versions", json={"changes": {}, "summary": "nothing", "by": "ana"})
    assert nothing.status_code == 400 and "changes nothing" in nothing.json()["detail"]
    p = client.post("/standards/versions", json={"changes": {"settings": {"work.due_soon_minutes": 90}},
                                                 "summary": "Hand over earlier", "by": "ana"})
    assert p.status_code == 200, p.json()
    version = p.json()["version"]
    assert version == 2

    assert client.post(f"/standards/versions/{version}/review",
                       json={"approve": True, "note": "ok", "by": "olga"}).status_code == 400  # not test-run yet
    run = client.post(f"/standards/versions/{version}/test-run").json()
    assert run["problems"] == []
    assert client.post(f"/standards/versions/{version}/review",
                       json={"approve": True, "note": "mine", "by": "ana"}).status_code == 403
    r = client.post(f"/standards/versions/{version}/review", json={"approve": True, "note": "Fine", "by": "olga"})
    assert r.json()["state"] == "approved"
    assert client.post(f"/standards/versions/{version}/publish", json={"by": "ana"}).status_code == 403
    assert client.post(f"/standards/versions/{version}/publish", json={"by": "olga"}).json()["state"] == "published"

    out = client.post("/standards/rollouts", json={"project": "okan", "version": version, "by": "olga"}).json()
    assert (out["version"], out["from_version"], out["state"]) == (2, 1, "live")
    [s] = client.get("/standards/conformance").json()["projects"]
    assert (s["version"], s["current"], s["applied"]) == (2, True, True)

    back = client.post("/standards/rollouts/okan/rollback", json={"reason": "Not yet", "by": "olga"}).json()
    assert back["to"] == {"name": "standard", "version": 1}
    assert [x["state"] for x in client.get("/standards/versions").json()["rollouts"]] == ["rolled_back"]
    assert client.post("/standards/rollouts/okan/rollback", json={"reason": "again", "by": "olga"}).status_code == 400

    missing = client.post("/standards/versions", json={"summary": "x", "by": "ana",
                                                       "promote": {"part": "settings", "key": "no.such"}})
    assert missing.status_code == 400 and "no settings no.such" in missing.json()["detail"]
