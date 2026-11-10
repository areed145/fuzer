"""Process definitions, forms, and workflow people management."""

import json
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path

from psycopg.types.json import Jsonb

from ..core.work import WorkError as PortError
from . import bpmn

SYSTEM = "system"
ADMIN_ROLE = "workflow_admin"
DEFAULT_OWNER = "engineer"
DEFAULT_SLA = timedelta(days=1)
DUE_SOON = timedelta(hours=24)
DEFINITIONS_DIR = Path(__file__).with_name("definitions")
TASK_KINDS = ("user", "agent", "fix")


class WorkError(PortError):
    status = 400


class NotFound(WorkError):
    status = 404


class Forbidden(WorkError):
    status = 403


class Conflict(WorkError):
    status = 409


class CheckFailed(WorkError):
    status = 422

    def __init__(self, problems: list[bpmn.Problem]):
        self.problems = problems
        super().__init__("; ".join(str(p) for p in problems))


def is_agent(actor: str) -> bool:
    return actor.startswith("agent:")


def utcnow() -> datetime:
    return datetime.now(UTC)


def _op(fn):
    """One transaction per public operation: commit on success, roll back on any error."""
    @wraps(fn)
    def run(conn, *args, **kwargs):
        try:
            from . import roles

            roles.refresh(conn)  # roles are data: the checks and lanes read them
            out = fn(conn, *args, **kwargs)
            conn.commit()
            return out
        except BaseException:
            conn.rollback()
            raise
    return run


# --- definitions --------------------------------------------------------------------------------

_parsed: dict[tuple[str, str], bpmn.Process] = {}


def _definition(conn, definition_id: int) -> bpmn.Process:
    """Parsed once per file content (keyed by its hash, so a reset database can't hand back a stale one)."""
    row = conn.execute("select process_key, md5(xml) as h from work_definition where id = %s", (definition_id,)).fetchone()
    key = (row["h"], row["process_key"])
    if key not in _parsed:
        xml = conn.execute("select xml from work_definition where id = %s", (definition_id,)).fetchone()["xml"]
        _parsed[key] = bpmn.parse(xml)[row["process_key"]]
    return _parsed[key]


def _published(conn, process_key: str) -> dict:
    row = conn.execute("select * from work_definition where process_key = %s and state = 'published' "
                       "order by version desc limit 1", (process_key,)).fetchone()
    if row is None:
        raise NotFound(f"no published process '{process_key}'")
    return row


def _published_keys(conn) -> set[str]:
    return {r["process_key"] for r in conn.execute("select distinct process_key from work_definition where state = 'published'")}


def _event(conn, kind: str, actor: str, item_id=None, process_id=None, token_id=None, definition_id=None,
           element=None, data=None, at=None):
    conn.execute(
        "insert into work_event (item_id, process_id, token_id, definition_id, kind, actor, element, data, at) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, coalesce(%s, now()))",
        (item_id, process_id, token_id, definition_id, kind, actor, element, Jsonb(data or {}), at))


@_op
def import_bpmn(conn, xml: str, by: str) -> list[dict]:
    """A .bpmn file as new draft versions, one per process in it."""
    try:
        procs = bpmn.parse(xml)
    except ValueError as e:
        raise CheckFailed([bpmn.Problem(None, str(e))]) from e
    if not procs:
        raise CheckFailed([bpmn.Problem(None, "the file has no bpmn:process")])
    out = []
    for key, p in procs.items():
        row = conn.execute(
            """
            insert into work_definition (process_key, version, name, xml, created_by)
            values (%s, coalesce((select max(version) from work_definition where process_key = %s), 0) + 1, %s, %s, %s)
            returning id, process_key, version, name, state
            """, (key, key, p.name, xml, by)).fetchone()
        _event(conn, "imported", by, definition_id=row["id"], data={"process_key": key, "version": row["version"]})
        out.append(row)
    return out

def _problems(conn, row: dict) -> list[bpmn.Problem]:
    return bpmn.check(row["xml"], _published_keys(conn), process_key=row["process_key"])

@_op
def check_definition(conn, definition_id: int, by: str) -> dict:
    """Run the publish checks; a draft with none left becomes `checked`."""
    row = conn.execute("select * from work_definition where id = %s for update", (definition_id,)).fetchone()
    if row is None:
        raise NotFound("unknown definition")
    problems = _problems(conn, row)
    state = row["state"] if row["state"] in ("published", "superseded") else ("checked" if not problems else "draft")
    conn.execute("update work_definition set problems = %s, state = %s where id = %s",
                 (Jsonb([vars(p) for p in problems]), state, definition_id))
    _event(conn, "checked", by, definition_id=definition_id, data={"problems": [str(p) for p in problems]})
    return {"id": definition_id, "state": state, "problems": [vars(p) for p in problems]}

@_op
def publish(conn, definition_id: int, by: str) -> dict:
    """Publish a version: needs the workflow-admin role (or `system`, loading the pack's starting set)
    and no problems. New processes start on it; running ones stay on theirs."""
    if by != SYSTEM and ADMIN_ROLE not in roles_of(conn, by):
        raise Forbidden(f"publishing needs the {ADMIN_ROLE} role; {by} doesn't have it")
    row = conn.execute("select * from work_definition where id = %s for update", (definition_id,)).fetchone()
    if row is None:
        raise NotFound("unknown definition")
    if row["state"] == "published":
        return {"id": definition_id, "state": "published", "version": row["version"]}
    if problems := _problems(conn, row):
        conn.rollback()
        conn.execute("update work_definition set problems = %s, state = 'draft' where id = %s",
                     (Jsonb([vars(p) for p in problems]), definition_id))
        _event(conn, "publish_refused", by, definition_id=definition_id, data={"problems": [str(p) for p in problems]})
        conn.commit()
        raise CheckFailed(problems)
    conn.execute("update work_definition set state = 'superseded' where process_key = %s and state = 'published'",
                 (row["process_key"],))
    conn.execute("update work_definition set state = 'published', problems = '[]', published_by = %s, "
                 "published_at = now() where id = %s", (by, definition_id))
    _event(conn, "published", by, definition_id=definition_id,
           data={"process_key": row["process_key"], "version": row["version"]})
    return {"id": definition_id, "state": "published", "version": row["version"]}

def _definition_row(conn, definition_id: int, lock: bool = False) -> dict:
    row = conn.execute(f"select * from work_definition where id = %s{' for update' if lock else ''}",
                       (definition_id,)).fetchone()
    if row is None:
        raise NotFound("unknown definition")
    return row

@_op
def draft(conn, process_key: str, by: str, from_id: int | None = None) -> dict:
    """The version to edit in the modeler: the open draft newer than what is published, else a new draft
    copied from `from_id` (default: the published version)."""
    conn.execute("select pg_advisory_xact_lock(hashtext('work_definition:' || %s))", (process_key,))
    row = conn.execute(
        """
        select id, process_key, version, name, state from work_definition d
        where process_key = %(k)s and state in ('draft', 'checked')
          and version > coalesce((select max(version) from work_definition where process_key = %(k)s
                                  and state in ('published', 'superseded')), 0)
        order by version desc limit 1
        """, {"k": process_key}).fetchone()
    if row and from_id is None:
        return row
    src = _definition_row(conn, from_id) if from_id is not None else _published(conn, process_key)
    if src["process_key"] != process_key:
        raise WorkError(f"definition {from_id} is a version of '{src['process_key']}', not '{process_key}'")
    row = conn.execute(
        """
        insert into work_definition (process_key, version, name, xml, created_by)
        values (%s, (select max(version) from work_definition where process_key = %s) + 1, %s, %s, %s)
        returning id, process_key, version, name, state
        """, (process_key, process_key, src["name"], src["xml"], by)).fetchone()
    _event(conn, "draft_started", by, definition_id=row["id"],
           data={"process_key": process_key, "version": row["version"], "from_version": src["version"]})
    return row

@_op
def save_draft(conn, definition_id: int, xml: str, by: str) -> dict:
    """Save the modeler's diagram over a draft. It goes back to `draft` until checked again; published and
    superseded versions never change."""
    row = _definition_row(conn, definition_id, lock=True)
    if row["state"] not in ("draft", "checked"):
        raise Conflict(f"v{row['version']} is {row['state']} and can't change; start a new draft from it")
    try:
        procs = bpmn.parse(xml)
    except ValueError as e:
        raise CheckFailed([bpmn.Problem(None, str(e))]) from e
    if row["process_key"] not in procs:
        raise CheckFailed([bpmn.Problem(None, f"the diagram has no process '{row['process_key']}'; a process "
                                              "keeps its id across versions (import it as a new process instead)")])
    conn.execute("update work_definition set xml = %s, name = %s, state = 'draft', problems = '[]' where id = %s",
                 (xml, procs[row["process_key"]].name, definition_id))
    _event(conn, "draft_saved", by, definition_id=definition_id,
           data={"process_key": row["process_key"], "version": row["version"]})
    return {"id": definition_id, "process_key": row["process_key"], "version": row["version"], "state": "draft"}

def compare(conn, definition_id: int, against_id: int | None = None) -> dict:
    """What a version changes against another (default: the published one, or for the published one the
    version it replaced)."""
    row = _definition_row(conn, definition_id)
    if against_id is not None:
        base = _definition_row(conn, against_id)
        if base["process_key"] != row["process_key"]:
            raise WorkError("both versions must be of the same process")
    else:
        base = conn.execute(
            "select * from work_definition where process_key = %(k)s and id <> %(id)s and "
            "(state = 'published' or (%(pub)s and state = 'superseded')) order by version desc limit 1",
            {"k": row["process_key"], "id": definition_id, "pub": row["state"] == "published"}).fetchone()
    against = {k: base[k] for k in ("id", "version", "state")} if base else None
    return {"id": definition_id, "version": row["version"], "against": against,
            "changes": bpmn.diff(base["xml"] if base else None, row["xml"], row["process_key"])}

def monitor(conn, process_key: str, definition_id: int | None = None, days: int = 30,
            now: datetime | None = None) -> dict:
    """Where work is in a process: per element, steps waiting now (and how many are overdue, the oldest)
    and steps finished in the last `days` with their median time; plus processes by version and outcome.
    Boundary timers watching a step aren't counted as work waiting; steps that pass straight through
    (start, gateways, ends) aren't counted as done (the outcomes say how processes ended)."""
    now = now or utcnow()
    since = now - timedelta(days=days)
    args = {"k": process_key, "d": definition_id, "now": now, "since": since}
    rows = conn.execute(
        """
        select t.element, max(t.name) as name,
               count(*) filter (where t.state = 'waiting' and t.attached_to is null) as waiting,
               count(*) filter (where t.state = 'waiting' and t.attached_to is null and t.due_at < %(now)s) as overdue,
               min(t.created_at) filter (where t.state = 'waiting' and t.attached_to is null) as oldest,
               count(*) filter (where t.state = 'done') as done,
               percentile_cont(0.5) within group (order by extract(epoch from t.finished_at - t.created_at))
                 filter (where t.state = 'done') as median_seconds
        from work_token t join work_process p on p.id = t.process_id
        where p.process_key = %(k)s and (%(d)s::bigint is null or p.definition_id = %(d)s)
          and (t.state = 'waiting' or (t.state = 'done' and t.finished_at >= %(since)s and t.kind not in ('auto', 'end')))
        group by t.element
        """, args).fetchall()
    procs = conn.execute(
        """
        select d.id as definition_id, d.version, p.state, p.outcome, count(*) as n
        from work_process p join work_definition d on d.id = p.definition_id
        where p.process_key = %(k)s and (%(d)s::bigint is null or p.definition_id = %(d)s)
          and (p.state = 'running' or p.ended_at >= %(since)s)
        group by d.id, d.version, p.state, p.outcome order by d.version desc, n desc
        """, args).fetchall()
    return {"process_key": process_key, "definition_id": definition_id, "days": days, "at": now,
            "elements": {r.pop("element"): r for r in rows}, "processes": procs}

def roles() -> dict:
    return {"person": sorted(bpmn.PERSON_ROLES), "agent": sorted(bpmn.AGENT_ROLES - bpmn.RETIRED_AGENT_ROLES)}

def export_bpmn(conn, definition_id: int) -> str:
    row = conn.execute("select xml from work_definition where id = %s", (definition_id,)).fetchone()
    if row is None:
        raise NotFound("unknown definition")
    return row["xml"]

def definitions(conn, process_key: str | None = None) -> list[dict]:
    return conn.execute(
        """
        select d.id, d.process_key, d.version, d.name, d.state, d.problems, d.created_by, d.created_at,
               d.published_by, d.published_at,
               (select count(*) from work_process p where p.definition_id = d.id and p.state = 'running') as running
        from work_definition d where %(k)s::text is null or d.process_key = %(k)s
        order by d.process_key, d.version desc
        """, {"k": process_key}).fetchall()

def load_defaults(conn, directory: Path = DEFINITIONS_DIR) -> list[dict]:
    """Import and publish the starting processes (definitions/*.bpmn) when they differ from what is published,
    and their task forms (definitions/*.form) when they differ from the latest version."""
    for f in sorted(directory.glob("*.form")):
        schema = json.loads(f.read_text())
        if (current := form(conn, schema["id"])) is None or current["schema"] != schema:
            import_form(conn, schema, SYSTEM)
    out = []
    files = sorted(directory.glob("*.bpmn"))
    # Called processes first, so call activities find them published.
    files.sort(key=lambda f: "callActivity" in f.read_text())
    for f in files:
        xml = f.read_text()
        keys = list(bpmn.parse(xml))
        current = conn.execute("select count(*) as n from work_definition where state = 'published' and "
                               "process_key = any(%s) and xml = %s", (keys, xml)).fetchone()["n"]
        if current == len(keys):
            continue
        for row in import_bpmn(conn, xml, SYSTEM):
            out.append(publish(conn, row["id"], SYSTEM) | {"process_key": row["process_key"]})
    return out

@_op
def import_form(conn, schema: dict, by: str) -> dict:
    """A form-js schema as the next version of the form named by its `id` (the key a userTask's
    fuzer:form names). Bulk actions sit beside it as `fuzer.bulk`: [{label, data, reason_key}]."""
    key = schema.get("id")
    if not key or not isinstance(schema.get("components"), list):
        raise WorkError("a form needs an id and a components list (a form-js schema)")
    row = conn.execute(
        "insert into work_form (key, version, schema, created_by) values "
        "(%s, coalesce((select max(version) from work_form where key = %s), 0) + 1, %s, %s) returning key, version",
        (key, key, Jsonb(schema), by)).fetchone()
    _event(conn, "form_imported", by, data=dict(row))
    return row

def form(conn, key: str) -> dict | None:
    return conn.execute("select key, version, schema, created_by, created_at from work_form where key = %s "
                        "order by version desc limit 1", (key,)).fetchone()

def forms(conn) -> list[dict]:
    return conn.execute("select distinct on (key) key, version, created_by, created_at from work_form "
                        "order by key, version desc").fetchall()

@_op
def set_person(conn, name: str, roles: list[str], by: str = SYSTEM) -> dict:
    if not name.strip() or is_agent(name):
        raise WorkError("a person needs a name (agents aren't people)")
    unknown = set(roles) - bpmn.PERSON_ROLES
    if unknown:
        raise WorkError(f"unknown roles: {', '.join(sorted(unknown))}")
    conn.execute("insert into work_person (name, roles) values (%s, %s) on conflict (name) do update "
                 "set roles = excluded.roles", (name, roles))
    _event(conn, "roles_set", by, data={"person": name, "roles": roles})
    return {"name": name, "roles": roles}

def roles_of(conn, name: str) -> list[str]:
    # Owners span all present and future human routing roles, never autonomous-agent identities.
    if name.startswith("user:"):
        account = conn.execute("select owner, enabled from access_account where id = %s", (name[5:],)).fetchone()
        if account and not account["enabled"]:
            return []
        if account and account["owner"]:
            return [r["name"] for r in conn.execute("select name from work_role where kind = 'person' order by name")]
    row = conn.execute("select roles from work_person where name = %s", (name,)).fetchone()
    return row["roles"] if row else []

def people(conn) -> list[dict]:
    rows = conn.execute("select p.name, p.roles, p.away_until, p.delegate, p.note, coalesce(a.owner, false) as owner, "
                        "a.display_name, a.enabled as account_enabled "
                        "from work_person p left join access_account a on p.name = 'user:' || a.id order by p.name").fetchall()
    for row in rows:
        row["assigned_roles"] = list(row["roles"])
        if row["name"].startswith("user:"):
            row["roles"] = roles_of(conn, row["name"])
    return rows

def role_summary(conn) -> list[dict]:
    """Each person role (#202): the published processes with steps for it, who holds it and how many
    unclaimed steps and items wait in its queue."""
    used: dict[str, set[str]] = {}
    for r in conn.execute("select id, process_key from work_definition where state = 'published'").fetchall():
        for n in _definition(conn, r["id"]).nodes.values():
            if n.type == "userTask" and n.role:
                used.setdefault(n.role, set()).add(r["process_key"])
    waiting = {r["role"]: r["n"] for r in conn.execute(
        """
        select role, count(*) as n from (
            select role from work_token where state = 'waiting' and kind = any(%(kinds)s) and assignee is null
            union all
            select i.role from work_item i where i.closed_at is null and i.assignee is null
              and not exists (select 1 from work_process p where p.item_id = i.id)
        ) q where role is not null group by role
        """, {"kinds": ["user", "fix"]})}
    holders: dict[str, list[str]] = {}
    for p in people(conn):
        for role in p["roles"]:
            holders.setdefault(role, []).append(p["name"])
    return [{"role": role, "processes": sorted(used.get(role, ())), "people": holders.get(role, []),
             "waiting": waiting.get(role, 0)} for role in sorted(bpmn.PERSON_ROLES)]
