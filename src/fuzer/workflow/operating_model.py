"""The operating model as a package (P8.1, #810): the standard way to operate, held as one versioned set of files that a
project applies, with its local differences listed against it.

A package is a folder:

    model.yaml          name, version, description
    roles.yaml          the roles: person or agent, default SLA, where an overdue step escalates, how steps are assigned
    work_types.yaml     the work types declared as data (exception_kind; a source pack's own types come with the pack)
    settings.yaml       the operating settings a project sets: detectors on and off and their parameters by class
                        (`key@qualifier`), signal and alarm thresholds, due policies and the other Work, SRE, Signals,
                        Alarms, Opportunities, Reports and Detection settings
    processes/*.bpmn    the published processes
    forms/*.form        their task forms

    snapshot(conn)                 this project's operating model as it stands, in the package's shape
    export(conn, folder, ...)      write it as a package
    load(folder)                   read a package
    standard()                     the built-in standard (`standard`, version STANDARD_VERSION): the shipped
                                   processes and forms, the built-in roles, no data work types, no settings changed
    apply(conn, package, by)       make this project run the package: roles and work types upserted, settings set and
                                   the project's other operating settings reset, changed processes and forms published;
                                   records the version and its digest
    applied(conn)                  the version this project last applied
    deviations(conn, package)      where this project differs from the package, one row per difference, each with
                                   the reason and owner it was named with (None for an unnamed deviation)
    name(conn, part, key, ...)     name a deviation: why this project differs there and who owns it
    unname(conn, part, key)        take a name back
    adopt(conn, by)                apply the model the project's bundle follows, set the bundle's settings deviations
                                   and name its deviations
    conformance(conn, package)     the model, whether it was applied as it is, and the deviations named and unnamed

Rounds come from the rounds source pack (round_point entities), so a bundle's source packs carry them; metric
targets are not held anywhere yet. Both join the package when they are.
"""

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import yaml

from ..core import settings

STANDARD_NAME, STANDARD_VERSION = "standard", 1
SETTING_GROUPS = ("Work", "SRE", "Signals", "Alarms", "Opportunities", "Reports", "Detection")
ROLE_FIELDS = ("kind", "description", "default_sla", "escalate_to", "assignment")
TYPE_FIELDS = ("label", "role", "process", "case_type", "audience", "close_case_on_clear", "description", "joins",
               "due_hours")
PARTS = ("roles", "work_types", "settings", "processes", "forms")
REVIEW_DAYS = 365  # a named deviation is reviewed a year after it is named, unless its owner says when (P8.2)


class OperatingModelError(ValueError):
    pass


def _operating(key: str) -> bool:
    base, _ = settings.split(key)
    s = settings.REGISTRY.get(base)
    return bool(s and s.group in SETTING_GROUPS and "project" in s.scopes)


def _project(conn) -> str:
    name = settings.project()
    if not name:
        raise OperatingModelError("no project: initialise one from a bundle first")
    return name


def snapshot(conn) -> dict:
    """This project's operating model as it stands: {roles, work_types, settings, processes, forms}."""
    roles = {r["name"]: {k: r[k] for k in ROLE_FIELDS}
             for r in conn.execute("select * from work_role order by name")}
    types = {r["name"]: {k: (list(r[k]) if k == "audience" else r[k]) for k in TYPE_FIELDS}
             for r in conn.execute("select * from exception_kind where source_system is null order by name")}
    scope = settings.scope_of(_project(conn))
    values = {r["key"]: r["value"] for r in conn.execute(
        "select key, value from setting where scope = %s order by key", (scope,)) if _operating(r["key"])}
    processes = {r["process_key"]: r["xml"] for r in conn.execute(
        "select distinct on (process_key) process_key, xml from work_definition where state = 'published' "
        "order by process_key, version desc")}
    forms = {r["key"]: r["schema"] for r in conn.execute(
        "select distinct on (key) key, schema from work_form order by key, version desc")}
    return {"roles": roles, "work_types": types, "settings": values, "processes": processes, "forms": forms}


def digest(package: dict) -> str:
    """A fingerprint of a package's content (not its name or version): two packages that run the same agree."""
    body = {p: package.get(p) or {} for p in PARTS}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _bpmn_keys(xml: str) -> list[str]:
    from . import bpmn

    return list(bpmn.parse(xml))


def export(conn, folder: Path, name: str = STANDARD_NAME, version: int = STANDARD_VERSION,
           description: str | None = None) -> dict:
    """Write this project's operating model as a package in `folder`. Returns {name, version, digest, counts}."""
    snap = snapshot(conn)
    folder = Path(folder)
    (folder / "processes").mkdir(parents=True, exist_ok=True)
    (folder / "forms").mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "version": int(version), "description": description or f"Exported from {_project(conn)}"}
    (folder / "model.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
    for part in ("roles", "work_types", "settings"):
        (folder / f"{part}.yaml").write_text(yaml.safe_dump(snap[part], sort_keys=True, allow_unicode=True))
    seen: set[str] = set()
    for key, xml in sorted(snap["processes"].items()):
        if xml in seen:  # one file can hold several processes
            continue
        seen.add(xml)
        (folder / "processes" / f"{key}.bpmn").write_text(xml)
    for key, schema in sorted(snap["forms"].items()):
        (folder / "forms" / f"{key}.form").write_text(json.dumps(schema, indent=2) + "\n")
    return {**meta, "digest": digest(snap), "counts": {p: len(snap[p]) for p in PARTS}}


def _files(folder: Path) -> tuple[dict, dict]:
    processes = {}
    for f in sorted((folder / "processes").glob("*.bpmn")) if (folder / "processes").is_dir() else []:
        xml = f.read_text()
        for key in _bpmn_keys(xml):
            processes[key] = xml
    forms = {}
    for f in sorted((folder / "forms").glob("*.form")) if (folder / "forms").is_dir() else []:
        schema = json.loads(f.read_text())
        forms[schema["id"]] = schema
    return processes, forms


def load(folder: Path) -> dict:
    """A package from its folder: {name, version, description, roles, work_types, settings, processes, forms}."""
    folder = Path(folder)
    if not (folder / "model.yaml").is_file():
        raise OperatingModelError(f"{folder}: not an operating model package (no model.yaml)")
    meta = yaml.safe_load((folder / "model.yaml").read_text()) or {}
    if not meta.get("name") or not isinstance(meta.get("version"), int):
        raise OperatingModelError(f"{folder}/model.yaml must name the model and give its version as a number")
    parts = {p: yaml.safe_load((folder / f"{p}.yaml").read_text()) or {} if (folder / f"{p}.yaml").is_file() else {}
             for p in ("roles", "work_types", "settings")}
    for key in parts["settings"]:
        if not _operating(key):
            raise OperatingModelError(f"{folder}/settings.yaml: {key} is not an operating setting")
    processes, forms = _files(folder)
    return {**meta, **parts, "processes": processes, "forms": forms}


def standard() -> dict:
    """The built-in standard: the shipped processes and forms, the built-in roles, nothing else changed."""
    from .definitions import DEFINITIONS_DIR
    from .roles import BUILTIN

    processes, forms = _files_from(DEFINITIONS_DIR)
    roles = {name: {"kind": kind, "description": desc, "default_sla": sla, "escalate_to": esc, "assignment": "queue"}
             for name, (kind, desc, sla, esc) in BUILTIN.items()}
    return {"name": STANDARD_NAME, "version": STANDARD_VERSION,
            "description": "The standard way to operate that ships with Fuzer", "roles": roles, "work_types": {},
            "settings": {}, "processes": processes, "forms": forms}


def _files_from(directory: Path) -> tuple[dict, dict]:
    processes = {k: f.read_text() for f in sorted(directory.glob("*.bpmn")) for k in _bpmn_keys(f.read_text())}
    forms = {(s := json.loads(f.read_text()))["id"]: s for f in sorted(directory.glob("*.form"))}
    return processes, forms


def resolve(ref: str | Path | dict) -> dict:
    """A package from a folder, `standard`, or a package already read."""
    if isinstance(ref, dict):
        return ref
    if str(ref) in (STANDARD_NAME, f"{STANDARD_NAME}@{STANDARD_VERSION}"):
        return standard()
    return load(Path(ref))


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def deviations(conn, package: dict | str | Path) -> list[dict]:
    """Where this project differs from the package: {part, key, change (changed | added | missing), package, local},
    in part and key order. `added` is something this project has that the package doesn't hold."""
    pkg, snap, names = resolve(package), snapshot(conn), _names(conn)
    out = []
    for part in PARTS:
        want, have = pkg.get(part) or {}, snap[part]
        for key in sorted(set(want) | set(have)):
            if key not in have:
                change = "missing"
            elif key not in want:
                change = "added"
            elif not _same(want[key], have[key]):
                change = "changed"
            else:
                continue
            show = part not in ("processes", "forms")  # a whole BPMN or form isn't a value to show in a row
            named = names.get((part, key)) or {}
            out.append({"part": part, "key": key, "change": change,
                        "package": want.get(key) if show else None, "local": have.get(key) if show else None,
                        "reason": named.get("reason"), "owner": named.get("owner"),
                        "review_by": named.get("review_by")})
    return out


def _names(conn) -> dict[tuple[str, str], dict]:
    return {(r["part"], r["key"]): r for r in conn.execute(
        "select part, key, reason, owner, named_by, named_at, review_by from operating_model_deviation "
        "where project = %s order by part, key", (_project(conn),))}


def name(conn, part: str, key: str, reason: str, owner: str, by: str, review_by: date | str | None = None) -> dict:
    """Name a deviation: why this project differs from its model at `part`/`key`, who owns the difference (a role
    or a person) and when it is next reviewed (`review_by`, by default REVIEW_DAYS from today; past it, the
    `deviation_reviews` detector raises work for the owner). Naming one that isn't a deviation yet is allowed (a
    bundle names what it is about to set)."""
    if part not in PARTS:
        raise OperatingModelError(f"no part {part!r}: one of {', '.join(PARTS)}")
    if not (key or "").strip() or not (reason or "").strip() or not (owner or "").strip():
        raise OperatingModelError("a named deviation needs a key, a reason and an owner")
    try:
        due = date.fromisoformat(str(review_by)[:10]) if review_by else date.today() + timedelta(days=REVIEW_DAYS)
    except ValueError:
        raise OperatingModelError(f"review_by {review_by!r} isn't a date (YYYY-MM-DD)") from None
    row = conn.execute(
        """
        insert into operating_model_deviation (project, part, key, reason, owner, named_by, review_by)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (project, part, key) do update set reason = excluded.reason, owner = excluded.owner,
            named_by = excluded.named_by, named_at = now(), review_by = excluded.review_by
        returning part, key, reason, owner, named_by, named_at, review_by
        """, (_project(conn), part, key.strip(), reason.strip(), owner.strip(), by, due)).fetchone()
    conn.commit()
    return row


def named(conn) -> list[dict]:
    """Every deviation this project has named: {part, key, reason, owner, named_by, named_at, review_by, overdue}."""
    today = date.today()
    return [{**r, "overdue": bool(r["review_by"] and r["review_by"] < today)} for r in _names(conn).values()]


def unname(conn, part: str, key: str) -> bool:
    n = conn.execute("delete from operating_model_deviation where project = %s and part = %s and key = %s",
                     (_project(conn), part, key)).rowcount
    conn.commit()
    return bool(n)


def _bundle_model(conn) -> dict:
    from ..core import projects

    ref = (projects.config(conn, _project(conn)).get("bundle") or {}).get("operating_model")
    if not ref:
        raise OperatingModelError("this project's bundle doesn't name an operating model")
    return ref


def _package_for(ref: dict) -> dict:
    if (ref["name"], ref["version"]) != (STANDARD_NAME, STANDARD_VERSION):
        raise OperatingModelError(f"operating model {ref['name']} v{ref['version']} isn't held here: only "
                                  f"{STANDARD_NAME} v{STANDARD_VERSION} ships; apply a package folder instead")
    return standard()


def conformance_package(conn) -> dict:
    """The package this project is held to: the operating model its bundle follows."""
    return _package_for(_bundle_model(conn))


def adopt(conn, by: str) -> dict:
    """Make this project run the operating model its bundle follows: apply it, set the bundle's settings deviations
    and name every deviation the bundle declares. What is left unnamed afterwards is the project's own. The shipped
    processes and forms load first, as the API's start loads them, so a project stood up from its bundle a moment ago
    adopts without the caller publishing the shipped definitions (which needs workflow_admin)."""
    from . import definitions

    ref = _bundle_model(conn)
    pkg = _package_for(ref)
    definitions.load_defaults(conn)
    done = apply(conn, pkg, by)
    project = _project(conn)
    for d in ref.get("deviations") or []:
        if d["part"] == "settings" and "value" in d:
            settings.set(conn, d["key"], d["value"], by=by, reason=d["reason"], project_name=project)
        name(conn, d["part"], d["key"], d["reason"], d["owner"], by, d.get("review_by"))
    conn.commit()
    return {**done, **conformance(conn, pkg)}


def conformance(conn, package: dict | str | Path | None = None) -> dict:
    """How this project stands against a package (by default the one its bundle follows): {model, version, applied
    (the package was the last one applied, unchanged since), deviations, named, unnamed, stale (named, but no longer
    a deviation)}."""
    pkg = resolve(package) if package is not None else conformance_package(conn)
    rows = deviations(conn, pkg)
    last = applied(conn)
    found = {(d["part"], d["key"]) for d in rows}
    return {"model": pkg["name"], "version": pkg["version"],
            "applied": bool(last and last["digest"] == digest(pkg)),
            "deviations": len(rows), "named": sum(1 for d in rows if d["owner"]),
            "unnamed": [{"part": d["part"], "key": d["key"], "change": d["change"]} for d in rows if not d["owner"]],
            "stale": [{"part": p, "key": k} for (p, k) in _names(conn) if (p, k) not in found]}


def apply(conn, package: dict | str | Path, by: str) -> dict:
    """Make this project run the package. Roles and work types it holds are upserted (others are kept, and show as
    `added` deviations); its settings are set and this project's other operating settings reset; its processes and
    forms that differ from what is published are imported and published. Records the version applied."""
    from . import definitions

    pkg = resolve(package)
    project = _project(conn)
    done = {p: 0 for p in PARTS}
    for name, r in sorted((pkg.get("roles") or {}).items()):
        done["roles"] += conn.execute(
            """
            insert into work_role (name, kind, description, default_sla, escalate_to, assignment, updated_by)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (name) do update set kind = excluded.kind, description = excluded.description,
                default_sla = excluded.default_sla, escalate_to = excluded.escalate_to,
                assignment = excluded.assignment, updated_by = excluded.updated_by, updated_at = now()
            where (work_role.kind, work_role.description, work_role.default_sla, work_role.escalate_to,
                   work_role.assignment) is distinct from (excluded.kind, excluded.description, excluded.default_sla,
                   excluded.escalate_to, excluded.assignment)
            """, (name, r["kind"], r.get("description"), r.get("default_sla"), r.get("escalate_to"),
                  r.get("assignment") or "queue", by)).rowcount
    for name, t in sorted((pkg.get("work_types") or {}).items()):
        done["work_types"] += conn.execute(
            """
            insert into exception_kind (name, label, role, process, case_type, audience, close_case_on_clear,
                                        description, joins, due_hours, updated_by)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (name) do update set label = excluded.label, role = excluded.role, process = excluded.process,
                case_type = excluded.case_type, audience = excluded.audience,
                close_case_on_clear = excluded.close_case_on_clear, description = excluded.description,
                joins = excluded.joins, due_hours = excluded.due_hours, updated_by = excluded.updated_by,
                updated_at = now()
            """, (name, t["label"], t.get("role"), t.get("process"), t.get("case_type"), list(t.get("audience") or []),
                  t.get("close_case_on_clear", True), t.get("description"), t.get("joins", True), t.get("due_hours"),
                  by)).rowcount
    want = pkg.get("settings") or {}
    have = snapshot(conn)["settings"]
    for key, value in sorted(want.items()):
        if key not in have or not _same(have[key], value):
            settings.set(conn, key, value, by=by, reason=f"operating model {pkg['name']} v{pkg['version']}",
                         project_name=project)
            done["settings"] += 1
    for key in sorted(set(have) - set(want)):
        settings.reset(conn, key, by=by, reason=f"operating model {pkg['name']} v{pkg['version']}", project_name=project)
        done["settings"] += 1
    current = snapshot(conn)
    for key, schema in sorted((pkg.get("forms") or {}).items()):
        if not _same(current["forms"].get(key), schema):
            definitions.import_form(conn, schema, by)
            done["forms"] += 1
    xmls = sorted(set((pkg.get("processes") or {}).values()), key=lambda x: "callActivity" in x)  # called first
    for xml in xmls:
        keys = [k for k, v in pkg["processes"].items() if v == xml]
        if all(current["processes"].get(k) == xml for k in keys):
            continue
        for row in definitions.import_bpmn(conn, xml, by):
            definitions.publish(conn, row["id"], by)
            done["processes"] += 1
    conn.execute(
        "insert into operating_model_applied (project, name, version, digest, applied_by) values (%s, %s, %s, %s, %s)",
        (project, pkg["name"], pkg["version"], digest(pkg), by))
    conn.commit()
    return {"name": pkg["name"], "version": pkg["version"], "digest": digest(pkg), "changed": done,
            "deviations": len(deviations(conn, pkg))}


def applied(conn) -> dict | None:
    """The operating model this project last applied: {name, version, digest, applied_by, applied_at}, or None."""
    return conn.execute(
        "select name, version, digest, applied_by, applied_at from operating_model_applied where project = %s "
        "order by id desc limit 1", (_project(conn),)).fetchone()
