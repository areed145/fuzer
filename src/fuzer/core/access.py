"""Accounts, revocable sessions and application permissions; workflow roles remain routing data."""

import hashlib
import hmac
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from . import settings

PERMISSIONS = {
    "read": "Browse the shared facility and evidence",
    "operate": "Make operational decisions and act on assigned human workflow roles",
    "ai": "Run configured AI services (may incur provider charges)",
    "configure": "Manage sources, settings, ontology, processes and workflow routing",
}
BUILTINS = {
    "reader": ("Browse evidence without making decisions", ["read"]),
    "operator": ("Participate in operations and assigned workflow roles", ["read", "operate"]),
    "administrator": ("Configure the product and operate workflows; no AI spend grant", ["read", "operate", "configure"]),
    "ai_user": ("Use configured AI services", ["read", "ai"]),
}
ACTOR_PREFIX = "user:"


class AccessError(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    id: str
    display_name: str
    enabled: bool
    owner: bool
    roles: tuple[str, ...]
    permissions: frozenset[str]
    csrf: str = ""
    project: str | None = None  # the project these roles and permissions are for (None: the database's first)

    @property
    def actor(self):
        return ACTOR_PREFIX + self.id

    def allows(self, permission: str) -> bool:
        return self.enabled and (self.owner or permission in self.permissions)


def mode() -> str:
    value = os.environ.get("FUZER_AUTH_MODE", "microsoft")
    if value not in ("microsoft", "local"):
        raise AccessError("FUZER_AUTH_MODE must be microsoft or local")
    if value == "local" and os.environ.get("RENDER"):
        raise AccessError("Local attribution mode cannot be used on Render")
    return value


def public_url() -> str:
    value = os.environ.get("FUZER_PUBLIC_URL", "").rstrip("/")
    parts = urlsplit(value)
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.path or parts.query
            or parts.fragment or parts.username or parts.password):
        raise AccessError("Set FUZER_PUBLIC_URL to the application's public origin, without a path")
    if parts.scheme != "https" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise AccessError("Microsoft sign-in requires HTTPS outside localhost")
    return value


def configured() -> bool:
    return bool(os.environ.get("FUZER_MICROSOFT_CLIENT_ID") and os.environ.get("FUZER_MICROSOFT_CLIENT_SECRET")
                and os.environ.get("FUZER_PUBLIC_URL"))


def readiness() -> dict:
    """Deployment references only; presence is not proof of an identity-provider exchange."""
    return {"mode": mode(), "configured": configured(),
            "bootstrap_present": bool(os.environ.get("FUZER_OWNER_BOOTSTRAP_TOKEN"))}


def microsoft_client():
    import msal

    if not configured():
        raise AccessError("Microsoft sign-in is not configured. Ask the deployment owner to complete sign-in setup.")
    public_url()
    return msal.ConfidentialClientApplication(
        os.environ["FUZER_MICROSOFT_CLIENT_ID"],
        authority="https://login.microsoftonline.com/common",
        client_credential=os.environ["FUZER_MICROSOFT_CLIENT_SECRET"],
        exclude_scopes=["offline_access"],
        timeout=10,
    )


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def event(conn, actor: str, action: str, **data):
    conn.execute("insert into access_event (actor, action, data) values (%s, %s, %s)",
                 (actor, action, Jsonb(data)))


def seed(conn):
    for name, (description, permissions) in BUILTINS.items():
        conn.execute("insert into access_role (name, description, permissions) values (%s, %s, %s) "
                     "on conflict do nothing", (name, description, permissions))
    conn.commit()


def _default_project(conn) -> str | None:
    from . import projects

    return projects.active(conn)


def _added(conn, project: str | None) -> bool:
    """Whether `project` is one added beside the database's first project (its roles are project grants)."""
    return project is not None and project != _default_project(conn)


def principal(conn, account_id: str, csrf: str = "", project: str | None = None) -> Principal | None:
    """The account with its roles in `project` (default: the project this request runs for). Roles in the
    database's first project are its memberships; in an added project, its grants there. Owners span all."""
    from . import db

    row = conn.execute("select * from access_account where id = %s", (account_id,)).fetchone()
    if row is None:
        return None
    project = project or db.scope().project
    if _added(conn, project):
        roles = conn.execute(
            "select r.name, r.permissions from access_project_grant g join access_role r on r.name = g.role "
            "where g.account_id = %s and g.project = %s order by r.name", (account_id, project)).fetchall()
    else:
        roles = conn.execute(
            "select r.name, r.permissions from access_membership m join access_role r on r.name = m.role "
            "where m.account_id = %s order by r.name", (account_id,)).fetchall()
    return Principal(row["id"], row["display_name"], row["enabled"], row["owner"],
                     tuple(r["name"] for r in roles),
                     frozenset(p for r in roles for p in r["permissions"]), csrf, project)


def project_roles(conn, account_id: str) -> dict[str, list[str]]:
    """The account's roles per project it is granted (the first project's are its memberships)."""
    out: dict[str, list[str]] = {}
    first = _default_project(conn)
    members = [r["role"] for r in conn.execute(
        "select role from access_membership where account_id = %s order by role", (account_id,))]
    if members:
        out[first or ""] = members
    for r in conn.execute("select project, role from access_project_grant where account_id = %s "
                          "order by project, role", (account_id,)):
        out.setdefault(r["project"], []).append(r["role"])
    return out


def projects_for(conn, person: Principal) -> list[str]:
    """The projects this person may open: every project for an owner, else those they hold a role in."""
    from . import projects

    if not person.enabled:
        return []
    names = projects.names(conn)
    if person.owner:
        return names
    granted = project_roles(conn, person.id)
    first = _default_project(conn)
    return [n for n in names if n in granted or (n == first and "" in granted)]


def set_project_roles(conn, by: Principal, account_id: str, project: str, roles: list[str]):
    """Give an account its application roles in one project (owners only). The database's first project's
    roles are the account's memberships, as in update_account."""
    from . import projects

    if not by.enabled or not by.owner:
        raise AccessError("Only an owner can grant application access")
    try:
        projects.config(conn, project)
    except projects.ProjectError as e:
        raise AccessError(str(e)) from None
    if conn.execute("select 1 from access_account where id = %s", (account_id,)).fetchone() is None:
        raise AccessError("Unknown account")
    known = {r["name"] for r in conn.execute("select name from access_role")}
    if set(roles) - known:
        raise AccessError("Unknown application role")
    if _added(conn, project):
        conn.execute("delete from access_project_grant where account_id = %s and project = %s", (account_id, project))
        for role in sorted(set(roles)):
            conn.execute("insert into access_project_grant (account_id, project, role) values (%s, %s, %s)",
                         (account_id, project, role))
    else:
        conn.execute("delete from access_membership where account_id = %s", (account_id,))
        for role in sorted(set(roles)):
            conn.execute("insert into access_membership (account_id, role) values (%s, %s)", (account_id, role))
    event(conn, by.actor, "project_roles_set", account_id=account_id, project=project, roles=sorted(set(roles)))
    conn.commit()


def sign_in(conn, claims: dict) -> tuple[Principal, str]:
    # These are MSAL-validated claims, never email, display name, or browser-provided identifiers.
    try:
        tenant = str(uuid.UUID(claims["tid"]))
        subject = str(uuid.UUID(claims["oid"]))
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        raise AccessError("Microsoft did not return a supported account identity") from e
    row = conn.execute(
        "insert into access_account (id, tenant_id, object_id, display_name) values (%s, %s, %s, %s) "
        "on conflict (tenant_id, object_id) do update set display_name = excluded.display_name returning id",
        (uuid.uuid4().hex, tenant, subject, str(claims.get("name") or "Microsoft account")[:200])).fetchone()
    account_id = row["id"]
    conn.execute("insert into work_person (name, roles) values (%s, '{}') on conflict do nothing",
                 (ACTOR_PREFIX + account_id,))
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    hours = settings.get(conn, "access.session_hours")
    conn.execute("delete from access_session where expires_at <= now()")
    conn.execute(
        "insert into access_session (token_hash, account_id, csrf, expires_at) "
        "values (%s, %s, %s, now() + %s * interval '1 hour')", (digest(token), account_id, csrf, hours))
    event(conn, ACTOR_PREFIX + account_id, "signed_in")
    conn.commit()
    return principal(conn, account_id, csrf), token


def session(conn, token: str) -> Principal | None:
    row = conn.execute("select account_id, csrf from access_session where token_hash = %s and expires_at > now()",
                       (digest(token),)).fetchone()
    return principal(conn, row["account_id"], row["csrf"]) if row else None


def bootstrap(conn, person: Principal, token: str):
    expected = os.environ.get("FUZER_OWNER_BOOTSTRAP_TOKEN", "")
    if len(expected) < 32 or not hmac.compare_digest(token.encode(), expected.encode()):
        raise AccessError("Invalid owner setup code")
    conn.execute("select pg_advisory_xact_lock(hashtext('access:owners'))")
    if conn.execute("select 1 from access_account where owner").fetchone():
        raise AccessError("An owner already exists; ask that owner to grant access")
    conn.execute("update access_account set owner = true, enabled = true where id = %s", (person.id,))
    event(conn, person.actor, "owner_bootstrapped")
    conn.commit()


def accounts(conn):
    rows = conn.execute("select id, tenant_id, object_id, display_name, enabled, owner, created_at from access_account "
                        "order by created_at").fetchall()
    for row in rows:
        row["roles"] = list(principal(conn, row["id"]).roles)
        row["projects"] = project_roles(conn, row["id"])
        row["actor"] = ACTOR_PREFIX + row["id"]
    return rows


def update_account(conn, by: Principal, account_id: str, *, enabled: bool, owner: bool, roles: list[str]):
    if not by.enabled or not by.owner:
        raise AccessError("Only an owner can grant application access")
    conn.execute("select pg_advisory_xact_lock(hashtext('access:owners'))")
    current = conn.execute("select owner from access_account where id = %s for update", (account_id,)).fetchone()
    if current is None:
        raise AccessError("Unknown account")
    if owner and not enabled:
        raise AccessError("An owner must be enabled")
    if current["owner"] and not owner:
        n = conn.execute("select count(*) as n from access_account where owner and enabled").fetchone()["n"]
        if n <= 1:
            raise AccessError("Keep at least one enabled owner")
    known = {r["name"] for r in conn.execute("select name from access_role")}
    if set(roles) - known:
        raise AccessError("Unknown application role")
    conn.execute("update access_account set enabled = %s, owner = %s where id = %s", (enabled, owner, account_id))
    conn.execute("delete from access_membership where account_id = %s", (account_id,))
    for role in sorted(set(roles)):
        conn.execute("insert into access_membership (account_id, role) values (%s, %s)", (account_id, role))
    # Apply revocation to existing sessions as well, not just future logins.
    if not enabled:
        conn.execute("delete from access_session where account_id = %s", (account_id,))
    event(conn, by.actor, "account_updated", account_id=account_id, enabled=enabled, owner=owner, roles=roles)
    conn.commit()


def set_role(conn, by: Principal, name: str, description: str, permissions: list[str]):
    if not by.enabled or not by.owner:
        raise AccessError("Only an owner can manage application roles")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or not description.strip():
        raise AccessError("Use a lowercase role name and a description")
    if set(permissions) - PERMISSIONS.keys():
        raise AccessError("Unknown application permission")
    conn.execute("insert into access_role (name, description, permissions) values (%s, %s, %s) "
                 "on conflict (name) do update set description = excluded.description, permissions = excluded.permissions",
                 (name, description, sorted(set(permissions))))
    event(conn, by.actor, "application_role_set", name=name, permissions=permissions)
    conn.commit()
