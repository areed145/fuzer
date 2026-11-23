"""One container (step 6 of docs/work-model.md): every level a site groups work at, one object with a `kind` and a
`parent`, governed by a case of its own that runs its gates (a project's select, define, execute; a turnaround's scope
freeze and readiness review).

    create(conn, kind, title, by, ...)   a container made here, with its governing case
    sync(conn)                           schedules, board projects and SAP revisions as containers, their jobs in them
    add_case(conn, id, item_id, by)      put a case in a container (one each)
    listing(conn) / get(conn, id)        the containers, each with its jobs by stage, its cases and its children

Jobs belong through `plan_item.container_id`; a case belongs through its job, or directly (`work_container_case`).
The records a container was made from (`plan_project`, `board_project`) stay where they are: the container points
at them by `source` and `source_key`, so the schedule and the board work as before.
"""

import re

from ..core import events, work

KINDS = ("business_plan", "program", "project", "turnaround", "package", "campaign")
CASE_TYPE = "container"


class ContainerError(ValueError):
    pass


def _code(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "container"


def _row(conn, container_id: int) -> dict:
    row = conn.execute("select * from work_container where id = %s", (container_id,)).fetchone()
    if row is None:
        raise ContainerError(f"no container {container_id}")
    return row


def _parent(conn, parent: int | None, child: int | None = None) -> None:
    seen = {child} if child else set()
    while parent:
        if parent in seen:
            raise ContainerError("a container can't be inside itself")
        seen.add(parent)
        parent = _row(conn, parent)["parent_id"]


def create(conn, kind: str, title: str, by: str, code: str | None = None, parent: int | None = None,
           owner: str | None = None, start_at=None, finish_by=None, source: str | None = None,
           source_key: str | None = None, governed: bool = True) -> dict:
    """A container, with its governing case (held by its owner, else whoever made it) unless `governed` is off."""
    if kind not in KINDS:
        raise ContainerError(f"kind must be one of {KINDS}")
    if not (title or "").strip():
        raise ContainerError("a container needs a title")
    _parent(conn, parent)
    row = conn.execute(
        "insert into work_container (kind, code, title, parent_id, owner, start_at, finish_by, source, source_key, "
        "created_by) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *",
        (kind, code or f"{_code(title)}-{kind}", title.strip(), parent, owner, start_at, finish_by, source, source_key,
         by)).fetchone()
    if governed:
        row = govern(conn, row["id"], by)
    events.publish(conn, "container.changed", f"container:{row['id']}", {"id": row["id"], "change": "created",
                                                                         "kind": kind, "by": by})
    return row


def govern(conn, container_id: int, by: str) -> dict:
    """Open the container's governing case if it has none: the case its gates and decisions are recorded on."""
    row = _row(conn, container_id)
    if row["item_id"]:
        return row
    case = work.open_case(conn, CASE_TYPE, f"{row['title']} ({row['kind'].replace('_', ' ')})", by,
                          assignee=row["owner"] or by, source="fuzer", payload={"container_id": container_id})
    return conn.execute("update work_container set item_id = %s, updated_at = now() where id = %s returning *",
                        (case["id"], container_id)).fetchone()


def move(conn, container_id: int, by: str, parent: int | None) -> dict:
    _parent(conn, parent, container_id)
    row = conn.execute("update work_container set parent_id = %s, updated_at = now() where id = %s returning *",
                       (parent, container_id)).fetchone()
    if row is None:
        raise ContainerError(f"no container {container_id}")
    events.publish(conn, "container.changed", f"container:{container_id}", {"id": container_id, "change": "moved",
                                                                            "parent": parent, "by": by})
    return row


def add_case(conn, container_id: int, item_id: int, by: str) -> dict:
    """Put a case in a container; a case is in one container, so this moves it from any other."""
    c = _row(conn, container_id)
    if c["item_id"] == item_id:
        raise ContainerError("a container's governing case isn't one of its cases")
    if not conn.execute("select 1 from work_item where id = %s", (item_id,)).fetchone():
        raise ContainerError(f"no case {item_id}")
    conn.execute("insert into work_container_case (item_id, container_id, added_by) values (%s, %s, %s) "
                 "on conflict (item_id) do update set container_id = excluded.container_id, "
                 "added_by = excluded.added_by, added_at = now()", (item_id, container_id, by))
    work.comment(conn, item_id, by, f"Put in {c['title']} ({c['kind'].replace('_', ' ')})")
    return get(conn, container_id)


def _upsert(conn, kind: str, code: str, title: str, by: str, source: str, key: str, start_at=None,
            finish_by=None) -> int:
    return conn.execute(
        """
        insert into work_container (kind, code, title, start_at, finish_by, source, source_key, created_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (code) do update set title = excluded.title, start_at = excluded.start_at,
            finish_by = excluded.finish_by, updated_at = now()
        returning id
        """, (kind, code, title, start_at, finish_by, source, key, by)).fetchone()["id"]


REVISION = re.compile(r"revision (\S+)")


def sync(conn, by: str = "fuzer") -> dict:
    """Schedules (`plan_project`), open board projects and SAP revisions as containers, and each job on the plan in
    the container its schedule or revision is. Idempotent; a job a person put in another container stays there."""
    made = 0
    for p in conn.execute("select * from plan_project").fetchall():
        kind = p["kind"] if p["kind"] in KINDS else "project"
        cid = _upsert(conn, kind, f"schedule:{p['code']}", p["title"], by, "plan_project", p["code"], p["start_at"],
                      p["finish_by"])
        made += conn.execute("update plan_item set container_id = %s where project = %s and container_id is null",
                             (cid, p["code"])).rowcount
    for b in conn.execute("select * from board_project where status = 'open'").fetchall():
        _upsert(conn, "project", f"board:{b['id']}", b["title"], by, "board_project", str(b["id"]))
    revisions: dict[str, list[int]] = {}
    for r in conn.execute("select id, notes from plan_item where source = 'sap' and notes like '%%revision %%'"):
        if m := REVISION.search(r["notes"] or ""):
            revisions.setdefault(m.group(1).rstrip(",."), []).append(r["id"])
    for rev, ids in revisions.items():
        cid = _upsert(conn, "turnaround", f"revision:{rev}", f"Turnaround {rev}", by, "sap_revision", rev)
        made += conn.execute("update plan_item set container_id = %s where id = any(%s) and container_id is null",
                             (cid, ids)).rowcount
    n = conn.execute("select count(*) as n from work_container").fetchone()["n"]
    return {"containers": n, "jobs_placed": made}


def _summary(conn, ids: list[int]) -> dict[int, dict]:
    """Each container's own jobs by stage and its cases (through its jobs, or put there)."""
    from .plan import job_stage

    out = {i: {"jobs": {}, "cases": set()} for i in ids}
    for j in conn.execute("select * from plan_item where container_id = any(%s)", (ids,)):
        s = out[j["container_id"]]
        st = job_stage(j)
        s["jobs"][st] = s["jobs"].get(st, 0) + 1
        if j["item_id"]:
            s["cases"].add(j["item_id"])
    for c in conn.execute("select item_id, container_id from work_container_case where container_id = any(%s)", (ids,)):
        out[c["container_id"]]["cases"].add(c["item_id"])
    return out


def listing(conn, kind: str | None = None, status: str | None = "open") -> list[dict]:
    """The containers, parents first, each with its jobs by stage, how many cases and the ids of its children."""
    rows = conn.execute("select * from work_container where (%s::text is null or kind = %s) "
                        "and (%s::text is null or status = %s) order by parent_id nulls first, start_at nulls last, id",
                        (kind, kind, status, status)).fetchall()
    summary = _summary(conn, [r["id"] for r in rows])
    children: dict[int, list[int]] = {}
    for r in rows:
        if r["parent_id"]:
            children.setdefault(r["parent_id"], []).append(r["id"])
    return [{**r, "jobs": summary[r["id"]]["jobs"], "cases": len(summary[r["id"]]["cases"]),
             "children": children.get(r["id"], [])} for r in rows]


def get(conn, container_id: int) -> dict:
    """One container: its parent chain, children, jobs (with their stage) and cases (with state and holder)."""
    from .plan import job_stage

    row = _row(conn, container_id)
    chain, up = [], row["parent_id"]
    while up:
        p = _row(conn, up)
        chain.insert(0, {"id": p["id"], "kind": p["kind"], "title": p["title"]})
        up = p["parent_id"]
    jobs = [{"id": j["id"], "title": j["title"], "start_at": j["start_at"], "end_at": j["end_at"], "crew": j["crew"],
             "item_id": j["item_id"], "stage": job_stage(j)}
            for j in conn.execute("select * from plan_item where container_id = %s order by start_at", (container_id,))]
    ids = list(_summary(conn, [container_id])[container_id]["cases"])
    cases = [dict(c) for c in conn.execute("select id, title, state, assignee, role from work_item where id = any(%s) "
                                           "order by id", (ids,))]
    kids = [dict(k) for k in conn.execute("select id, kind, title, status from work_container where parent_id = %s "
                                          "order by start_at nulls last, id", (container_id,))]
    return {**row, "parents": chain, "children": kids, "jobs": jobs, "cases": cases}
