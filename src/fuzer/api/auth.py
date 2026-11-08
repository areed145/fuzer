"""Microsoft authorization-code sign-in and owner-managed application access."""

import logging
import secrets
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field
from requests.exceptions import RequestException

from ..core import access, db, settings
from .security import cookie_name, csrf_check, identify

router = APIRouter(prefix="/auth")
log = logging.getLogger(__name__)


def _owner(request):
    person = identify(request)
    if person is None or not person.owner or not person.enabled:
        raise HTTPException(403, "Only an owner can manage sign-in access")
    return person


def _cookie(response, kind, value, max_age):
    response.set_cookie(cookie_name(kind), value, max_age=max_age, httponly=True,
                        secure=access.public_url().startswith("https://"), samesite="lax", path="/")


@router.get("/me")
def me(request: Request):
    if access.mode() == "local":
        identify(request)
        return {"mode": "local", "configured": True, "user": None}
    if not access.configured():
        return {"mode": "microsoft", "configured": False, "user": None}
    with db.connect() as conn:
        person = access.session(conn, request.cookies.get(cookie_name("session"), ""))
        has_owner = bool(conn.execute("select 1 from access_account where owner").fetchone())
        visible = access.projects_for(conn, person) if person else []
    return {"mode": "microsoft", "configured": True,
            "user": None if person is None else {
                **asdict(person), "actor": person.actor, "permissions": sorted(person.permissions),
                "projects": visible},
            "needs_owner": not has_owner}


@router.get("/login")
def login(request: Request):
    if access.mode() != "microsoft":
        raise HTTPException(400, "Microsoft sign-in is not enabled")
    try:
        with db.connect() as conn:
            conn.execute("delete from access_login where expires_at <= now()")
            previous = conn.execute("select flow from access_login where token_hash = %s",
                                    (access.digest(request.cookies.get(cookie_name("login"), "")),)).fetchone()
            if previous:
                conn.commit()
                return RedirectResponse(previous["flow"]["auth_uri"], status_code=303)
            if conn.execute("select count(*) as n from access_login").fetchone()["n"] >= 200:
                raise HTTPException(429, "Sign-in is busy. Try again in a few minutes.", headers={"Retry-After": "60"})
            flow = access.microsoft_client().initiate_auth_code_flow(
                scopes=[], redirect_uri=access.public_url() + "/auth/callback",
                response_mode="query", prompt="select_account")
            return_to = request.query_params.get("return_to", "")
            if return_to.startswith("#/") and len(return_to) <= 2048:
                flow["_return_to"] = return_to
            nonce = secrets.token_urlsafe(32)
            conn.execute("insert into access_login (token_hash, flow, expires_at) "
                         "values (%s, %s, now() + interval '10 minutes')", (access.digest(nonce), Jsonb(flow)))
            conn.commit()
        response = RedirectResponse(flow["auth_uri"], status_code=303)
        _cookie(response, "login", nonce, 600)
        return response
    except access.AccessError as e:
        raise HTTPException(503, str(e)) from e
    except RequestException:
        log.warning("Microsoft sign-in service unavailable during authorization")
        return RedirectResponse("/#/?signin=unavailable", status_code=303)


@router.get("/callback")
def callback(request: Request):
    if access.mode() != "microsoft":
        raise HTTPException(400, "Microsoft sign-in is not enabled")
    with db.connect() as conn:
        row = conn.execute("delete from access_login where token_hash = %s and expires_at > now() returning flow",
                           (access.digest(request.cookies.get(cookie_name("login"), "")),)).fetchone()
        conn.commit()  # A browser-bound authorization response can be redeemed exactly once.
        if row is None:
            return RedirectResponse("/#/?signin=expired", status_code=303)
        try:
            result = access.microsoft_client().acquire_token_by_auth_code_flow(row["flow"], dict(request.query_params))
            if "error" in result or "id_token_claims" not in result:
                log.warning("Microsoft sign-in refused: %s", result.get("error", "missing_identity"))
                return RedirectResponse("/#/?signin=failed", status_code=303)
            _, token = access.sign_in(conn, result["id_token_claims"])
        except ValueError:
            log.warning("Microsoft sign-in response validation failed")
            return RedirectResponse("/#/?signin=failed", status_code=303)
        except RequestException:
            log.warning("Microsoft sign-in service unavailable during code exchange")
            return RedirectResponse("/#/?signin=unavailable", status_code=303)
        old = request.cookies.get(cookie_name("session"))
        if old:
            conn.execute("delete from access_session where token_hash = %s", (access.digest(old),))
            conn.commit()
        hours = settings.get(conn, "access.session_hours")
    response = RedirectResponse("/" + row["flow"].get("_return_to", ""), status_code=303)
    _cookie(response, "session", token, hours * 3600)
    response.delete_cookie(cookie_name("login"), path="/", secure=access.public_url().startswith("https://"),
                           httponly=True, samesite="lax")
    return response


@router.post("/logout")
def logout(request: Request):
    person = identify(request)
    if person:
        csrf_check(request, person)
        with db.connect() as conn:
            conn.execute("delete from access_session where token_hash = %s",
                         (access.digest(request.cookies.get(cookie_name("session"), "")),))
            access.event(conn, person.actor, "signed_out")
            conn.commit()
    response = JSONResponse({"ok": True})
    if person:
        response.delete_cookie(cookie_name("session"), path="/", secure=access.public_url().startswith("https://"),
                               httponly=True, samesite="lax")
    return response


class Setup(BaseModel):
    token: str = Field(min_length=32, max_length=512)


@router.post("/bootstrap")
def bootstrap(body: Setup, request: Request):
    person = identify(request)
    if person is None:
        raise HTTPException(400, "Owner setup requires Microsoft sign-in")
    csrf_check(request, person)
    try:
        with db.connect() as conn:
            access.bootstrap(conn, person, body.token)
    except access.AccessError as e:
        raise HTTPException(403, str(e)) from e
    return {"ok": True}


@router.get("/access")
def administration(request: Request):
    _owner(request)
    with db.connect() as conn:
        return {"accounts": access.accounts(conn),
                "roles": conn.execute("select * from access_role order by name").fetchall(),
                "permissions": access.PERMISSIONS}


class AccountUpdate(BaseModel):
    enabled: bool
    owner: bool = False
    roles: list[str] = Field(max_length=100)


@router.put("/accounts/{account_id}")
def account_update(account_id: str, body: AccountUpdate, request: Request):
    person = _owner(request)
    csrf_check(request, person)
    try:
        with db.connect() as conn:
            access.update_account(conn, person, account_id, **body.model_dump())
    except access.AccessError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


class RoleUpdate(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    permissions: list[str] = Field(max_length=20)


@router.put("/roles/{name}")
def role_update(name: str, body: RoleUpdate, request: Request):
    person = _owner(request)
    csrf_check(request, person)
    try:
        with db.connect() as conn:
            access.set_role(conn, person, name, **body.model_dump())
    except access.AccessError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


class ProjectRoles(BaseModel):
    roles: list[str] = Field(max_length=100)


@router.put("/accounts/{account_id}/projects/{project}")
def project_roles_update(account_id: str, project: str, body: ProjectRoles, request: Request):
    """An account's application roles in one project (owners only; owners already span every project)."""
    person = _owner(request)
    csrf_check(request, person)
    try:
        with db.connect() as conn:
            access.set_project_roles(conn, person, account_id, project, body.roles)
    except access.AccessError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}
