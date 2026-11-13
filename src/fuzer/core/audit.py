"""The project's audit record (R.5, D9 in docs/ot-security.md): every write, approval, permission change and sign-in
that names who did it, read for one project and exportable by an owner.

There is no second audit table. Each table that records a person or agent (a column named `by`, `actor` or
`<verb>_by`) is a source, paired with its time column (`at`, or `<verb>_at`), and read from the project's own schema;
the shared tables (access events, settings, the project row, cover) are filtered to the project. Columns are found
from the database catalog, so a new table that names an actor joins the record without code here. A column that is
not an audit fact is excluded below with its reason; a test fails when an actor column has neither a time column nor
an exclusion.

Each row says whether its source keeps every change (`history`) or only the latest: a decision column such as
`decided_by` is overwritten when someone decides again, so the record shows the last decision, not every one.
"""

import csv
import io
from dataclasses import dataclass
from datetime import datetime

from psycopg import sql

from . import db

# Actor columns that are not audit facts, with why. Keyed (table, column); table "*" matches any table.
EXCLUDED = {
    ("assistant_answer", "by_user"): "account-private assistant conversation, never shared",
    ("assistant_conversation", "by_user"): "account-private assistant conversation, never shared",
    ("assistant_fact", "by_user"): "account-private assistant memory, never shared",
    ("assistant_fact", "invalidated_by"): "account-private assistant memory, never shared",
    ("assistant_fact", "edited_by"): "account-private assistant memory, never shared",
    ("assistant_fact", "ended_by"): "account-private assistant memory, never shared",
    ("assistant_figure", "by_source"): "names the source that produced a figure, not a person",
    ("feedback", "by_user"): "a person's own rating of an answer or brief, account-scoped",
    ("value_ranking", "by_user"): "a person's own ranking, account-scoped",
    ("lesson_history", "by_user"): "the lesson's own edit history; the lesson's decision is in the record",
    ("work_draft", "by_user"): "an account-private draft until confirmed; confirmed_by is in the record",
    ("lesson", "review_by"): "a review-due date, not an actor",
    ("operating_model_deviation", "review_by"): "a review-due date, not an actor",
    ("learned_rule", "taught_by"): "the aliases a rule was learned from, not an actor",
    ("work_token", "claimed_by"): "a claim is a work event (kind claimed), which is in the record",
}
# Actor columns whose time column is not named after them.
TIMES = {
    ("daily_ops_report", "drafted_by"): "created_at",
    ("exception_record", "shelved_by"): "updated_at",
    ("plan_item", "moved_by"): "updated_at",
    ("round_run", "taken_by"): "closed_at",
    ("shift_handover", "created_by"): "drafted_at",
}
# Append-only logs: every change is a row. Everything else keeps only its latest value.
LOGS = {"work_event", "writeback_event", "source_audit", "ontology_audit", "setting_history", "access_event",
        "gate_decision", "field_photo", "round_reading", "board_history", "id_event"}
# What happened, for tables whose row says it better than its column name.
ACTIONS = {
    "work_event": "kind",
    "writeback_event": "state",
    "source_audit": "object || '.' || action",
    "ontology_audit": "object || '.' || action",
    "access_event": "action",
    "gate_decision": "kind || case when fired then '.held' else '.passed' end",
    "setting_history": "'changed ' || key",
    "setting": "'set ' || key",
    "case_disposition": "disposition",
}
REFS = {"work_event": "'work:' || item_id", "writeback_event": "'writeback:' || writeback_id",
        "setting_history": "key", "setting": "key", "project": "name", "work_person_cover": "name"}
FIELDS = ("at", "project", "actor", "source", "action", "ref", "history", "detail")


class AuditError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    schema: str
    table: str
    actor: str
    at: str | None
    drop: tuple[str, ...]  # binary columns left out of the detail

    @property
    def history(self) -> bool:
        return self.table in LOGS

    @property
    def verb(self) -> str:
        return "" if self.actor in ("by", "actor") else self.actor.removesuffix("_by")


def _time_column(actor: str, times: set[str]) -> str | None:
    if actor in ("by", "actor"):
        return "at" if "at" in times else None
    verb = actor.removesuffix("_by")
    for name in (f"{verb}_at", f"{verb}ed_at", f"{verb}d_at"):
        if name in times:
            return name
    return None


def sources(conn, schema: str | None = None) -> list[Source]:
    """Every actor column in the project's schema and the shared one, paired with its time column (at None when it
    has none). Excluded columns are left out."""
    schema = schema or db.scope().schema
    rows = conn.execute(
        "select table_schema, table_name, column_name, data_type from information_schema.columns "
        "where table_schema = any(%s) order by table_schema, table_name, ordinal_position",
        ([schema, db.SHARED],)).fetchall()
    tables: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        tables.setdefault((r["table_schema"], r["table_name"]), []).append(r)
    out = []
    for (sch, table), cols in tables.items():
        times = {c["column_name"] for c in cols if c["data_type"].startswith("timestamp")}
        drop = tuple(c["column_name"] for c in cols if c["data_type"] == "bytea")
        for c in cols:
            name = c["column_name"]
            if not (name in ("by", "actor") or name.endswith("_by") or name.startswith("by_")):
                continue
            if (table, name) in EXCLUDED:
                continue
            at = TIMES.get((table, name)) or _time_column(name, times)
            out.append(Source(sch, table, name, at if at in times else None, drop))
    return out


def _filter(src: Source) -> sql.Composable | None:
    """The shared tables hold every project's rows; keep this project's. None: a shared table with no filter yet,
    left out of the record (coverage lists it, and a test fails on it) rather than leaking other projects' rows."""
    if src.schema != db.SHARED:
        return sql.SQL("true")
    if src.table == "access_event":
        return sql.SQL("(project = %(project)s or (project is null and (actor = any(%(actors)s) "
                       "or data->>'account_id' = any(%(accounts)s))))")
    if src.table in ("setting", "setting_history"):
        return sql.SQL("scope in ('global', 'project:' || %(project)s)")
    if src.table == "project":
        return sql.SQL("name = %(project)s")
    if src.table == "work_person_cover":
        return sql.SQL("(cardinality(projects) = 0 or %(project)s = any(projects))")
    if src.table == "operating_model_rollout":
        return sql.SQL("project = %(project)s")
    if src.table == "operating_model_version":
        return sql.SQL("true")  # the shared standard every project follows: a change to it is each project's
    return None


def _people(conn, project: str) -> tuple[list[str], list[str]]:
    """Accounts that can open `project` (owners and those granted there): their deployment-wide access events
    (sign-ins, account changes) belong in its record; nobody else's do. The database's first project also holds
    the accounts granted nowhere yet, so a pending sign-in is in one record."""
    from . import access

    first = access._default_project(conn)
    if project == first:
        rows = conn.execute("select id from access_account where owner union "
                            "select account_id from access_membership union "
                            "select id from access_account a where not exists (select 1 from access_membership m "
                            "where m.account_id = a.id) and not exists (select 1 from access_project_grant g "
                            "where g.account_id = a.id)").fetchall()
    else:
        rows = conn.execute("select id from access_account where owner union "
                            "select account_id from access_project_grant where project = %s", (project,)).fetchall()
    ids = sorted(r["id"] for r in rows)
    return [access.ACTOR_PREFIX + i for i in ids], ids


def record(conn, project: str, since: datetime, until: datetime, limit: int = 10000) -> list[dict]:
    """The project's audit record between `since` (inclusive) and `until` (exclusive), oldest first. `conn` must
    run for `project` (its search path finds the project's schema first)."""
    if since >= until:
        raise AuditError("The start of the window must be before its end")
    schema = conn.execute("select current_schema() as s").fetchone()["s"]
    actors, accounts = _people(conn, project)
    params = {"project": project, "actors": actors, "accounts": accounts, "since": since, "until": until,
              "limit": limit + 1}
    rows: list[dict] = []
    for src in sources(conn, schema):
        if src.at is None:
            continue
        where = _filter(src)
        if where is None:
            continue
        action = sql.SQL(ACTIONS[src.table]) if src.table in ACTIONS else sql.Literal(src.verb or "recorded")
        ref = sql.SQL(REFS[src.table]) if src.table in REFS else sql.Literal(None)
        query = sql.SQL(
            "select t.{at} as at, t.{actor} as actor, ({action})::text as action, ({ref})::text as ref, "
            "to_jsonb(t) - {drop}::text[] as detail from {table} t "
            "where t.{actor} is not null and t.{at} >= %(since)s and t.{at} < %(until)s and {where} "
            "order by t.{at} limit %(limit)s").format(
                at=sql.Identifier(src.at), actor=sql.Identifier(src.actor), action=action, ref=ref,
                drop=sql.Literal(list(src.drop)), table=sql.Identifier(src.schema, src.table), where=where)
        for r in conn.execute(query, params):
            if r["ref"] is None and "id" in r["detail"]:
                r["ref"] = f"{src.table}:{r['detail']['id']}"
            r.update(project=project, source=f"{src.table}.{src.actor}", history=src.history)
            rows.append(r)
    rows.sort(key=lambda r: (r["at"], r["source"]))
    if len(rows) > limit:
        raise AuditError(f"More than {limit} audit records in this window; export a shorter window")
    return rows


def coverage(conn, schema: str | None = None) -> dict:
    """Which actor columns are in the record, which keep every change, and which are excluded and why."""
    srcs = sources(conn, schema)
    return {"included": [{"source": f"{s.table}.{s.actor}", "time": s.at, "history": s.history}
                         for s in srcs if s.at and _filter(s) is not None],
            "unpaired": [f"{s.table}.{s.actor}" for s in srcs if s.at is None],
            "unfiltered": [f"{s.table}.{s.actor}" for s in srcs if s.at and _filter(s) is None],
            "excluded": [{"source": f"{t}.{c}", "reason": why} for (t, c), why in sorted(EXCLUDED.items())]}


def to_csv(rows: list[dict]) -> str:
    import json

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(FIELDS)
    for r in rows:
        w.writerow([r["at"].isoformat(), r["project"], r["actor"], r["source"], r["action"], r["ref"] or "",
                    "every change" if r["history"] else "latest only", json.dumps(r["detail"], default=str)])
    return out.getvalue()
