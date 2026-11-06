"""Use disposable accounts and a fake identity provider, never real Microsoft credentials."""

import re
import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from fuzer.api import app
from fuzer.api import security
from fuzer.core import access, db, settings
from fuzer.workflow import engine as work
from fuzer.workflow import roles as workflow_roles

TENANT = str(uuid.UUID(int=1))
SUBJECT = str(uuid.UUID(int=2))


@pytest.fixture
def runtime(okan_db, monkeypatch):
    monkeypatch.setenv("FUZER_AUTH_MODE", "microsoft")
    monkeypatch.setenv("FUZER_PUBLIC_URL", "https://testserver")
    monkeypatch.setenv("FUZER_MICROSOFT_CLIENT_ID", "test-client")
    monkeypatch.setenv("FUZER_MICROSOFT_CLIENT_SECRET", "test-only-not-a-real-secret")
    monkeypatch.setenv("FUZER_OWNER_BOOTSTRAP_TOKEN", "test-owner-code-that-is-long-enough-for-setup")
    monkeypatch.delenv("RENDER", raising=False)
    with db.connect() as conn:
        conn.execute("truncate access_event, access_login, access_session, access_membership, access_account, access_role cascade")
        access.seed(conn)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def signed_in(client, *, owner=False, roles=("reader",), enabled=True, subject=SUBJECT):
    with db.connect() as conn:
        person, token = access.sign_in(conn, {"tid": TENANT, "oid": subject, "name": "Test participant"})
        conn.execute("update access_account set enabled = %s, owner = %s where id = %s", (enabled, owner, person.id))
        for role in roles:
            conn.execute("insert into access_membership (account_id, role) values (%s, %s)", (person.id, role))
        conn.commit()
        person = access.principal(conn, person.id, person.csrf)
    client.cookies.set("__Host-fuzer_session", token, domain="testserver.local", path="/")
    client.headers["X-Fuzer-CSRF"] = person.csrf
    return person, token


def test_every_data_and_execution_route_is_protected_before_validation(runtime):
    client = runtime
    def flattened(routes):
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
            elif hasattr(route, "original_router"):  # FastAPI's lazy include_router, as well as older eager routes.
                yield from flattened(route.original_router.routes)

    protected = [route for route in flattened(app.routes)
                 if not route.path.startswith("/auth") and route.path not in ("/", "/health")]
    assert len(protected) > 100
    for route in protected:
        assert isinstance(route, security.AuthenticatedRoute), route.path
        url = re.sub(r"\{[^}]+\}", "1", route.path)
        for method in route.methods:
            response = client.request(method, url)
            assert response.status_code == 401, (method, url, response.text)
    assert client.get("/health").status_code == 200
    assert client.get("/auth/me").json()["user"] is None


def test_default_is_fail_closed_and_local_mode_rejects_remote_requests(runtime, monkeypatch):
    monkeypatch.delenv("FUZER_AUTH_MODE")
    monkeypatch.delenv("FUZER_MICROSOFT_CLIENT_SECRET")
    assert runtime.get("/stats").status_code == 503
    assert runtime.get("/auth/me").json()["configured"] is False
    monkeypatch.setenv("FUZER_AUTH_MODE", "local")
    with TestClient(app, base_url="http://public.example", client=("203.0.113.1", 50000)) as external:
        assert external.get("/stats").status_code == 403
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(access.AccessError, match="Render"):
        access.mode()


@pytest.mark.parametrize("return_to, destination", [
    ("#/investigate/okan?step=evidence", "/#/investigate/okan?step=evidence"),
    ("https://not-fuzer.example", "/"),
])
def test_microsoft_exchange_is_browser_bound_single_use_and_creates_pending_account(runtime, monkeypatch, return_to, destination):
    class Microsoft:
        def initiate_auth_code_flow(self, scopes, **kwargs):
            assert scopes == [] and kwargs["response_mode"] == "query"
            assert kwargs["redirect_uri"] == "https://testserver/auth/callback"
            return {"auth_uri": "https://login.microsoftonline.com/test", "state": "expected-state", "nonce": "nonce"}

        def acquire_token_by_auth_code_flow(self, flow, response):
            assert response["state"] == flow["state"]
            return {"id_token_claims": {"tid": TENANT, "oid": SUBJECT, "name": "Personal or work account"}}

    monkeypatch.setattr(access, "microsoft_client", Microsoft)
    response = runtime.get("/auth/login", params={"return_to": return_to}, follow_redirects=False)
    assert response.status_code == 303
    assert "HttpOnly" in response.headers["set-cookie"] and "Secure" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    response = runtime.get("/auth/callback?state=expected-state&code=test-code", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == destination
    me = runtime.get("/auth/me").json()
    assert me["user"]["display_name"] == "Personal or work account"
    assert not me["user"]["enabled"] and not me["user"]["owner"]
    assert me["needs_owner"]
    assert runtime.get("/stats").status_code == 403
    replay = runtime.get("/auth/callback?state=expected-state&code=test-code", follow_redirects=False)
    assert "expired" in replay.headers["location"]


def test_failed_identity_validation_never_creates_a_session(runtime, monkeypatch):
    class Microsoft:
        def initiate_auth_code_flow(self, **kwargs):
            return {"auth_uri": "https://login.microsoftonline.com/test", "state": "expected"}

        def acquire_token_by_auth_code_flow(self, flow, response):
            raise ValueError("state or nonce mismatch")

    monkeypatch.setattr(access, "microsoft_client", Microsoft)
    runtime.get("/auth/login", follow_redirects=False)
    response = runtime.get("/auth/callback?state=wrong&code=untrusted", follow_redirects=False)
    assert "failed" in response.headers["location"]
    assert runtime.get("/auth/me").json()["user"] is None
    with db.connect() as conn:
        assert conn.execute("select count(*) as n from access_account").fetchone()["n"] == 0
        assert conn.execute("select count(*) as n from access_session").fetchone()["n"] == 0


def test_owner_bootstrap_requires_session_csrf_secret_and_cannot_be_reused(runtime):
    person, _ = signed_in(runtime, enabled=False)
    assert runtime.post("/auth/bootstrap", json={"token": "wrong-code-that-is-at-least-32-chars"}).status_code == 403
    code = "test-owner-code-that-is-long-enough-for-setup"
    runtime.headers.pop("X-Fuzer-CSRF")
    assert runtime.post("/auth/bootstrap", json={"token": code}).status_code == 403
    runtime.headers["X-Fuzer-CSRF"] = person.csrf
    assert runtime.post("/auth/bootstrap", json={"token": code}).status_code == 200
    assert runtime.get("/auth/me").json()["user"]["owner"]
    assert runtime.post("/auth/bootstrap", json={"token": code}).status_code == 403
    with db.connect() as conn:
        events = conn.execute("select data::text from access_event").fetchall()
        assert code not in str(events)


def test_reader_cannot_mutate_spend_or_configure(runtime):
    signed_in(runtime)
    assert runtime.get("/demo/okan/investigation").status_code == 200
    assert runtime.get("/ontology/declared").status_code == 200
    for method, path, body in [
        ("POST", "/rebuild", {}),
        ("PUT", "/settings/agents.model", {"value": "changed"}),
        ("PUT", "/work/people/attacker", {"roles": ["workflow_admin"], "by": "system"}),
        ("POST", "/assistant/ask", {"question": "charge", "by": "system"}),
        ("POST", "/work/tasks/1/complete", {"by": "system", "data": {}}),
        ("POST", "/board/pins", {"key": "x", "rank": 1, "reason": "x", "by": "system"}),
    ]:
        assert runtime.request(method, path, json=body).status_code == 403, path
    assert runtime.get("/settings").status_code == 403
    assert runtime.get("/auth/access").status_code == 403


def test_operator_audit_actor_is_server_bound_and_sort_by_is_not_rewritten(runtime):
    person, _ = signed_in(runtime, roles=("operator",))
    result = runtime.post("/board/pins", json={"key": "access-test", "rank": 2, "reason": "Test", "by": "system"})
    assert result.status_code == 200, result.text
    with db.connect() as conn:
        assert conn.execute("select pinned_by from board_pin where key='access-test'").fetchone()["pinned_by"] == person.actor
    assert runtime.get("/downtime/ranking?by=tag").status_code == 200
    runtime.headers["Origin"] = "https://not-fuzer.example"
    assert runtime.post("/board/pins", json={"key": "access-test"}).status_code == 403
    runtime.headers.pop("Origin")
    runtime.headers.pop("X-Fuzer-CSRF")
    assert runtime.post("/board/pins", json={"key": "access-test"}).status_code == 403


def test_owner_roles_are_dynamic_and_only_human_across_inbox_roster_and_routing(runtime):
    person, _ = signed_in(runtime, owner=True)
    assert runtime.put("/work/roles/future_human_role", json={
        "kind": "person", "by": "agent:sre", "description": "Future human responsibility",
    }).status_code == 200
    with db.connect() as conn:
        held = work.roles_of(conn, person.actor)
        assert "future_human_role" in held and "workflow_admin" in held
        assert not set(held) & set(workflow_roles.BUILTIN.keys() - {k for k, v in workflow_roles.BUILTIN.items() if v[0] == "person"})
        assert "future_human_role" in next(p for p in work.people(conn) if p["name"] == person.actor)["roles"]
    listing = runtime.get("/work/roles/catalog").json()
    assert person.actor in [p["name"] for r in listing if r["name"] == "future_human_role" for p in r["members"]]
    assert runtime.delete("/work/people/" + person.actor + "?by=system").status_code == 403
    with db.connect() as conn:
        item = work.create_item(conn, "test", "Future role case", "system", role="future_human_role")
    assert runtime.post(f"/work/items/{item['id']}/comment", json={"by": "system", "text": "Owner can participate"}).status_code == 200
    with db.connect() as conn:
        conn.execute("delete from work_role where name='future_human_role'")
        conn.commit()


def test_workflow_role_is_required_even_with_operational_permission(runtime):
    person, _ = signed_in(runtime, roles=("operator",))
    with db.connect() as conn:
        item = work.create_item(conn, "test", "Engineering case", "system", role="engineer")
    path = f"/work/items/{item['id']}/comment"
    assert runtime.post(path, json={"by": "system", "text": "Cannot bypass role"}).status_code == 403
    with db.connect() as conn:
        work.set_person(conn, person.actor, ["engineer"])
    assert runtime.post(path, json={"by": "system", "text": "Assigned role"}).status_code == 200


def test_owner_can_extend_app_roles_revoke_access_and_cannot_remove_last_owner(runtime):
    owner, _ = signed_in(runtime, owner=True)
    response = runtime.put(f"/auth/accounts/{owner.id}", json={"enabled": True, "owner": False, "roles": ["reader"]})
    assert response.status_code == 400 and "at least one" in response.json()["detail"]
    assert runtime.put("/auth/roles/reliability_reviewer", json={
        "description": "Future reusable access role", "permissions": ["read", "operate"],
    }).status_code == 200
    with TestClient(app, base_url="https://testserver") as other:
        other_person, _ = signed_in(other, enabled=False, roles=(), subject=str(uuid.UUID(int=3)))
        assert other.get("/stats").status_code == 403
        assert runtime.put(f"/auth/accounts/{other_person.id}", json={
            "enabled": True, "roles": ["reliability_reviewer"],
        }).status_code == 200
        assert other.get("/stats").status_code == 200
        assert other.put("/auth/roles/escalation", json={"description": "No", "permissions": ["configure"]}).status_code == 403
        assert runtime.put(f"/auth/accounts/{other_person.id}", json={"enabled": False, "roles": []}).status_code == 200
        assert other.get("/stats").status_code == 401
    assert runtime.put("/auth/roles/invalid", json={"description": "No", "permissions": ["all_permissions"]}).status_code == 400


def test_session_expiry_and_logout_revoke_server_tokens(runtime):
    _, token = signed_in(runtime)
    assert runtime.post("/auth/logout").status_code == 200
    runtime.cookies.set("__Host-fuzer_session", token, domain="testserver.local", path="/")
    assert runtime.get("/stats").status_code == 401
    _, token = signed_in(runtime, subject=str(uuid.UUID(int=4)))
    with db.connect() as conn:
        conn.execute("update access_session set expires_at=now()-interval '1 second' where token_hash=%s", (access.digest(token),))
        conn.commit()
    assert runtime.get("/stats").status_code == 401


def test_conversations_uploads_jobs_and_answers_are_account_scoped(runtime):
    from fuzer.agents import history, service
    person, _ = signed_in(runtime, owner=True)
    with db.connect() as conn:
        history.ensure(conn, "access-private", "user:someone-else", "Private")
        conn.commit()
    for method, path in [("GET", "/assistant/conversations/access-private"),
                         ("GET", "/assistant/conversations/access-private/images/hash"),
                         ("DELETE", "/assistant/conversations/access-private")]:
        assert runtime.request(method, path).status_code == 404
    assert all(c["by_user"] == person.actor for c in runtime.get("/assistant/conversations?by=user:someone-else").json())
    assert runtime.post("/assistant/ask", json={"question": "Read their messages", "conversation_id": "access-private"}).status_code == 404
    service._jobs["access-private-job"] = {"by_user": "user:someone-else"}
    try:
        assert runtime.get("/assistant/jobs/access-private-job").status_code == 404
    finally:
        service._jobs.pop("access-private-job")
    assert all(a["by_user"] == person.actor for a in runtime.get("/assistant/answers").json())


def test_paid_get_bool_spellings_and_server_resource_bounds(runtime, monkeypatch):
    signed_in(runtime, roles=("administrator",))
    for value in ("true", "1", "yes", "on", "TRUE"):
        assert runtime.get(f"/sources/connections/1/tables/test?claude={value}").status_code == 403
    monkeypatch.setattr(security, "_active", 4)
    with settings.Overrides({"access.api_concurrency": 4}):
        response = runtime.get("/demo/okan/investigation")
        assert response.status_code == 429 and response.headers["retry-after"]
    monkeypatch.setattr(security, "_active", 0)
    monkeypatch.setattr(security, "MAX_BODY", 128)
    assert runtime.post("/board/pins", content="x" * 129, headers={"Content-Type": "application/json"}).status_code == 413


def test_remote_mcp_cannot_bypass_login(runtime):
    from fuzer.agents import mcp_server
    with pytest.raises(access.AccessError, match="authenticated web API"):
        mcp_server.main(http=True)
