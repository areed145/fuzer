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


def principal(conn, account_id: str, csrf: str = "") -> Principal | None:
    row = conn.execute("select * from access_account where id = %s", (account_id,)).fetchone()
    if row is None:
        return None
    roles = conn.execute(
        "select r.name, r.permissions from access_membership m join access_role r on r.name = m.role "
        "where m.account_id = %s order by r.name", (account_id,)).fetchall()
    return Principal(row["id"], row["display_name"], row["enabled"], row["owner"],
                     tuple(r["name"] for r in roles),
                     frozenset(p for r in roles for p in r["permissions"]), csrf)


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
