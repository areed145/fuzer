"""Change control and rollout (P8.3): a change to the standard is proposed, test-run on a sample of real closed cases,
reviewed by a person other than its author, published as a new version, and rolled out asset by asset, each with a
rollback. A local deviation that proves better is promoted to the standard the same way.

    propose(conn, changes, summary, by)       a new version: the latest published one with `changes` ({part: {key:
                                              value, or None to remove}}) applied, state `proposed`
    promote(conn, part, key, summary, by)     propose this project's own value at part/key as the standard
    test_run(conn, name, version)             how the change would have handled this project's closed cases of the
                                              work types it touches: their route and process before and after, and
                                              whether each would have met the new SLA; stored on the version
    review(conn, name, version, by, ...)      a person who didn't propose it, with the workflow_admin role, approves
                                              or rejects it with a note, after a test run
    publish(conn, name, version, by)          an approved version becomes one assets can run
    rollout(conn, project, name, version, by) the asset applies the version (operating_model.apply); recorded with the
                                              version it ran before
    rollback(conn, project, by, reason)       the asset goes back to the version it ran before its last rollout
    versions(conn), history(conn, project)    the versions and their states; an asset's rollouts and rollbacks

Versions and rollouts are held in the shared schema, since one standard serves every asset; each asset's applied
record and deviations stay in its own register. `following(conn, project)` is the version an asset was last rolled
out to, which conformance (P8.2) holds it to instead of its bundle's.
"""

import copy
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from . import operating_model as om

STATES = ("proposed", "approved", "rejected", "published")
SAMPLE, SAMPLE_DAYS = 20, 180  # closed cases a test run reads: up to this many, closed in the last this many days


class ChangeError(ValueError):
    pass


def _row(conn, name: str, version: int) -> dict:
    row = conn.execute("select * from fuzer.operating_model_version where name = %s and version = %s",
                       (name, version)).fetchone()
    if row is None:
        raise ChangeError(f"no version {version} of {name}")
    return row


def package(conn, name: str, version: int, published: bool = False) -> dict | None:
    """The package of a version (the built-in standard for standard v1); None when it isn't held (or, with
    `published`, isn't published)."""
    if (name, version) == (om.STANDARD_NAME, om.STANDARD_VERSION):
        return om.standard()
    row = conn.execute("select package, state from fuzer.operating_model_version where name = %s and version = %s",
                       (name, version)).fetchone()
    if row is None or (published and row["state"] != "published"):
        return None
    return {**row["package"], "name": name, "version": version}


def latest(conn, name: str = om.STANDARD_NAME) -> int:
    """The latest published version of a model."""
    row = conn.execute("select max(version) as v from fuzer.operating_model_version where name = %s and "
                       "state = 'published'", (name,)).fetchone()
    return max(row["v"] or 0, om.STANDARD_VERSION if name == om.STANDARD_NAME else 0)


def diff(base: dict, pkg: dict) -> list[dict]:
    """What a package changes from its base: {part, key, change (changed | added | removed)}."""
    out = []
    for part in om.PARTS:
        a, b = base.get(part) or {}, pkg.get(part) or {}
        for key in sorted(set(a) | set(b)):
            if key not in a:
                out.append({"part": part, "key": key, "change": "added"})
            elif key not in b:
                out.append({"part": part, "key": key, "change": "removed"})
            elif not om._same(a[key], b[key]):
                out.append({"part": part, "key": key, "change": "changed"})
    return out


def propose(conn, changes: dict, summary: str, by: str, name: str = om.STANDARD_NAME,
            base: int | None = None) -> dict:
    """A new version of `name`: version `base` (default the latest published) with `changes` applied."""
    from ..core import gates
    from . import bpmn

    if not (summary or "").strip():
        raise ChangeError("a proposal says what it changes and why")
    gates.require_person(conn, "publish", by, what=f"proposing a change to {name}")
    base = base or latest(conn, name)
    pkg = package(conn, name, base, published=True)
    if pkg is None:
        raise ChangeError(f"{name} v{base} isn't published, so nothing can be based on it")
    pkg = copy.deepcopy(pkg)
    for part, kv in (changes or {}).items():
        if part not in om.PARTS:
            raise ChangeError(f"no part {part!r}: one of {', '.join(om.PARTS)}")
        for key, value in (kv or {}).items():
            if part == "settings" and not om._operating(key):
                raise ChangeError(f"{key} is not an operating setting")
            if part == "processes" and value is not None:
                try:
                    if key not in bpmn.parse(value):
                        raise ChangeError(f"the BPMN given for {key} doesn't define a process {key}")
                except ChangeError:
                    raise
                except Exception as e:
                    raise ChangeError(f"the BPMN given for {key} doesn't parse: {e}") from None
            if value is None:
                (pkg.get(part) or {}).pop(key, None)
            else:
                pkg.setdefault(part, {})[key] = value
    changed = diff(package(conn, name, base, published=True), pkg)
    if not changed:
        raise ChangeError("the proposal changes nothing")
    version = max(latest(conn, name), conn.execute(
        "select coalesce(max(version), 0) as v from fuzer.operating_model_version where name = %s",
        (name,)).fetchone()["v"]) + 1
    pkg = {**pkg, "name": name, "version": version}
    row = conn.execute(
        """
        insert into fuzer.operating_model_version (name, version, base_version, package, digest, changes, summary,
                                                   state, proposed_by)
        values (%s, %s, %s, %s, %s, %s, %s, 'proposed', %s) returning *
        """, (name, version, base, Jsonb(pkg), om.digest(pkg), Jsonb(changed), summary.strip(), by)).fetchone()
    conn.commit()
    return _public(row)


def promote(conn, part: str, key: str, summary: str, by: str) -> dict:
    """Propose this project's own value at part/key (a local deviation that proved better) as the standard."""
    have = om.snapshot(conn).get(part)
    if have is None:
        raise ChangeError(f"no part {part!r}: one of {', '.join(om.PARTS)}")
    if key not in have:
        raise ChangeError(f"this project has no {part} {key} to promote")
    project = om._project(conn)
    return propose(conn, {part: {key: have[key]}}, f"{summary.strip()} (promoted from {project}'s deviation)", by,
                   name=om.conformance_package(conn)["name"])


def _sla(pkg: dict, role: str | None) -> timedelta | None:
    from . import bpmn

    r = (pkg.get("roles") or {}).get((role or "").removeprefix("role:"))
    return bpmn.parse_duration(r["default_sla"]) if r and r.get("default_sla") else None


def test_run(conn, name: str, version: int, sample: int = SAMPLE, days: int = SAMPLE_DAYS) -> dict:
    """Run the change against this project's closed cases of the work types it touches: for each, its role, process
    and SLA under the base and the proposed version, how long the case took, and whether it would have met the new
    SLA. A changed process is checked to parse and to name only roles the version holds. Stored on the version."""
    from . import bpmn

    row = _row(conn, name, version)
    if row["state"] not in ("proposed", "approved"):
        raise ChangeError(f"{name} v{version} is {row['state']}: only a proposal is test-run")
    pkg = package(conn, name, version)
    base = package(conn, name, row["base_version"], published=True)
    changed = row["changes"]
    problems = []
    for c in changed:
        if c["part"] == "processes" and c["change"] != "removed":
            try:
                tasks = bpmn.parse(pkg["processes"][c["key"]])[c["key"]]
            except Exception as e:
                problems.append(f"{c['key']} doesn't parse: {e}")
                continue
            for role in sorted({(t.role or "").removeprefix("role:") for t in tasks.nodes.values()
                                if getattr(t, "role", None)} - set(pkg.get("roles") or {})):
                problems.append(f"{c['key']} names a role the version doesn't hold: {role}")
    procs = {c["key"] for c in changed if c["part"] == "processes"}
    roles = {c["key"] for c in changed if c["part"] == "roles"}
    from ..core import exceptions

    exceptions.load()
    kinds = {n: {"role": t.role, "process": t.process} for n, t in exceptions.types(conn).items()}
    types = {c["key"] for c in changed if c["part"] == "work_types"}
    types |= {t for t, k in kinds.items() if (k["process"] in procs) or ((k["role"] or "").removeprefix("role:") in roles)}
    since = datetime.now(UTC) - timedelta(days=days)
    cases = conn.execute(
        """
        select i.id, i.type, i.role, i.created_at, i.closed_at, i.state from work_item i
        where i.closed_at is not null and i.closed_at >= %s and (i.type = any(%s) or i.role = any(%s))
        order by i.closed_at desc limit %s
        """, (since, sorted(types), sorted(roles | {f"role:{r}" for r in roles}), sample)).fetchall()
    out = []
    for c in cases:
        k = kinds.get(c["type"]) or {}
        before = {"role": c["role"] or k.get("role"), "process": k.get("process")}
        wt = (pkg.get("work_types") or {}).get(c["type"]) or {}
        after = {"role": wt.get("role") or before["role"], "process": wt.get("process") or before["process"]}
        took = c["closed_at"] - c["created_at"]
        sla_before, sla_after = _sla(base, before["role"]), _sla(pkg, after["role"])
        out.append({"case": c["id"], "type": c["type"], "before": before, "after": after,
                    "process_changed": after["process"] in procs, "took_h": round(took.total_seconds() / 3600, 1),
                    "sla_before_h": sla_before and round(sla_before.total_seconds() / 3600, 1),
                    "sla_after_h": sla_after and round(sla_after.total_seconds() / 3600, 1),
                    "met_before": None if sla_before is None else took <= sla_before,
                    "met_after": None if sla_after is None else took <= sla_after})
    result = {"project": om._project(conn), "at": datetime.now(UTC).isoformat(timespec="seconds"),
              "types": sorted(types), "cases": out, "problems": problems,
              "summary": {"cases": len(out), "process_changed": sum(1 for x in out if x["process_changed"]),
                          "met_before": sum(1 for x in out if x["met_before"]),
                          "met_after": sum(1 for x in out if x["met_after"])}}
    conn.execute("update fuzer.operating_model_version set test_run = %s where name = %s and version = %s",
                 (Jsonb(json.loads(json.dumps(result, default=str))), name, version))
    conn.commit()
    return result


def review(conn, name: str, version: int, by: str, approve: bool, note: str) -> dict:
    """A person other than its author, holding workflow_admin, approves or rejects a tested proposal, with a note."""
    from ..core import gates
    from .definitions import ADMIN_ROLE, Forbidden, roles_of

    row = _row(conn, name, version)
    if row["state"] != "proposed":
        raise ChangeError(f"{name} v{version} is {row['state']}: only a proposal is reviewed")
    if not row["test_run"]:
        raise ChangeError(f"{name} v{version} hasn't been test-run on closed cases yet")
    if approve and row["test_run"].get("problems"):
        raise ChangeError(f"its test run found problems: {'; '.join(row['test_run']['problems'])}")
    if not (note or "").strip():
        raise ChangeError("a review says why")
    gates.require_person(conn, "publish", by, what=f"reviewing {name} v{version}")
    if by == row["proposed_by"]:
        raise Forbidden("a change is reviewed by someone other than the person who proposed it")
    if ADMIN_ROLE not in roles_of(conn, by):
        raise Forbidden(f"reviewing a change to the standard needs the {ADMIN_ROLE} role; {by} doesn't have it")
    row = conn.execute(
        "update fuzer.operating_model_version set state = %s, reviewed_by = %s, reviewed_at = now(), "
        "review_note = %s where name = %s and version = %s returning *",
        ("approved" if approve else "rejected", by, note.strip(), name, version)).fetchone()
    conn.commit()
    return _public(row)


def publish(conn, name: str, version: int, by: str) -> dict:
    """An approved version becomes one assets can run."""
    from ..core import gates
    from .definitions import ADMIN_ROLE, Forbidden, roles_of

    row = _row(conn, name, version)
    if row["state"] != "approved":
        raise ChangeError(f"{name} v{version} is {row['state']}: only an approved change is published")
    gates.require_person(conn, "publish", by, what=f"publishing {name} v{version}")
    if ADMIN_ROLE not in roles_of(conn, by):
        raise Forbidden(f"publishing a version of the standard needs the {ADMIN_ROLE} role; {by} doesn't have it")
    row = conn.execute("update fuzer.operating_model_version set state = 'published', published_by = %s, "
                       "published_at = now() where name = %s and version = %s returning *",
                       (by, name, version)).fetchone()
    conn.commit()
    return _public(row)


def following(conn, project: str) -> dict | None:
    """The version `project` was last rolled out to and still runs: {name, version}, or None (its bundle's)."""
    row = conn.execute("select name, version from fuzer.operating_model_rollout where project = %s and state = 'live' "
                       "order by id desc limit 1", (project,)).fetchone()
    return dict(row) if row else None


def _running(conn, project: str) -> dict:
    """What a project runs now: its last rollout, else its bundle's model (`conn` scoped to that project)."""
    if f := following(conn, project):
        return f
    try:
        ref = om._bundle_model(conn)
        return {"name": ref["name"], "version": ref["version"]}
    except om.OperatingModelError:
        return {"name": om.STANDARD_NAME, "version": om.STANDARD_VERSION}


@contextmanager
def _at(conn, project: str):
    """A connection for `project`'s register: `conn` itself when it is already that project's."""
    from ..core import db, projects

    if project == om._project(conn):
        yield conn
        return
    if project not in projects.stored(conn):
        raise ChangeError(f"no project {project!r}")
    with projects.use(conn, project), db.connect() as there:
        yield there


def _apply_keeping(conn, pkg: dict, by: str) -> dict:
    """Apply `pkg` to this project, keeping its named deviations: a named setting keeps its local value, as adopt
    sets it. A named difference in another part (a role, work type, process or form) the version replaces is
    listed in `overwritten`, so whoever rolled it out can see what the asset lost and name it again."""
    from ..core import settings

    before = {(d["part"], d["key"]): d for d in om.deviations(conn, pkg) if d["owner"]}
    snap = om.snapshot(conn)
    done = om.apply(conn, pkg, by)
    project = om._project(conn)
    kept, overwritten = [], []
    for (part, key), d in sorted(before.items()):
        if part == "settings" and key in snap["settings"]:
            settings.set(conn, key, snap["settings"][key], by=by, reason=d["reason"], project_name=project)
            kept.append(f"{part}:{key}")
        elif d["change"] == "changed":
            overwritten.append(f"{part}:{key}")
    conn.commit()
    return {**done, "kept": kept, "overwritten": overwritten}


def rollout(conn, project: str, name: str, version: int, by: str) -> dict:
    """Roll a published version out to one asset: it applies it, and the version it ran before is kept for a
    rollback."""
    pkg = package(conn, name, version, published=True)
    if pkg is None:
        raise ChangeError(f"{name} v{version} isn't published, so it can't be rolled out")
    with _at(conn, project) as there:
        was = _running(there, project)
        done = _apply_keeping(there, pkg, by)
    conn.execute("update fuzer.operating_model_rollout set state = 'superseded' where project = %s and state = 'live'",
                 (project,))
    row = conn.execute(
        """
        insert into fuzer.operating_model_rollout (project, name, version, from_name, from_version, state, by)
        values (%s, %s, %s, %s, %s, 'live', %s) returning *
        """, (project, name, version, was["name"], was["version"], by)).fetchone()
    conn.commit()
    return {**dict(row), "applied": done}


def rollback(conn, project: str, by: str, reason: str) -> dict:
    """Take one asset back to the version it ran before its last rollout."""
    if not (reason or "").strip():
        raise ChangeError("a rollback says why")
    last = conn.execute("select * from fuzer.operating_model_rollout where project = %s and state = 'live' "
                        "order by id desc limit 1", (project,)).fetchone()
    if last is None:
        raise ChangeError(f"{project} has no rollout to roll back")
    pkg = package(conn, last["from_name"], last["from_version"])
    if pkg is None:
        raise ChangeError(f"{last['from_name']} v{last['from_version']} isn't held, so there is nothing to go back to")
    with _at(conn, project) as there:
        done = _apply_keeping(there, pkg, by)
    conn.execute("update fuzer.operating_model_rollout set state = 'rolled_back', rolled_back_by = %s, "
                 "rolled_back_at = now(), rollback_reason = %s where id = %s", (by, reason.strip(), last["id"]))
    conn.execute(  # the rollout it replaced, if any, is the one the asset follows again
        """
        update fuzer.operating_model_rollout set state = 'live' where id = (
            select id from fuzer.operating_model_rollout where project = %s and state = 'superseded' and id < %s
              and name = %s and version = %s order by id desc limit 1)
        """, (project, last["id"], last["from_name"], last["from_version"]))
    conn.commit()
    return {"project": project, "from": {"name": last["name"], "version": last["version"]},
            "to": {"name": last["from_name"], "version": last["from_version"]}, "applied": done}


def _public(row: dict) -> dict:
    return {k: v for k, v in dict(row).items() if k != "package"}


def versions(conn, name: str | None = None) -> list[dict]:
    """Every version held (the built-in standard v1 first), without their packages."""
    rows = [_public(r) for r in conn.execute(
        "select * from fuzer.operating_model_version where %s::text is null or name = %s order by name, version",
        (name, name))]
    builtin = {"name": om.STANDARD_NAME, "version": om.STANDARD_VERSION, "state": "published", "summary":
               "The standard way to operate that ships with Fuzer", "proposed_by": "fuzer"}
    return ([builtin] if name in (None, om.STANDARD_NAME) else []) + rows


def history(conn, project: str | None = None) -> list[dict]:
    """Rollouts and rollbacks, newest first (one asset's, or every asset's)."""
    return [dict(r) for r in conn.execute(
        "select * from fuzer.operating_model_rollout where %s::text is null or project = %s order by id desc",
        (project, project))]
