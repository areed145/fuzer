"""Use disposable accounts and a fake identity provider, never real Microsoft credentials."""

import re
import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from fuzer.api import app, security
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
        conn.execute("truncate access_event, access_login, access_session, access_membership, access_account, access_role, "
                     "access_document_group cascade")
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
        # webhooks are authenticated by each system's shared secret instead of a session (#465), still 401 without
        assert isinstance(route, security.AuthenticatedRoute) or route.path.startswith("/hooks/"), route.path
        url = re.sub(r"\{[^}]+\}", "1", route.path)
        for method in route.methods:
            response = client.request(method, url)
            assert response.status_code == 401, (method, url, response.text)
    assert client.get("/health").status_code == 200
    assert client.head("/").status_code != 405  # Render probes the port with HEAD /
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
    assert runtime.get("/demo/config").json() == {"enabled": True}
    assert runtime.get("/assistant/agents").status_code == 200
    assert runtime.get("/demo/investigation").status_code == 200
    assert runtime.get("/demo/okan/investigation").status_code == 200  # the earlier URL, naming the project's pack
    assert runtime.get("/ontology/declared").status_code == 200
    assert len(runtime.get("/assistant/agents").json()["agents"]) == 8  # the roster is readable, asking is not
    # where work goes is readable, and explaining an intake writes nothing (#509)
    routes = {r["type"]: r for r in runtime.get("/routing").json()}
    assert routes["source_issue"]["starts"] == "queue" and routes["source_issue"]["role"] == "data_steward"
    explained = runtime.get("/routing/source_issue/explain", params={"key": "x:stale", "refs": ["tag:NOPE"]})
    assert explained.status_code == 200 and explained.json()["outcome"] in ("queue", "updates")
    assert runtime.get("/routing/no_such_type/explain").status_code == 400
    for method, path, body in [
        ("POST", "/rebuild", {}),
        ("PUT", "/settings/agents.model", {"value": "changed"}),
        ("PUT", "/settings/demo.enabled", {"value": False, "scope": "global", "reason": "hide onboarding"}),
        ("PUT", "/settings/detectors/drifts/on", {"on": False, "reason": "quiet the board"}),
        ("PUT", "/settings/detectors/drifts/params/sre.drift_rise", {"value": 0.5, "class": "CO", "reason": "x"}),
        ("PUT", "/work/people/attacker", {"roles": ["workflow_admin"], "by": "system"}),
        ("POST", "/assistant/ask", {"question": "charge", "by": "system"}),
        ("POST", "/work/tasks/1/complete", {"by": "system", "data": {}}),
        ("POST", "/board/pins", {"key": "x", "rank": 1, "reason": "x", "by": "system"}),
    ]:
        assert runtime.request(method, path, json=body).status_code == 403, path
    assert runtime.get("/settings").status_code == 403
    assert runtime.get("/settings/detectors").status_code == 403
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


def test_users_keeps_saved_assignments_distinct_from_effective_access_and_owner_grants(runtime):
    owner, _ = signed_in(runtime, owner=True)
    accounts = runtime.get("/auth/access").json()["accounts"]
    own = next(a for a in accounts if a["id"] == owner.id)
    assert own["tenant_id"] == TENANT and own["object_id"] == SUBJECT
    with TestClient(app, base_url="https://testserver") as admin, TestClient(app, base_url="https://testserver") as pending:
        administrator, _ = signed_in(admin, roles=("administrator",), subject=str(uuid.UUID(int=20)))
        person, _ = signed_in(pending, enabled=False, roles=(), subject=str(uuid.UUID(int=21)))
        assert admin.get("/auth/access").status_code == 403
        assert admin.put(f"/auth/accounts/{person.id}", json={"enabled": True, "owner": True, "roles": []}).status_code == 403
        assert admin.put("/auth/roles/grant_owner", json={"description": "Not permitted", "permissions": ["configure"]}).status_code == 403
        assert admin.put(f"/work/people/{person.actor}", json={"roles": ["engineer"], "by": "system"}).status_code == 200
        row = next(p for p in admin.get("/work/people").json() if p["name"] == person.actor)
        assert row["assigned_roles"] == ["engineer"] and row["roles"] == [] and row["owner"] is False
        load = next(p for p in admin.get("/work/workload").json()["people"] if p["name"] == person.actor)
        assert load["assigned_roles"] == ["engineer"] and load["roles"] == []
        with db.connect() as conn:
            assert conn.execute("select actor from work_event where kind='roles_set' and data->>'person'=%s "
                                "order by id desc limit 1", (person.actor,)).fetchone()["actor"] == administrator.actor
        assert pending.get("/stats").status_code == 403
        assert runtime.put(f"/auth/accounts/{person.id}", json={"enabled": True, "roles": ["reader"]}).status_code == 200
        row = next(p for p in admin.get("/work/people").json() if p["name"] == person.actor)
        assert row["roles"] == row["assigned_roles"] == ["engineer"]
        assert pending.get("/stats").status_code == 200
        assert runtime.put("/auth/roles/reader", json={"description": "Reader with operational access for verification",
                                                    "permissions": ["read", "operate"]}).status_code == 200
        assert pending.get("/auth/me").json()["user"]["permissions"] == ["operate", "read"]
        assert runtime.put("/auth/roles/reader", json={"description": "Read evidence", "permissions": ["read"]}).status_code == 200
        assert pending.get("/auth/me").json()["user"]["permissions"] == ["read"]
        assert pending.put(f"/work/people/{person.actor}", json={"roles": ["workflow_admin"], "by": "system"}).status_code == 403
        assert runtime.put(f"/auth/accounts/{person.id}", json={"enabled": False, "roles": ["reader"]}).status_code == 200
        assert pending.get("/stats").status_code == 401
        row = next(p for p in admin.get("/work/people").json() if p["name"] == person.actor)
        assert row["assigned_roles"] == ["engineer"] and row["roles"] == []
    assert runtime.put(f"/auth/accounts/{owner.id}", json={"enabled": False, "owner": True, "roles": []}).status_code == 400
    own_roster = next(p for p in runtime.get("/work/people").json() if p["name"] == owner.actor)
    assert own_roster["owner"] and "workflow_admin" in own_roster["roles"]


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
        response = runtime.get("/demo/investigation")
        assert response.status_code == 429 and response.headers["retry-after"]
    monkeypatch.setattr(security, "_active", 0)
    monkeypatch.setattr(security, "MAX_BODY", 128)
    assert runtime.post("/board/pins", content="x" * 129, headers={"Content-Type": "application/json"}).status_code == 413


def test_remote_mcp_cannot_bypass_login(runtime):
    from fuzer.agents import mcp_server
    with pytest.raises(access.AccessError, match="authenticated web API"):
        mcp_server.main(http=True)


def _draft(owner, role="production_engineer", title="Review deferment"):
    from fuzer.workflow import work_drafts

    with db.connect() as conn:
        return work_drafts.create(conn, title, role, "Rate fell; see the record.", ["fz-evidence"], owner)


def test_a_draft_belongs_to_who_asked_and_a_person_creates_it_once(runtime):
    person, _ = signed_in(runtime, roles=("operator",))
    draft = _draft(person.actor)
    path = f"/work/drafts/{draft['id']}"
    assert runtime.get(path).json()["item_id"] is None

    first = runtime.post(f"{path}/confirm", json={"by": "someone-else"})
    assert first.status_code == 200 and first.json()["created"] is True
    again = runtime.post(f"{path}/confirm", json={"by": "someone-else"}).json()
    assert again == {"item_id": first.json()["item_id"], "created": False}

    item = runtime.get(f"/work/items/{again['item_id']}").json()
    assert (item["role"], item["created_by"], item["source"]) == ("production_engineer", person.actor, "assistant")
    assert item["assignee"] is None
    assert runtime.get(path).json()["confirmed_by"] == person.actor


def test_another_account_cannot_see_or_confirm_a_draft(runtime):
    signed_in(runtime, roles=("operator",))
    draft = _draft("someone-else")
    assert runtime.get(f"/work/drafts/{draft['id']}").status_code == 404
    assert runtime.post(f"/work/drafts/{draft['id']}/confirm", json={"by": "someone-else"}).status_code == 404
    with db.connect() as conn:
        assert conn.execute("select item_id from work_draft where id = %s", (draft["id"],)).fetchone()["item_id"] is None


def test_a_reader_can_see_their_draft_but_not_create_it(runtime):
    person, _ = signed_in(runtime, roles=("reader",))
    draft = _draft(person.actor)
    assert runtime.get(f"/work/drafts/{draft['id']}").status_code == 200
    assert runtime.post(f"/work/drafts/{draft['id']}/confirm", json={"by": person.actor}).status_code == 403


def test_entity_names_are_read_access_and_bounded(runtime):
    _person, _ = signed_in(runtime, roles=("reader",))
    with db.connect() as conn:
        id, label = conn.execute("select fuzer_id, label from entity_label limit 1").fetchone().values()
    known = runtime.get("/entities/names", params={"id": [id, "fz-tag-000000000000"]})
    assert known.status_code == 200
    assert list(known.json()) == [id] and known.json()[id]["label"] == label
    assert runtime.get("/entities/names").json() == {}
    assert runtime.get("/entities/names", params={"id": ["fz-a"] * 201}).status_code == 422
    assert runtime.get(f"/entities/{id}").status_code == 200, "the fixed route doesn't shadow an entity"
    runtime.cookies.clear()
    assert runtime.get("/entities/names", params={"id": [id]}).status_code == 401


# ---- access per project (multi-project phase 3) -------------------------------------------------------

@pytest.fixture
def second_project(runtime):
    """A second project beside this database's register, in its own schema."""
    from fuzer.core import projects

    with db.connect() as conn:
        db.create_project_schema(conn, "proj_ridge")
        projects.save(conn, "ridge", {"schema": "proj_ridge", "pack": "okan"}, "test")
    yield "ridge"
    with db.connect() as conn:
        conn.execute("delete from project where name = 'ridge'")
        conn.execute("drop schema if exists proj_ridge cascade")
        conn.commit()


def test_roles_are_per_project_and_owners_span_every_project(runtime, second_project):
    client = runtime
    reader, _ = signed_in(client, roles=("operator",))
    with db.connect() as conn:
        here = access.principal(conn, reader.id)
        there = access.principal(conn, reader.id, project=second_project)
        assert here.allows("operate") and not there.allows("read")  # a role here is not a role there
        assert second_project not in access.projects_for(conn, here)
        boss, _ = access.sign_in(conn, {"tid": TENANT, "oid": str(uuid.UUID(int=9)), "name": "Owner"})
        conn.execute("update access_account set enabled = true, owner = true where id = %s", (boss.id,))
        conn.commit()
        boss = access.principal(conn, boss.id)
        assert access.principal(conn, boss.id, project=second_project).allows("configure")
        assert second_project in access.projects_for(conn, boss)

        access.set_project_roles(conn, boss, reader.id, second_project, ["reader"])
        there = access.principal(conn, reader.id, project=second_project)
        assert there.allows("read") and not there.allows("operate") and there.project == second_project
        assert access.principal(conn, reader.id).allows("operate")  # the first project's roles are unchanged
        assert second_project in access.projects_for(conn, access.principal(conn, reader.id))
        assert access.project_roles(conn, reader.id)[second_project] == ["reader"]
        with pytest.raises(access.AccessError, match="Only an owner"):
            access.set_project_roles(conn, here, reader.id, second_project, ["administrator"])
        with pytest.raises(access.AccessError, match="no project"):
            access.set_project_roles(conn, boss, reader.id, "nowhere", ["reader"])
        # Disabling the account takes every project away.
        conn.execute("update access_account set enabled = false where id = %s", (reader.id,))
        conn.commit()
        assert access.projects_for(conn, access.principal(conn, reader.id)) == []
        assert not access.principal(conn, reader.id, project=second_project).allows("read")


def test_project_grants_over_http_are_owner_only(runtime, second_project):
    client = runtime
    person, _ = signed_in(client, roles=("administrator",))
    r = client.put(f"/auth/accounts/{person.id}/projects/{second_project}", json={"roles": ["reader"]})
    assert r.status_code == 403  # configure access is not owner access
    _owner, _ = signed_in(client, owner=True, roles=(), subject=str(uuid.UUID(int=7)))
    r = client.put(f"/auth/accounts/{person.id}/projects/{second_project}", json={"roles": ["reader"]})
    assert r.status_code == 200, r.text
    me = client.get("/auth/me").json()["user"]
    assert second_project in me["projects"]  # an owner sees every project
    admin = client.get("/auth/access").json()
    row = next(a for a in admin["accounts"] if a["id"] == person.id)
    assert row["projects"][second_project] == ["reader"]


def test_requests_run_for_the_project_the_ui_picked(runtime, second_project):
    """The X-Fuzer-Project header scopes a request to that project's register, after the server checks the
    person may open it; an unknown project is 404 and an ungranted one 403 (multi-project phase 4)."""
    client = runtime
    person, _ = signed_in(client, roles=("operator",))
    with db.connect() as conn:
        id = conn.execute("select fuzer_id from entity_label limit 1").fetchone()["fuzer_id"]
    names = {"id": [id]}
    assert list(client.get("/entities/names", params=names).json()) == [id]  # no header: the first project

    there = {"X-Fuzer-Project": second_project}
    r = client.get("/entities/names", params=names, headers=there)
    assert r.status_code == 403 and "access to this project" in r.json()["detail"]
    assert client.get("/entities/names", params=names, headers={"X-Fuzer-Project": "nowhere"}).status_code == 404
    assert client.get("/auth/me", headers=there).json()["user"]["permissions"] == []

    with db.connect() as conn:
        conn.execute("insert into access_project_grant (account_id, project, role) values (%s, %s, 'reader')",
                     (person.id, second_project))
        conn.commit()
    r = client.get("/entities/names", params=names, headers=there)
    assert r.status_code == 200 and r.json() == {}  # ridge's register is its own, and empty
    me = client.get("/auth/me", headers=there).json()
    assert me["project"] == second_project and me["user"]["permissions"] == ["read"]
    assert client.post("/work/tick", headers=there).status_code == 403  # a reader there, an operator here
    assert list(client.get("/entities/names", params=names).json()) == [id]  # the first project is unchanged
    # A link the browser opens itself (a document) names the project in the query; only reads accept it.
    assert client.get("/entities/names", params={**names, "fuzer_project": second_project}).json() == {}


def test_overarching_people_work_every_field_they_cover_but_open_only_granted_ones(runtime, second_project):
    """A person can be on one field's roster, or overarching across fields (#497): their roles count in each field
    they cover, the cross-field Inbox gathers their work, and it only lists fields the viewer may open."""
    from fuzer.core import projects

    with db.connect() as conn:
        work.set_cover(conn, "vera", ["engineer"], [], by="test")  # every field
        assert "engineer" in work.roles_of(conn, "vera")
        assert next(p for p in work.people(conn) if p["name"] == "vera")["covers"] == []
        with projects.use(conn, second_project), db.connect() as ridge:
            assert work.roles_of(ridge, "vera") == ["engineer"]
            item = work.create_item(ridge, "check", "Ridge compressor check", "test", role="engineer")
            ridge.commit()
        work.set_cover(conn, "vera", ["engineer"], [second_project], by="test")  # only ridge now
        assert "engineer" not in work.roles_of(conn, "vera")
        with pytest.raises(work.WorkError, match="no project"):
            work.set_cover(conn, "vera", ["engineer"], ["nowhere"], by="test")
        work.set_person(conn, "vera", ["operator"], by="test")  # and on this field's own roster
        assert work.roles_of(conn, "vera") == ["operator"]

    fields = {f["project"]: f for f in work.work_across("vera")}
    assert fields[second_project]["roles"] == ["engineer"]
    assert any(r["item_id"] == item["id"] for r in fields[second_project]["queue"])
    assert len(fields) == 2

    signed_in(runtime, roles=("operator",))  # granted this field only
    shown = runtime.get("/work/inbox/fields", params={"person": "vera"}).json()
    assert [f["project"] for f in shown] != [] and second_project not in [f["project"] for f in shown]
    assert runtime.put("/work/people/vera/cover", json={"roles": ["engineer"]}).status_code == 403  # configure only
    with db.connect() as conn:
        work.remove_cover(conn, "vera", by="test")
        work.remove_person(conn, "vera", by="test")


def test_audit_record_is_owner_only_and_per_project(runtime, second_project):
    client = runtime
    reader, _ = signed_in(client, roles=("administrator",), subject=str(uuid.UUID(int=21)))
    assert client.get("/auth/audit").status_code == 403  # configure access is not owner access
    owner, _ = signed_in(client, owner=True, roles=(), subject=str(uuid.UUID(int=22)))
    r = client.put(f"/auth/accounts/{reader.id}/projects/{second_project}", json={"roles": ["reader"]})
    assert r.status_code == 200, r.text
    with db.connect() as conn:
        # A pending account, granted nowhere: its sign-in is in the first project's record only.
        loner, _ = access.sign_in(conn, {"tid": TENANT, "oid": str(uuid.UUID(int=23)), "name": "Okan only"})

    def actions(project):
        r = client.get("/auth/audit", params={"project": project})
        assert r.status_code == 200, r.text
        assert all(row["project"] == project for row in r.json()["rows"])
        return [(row["actor"], row["action"], row["detail"].get("project")) for row in r.json()["rows"]]

    with db.connect() as conn:
        first = access._default_project(conn)
    here, there = actions(first), actions(second_project)
    grant = (owner.actor, "project_roles_set", second_project)
    assert grant in there and grant not in here  # the grant belongs to the project it concerns
    assert (loner.actor, "signed_in", None) in here and (loner.actor, "signed_in", None) not in there
    assert (reader.actor, "signed_in", None) in there  # a person granted there: their sign-in is in its record
    # The first project's register (work, sources) never shows up in the second project's record.
    assert not any(a == "project_roles_set" and p == first for _, a, p in there)
    def sources(project):
        return {row["source"] for row in client.get("/auth/audit", params={"project": project}).json()["rows"]}

    assert {"work_item.created_by", "source_audit.by"} <= sources(first)
    assert not {"work_item.created_by", "source_audit.by"} & sources(second_project)  # its own register only
    # Exports are themselves recorded, and CSV downloads name the project and window.
    r = client.get("/auth/audit", params={"project": second_project, "format": "csv",
                                          "since": "2020-01-01T00:00:00Z"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert f"fuzer-audit-{second_project}-20200101-" in r.headers["content-disposition"]
    assert (owner.actor, "audit_exported", second_project) in actions(second_project)
    assert client.get("/auth/audit", params={"project": "nowhere"}).status_code == 404
    cov = client.get("/auth/audit/coverage").json()
    assert cov["unpaired"] == [] and cov["excluded"]


def _area_and_two_subjects(conn):
    """An area of the register, something inside it, and something in another area."""
    inside = conn.execute(
        "select e.src, e.dst from edge e join entity t on t.fuzer_id = e.dst where t.kind = 'area' "
        "and e.rel = 'part_of' order by e.dst, e.src limit 1").fetchone()
    other = conn.execute(
        "select e.src from edge e join entity t on t.fuzer_id = e.dst where t.kind = 'area' and e.rel = 'part_of' "
        "and e.dst <> %s order by e.src limit 1", (inside["dst"],)).fetchone()
    return inside["dst"], inside["src"], other["src"]


def test_area_grants_narrow_what_a_person_acts_on_never_what_they_read(runtime):
    client = runtime
    person, person_token = signed_in(client, roles=("operator",), subject=str(uuid.UUID(int=31)))

    def be(who, token):
        client.cookies.set("__Host-fuzer_session", token, domain="testserver.local", path="/")
        client.headers["X-Fuzer-CSRF"] = who.csrf

    with db.connect() as conn:
        area, mine, theirs = _area_and_two_subjects(conn)
        work.set_person(conn, person.actor, ["engineer"])
        here = work.create_item(conn, "test", "In my area", "system", role="engineer", subjects=[mine])
        there = work.create_item(conn, "test", "Next area over", "system", role="engineer", subjects=[theirs])
        nowhere = work.create_item(conn, "test", "Facility-wide", "system", role="engineer")

    def comment(item):
        return client.post(f"/work/items/{item['id']}/comment", json={"by": "system", "text": "On it"}).status_code

    assert comment(there) == 200  # no area grants: the whole project, as before
    r = client.put(f"/auth/accounts/{person.id}/projects/okan/areas", json={"areas": [area]})
    assert r.status_code == 403  # only an owner grants access
    owner, owner_token = signed_in(client, owner=True, roles=(), subject=str(uuid.UUID(int=32)))
    with db.connect() as conn:
        project = access._default_project(conn)
    assert client.put(f"/auth/accounts/{person.id}/projects/{project}/areas",
                      json={"areas": ["fz-not-an-area"]}).status_code == 400
    assert client.put(f"/auth/accounts/{person.id}/projects/{project}/areas", json={"areas": [area]}).status_code == 200
    admin = client.get("/auth/access").json()
    assert next(a for a in admin["accounts"] if a["id"] == person.id)["areas"] == {project: [area]}
    assert comment(there) == 200  # an owner spans every area

    be(person, person_token)
    assert comment(here) == 200
    assert comment(nowhere) == 200  # work about nothing in the register is shared
    r = client.post(f"/work/items/{there['id']}/comment", json={"by": "system", "text": "On it"})
    assert r.status_code == 403 and "outside your areas" in r.json()["detail"]
    assert client.get(f"/work/items/{there['id']}").status_code == 200  # reading is never narrowed
    with db.connect() as conn:
        tokens = conn.execute("select id from work_token where item_id = %s", (there["id"],)).fetchall()
        assert access.may_act_on(conn, access.principal(conn, person.id), [mine])
        assert not access.may_act_on(conn, access.principal(conn, person.id), [theirs])
        events = conn.execute("select actor, project from access_event where action = 'areas_set'").fetchall()
        assert [(e["actor"], e["project"]) for e in events] == [(owner.actor, project)]  # in that project's record
    for t in tokens:
        assert client.post(f"/work/tasks/{t['id']}/claim", json={"by": "system"}).status_code == 403

    be(owner, owner_token)
    assert client.put(f"/auth/accounts/{person.id}/projects/{project}/areas", json={"areas": []}).status_code == 200
    be(person, person_token)
    assert comment(there) == 200  # no areas again: the whole project


def _manual_and_procedure(conn):
    """A licensed OEM manual with one of its sections and the equipment model it applies to, and an open procedure."""
    def doc(doc_type):
        return conn.execute("select g.fuzer_id from golden g join entity e on e.fuzer_id = g.fuzer_id "
                            "and e.kind = 'document' and e.merged_into is null "
                            "where g.field = 'doc_type' and g.value #>> '{}' = %s order by 1 limit 1",
                            (doc_type,)).fetchone()["fuzer_id"]
    manual, procedure = doc("oem_manual"), doc("procedure")
    section = conn.execute("select src from edge where rel = 'part_of' and dst = %s limit 1", (manual,)).fetchone()["src"]
    title = conn.execute("select value #>> '{}' as t from golden where fuzer_id = %s and field = 'title'",
                         (manual,)).fetchone()["t"]
    return manual, section, title, procedure


def _ids(value) -> set[str]:
    out = set()
    if isinstance(value, dict):
        out |= {v for k, v in value.items() if k in access.REFERENCE_KEYS and isinstance(v, str)}
        for v in value.values():
            out |= _ids(v)
    elif isinstance(value, list):
        for v in value:
            out |= _ids(v)
    return out


def test_restricted_documents_are_hidden_everywhere_from_people_outside_their_group(runtime):
    from fuzer.agents import mcp_server
    from fuzer.core.capabilities import ASKER

    client = runtime
    person, person_token = signed_in(client, roles=("reader",), subject=str(uuid.UUID(int=41)))

    def be(who, token):
        client.cookies.set("__Host-fuzer_session", token, domain="testserver.local", path="/")
        client.headers["X-Fuzer-CSRF"] = who.csrf

    with db.connect() as conn:
        manual, section, title, procedure = _manual_and_procedure(conn)
        project = access._default_project(conn)
    words = " ".join(title.split()[:2])
    assert client.get(f"/entities/{manual}").status_code == 200  # nothing restricted: open, as before
    assert manual in _ids(client.get("/entities", params={"q": words, "kind": "document"}).json())
    assert {manual, section} & _ids(client.get("/knowledge/search", params={"q": words, "limit": 50}).json())
    token = ASKER.set(person.actor)
    try:
        assert manual in _ids(mcp_server.search(words, kind="document"))
        assert {manual, section} & _ids(mcp_server.search_knowledge(words, limit=20))
    finally:
        ASKER.reset(token)
    assert client.put(f"/auth/projects/{project}/document-groups",
                      json={"restricted": {"oem_manual": "licensed"}}).status_code == 403  # owners only

    owner, owner_token = signed_in(client, owner=True, roles=(), subject=str(uuid.UUID(int=42)))
    listed = client.get(f"/auth/projects/{project}/document-groups").json()
    assert listed["restricted"] == {} and {"doc_type": "oem_manual", "documents": 22} in listed["classes"]
    assert client.put(f"/auth/projects/{project}/document-groups",
                      json={"restricted": {"oem_manual": "licensed"}}).status_code == 200
    assert client.get(f"/entities/{manual}").status_code == 200  # an owner sees everything

    be(person, person_token)
    for path in (f"/entities/{manual}", f"/entities/{section}", f"/documents/{manual}/file", f"/graph/{manual}"):
        assert client.get(path).status_code == 404, path
    assert manual not in _ids(client.get("/lookup", params={"text": title}).json())
    found = client.get("/entities", params={"q": words, "kind": "document"}).json()
    assert manual not in _ids(found)
    passages = client.get("/knowledge/search", params={"q": words, "limit": 50}).json()
    assert not ({manual, section} & _ids(passages))
    assert client.get(f"/entities/{procedure}").status_code == 200  # an open class stays open
    token = ASKER.set(person.actor)
    try:
        assert manual not in _ids(mcp_server.search(words, kind="document"))
        assert mcp_server.get_entity(manual) == {"error": "nothing found; try the search tool"}
        assert not ({manual, section} & _ids(mcp_server.search_knowledge(words, limit=20)))
        assert "error" not in mcp_server.get_entity(procedure)
    finally:
        ASKER.reset(token)

    be(owner, owner_token)
    assert client.put(f"/auth/accounts/{person.id}/projects/{project}/groups",
                      json={"groups": ["licensed"]}).status_code == 200
    admin = client.get("/auth/access").json()
    assert next(a for a in admin["accounts"] if a["id"] == person.id)["groups"] == {project: ["licensed"]}
    be(person, person_token)
    assert client.get(f"/entities/{manual}").status_code == 200  # in the group: seen again
    with db.connect() as conn:
        events = conn.execute("select action, project from access_event where action in "
                              "('document_groups_set', 'groups_set') order by id").fetchall()
    assert [(e["action"], e["project"]) for e in events] == [("document_groups_set", project), ("groups_set", project)]
