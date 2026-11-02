"""The work engine: runs BPMN processes (the subset in bpmn.py) with all running state in Postgres.

A process is a `work_process` row on a `work_item` (the thing people and agents see). Each step it
reaches is a `work_token` row: ready steps are moved on straight away; user, agent, timer, message,
join and call steps wait. Rows stay when done, so the tokens are also the path taken. Everything that
happens is a `work_event`.

Every public operation is one transaction that locks the process row first (`for update`), so two
people, a worker and the timer tick can't move the same process at once. Agent steps are claimed by
workers with `for update skip locked` and a lease (worker.py); timers and SLAs are fired by `tick`.

Guardrails: a `requiresHuman` step can't be completed, claimed or handed to an agent; an agent step
that keeps failing goes to a person; a step that can't go on (a condition that fails to evaluate, no
branch matching) becomes a `fix` task for the process owner, never a silent stop.

Actors are a person's name, `agent:<role>` or `system`.
"""

import json
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from psycopg.types.json import Jsonb

from . import bpmn

SYSTEM = "system"
ADMIN_ROLE = "workflow_admin"
DEFAULT_OWNER = "engineer"
DEFAULT_SLA = timedelta(days=1)
DUE_SOON = timedelta(hours=24)
DEFINITIONS_DIR = Path(__file__).with_name("definitions")
TASK_KINDS = ("user", "agent", "fix")


class WorkError(Exception):
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
    return datetime.now(timezone.utc)


def _op(fn):
    """One transaction per public operation: commit on success, roll back on any error."""
    @wraps(fn)
    def run(conn, *args, **kwargs):
        try:
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
    return {"person": sorted(bpmn.PERSON_ROLES), "agent": sorted(bpmn.AGENT_ROLES)}


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


# --- task forms ---------------------------------------------------------------------------------

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


# --- people -------------------------------------------------------------------------------------

@_op
def set_person(conn, name: str, roles: list[str], by: str = SYSTEM) -> dict:
    unknown = set(roles) - bpmn.PERSON_ROLES
    if unknown:
        raise WorkError(f"unknown roles: {', '.join(sorted(unknown))}")
    conn.execute("insert into work_person (name, roles) values (%s, %s) on conflict (name) do update "
                 "set roles = excluded.roles", (name, roles))
    _event(conn, "roles_set", by, data={"person": name, "roles": roles})
    return {"name": name, "roles": roles}


def roles_of(conn, name: str) -> list[str]:
    row = conn.execute("select roles from work_person where name = %s", (name,)).fetchone()
    return row["roles"] if row else []


def people(conn) -> list[dict]:
    return conn.execute("select name, roles from work_person order by name").fetchall()


# --- running ------------------------------------------------------------------------------------

class _Run:
    """One operation: the clock, who is acting, and the processes it has locked (data written at the end)."""

    def __init__(self, conn, actor: str, now: datetime | None = None):
        self.conn, self.actor, self.now = conn, actor, now or utcnow()
        self.procs: dict[int, dict] = {}
        self.items: set[int] = set()

    def proc(self, process_id: int, skip_locked: bool = False) -> dict | None:
        if process_id not in self.procs:
            row = self.conn.execute(f"select * from work_process where id = %s for update{' skip locked' if skip_locked else ''}",
                                    (process_id,)).fetchone()
            if row is None:
                if skip_locked:
                    return None
                raise NotFound("unknown process")
            self.procs[process_id] = row
            self.items.add(row["item_id"])
        return self.procs[process_id]

    def defn(self, proc: dict) -> bpmn.Process:
        return _definition(self.conn, proc["definition_id"])

    def event(self, kind: str, proc: dict | None = None, token: dict | None = None, actor: str | None = None,
              item_id=None, **data):
        _event(self.conn, kind, actor or self.actor, item_id or (proc or {}).get("item_id"),
               (proc or {}).get("id"), (token or {}).get("id"), (proc or {}).get("definition_id"),
               (token or {}).get("element"), data, self.now)

    def flush(self):
        for p in self.procs.values():
            self.conn.execute("update work_process set data = %s, state = %s, outcome = %s, ended_at = %s where id = %s",
                              (Jsonb(p["data"]), p["state"], p["outcome"], p["ended_at"], p["id"]))
        for item_id in self.items:
            _sync_item(self.conn, item_id, self.now)


def _scope(run: _Run, proc: dict) -> dict:
    return {**proc["data"], "thresholds": run.defn(proc).thresholds}


def _token(run: _Run, proc: dict, node: bpmn.Node, state: str, kind: str, **cols) -> dict:
    cols = {"process_id": proc["id"], "item_id": proc["item_id"], "element": node.id, "name": node.name,
            "kind": kind, "state": state, "created_at": run.now, **cols}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return run.conn.execute(f"insert into work_token ({names}) values ({marks}) returning *", list(cols.values())).fetchone()


def _update(run: _Run, token: dict, **cols) -> dict:
    sets = ", ".join(f"{k} = %s" for k in cols)
    return run.conn.execute(f"update work_token set {sets} where id = %s returning *", [*cols.values(), token["id"]]).fetchone()


def _finish(run: _Run, token: dict, state: str = "done", by: str | None = None) -> dict:
    return _update(run, token, state=state, finished_at=run.now, finished_by=by, lease_until=None)


def _start(run: _Run, definition: dict, data: dict, item_id: int, parent_token: int | None = None,
           correlation_key: str | None = None) -> dict:
    p = _definition(run.conn, definition["id"])
    proc = run.conn.execute(
        "insert into work_process (definition_id, process_key, item_id, parent_token, correlation_key, data, "
        "started_by, started_at) values (%s, %s, %s, %s, %s, %s, %s, %s) returning *",
        (definition["id"], definition["process_key"], item_id, parent_token, correlation_key, Jsonb(data),
         run.actor, run.now)).fetchone()
    run.procs[proc["id"]] = proc
    run.items.add(item_id)
    run.event("started", proc, process_key=definition["process_key"], version=definition["version"],
              correlation_key=correlation_key, parent_token=parent_token)
    _token(run, proc, p.start, "ready", "auto")
    _advance(run, proc)
    return proc


def _advance(run: _Run, proc: dict) -> None:
    """Move every ready step on until all are waiting or the process has ended."""
    while proc["state"] == "running":
        t = run.conn.execute("select * from work_token where process_id = %s and state = 'ready' order by id limit 1",
                             (proc["id"],)).fetchone()
        if t is None:
            return
        try:
            _step(run, proc, t)
        except (bpmn.ExpressionError, ValueError) as e:
            _fail(run, proc, t, str(e))


def _step(run: _Run, proc: dict, t: dict) -> None:
    p = run.defn(proc)
    n = p.nodes[t["element"]]
    match n.type:
        case "startEvent":
            _leave(run, proc, t, n.outgoing)
        case "endEvent":
            _end(run, proc, t, n)
        case "exclusiveGateway":
            _leave(run, proc, t, [_choose(p, n, _scope(run, proc))])
        case "parallelGateway":
            _parallel(run, proc, t, n)
        case "userTask":
            sla = bpmn.parse_duration(n.attrs.get("sla", ""))
            assignee = n.attrs.get("assignee")
            if assignee and assignee.startswith("="):
                assignee = bpmn.evaluate(assignee[1:], _scope(run, proc))
            t = _update(run, t, state="waiting", kind="user", role=n.role, assignee=assignee or None,
                        requires_human=n.requires_human, due_at=run.now + sla if sla else None)
            _boundaries(run, proc, t, n)
            run.event("step_started", proc, t, step=n.label, role=n.role, assignee=t["assignee"],
                      due=t["due_at"].isoformat() if t["due_at"] else None)
        case "serviceTask":
            t = _update(run, t, state="waiting", kind="agent", role=n.agent, run_after=run.now)
            _boundaries(run, proc, t, n)
            run.event("step_started", proc, t, step=n.label, agent=n.agent)
        case "intermediateCatchEvent":
            if n.event == "timer":
                at, _, _ = bpmn.due(*n.timer, _scope(run, proc), run.now)
                _update(run, t, state="waiting", kind="timer", due_at=at)
            else:
                _update(run, t, state="waiting", kind="message", message_name=n.message,
                        correlation_key=proc["correlation_key"])
        case "callActivity":
            t = _update(run, t, state="waiting", kind="call")
            _boundaries(run, proc, t, n)
            child_def = _published(run.conn, n.called)
            run.event("step_started", proc, t, step=n.label, calls=n.called)
            _start(run, child_def, dict(proc["data"]), proc["item_id"], t["id"], proc["correlation_key"])


def _choose(p: bpmn.Process, n: bpmn.Node, data: dict) -> str:
    if len(n.outgoing) == 1 and not p.flows[n.outgoing[0]].condition:
        return n.outgoing[0]
    for fid in n.outgoing:
        f = p.flows[fid]
        if fid != n.default and f.condition and bpmn.evaluate(f.condition, data):
            return fid
    if n.default:
        return n.default
    raise bpmn.ExpressionError(f"no branch of gateway '{n.label}' matched and it has no default")


def _parallel(run: _Run, proc: dict, t: dict, n: bpmn.Node) -> None:
    p = run.defn(proc)
    if len(n.incoming) > 1:
        _update(run, t, state="waiting", kind="join")
        arrived = run.conn.execute("select * from work_token where process_id = %s and element = %s and state = 'waiting' "
                                   "and kind = 'join' order by id", (proc["id"], n.id)).fetchall()
        first = {}
        for a in arrived:
            first.setdefault(a["flow"], a)
        if not set(n.incoming) <= set(first):
            return
        for a in first.values():
            _finish(run, a)
    else:
        _finish(run, t)
    for fid in n.outgoing:
        _token(run, proc, p.nodes[p.flows[fid].target], "ready", "auto", flow=fid)


def _leave(run: _Run, proc: dict, t: dict, flows: list[str], by: str | None = None) -> None:
    p = run.defn(proc)
    _finish(run, t, by=by)
    for fid in flows:
        _token(run, proc, p.nodes[p.flows[fid].target], "ready", "auto", flow=fid)


def _boundaries(run: _Run, proc: dict, t: dict, n: bpmn.Node) -> None:
    p = run.defn(proc)
    for bid in p.boundaries.get(n.id, []):
        b = p.nodes[bid]
        if b.event == "timer":
            at, cycles, _ = bpmn.due(*b.timer, _scope(run, proc), run.now)
            _token(run, proc, b, "waiting", "timer", due_at=at, cycles_left=cycles, attached_to=t["id"])
        else:
            _token(run, proc, b, "waiting", "message", message_name=b.message, attached_to=t["id"],
                   correlation_key=proc["correlation_key"])


def _cancel_boundaries(run: _Run, t: dict) -> None:
    run.conn.execute("update work_token set state = 'cancelled', finished_at = %s where attached_to = %s and state = 'waiting'",
                     (run.now, t["id"]))


def _cancel_activity(run: _Run, proc: dict, t: dict, why: str) -> None:
    """Interrupt an activity: the step, its boundary events and, for a call activity, the called process."""
    _finish(run, t, state="cancelled")
    _cancel_boundaries(run, t)
    for child in run.conn.execute("select id from work_process where parent_token = %s and state = 'running'", (t["id"],)):
        _cancel_process(run, run.proc(child["id"]), why)
    if t["kind"] in TASK_KINDS:
        run.event("step_cancelled", proc, t, why=why)


def _cancel_process(run: _Run, proc: dict, why: str, state: str = "cancelled") -> None:
    for t in run.conn.execute("select * from work_token where process_id = %s and state in ('ready', 'waiting')",
                              (proc["id"],)).fetchall():
        if t["kind"] == "call":
            _cancel_activity(run, proc, t, why)
        else:
            _finish(run, t, state="cancelled")
    proc.update(state=state, ended_at=run.now)


def _fail(run: _Run, proc: dict, t: dict, error: str) -> None:
    """A step that can't go on becomes a task for the process owner, who can fix the data and retry it."""
    owner = run.defn(proc).attrs.get("ownerRole") or DEFAULT_OWNER
    _cancel_boundaries(run, t)
    t = _update(run, t, state="waiting", kind="fix", role=owner, assignee=None, error=error, due_at=run.now + DEFAULT_SLA)
    run.event("failed", proc, t, error=error, role=owner)


def _end(run: _Run, proc: dict, t: dict, n: bpmn.Node) -> None:
    _update(run, t, state="done", kind="end", finished_at=run.now)
    if n.event == "terminate":
        _cancel_process(run, proc, f"terminated at '{n.label}'", state="terminated")
        _process_ended(run, proc, n.label, "terminated")
        return
    left = run.conn.execute("select count(*) as n from work_token where process_id = %s and state in ('ready', 'waiting')",
                            (proc["id"],)).fetchone()["n"]
    if left == 0:
        _process_ended(run, proc, n.label, "completed")


def _process_ended(run: _Run, proc: dict, outcome: str, state: str) -> None:
    proc.update(state=state, outcome=outcome, ended_at=run.now)
    run.event("completed_process", proc, outcome=outcome, state=state)
    if proc["parent_token"]:
        parent_token = run.conn.execute("select * from work_token where id = %s", (proc["parent_token"],)).fetchone()
        parent = run.proc(parent_token["process_id"])
        if parent_token["state"] != "waiting":
            return
        parent["data"].update(proc["data"])
        _cancel_boundaries(run, parent_token)
        _leave(run, parent, parent_token, run.defn(parent).nodes[parent_token["element"]].outgoing)
        _advance(run, parent)
    else:
        run.conn.execute("update work_item set state = 'done', outcome = %s, closed_at = %s, updated_at = %s "
                         "where id = %s and closed_at is null", (outcome, run.now, run.now, proc["item_id"]))
        run.event("state_changed", proc, state="done", outcome=outcome)


def _sync_item(conn, item_id: int, now: datetime) -> None:
    """The item's step, queue and due date from its open tasks (any process on it)."""
    conn.execute(
        """
        update work_item i set step = s.step, assignee = s.assignee, role = s.role, due = s.due, updated_at = %(now)s,
               priority = coalesce(s.priority, i.priority)
        from (
            select string_agg(coalesce(t.name, t.element), ', ' order by t.id) as step,
                   case when count(*) = 1 then max(t.assignee) end as assignee,
                   case when count(distinct t.role) = 1 then max(t.role) end as role,
                   min(t.due_at) as due,
                   (select max((p.data->>'priority')::double precision) from work_process p
                    where p.item_id = %(id)s and jsonb_typeof(p.data->'priority') = 'number') as priority
            from work_token t where t.item_id = %(id)s and t.state = 'waiting' and t.kind = any(%(kinds)s)
        ) s
        where i.id = %(id)s and exists (select 1 from work_process p where p.item_id = i.id)
        """, {"id": item_id, "now": now, "kinds": list(TASK_KINDS)})


def _correlation(p: bpmn.Process, data: dict) -> str | None:
    if expr := p.attrs.get("correlationKey"):
        try:
            value = bpmn.evaluate(expr, data)
        except bpmn.ExpressionError as e:
            raise WorkError(f"can't work out the correlation key: {e}") from e
        return None if value is None else str(value)
    return None


def _new_item(run: _Run, p: bpmn.Process, data: dict, title, subjects, priority) -> int:
    item = run.conn.execute(
        "insert into work_item (type, title, subjects, priority, created_by, source, payload, created_at, updated_at) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (p.attrs.get("workItemType") or p.key, title or data.get("title") or p.name or p.key,
         list(subjects or data.get("subjects") or []), priority if priority is not None else data.get("priority"),
         run.actor, data.get("source") or run.actor, Jsonb({"start": data}), run.now, run.now)).fetchone()["id"]
    run.event("created", item_id=item, type=p.attrs.get("workItemType") or p.key)
    return item


def _start_new(run: _Run, process_key: str, data: dict, title, subjects, priority, correlation_key) -> dict:
    d = _published(run.conn, process_key)
    p = _definition(run.conn, d["id"])
    key = correlation_key if correlation_key is not None else _correlation(p, data)
    item_id = _new_item(run, p, data, title, subjects, priority)
    proc = _start(run, d, data, item_id, correlation_key=key)
    return {"process_id": proc["id"], "item_id": item_id, "state": proc["state"], "correlation_key": key,
            "started": True}


@_op
def start(conn, process_key: str, data: dict | None = None, by: str = SYSTEM, title: str | None = None,
          subjects: list[str] | None = None, priority: float | None = None, correlation_key: str | None = None,
          now: datetime | None = None) -> dict:
    """Start the published version of a process on a new work item."""
    run = _Run(conn, by, now)
    out = _start_new(run, process_key, dict(data or {}), title, subjects, priority, correlation_key)
    run.flush()
    return out


@_op
def start_or_correlate(conn, process_key: str, data: dict, by: str = SYSTEM, message: str = "evidence",
                       title: str | None = None, subjects: list[str] | None = None, priority: float | None = None,
                       now: datetime | None = None) -> dict:
    """What the SRE calls per grouped upset: when a process for the same correlation key (incident id)
    is already open, the data goes to it as a message (new evidence); otherwise a new one starts."""
    run = _Run(conn, by, now)
    d = _published(conn, process_key)
    key = _correlation(_definition(conn, d["id"]), data)
    if key is None:
        raise WorkError(f"process '{process_key}' has no fuzer:correlationKey, or the data gives no value for it")
    conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"work:{process_key}:{key}",))
    open_proc = conn.execute("select id, item_id from work_process where process_key = %s and correlation_key = %s "
                             "and state = 'running' and parent_token is null order by id limit 1", (process_key, key)).fetchone()
    if open_proc:
        sent = _send(run, message, key, data, process_key)
        run.flush()
        return {"process_id": open_proc["id"], "item_id": open_proc["item_id"], "correlation_key": key,
                "started": False, **sent}
    out = _start_new(run, process_key, dict(data), title, subjects, priority, key)
    run.flush()
    return out


def _locked_task(run: _Run, token_id: int) -> tuple[dict, dict]:
    t = run.conn.execute("select process_id from work_token where id = %s", (token_id,)).fetchone()
    if t is None:
        raise NotFound("unknown task")
    proc = run.proc(t["process_id"])
    return proc, run.conn.execute("select * from work_token where id = %s", (token_id,)).fetchone()


def _waiting_task(t: dict) -> None:
    if t["state"] != "waiting" or t["kind"] not in TASK_KINDS:
        raise Conflict(f"step '{t['name'] or t['element']}' isn't waiting for anyone (it is {t['state']})")


@_op
def complete(conn, token_id: int, by: str, data: dict | None = None, worker: str | None = None,
             now: datetime | None = None) -> dict:
    """Complete a person's or agent's step with its form data (merged into the process data)."""
    run = _Run(conn, by, now)
    proc, t = _locked_task(run, token_id)
    _waiting_task(t)
    if t["requires_human"] and is_agent(by):
        raise Forbidden(f"step '{t['name'] or t['element']}' needs a person; {by} can't complete it")
    if t["kind"] in ("user", "fix") and t["assignee"] and t["assignee"] != by:
        raise Conflict(f"step '{t['name'] or t['element']}' is claimed by {t['assignee']}")
    if t["kind"] == "agent":
        if is_agent(by) and by != f"agent:{t['role']}":
            raise Forbidden(f"step '{t['name'] or t['element']}' is for agent:{t['role']}, not {by}")
        live = t["claimed_by"] and t["lease_until"] and t["lease_until"] > run.now
        if worker is not None and t["claimed_by"] != worker:
            raise Conflict(f"worker {worker} no longer holds step {token_id} (claimed by {t['claimed_by']})")
        if worker is None and live:
            raise Conflict(f"an agent is working on step '{t['name'] or t['element']}' ({t['claimed_by']})")
    data = dict(data or {})
    proc["data"].update(data)
    kind = "agent_result" if t["kind"] == "agent" and is_agent(by) else "completed"
    run.event(kind, proc, t, step=t["name"] or t["element"], data=data, decision=data.get("decision"))
    if t["kind"] == "fix":
        _update(run, t, state="ready", kind="auto", error=None, role=None, due_at=None)
    else:
        _cancel_boundaries(run, t)
        _leave(run, proc, t, run.defn(proc).nodes[t["element"]].outgoing, by=by)
    _advance(run, proc)
    run.flush()
    return {"task_id": token_id, "process_id": proc["id"], "item_id": proc["item_id"], "process_state": proc["state"]}


@_op
def claim(conn, token_id: int, by: str, now: datetime | None = None) -> dict:
    run = _Run(conn, by, now)
    proc, t = _locked_task(run, token_id)
    _waiting_task(t)
    if t["kind"] == "agent":
        raise Conflict("agent steps are claimed by workers; hand it off to take it over")
    if t["requires_human"] and is_agent(by):
        raise Forbidden(f"step '{t['name'] or t['element']}' needs a person")
    if t["assignee"] and t["assignee"] != by:
        raise Conflict(f"already claimed by {t['assignee']}")
    _update(run, t, assignee=by)
    run.event("claimed", proc, t)
    run.flush()
    return {"task_id": token_id, "assignee": by}


@_op
def release(conn, token_id: int, by: str, now: datetime | None = None) -> dict:
    run = _Run(conn, by, now)
    proc, t = _locked_task(run, token_id)
    _waiting_task(t)
    if t["assignee"] != by:
        raise Conflict(f"only {t['assignee'] or 'whoever claims it'} can release it")
    _update(run, t, assignee=None)
    run.event("released", proc, t)
    run.flush()
    return {"task_id": token_id, "assignee": None}


@_op
def hand_off(conn, token_id: int, by: str, to: str, reason: str | None = None, now: datetime | None = None) -> dict:
    """Give a step to a person (`alice`), a role's queue (`role:ops_lead`) or an agent (`agent:troubleshooter`)."""
    run = _Run(conn, by, now)
    proc, t = _locked_task(run, token_id)
    _waiting_task(t)
    before = t["assignee"] or (f"agent:{t['role']}" if t["kind"] == "agent" else f"role:{t['role']}")
    if t["kind"] == "agent" and t["claimed_by"] and t["lease_until"] and t["lease_until"] > run.now:
        raise Conflict(f"an agent is working on it ({t['claimed_by']})")
    n = run.defn(proc).nodes[t["element"]]
    if to.startswith("agent:"):
        role = to.split(":", 1)[1]
        if t["requires_human"]:
            raise Forbidden(f"step '{n.label}' needs a person; it can't go to an agent")
        if role not in bpmn.AGENT_ROLES:
            raise WorkError(f"unknown agent '{role}'")
        if t["kind"] == "fix":
            raise WorkError("a failed step needs a person to fix it")
        _update(run, t, kind="agent", role=role, assignee=None, run_after=run.now, attempts=0, claimed_by=None,
                lease_until=None, error=None)
    else:
        role, person = (to.split(":", 1)[1], None) if to.startswith("role:") else (None, to)
        if role and role not in bpmn.PERSON_ROLES:
            raise WorkError(f"unknown role '{role}'")
        cols = {"assignee": person, "claimed_by": None, "lease_until": None}
        if role:
            cols["role"] = role
        if t["kind"] == "agent":
            cols |= {"kind": "user", "role": role or n.attrs.get("fallbackRole") or _owner(run, proc),
                     "due_at": t["due_at"] or run.now + DEFAULT_SLA}
        _update(run, t, **cols)
    run.event("handed_off", proc, t, **{"from": before, "to": to, "reason": reason})
    run.flush()
    return {"task_id": token_id, "to": to}


def _owner(run: _Run, proc: dict) -> str:
    return run.defn(proc).attrs.get("ownerRole") or DEFAULT_OWNER


def agent_failed(conn, token_id: int, worker: str, error: str, now: datetime | None = None,
                 max_attempts: int = 3, backoff: timedelta = timedelta(seconds=30)) -> dict:
    """An agent step raised: retry later with backoff, or after `max_attempts` give it to a person."""
    return _agent_failed(conn, token_id, worker, error, now, max_attempts, backoff)


@_op
def _agent_failed(conn, token_id, worker, error, now, max_attempts, backoff) -> dict:
    run = _Run(conn, f"agent:{worker}", now)
    proc, t = _locked_task(run, token_id)
    if t["state"] != "waiting" or t["kind"] != "agent" or t["claimed_by"] != worker:
        return {"task_id": token_id, "ignored": True}
    actor = f"agent:{t['role']}"
    if t["attempts"] >= max_attempts:
        n = run.defn(proc).nodes[t["element"]]
        role = n.attrs.get("fallbackRole") or _owner(run, proc)
        _update(run, t, kind="user", role=role, assignee=None, claimed_by=None, lease_until=None, error=error,
                due_at=run.now + DEFAULT_SLA)
        run.event("handed_off", proc, t, actor=actor, **{"from": actor, "to": f"role:{role}",
                  "reason": f"agent {t['role']} failed {t['attempts']} times; last error: {error}"})
        out = {"task_id": token_id, "handed_to": role}
    else:
        retry = run.now + backoff * 2 ** (t["attempts"] - 1)
        _update(run, t, claimed_by=None, lease_until=None, run_after=retry, error=error)
        run.event("agent_failed", proc, t, actor=actor, error=error, attempt=t["attempts"], retry_at=retry.isoformat())
        out = {"task_id": token_id, "retry_at": retry}
    run.flush()
    return out


def _send(run: _Run, name: str, key: str, payload: dict, process_key: str | None = None) -> dict:
    waiting = run.conn.execute(
        """
        select t.id, t.process_id from work_token t join work_process p on p.id = t.process_id
        where t.state = 'waiting' and t.kind = 'message' and t.message_name = %s and t.correlation_key = %s
          and (%s::text is null or p.process_key = %s or p.parent_token is not null)
        order by t.id
        """, (name, key, process_key, process_key)).fetchall()
    delivered = []
    for w in waiting:
        if w["process_id"] in delivered:
            continue
        proc = run.proc(w["process_id"])
        t = run.conn.execute("select * from work_token where id = %s", (w["id"],)).fetchone()
        if t["state"] != "waiting" or proc["state"] != "running":
            continue
        proc["data"].update(payload)
        run.event("message", proc, t, name=name, correlation_key=key, payload=payload, delivered=True)
        _fire(run, proc, t)
        delivered.append(proc["id"])
    recorded = []
    if not delivered:  # nothing waiting for it: keep it on the open process as evidence
        for r in run.conn.execute("select id from work_process where correlation_key = %s and state = 'running' and "
                                  "parent_token is null and (%s::text is null or process_key = %s) order by id",
                                  (key, process_key, process_key)).fetchall():
            proc = run.proc(r["id"])
            proc["data"].setdefault("messages", []).append({"name": name, "payload": payload, "by": run.actor,
                                                           "at": run.now.isoformat()})
            run.event("message", proc, name=name, correlation_key=key, payload=payload, delivered=False)
            recorded.append(proc["id"])
    return {"delivered": delivered, "recorded": recorded}


@_op
def send_message(conn, name: str, correlation_key: str, payload: dict | None = None, by: str = SYSTEM,
                 process_key: str | None = None, now: datetime | None = None) -> dict:
    """Send a message (new evidence, a SAP notification raised, a comment) to the open process with this
    correlation key. A process waiting for it moves on; otherwise it is kept on the process as evidence."""
    run = _Run(conn, by, now)
    out = _send(run, name, str(correlation_key), dict(payload or {}), process_key)
    run.flush()
    return out


def _fire(run: _Run, proc: dict, t: dict) -> None:
    """A timer passed or a message arrived for a waiting event."""
    p = run.defn(proc)
    n = p.nodes[t["element"]]
    if n.type == "intermediateCatchEvent":
        _leave(run, proc, t, n.outgoing)
    else:
        activity = run.conn.execute("select * from work_token where id = %s", (t["attached_to"],)).fetchone()
        if n.cancel:
            _cancel_activity(run, proc, activity, f"boundary event '{n.label}'")
            _leave(run, proc, t, n.outgoing)
        else:
            for fid in n.outgoing:
                _token(run, proc, p.nodes[p.flows[fid].target], "ready", "auto", flow=fid)
            # A message boundary fires once, as in SpiffWorkflow; later messages are kept as evidence.
            if n.event == "timer" and t["cycles_left"]:
                _, _, interval = bpmn.due(*n.timer, _scope(run, proc), run.now)
                _update(run, t, due_at=t["due_at"] + interval,
                        cycles_left=t["cycles_left"] - 1 if t["cycles_left"] > 0 else -1)
            else:
                _finish(run, t)
    _advance(run, proc)


def tick(conn, now: datetime | None = None, limit: int = 1000) -> dict:
    """Fire timers that are due and escalate person tasks past their SLA (run from a Dagster schedule).
    Each process is its own transaction; a process another tick holds is skipped."""
    now = now or utcnow()
    due = conn.execute("select id, process_id from work_token where state = 'waiting' and kind = 'timer' and due_at <= %s "
                       "order by due_at, id limit %s", (now, limit)).fetchall()
    late = conn.execute("select id, process_id from work_token where state = 'waiting' and kind = 'user' and due_at <= %s "
                        "and escalated_at is null order by due_at, id limit %s", (now, limit)).fetchall()
    conn.commit()
    fired = sum(_tick_timer(conn, r["process_id"], r["id"], now) for r in due)
    escalated = sum(_escalate(conn, r["process_id"], r["id"], now) for r in late)
    return {"timers": fired, "escalated": escalated}


@_op
def _tick_timer(conn, process_id: int, token_id: int, now: datetime) -> int:
    run = _Run(conn, SYSTEM, now)
    proc = run.proc(process_id, skip_locked=True)
    if proc is None or proc["state"] != "running":
        return 0
    t = conn.execute("select * from work_token where id = %s", (token_id,)).fetchone()
    if t["state"] != "waiting" or t["due_at"] > now:
        return 0
    run.event("timer_fired", proc, t, due=t["due_at"].isoformat())
    _fire(run, proc, t)
    run.flush()
    return 1


@_op
def _escalate(conn, process_id: int, token_id: int, now: datetime) -> int:
    run = _Run(conn, SYSTEM, now)
    proc = run.proc(process_id, skip_locked=True)
    if proc is None:
        return 0
    t = conn.execute("select * from work_token where id = %s", (token_id,)).fetchone()
    if t["state"] != "waiting" or t["kind"] != "user" or t["escalated_at"] or t["due_at"] > now:
        return 0
    n = run.defn(proc).nodes[t["element"]]
    holder = t["assignee"] or f"the {t['role']} queue"
    to = n.attrs.get("escalateTo")
    why = (f"SLA {n.attrs.get('sla', '')} on '{n.label}' passed at {t['due_at']:%Y-%m-%d %H:%M} UTC "
           f"while it was with {holder}") + (f"; moved to the {to} queue" if to else "")
    cols = {"escalated_at": now}
    if to:
        cols |= {"role": to, "assignee": None}
    _update(run, t, **cols)
    run.event("escalated", proc, t, why=why, **{"from": holder, "to": f"role:{to}" if to else holder,
                                                 "due": t["due_at"].isoformat()})
    run.flush()
    return 1


@_op
def migrate(conn, process_id: int, definition_id: int, by: str, mapping: dict[str, str] | None = None,
            now: datetime | None = None) -> dict:
    """Move one running process to another published version of its definition. Each open step must exist
    in the new version, under its own id or the one `mapping` gives it."""
    run = _Run(conn, by, now)
    proc = run.proc(process_id)
    if proc["state"] != "running":
        raise Conflict("only running processes can move to a new version")
    new = conn.execute("select * from work_definition where id = %s", (definition_id,)).fetchone()
    if new is None or new["process_key"] != proc["process_key"] or new["state"] not in ("published", "superseded"):
        raise WorkError(f"definition {definition_id} isn't a published version of '{proc['process_key']}'")
    old_p, new_p = run.defn(proc), _definition(conn, definition_id)
    mapping = mapping or {}
    open_tokens = conn.execute("select * from work_token where process_id = %s and state in ('ready', 'waiting')",
                               (process_id,)).fetchall()
    for t in open_tokens:
        el = mapping.get(t["element"], t["element"])
        if el not in new_p.nodes or new_p.nodes[el].type != old_p.nodes[t["element"]].type:
            raise Conflict(f"step '{t['element']}' has no matching {old_p.nodes[t['element']].type} in version {new['version']}")
    for t in open_tokens:
        el = mapping.get(t["element"], t["element"])
        if (el, new_p.nodes[el].name) != (t["element"], t["name"]):
            _update(run, t, element=el, name=new_p.nodes[el].name)
    old = proc["definition_id"]
    conn.execute("update work_process set definition_id = %s where id = %s", (definition_id, process_id))
    proc["definition_id"] = definition_id
    run.event("migrated", proc, from_definition=old, to_definition=definition_id, version=new["version"], mapping=mapping)
    run.flush()
    return {"process_id": process_id, "definition_id": definition_id, "version": new["version"]}


@_op
def cancel(conn, process_id: int, by: str, reason: str | None = None, now: datetime | None = None) -> dict:
    run = _Run(conn, by, now)
    proc = run.proc(process_id)
    if proc["state"] != "running":
        raise Conflict(f"process is {proc['state']}")
    _cancel_process(run, proc, reason or f"cancelled by {by}")
    run.event("cancelled", proc, reason=reason)
    if not proc["parent_token"]:
        conn.execute("update work_item set state = 'cancelled', closed_at = %s, updated_at = %s where id = %s",
                     (run.now, run.now, proc["item_id"]))
    run.flush()
    return {"process_id": process_id, "state": "cancelled"}


# --- items without a process --------------------------------------------------------------------

@_op
def create_item(conn, type: str, title: str, by: str, subjects: list[str] | None = None, priority: float | None = None,
                assignee: str | None = None, role: str | None = None, due: datetime | None = None,
                source: str | None = None, payload: dict | None = None) -> dict:
    row = conn.execute(
        "insert into work_item (type, title, subjects, priority, assignee, role, due, created_by, source, payload) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *",
        (type, title, list(subjects or []), priority, assignee, role, due, by, source or by, Jsonb(payload or {}))).fetchone()
    _event(conn, "created", by, item_id=row["id"], data={"type": type, "assignee": assignee, "role": role})
    return row


def _item(conn, item_id: int, lock: bool = False) -> dict:
    row = conn.execute(f"select * from work_item where id = %s{' for update' if lock else ''}", (item_id,)).fetchone()
    if row is None:
        raise NotFound("unknown work item")
    return row


def _has_process(conn, item_id: int) -> bool:
    return conn.execute("select exists (select 1 from work_process where item_id = %s and state = 'running') as x",
                        (item_id,)).fetchone()["x"]


@_op
def transition(conn, item_id: int, state: str, by: str, reason: str | None = None) -> dict:
    """Move an item that has no running process to another state (done and cancelled close it)."""
    item = _item(conn, item_id, lock=True)
    if _has_process(conn, item_id):
        raise Conflict("this item moves with its process: complete its open step instead")
    closed = state in ("done", "cancelled")
    conn.execute("update work_item set state = %s, closed_at = case when %s then now() end, updated_at = now() "
                 "where id = %s", (state, closed, item_id))
    _event(conn, "state_changed", by, item_id=item_id, data={"from": item["state"], "state": state, "reason": reason})
    return {"item_id": item_id, "state": state}


@_op
def assign_item(conn, item_id: int, by: str, to: str, reason: str | None = None) -> dict:
    """Hand off an item without a process (a person, `role:<role>` or `agent:<role>`)."""
    item = _item(conn, item_id, lock=True)
    if _has_process(conn, item_id):
        raise Conflict("this item moves with its process: hand off its open step instead")
    person, role = (None, to.split(":", 1)[1]) if ":" in to else (to, item["role"])
    conn.execute("update work_item set assignee = %s, role = %s, updated_at = now() where id = %s", (person, role, item_id))
    _event(conn, "handed_off", by, item_id=item_id,
           data={"from": item["assignee"] or item["role"], "to": to, "reason": reason})
    return {"item_id": item_id, "to": to}


@_op
def comment(conn, item_id: int, by: str, text: str, attachments: list[dict] | None = None) -> dict:
    _item(conn, item_id)
    _event(conn, "commented", by, item_id=item_id, data={"text": text, "attachments": attachments or []})
    return {"item_id": item_id}


# --- reading ------------------------------------------------------------------------------------

def list_items(conn, state: str | None = "open", type: str | None = None, assignee: str | None = None,
               roles: list[str] | None = None, subject: str | None = None, overdue: bool | None = None,
               q: str | None = None, limit: int = 50, offset: int = 0, now: datetime | None = None) -> dict:
    """Items with filters, ranked by priority, with the total and how many are overdue or due soon."""
    now = now or utcnow()
    where = ["(%(state)s::text is null or i.state = %(state)s)", "(%(type)s::text is null or i.type = %(type)s)",
             "(%(assignee)s::text is null or i.assignee = %(assignee)s)",
             "(%(roles)s::text[] is null or i.role = any(%(roles)s))",
             "(%(subject)s::text is null or i.subjects @> array[%(subject)s])",
             "(%(q)s::text is null or i.title ilike '%%' || %(q)s || '%%')"]
    if overdue is not None:
        where.append("(i.due < %(now)s)" if overdue else "(i.due is null or i.due >= %(now)s)")
    args = {"state": state, "type": type, "assignee": assignee, "roles": roles, "subject": subject, "q": q,
            "now": now, "soon": now + DUE_SOON, "limit": limit, "offset": offset}
    cond = " and ".join(where)
    counts = conn.execute(
        f"select count(*) as total, count(*) filter (where i.due < %(now)s and i.closed_at is null) as overdue, "
        f"count(*) filter (where i.due >= %(now)s and i.due < %(soon)s and i.closed_at is null) as due_soon "
        f"from work_item i where {cond}", args).fetchone()
    rows = conn.execute(
        f"""
        select i.id, i.type, i.title, i.subjects, i.state, i.outcome, i.step, i.priority, i.assignee, i.role, i.due,
               i.created_by, i.source, i.created_at, i.updated_at, i.closed_at,
               i.due < %(now)s and i.closed_at is null as overdue
        from work_item i where {cond}
        order by i.priority desc nulls last, i.due nulls last, i.id limit %(limit)s offset %(offset)s
        """, args).fetchall()
    return {"items": rows, **counts}


_TASKS = """
    select t.id as task_id, t.element, t.name, t.kind, t.role, t.assignee, t.requires_human, t.due_at, t.escalated_at,
           t.error, t.created_at, t.due_at < %(now)s as overdue, t.claimed_by, t.lease_until,
           i.id as item_id, i.type, i.title, i.priority, i.subjects, p.id as process_id, p.process_key, p.definition_id
    from work_token t join work_process p on p.id = t.process_id join work_item i on i.id = t.item_id
    where t.state = 'waiting' and t.kind = any(%(kinds)s) and {who}
    order by i.priority desc nulls last, t.due_at nulls last, t.id limit %(limit)s
"""
_ITEMS = """
    select null::bigint as task_id, null as element, i.step as name, 'item' as kind, i.role, i.assignee,
           false as requires_human, i.due as due_at, null::timestamptz as escalated_at, null as error, i.created_at,
           i.due < %(now)s as overdue, null as claimed_by, null::timestamptz as lease_until, i.id as item_id, i.type, i.title, i.priority, i.subjects,
           null::bigint as process_id, null as process_key, null::bigint as definition_id
    from work_item i
    where i.closed_at is null and {who} and not exists (select 1 from work_process p where p.item_id = i.id)
    order by i.priority desc nulls last, i.due nulls last, i.id limit %(limit)s
"""


def my_work(conn, person: str, roles: list[str] | None = None, limit: int = 200, now: datetime | None = None) -> dict:
    """A person's open tasks ('mine': claimed by or assigned to them) and their roles' queue (unclaimed)."""
    now = now or utcnow()
    roles = roles if roles is not None else roles_of(conn, person)
    args = {"person": person, "roles": roles, "now": now, "limit": limit, "kinds": ["user", "fix"]}
    mine = conn.execute(_TASKS.format(who="t.assignee = %(person)s"), args).fetchall() + \
        conn.execute(_ITEMS.format(who="i.assignee = %(person)s"), args).fetchall()
    queue = conn.execute(_TASKS.format(who="t.assignee is null and t.role = any(%(roles)s)"), args).fetchall() + \
        conn.execute(_ITEMS.format(who="i.assignee is null and i.role = any(%(roles)s)"), args).fetchall()
    soon = now + DUE_SOON
    _bulk_labels(conn, mine + queue)

    def counts(rows):
        return {"total": len(rows), "overdue": sum(bool(r["overdue"]) for r in rows),
                "due_soon": sum(1 for r in rows if r["due_at"] and now <= r["due_at"] < soon)}
    return {"person": person, "roles": roles, "mine": mine, "queue": queue,
            "counts": {"mine": counts(mine), "queue": counts(queue)}}


def _bulk_labels(conn, rows: list[dict]) -> None:
    """Each person step's bulk actions (labels from its form), so the inbox knows which rows can be ticked."""
    labels: dict[tuple, list[str]] = {}
    for r in rows:
        key = (r["definition_id"], r["element"])
        if r["kind"] != "user" or r["requires_human"]:
            r["bulk"] = []
            continue
        if key not in labels:
            f = _definition(conn, r["definition_id"]).nodes[r["element"]].attrs.get("form")
            schema = (form(conn, f) or {}).get("schema") or {} if f else {}
            labels[key] = [b["label"] for b in schema.get("fuzer", {}).get("bulk", [])]
        r["bulk"] = labels[key]


def agent_work(conn, role: str, limit: int = 50, now: datetime | None = None) -> list[dict]:
    """Agent steps waiting for an agent role, claimed or not."""
    now = now or utcnow()
    return conn.execute(_TASKS.format(who="t.role = %(role)s"),
                        {"role": role, "now": now, "limit": limit, "kinds": ["agent"]}).fetchall()


def agent_inbox(conn, role: str, limit: int = 50, now: datetime | None = None) -> dict:
    """What an agent role has to do: its agent steps, person steps it claimed or was handed (not
    requiresHuman ones, which it can't take), and items without a process in its queue."""
    now = now or utcnow()
    args = {"person": f"agent:{role}", "roles": [role], "now": now, "limit": limit, "kinds": ["user"]}
    return {"agent": role, "steps": agent_work(conn, role, limit, now),
            "tasks": conn.execute(_TASKS.format(who="t.assignee = %(person)s"), args).fetchall(),
            "items": conn.execute(_ITEMS.format(who="(i.assignee = %(person)s or (i.assignee is null and "
                                                   "i.role = any(%(roles)s)))"), args).fetchall()}


def _form_of(conn, t: dict) -> dict | None:
    proc = conn.execute("select definition_id from work_process where id = %s", (t["process_id"],)).fetchone()
    key = _definition(conn, proc["definition_id"]).nodes[t["element"]].attrs.get("form")
    return (form(conn, key) or {"key": key, "version": None, "schema": None}) if key else None


def task(conn, token_id: int, now: datetime | None = None) -> dict:
    """One step as the inbox shows it: the step, its item, the process data (the form's starting values),
    the form-js form and its bulk actions, and the evidence so far: what agents found, messages that
    reached the process, comments, and the entities it is about."""
    now = now or utcnow()
    t = conn.execute(
        """
        select t.id as task_id, t.element, t.name, t.kind, t.state, t.role, t.assignee, t.requires_human, t.due_at,
               t.escalated_at, t.error, t.created_at, t.finished_at, t.finished_by, t.claimed_by, t.lease_until,
               t.attempts, t.due_at < %s as overdue, t.process_id, t.item_id, p.process_key, p.definition_id,
               d.version, p.data
        from work_token t join work_process p on p.id = t.process_id join work_definition d on d.id = p.definition_id
        where t.id = %s
        """, (now, token_id)).fetchone()
    if t is None:
        raise NotFound("unknown task")
    item = conn.execute("select id, type, title, subjects, state, outcome, step, priority, assignee, role, due, "
                        "created_by, source, created_at from work_item where id = %s", (t["item_id"],)).fetchone()
    f = _form_of(conn, {"process_id": t["process_id"], "element": t["element"]})
    events = conn.execute("select id, kind, actor, element, data, at from work_event where item_id = %s "
                          "and kind in ('agent_result', 'message', 'commented') order by id desc", (t["item_id"],)).fetchall()
    subjects = conn.execute(
        "select s.id as fuzer_id, e.kind, l.label from unnest(%s::text[]) with ordinality s(id, n) "
        "left join entity e on e.fuzer_id = s.id left join entity_label l on l.fuzer_id = s.id order by s.n",
        (item["subjects"],)).fetchall()
    return {
        "task": {k: v for k, v in t.items() if k != "data"}, "item": item, "data": t["data"],
        "form": f and {"key": f["key"], "version": f["version"],
                       "schema": f["schema"] and {k: v for k, v in f["schema"].items() if k != "fuzer"}},
        "bulk": ((f or {}).get("schema") or {}).get("fuzer", {}).get("bulk", []),
        "evidence": {"agents": [e for e in events if e["kind"] == "agent_result"],
                     "messages": [e for e in events if e["kind"] == "message"],
                     "comments": [e for e in events if e["kind"] == "commented"],
                     "subjects": subjects},
    }


def bulk_complete(conn, token_ids: list[int], by: str, action: str, reason: str | None = None,
                  now: datetime | None = None) -> dict:
    """Complete several person steps with one of their forms' bulk actions (closing false alarms, say).
    Only actions a step's form declares count, and never on a requiresHuman step: those stay one by one.
    Each step is its own transaction; the ones refused say why."""
    done, refused = [], []
    for tid in token_ids:
        try:
            t = conn.execute("select * from work_token where id = %s", (tid,)).fetchone()
            if t is None:
                raise NotFound("unknown task")
            conn.commit()
            if t["requires_human"]:
                raise Forbidden(f"step '{t['name'] or t['element']}' needs a decision of its own")
            f = _form_of(conn, t) if t["kind"] == "user" else None
            bulk = next((b for b in ((f or {}).get("schema") or {}).get("fuzer", {}).get("bulk", [])
                         if b["label"] == action), None)
            if bulk is None:
                raise WorkError(f"step '{t['name'] or t['element']}' has no bulk action '{action}'")
            data = dict(bulk["data"])
            if reason and bulk.get("reason_key"):
                data[bulk["reason_key"]] = reason
            complete(conn, tid, by, data, now=now)
            done.append(tid)
        except WorkError as e:
            conn.rollback()
            refused.append({"task_id": tid, "error": str(e)})
    return {"done": done, "refused": refused}


def item(conn, item_id: int) -> dict:
    """An item with its processes (version, state, path taken, open steps) and its full history."""
    row = _item(conn, item_id)
    procs = conn.execute(
        "select p.id, p.process_key, d.version, p.definition_id, p.parent_token, p.correlation_key, p.state, p.outcome, "
        "p.data, p.started_by, p.started_at, p.ended_at from work_process p join work_definition d on d.id = p.definition_id "
        "where p.item_id = %s order by p.id", (item_id,)).fetchall()
    steps = conn.execute(
        "select id, process_id, element, name, kind, state, flow, role, assignee, requires_human, due_at, escalated_at, "
        "attempts, error, created_at, finished_at, finished_by from work_token where item_id = %s order by id",
        (item_id,)).fetchall()
    for p in procs:
        mine = [s for s in steps if s["process_id"] == p["id"]]
        p["path"] = [s for s in mine if s["state"] == "done"]
        p["open"] = [s for s in mine if s["state"] in ("ready", "waiting")]
    events = conn.execute("select id, kind, actor, element, data, at, process_id, token_id from work_event "
                          "where item_id = %s order by id", (item_id,)).fetchall()
    return {**row, "processes": procs, "events": events}


def process(conn, process_id: int) -> dict:
    row = conn.execute("select item_id from work_process where id = %s", (process_id,)).fetchone()
    if row is None:
        raise NotFound("unknown process")
    detail = item(conn, row["item_id"])
    return next(p for p in detail["processes"] if p["id"] == process_id) | {"item_id": row["item_id"]}
