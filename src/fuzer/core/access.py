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
# The demo account (D.1): a fixed, non-Microsoft account for demonstrations and automated checks of a deployment.
# It exists only while FUZER_DEMO_TOKEN is set, can never own or configure, and audits as `user:demo`.
DEMO_ID = "demo"
DEMO_TENANT = "fuzer-demo"  # never a UUID, so no Microsoft identity can collide with it
DEMO_NAME = "Demo account"
DEMO_PROJECTS = ("okan", "kestrel", "harrier")
DEMO_DENIED = frozenset({"configure"})
DEMO_SESSION_HOURS = 12


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
    """An access event for the audit record (R.5). One naming a `project` is that project's; one without (a sign-in,
    an account change) is deployment-wide and joins the record of each project its people can open."""
    conn.execute("insert into access_event (actor, action, data, project) values (%s, %s, %s, %s)",
                 (actor, action, Jsonb(data), data.get("project")))


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
    permissions = frozenset(p for r in roles for p in r["permissions"])
    owner = row["owner"]
    if row["id"] == DEMO_ID:  # whatever an owner grants it, the demo account never owns or configures
        owner, permissions = False, permissions - DEMO_DENIED
    return Principal(row["id"], row["display_name"], row["enabled"], owner,
                     tuple(r["name"] for r in roles), permissions, csrf, project)


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
    names = projects.stored(conn)
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


def demo_enabled() -> bool:
    """The demo sign-in is on only while the deployment holds a long FUZER_DEMO_TOKEN."""
    return len(os.environ.get("FUZER_DEMO_TOKEN", "")) >= 32


def demo_projects() -> list[str]:
    value = os.environ.get("FUZER_DEMO_PROJECTS")
    names = [n.strip() for n in value.split(",")] if value is not None else list(DEMO_PROJECTS)
    return [n for n in names if n]


def demo_sign_in(conn, token: str) -> tuple[Principal, str]:
    """Sign in as the demo account with the deployment's FUZER_DEMO_TOKEN. The account is created enabled with the
    operator role in the demo projects that exist; an owner can disable it or change its roles on Users, and a
    disabled demo account stays signed out. It never owns or configures (see principal)."""
    from . import projects

    expected = os.environ.get("FUZER_DEMO_TOKEN", "")
    if not demo_enabled() or not hmac.compare_digest(token.encode(), expected.encode()):
        raise AccessError("Demo sign-in is not available")
    created = conn.execute(
        "insert into access_account (id, tenant_id, object_id, display_name, enabled, owner) "
        "values (%s, %s, %s, %s, true, false) on conflict (id) do nothing returning id",
        (DEMO_ID, DEMO_TENANT, DEMO_ID, DEMO_NAME)).fetchone()
    if created:
        stored = set(projects.stored(conn))
        first = _default_project(conn)
        granted = [n for n in demo_projects() if n in stored]
        for name in granted:
            if name == first:
                conn.execute("insert into access_membership (account_id, role) values (%s, 'operator') "
                             "on conflict do nothing", (DEMO_ID,))
            else:
                conn.execute("insert into access_project_grant (account_id, project, role) "
                             "values (%s, %s, 'operator') on conflict do nothing", (DEMO_ID, name))
        conn.execute("insert into work_person (name, roles) values (%s, '{}') on conflict do nothing",
                     (ACTOR_PREFIX + DEMO_ID,))
        event(conn, ACTOR_PREFIX + DEMO_ID, "demo_account_created", projects=granted, roles=["operator"])
    conn.execute("update access_account set owner = false where id = %s and owner", (DEMO_ID,))
    if not conn.execute("select enabled from access_account where id = %s", (DEMO_ID,)).fetchone()["enabled"]:
        conn.commit()
        raise AccessError("The demo account is disabled. An owner can enable it on Users.")
    session_token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    hours = min(settings.get(conn, "access.session_hours"), DEMO_SESSION_HOURS)
    conn.execute("delete from access_session where expires_at <= now()")
    conn.execute(
        "insert into access_session (token_hash, account_id, csrf, expires_at) "
        "values (%s, %s, %s, now() + %s * interval '1 hour')", (digest(session_token), DEMO_ID, csrf, hours))
    event(conn, ACTOR_PREFIX + DEMO_ID, "signed_in", method="demo_token")
    conn.commit()
    return principal(conn, DEMO_ID, csrf), session_token


def session(conn, token: str) -> Principal | None:
    row = conn.execute("select account_id, csrf from access_session where token_hash = %s and expires_at > now()",
                       (digest(token),)).fetchone()
    if row and row["account_id"] == DEMO_ID and not demo_enabled():
        return None  # removing FUZER_DEMO_TOKEN signs the demo account out everywhere, not just at next sign-in
    return principal(conn, row["account_id"], row["csrf"]) if row else None


def bootstrap(conn, person: Principal, token: str):
    expected = os.environ.get("FUZER_OWNER_BOOTSTRAP_TOKEN", "")
    if len(expected) < 32 or not hmac.compare_digest(token.encode(), expected.encode()):
        raise AccessError("Invalid owner setup code")
    if person.id == DEMO_ID:
        raise AccessError("The demo account cannot become an owner")
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
        row["areas"] = {}
        for r in conn.execute("select project, area from access_area_grant where account_id = %s order by project, area",
                              (row["id"],)):
            row["areas"].setdefault(r["project"], []).append(r["area"])
        row["groups"] = {}
        for r in conn.execute("select project, grp from access_group_grant where account_id = %s order by project, grp",
                              (row["id"],)):
            row["groups"].setdefault(r["project"], []).append(r["grp"])
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
    if owner and account_id == DEMO_ID:
        raise AccessError("The demo account cannot be an owner")
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


# Containment in the register, child to parent (D7): a tag is part of an area, equipment is installed at a tag, a well
# is located at a platform. Current edges only.
CONTAINMENT = ("part_of", "installed_at", "located_at")


def areas(conn, account_id: str, project: str | None = None) -> list[str]:
    """The areas an account is narrowed to in `project` (default: the request's); [] means the whole project."""
    from . import db

    project = project or db.scope().project or _default_project(conn)
    return [r["area"] for r in conn.execute(
        "select area from access_area_grant where account_id = %s and project = %s order by area",
        (account_id, project))]


def set_areas(conn, by: Principal, account_id: str, project: str, area_ids: list[str]):
    """Narrow an account to areas of one project's register (owners only; [] gives back the whole project). `conn`
    must run for `project`, so the areas are checked against its register."""
    if not by.enabled or not by.owner:
        raise AccessError("Only an owner can grant application access")
    if conn.execute("select 1 from access_account where id = %s", (account_id,)).fetchone() is None:
        raise AccessError("Unknown account")
    wanted = sorted(set(area_ids))
    known = {r["fuzer_id"] for r in conn.execute(
        "select fuzer_id from entity where fuzer_id = any(%s) and kind in ('area', 'facility') and merged_into is null",
        (wanted,))}
    if set(wanted) - known:
        raise AccessError("Unknown area: " + ", ".join(sorted(set(wanted) - known)))
    conn.execute("delete from access_area_grant where account_id = %s and project = %s", (account_id, project))
    for area in wanted:
        conn.execute("insert into access_area_grant (account_id, project, area) values (%s, %s, %s)",
                     (account_id, project, area))
    event(conn, by.actor, "areas_set", account_id=account_id, project=project, areas=wanted)
    conn.commit()


def within(conn, subjects: list[str], area_ids: list[str]) -> bool:
    """Whether any of `subjects` sits inside one of `area_ids`, walking the register's containment upwards."""
    if not subjects or not area_ids:
        return False
    row = conn.execute(
        """
        with recursive up(id, depth) as (
            select coalesce(e.merged_into, s.id), 0 from unnest(%(subjects)s::text[]) as s(id)
                left join entity e on e.fuzer_id = s.id
            union
            select g.dst, up.depth + 1 from up join edge g on g.src = up.id
            where g.rel = any(%(rels)s) and (g.valid_to is null or g.valid_to >= current_date) and up.depth < 12
        )
        select exists (select 1 from up where id = any(%(areas)s)) as inside
        """, {"subjects": subjects, "areas": area_ids, "rels": list(CONTAINMENT)}).fetchone()
    return bool(row["inside"])


def may_act_on(conn, person: Principal, subjects: list[str]) -> bool:
    """D7: an owner, or a person with no area grants here, may act on anything they otherwise may. A person narrowed
    to areas may act on work about something inside one of them, or on work about nothing in the register."""
    if person.owner:
        return True
    granted = areas(conn, person.id, person.project)
    if not granted:
        return True
    return not subjects or within(conn, subjects, granted)


# Restricted documents (R.5, #29). An owner restricts a document class (a document's doc_type: oem_manual, procedure,
# ...) to a named group in one project and puts people in groups. Someone outside a class's group can't see its
# documents or their sections on any surface: the API answers 404 for them and leaves them out of every list, and
# the assistant's tools leave them out of what the model sees. Owners see everything. Local trusted mode (no
# signed-in person) is not filtered.
DOCUMENT_KINDS = ("document", "document_section")
REFERENCE_KEYS = ("fuzer_id", "id", "src", "dst", "document", "doc", "entity", "ref", "subject")


def _project(conn, project: str | None) -> str | None:
    from . import db

    return project or db.scope().project or _default_project(conn)


def document_groups(conn, project: str | None = None) -> dict[str, str]:
    """The restricted document classes in `project`, each with the group that may see it."""
    return {r["doc_type"]: r["grp"] for r in conn.execute(
        "select doc_type, grp from access_document_group where project = %s order by doc_type",
        (_project(conn, project),))}


def _names(values, what: str) -> list[str]:
    out = sorted({str(v).strip() for v in values if str(v).strip()})
    if any(len(v) > 80 for v in out):
        raise AccessError(f"A {what} name is at most 80 characters")
    return out


def set_document_groups(conn, by: Principal, project: str, classes: dict[str, str]):
    """Restrict document classes in one project to groups (owners only); a class left out is open again."""
    if not by.enabled or not by.owner:
        raise AccessError("Only an owner can restrict documents")
    wanted = {str(t).strip(): str(g).strip() for t, g in sorted(classes.items()) if str(t).strip()}
    if any(not g for g in wanted.values()):
        raise AccessError("Each restricted document class needs a group")
    _names(wanted, "document class")
    _names(wanted.values(), "group")
    conn.execute("delete from access_document_group where project = %s", (project,))
    for doc_type, grp in wanted.items():
        conn.execute("insert into access_document_group (project, doc_type, grp) values (%s, %s, %s)",
                     (project, doc_type, grp))
    event(conn, by.actor, "document_groups_set", project=project, classes=wanted)
    conn.commit()


def groups(conn, account_id: str, project: str | None = None) -> list[str]:
    """The groups an account is in, in `project`."""
    return [r["grp"] for r in conn.execute(
        "select grp from access_group_grant where account_id = %s and project = %s order by grp",
        (account_id, _project(conn, project)))]


def set_groups(conn, by: Principal, account_id: str, project: str, names: list[str]):
    """Put an account in groups in one project (owners only; [] takes it out of all of them)."""
    if not by.enabled or not by.owner:
        raise AccessError("Only an owner can grant application access")
    if conn.execute("select 1 from access_account where id = %s", (account_id,)).fetchone() is None:
        raise AccessError("Unknown account")
    wanted = _names(names, "group")
    conn.execute("delete from access_group_grant where account_id = %s and project = %s", (account_id, project))
    for grp in wanted:
        conn.execute("insert into access_group_grant (account_id, project, grp) values (%s, %s, %s)",
                     (account_id, project, grp))
    event(conn, by.actor, "groups_set", account_id=account_id, project=project, groups=wanted)
    conn.commit()


def hidden_documents(conn, person: Principal | None, project: str | None = None) -> frozenset[str]:
    """The documents and document sections `person` may not see in `project` (`conn` runs for it): those of a
    restricted class whose group they are not in, with any ids merged into them. Empty for an owner, for no person
    (trusted local mode) and when nothing is restricted."""
    if person is None or person.owner:
        return frozenset()
    project = _project(conn, project)
    restricted = document_groups(conn, project)
    if not restricted:
        return frozenset()
    mine = set(groups(conn, person.id, project))
    classes = sorted(t for t, g in restricted.items() if g not in mine)
    if not classes:
        return frozenset()
    rows = conn.execute(
        """
        with docs as (
            select g.fuzer_id from golden g join entity e on e.fuzer_id = g.fuzer_id and e.kind = 'document'
            where g.field = 'doc_type' and g.value #>> '{}' = any(%(classes)s)
        ), sections as (
            select x.src as fuzer_id from edge x join docs d on d.fuzer_id = x.dst
                join entity s on s.fuzer_id = x.src and s.kind = 'document_section'
            where x.rel = 'part_of'
        ), ids as (select fuzer_id from docs union select fuzer_id from sections)
        select fuzer_id from ids union select e.fuzer_id from entity e join ids on e.merged_into = ids.fuzer_id
        """, {"classes": classes}).fetchall()
    return frozenset(r["fuzer_id"] for r in rows)


def hidden_for(conn, actor: str) -> frozenset[str]:
    """What `actor` may not see: a signed-in account (`user:<id>`) by its groups, anyone else (an unknown or disabled
    account, an anonymous local process) as someone in no group."""
    person = principal(conn, actor.removeprefix(ACTOR_PREFIX)) if actor.startswith(ACTOR_PREFIX) else None
    if person is None or not person.enabled:
        person = Principal(id="", display_name=actor, enabled=False, owner=False, roles=(), permissions=frozenset())
    return hidden_documents(conn, person)


def refers_to(value, hidden: frozenset[str]) -> bool:
    """Whether a record (a dict) names a hidden document by one of its reference keys."""
    return isinstance(value, dict) and any(isinstance(value.get(k), str) and value[k] in hidden
                                           for k in REFERENCE_KEYS)


def scrub(value, hidden: frozenset[str]):
    """`value` without the records that name a hidden document: list items (records or bare ids) are dropped,
    nested anywhere. A top-level record that is itself a hidden document is the caller's to refuse."""
    if not hidden:
        return value
    if isinstance(value, dict):
        return {k: scrub(v, hidden) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, hidden) for v in value
                if not refers_to(v, hidden) and not (isinstance(v, str) and v in hidden)]
    return value

