"""The source catalog over HTTP (#225): systems, connections, datasets, mapping versions, checks, rules, runs,
profiles, health and history; test and discover a connection, preview a mapping change, run, pause and
retire a dataset. Every change takes `by` (who) and `reason` (kept in the audit log)."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(prefix="/sources", route_class=AuthenticatedRoute)


def _data_dir() -> str:
    from .sources import _data_dir as d

    return d()


def _do(conn, fn, *args, **kwargs):
    """Run a catalog change: commit it, or turn a validation error into a 400."""
    from ..core.exceptions import ExceptionError
    from ..core.jsonsafe import plain
    from ..sources import catalog, impact, quality, rules

    try:
        out = fn(*args, **kwargs)
    except (catalog.CatalogError, quality.CheckError, rules.RuleError, impact.ImpactError, ExceptionError) as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e
    conn.commit()
    return plain(out)


class Change(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    by: str = "anonymous"
    reason: str | None = None


class Named(Change):
    name: str


class NewDataset(Named):
    system: str


class NewConnection(BaseModel):
    name: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    by: str = "anonymous"
    reason: str | None = None


class Text(BaseModel):
    yaml: str
    note: str | None = None
    by: str = "anonymous"


class Who(BaseModel):
    by: str = "anonymous"
    reason: str | None = None


class Retire(Who):
    retract: bool


class NewCheck(BaseModel):
    kind: str
    column: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    blocking: bool = False
    note: str | None = None
    by: str = "anonymous"


class Rule(BaseModel):
    config: dict[str, Any]
    by: str = "anonymous"
    reason: str | None = None


class Rank(Who):
    rank: int | None


class Impact(BaseModel):
    yaml: str | None = None
    version_id: int | None = None
    limit: int | None = None


# ---- the whole catalog -----------------------------------------------------------------------------

@router.get("/catalog")
def catalog_view(conn=Depends(get_conn)):
    """Systems with their connections (secrets shown only as resolving or not) and datasets, with health."""
    from ..core.jsonsafe import plain
    from ..sources import catalog, connections, quality

    conn.read_only = True
    health = {h["dataset"]: h for h in quality.health(conn)}
    ds = catalog.datasets(conn)
    last = {r["dataset_id"]: r["status"] for r in conn.execute(
        "select distinct on (dataset_id) dataset_id, status from source_run order by dataset_id, id desc")}
    data_dir = _data_dir()

    def present(d) -> bool:  # its data is there (a live system is taken as there)
        if d["connection_type"] not in (None, "folder"):
            return True
        try:
            return connections.connector(d, data_dir) is not None
        except Exception:
            return False

    systems = []
    for s in catalog.systems(conn):
        systems.append({**s, "connections": [{**c, "display": connections.display(c, data_dir),
                                              "secrets": {k: {"ref": v, "resolves": connections.resolve(v) is not None}
                                                          for k, v in (c["secrets"] or {}).items()}}
                                             for c in catalog.connections(conn, s["name"])],
                        "datasets": [{"name": n, "record_type": d["record_type"], "reader": d["reader"], "status": d["status"],
                                      "load_mode": d["load_mode"], "rows": d["rows"], "loaded_at": d["loaded_at"],
                                      "connection": d["connection_name"], "owner": d["owner"] or d["system_owner"],
                                      "schedule": d["schedule"], "last_run": last.get(d["id"]),
                                      "present": d["fingerprint"] is not None or present(d), "health": health.get(n)}
                                     for n, d in ds.items() if d["system_id"] == s["id"]]})
    return plain({"systems": systems, "types": _types(), "check_kinds": quality.CHECK_KINDS, "data_dir": data_dir,
                  "rebuild": quality.rebuild_status(conn)})


@router.get("/status")
def status(conn=Depends(get_conn)):
    """Whether a rebuild would apply changes (loads, mappings, rules), and whether one is running."""
    from ..sources import quality

    return {**quality.rebuild_status(conn), "running": deps.rebuilder.status()}


def _types() -> dict:
    from ..sources import catalog, connections

    return {"connections": {k: {"config": t.config, "secrets": list(t.secrets)} for k, t in connections.TYPES.items()},
            "load_modes": catalog.LOAD_MODES, "drift_policies": catalog.DRIFT_POLICIES, "statuses": catalog.STATUSES}


@router.get("/health")
def health(dataset: str | None = None, conn=Depends(get_conn)):
    from ..sources import quality

    return quality.health(conn, dataset)


@router.get("/audit")
def audit(object: str | None = None, object_id: int | None = None, limit: int = 100, conn=Depends(get_conn)):
    from ..sources import catalog

    return catalog.audit(conn, object, object_id, min(limit, 500))


@router.post("/seed")
def seed(conn=Depends(get_conn)):
    from ..sources import catalog

    return _do(conn, catalog.seed, conn)


@router.get("/export")
def export(names: str | None = None, conn=Depends(get_conn)):
    """The catalog as YAML files (name -> text), for review or another environment."""
    from ..sources import catalog

    return catalog.export(conn, names.split(",") if names else None)


@router.post("/import")
def import_(body: Text, conn=Depends(get_conn)):
    """A mapping file becomes a draft version; a systems.yaml updates systems and connections."""
    from ..sources import catalog

    return _do(conn, catalog.import_, conn, body.yaml, body.by, body.note)


class PackImport(BaseModel):
    yaml: str
    config: dict[str, dict[str, Any]] = Field(default_factory=dict)  # connection name -> config changes
    by: str = "anonymous"
    reason: str | None = None


@router.get("/systems/{name}/pack")
def export_pack(name: str, conn=Depends(get_conn)):
    """One system as a source pack: its connections, datasets, mappings in use and checks, in one YAML file."""
    from ..sources import catalog

    try:
        return {"system": name, "yaml": catalog.export_pack(conn, name)}
    except catalog.CatalogError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/packs")
def import_pack(body: PackImport, conn=Depends(get_conn)):
    """Install a source pack in one step: its system, connections, datasets, mappings and checks, the work types it
    raises and the detectors it brings (P1.5). Safe to repeat."""
    from ..sources import packs

    return _do(conn, packs.install, conn, body.yaml, body.by, body.reason, body.config, data_dir=_data_dir())


# ---- the work a system brings (#465) -----------------------------------------------------------

@router.get("/systems/{name}/workflows")
def system_workflows(name: str, conn=Depends(get_conn)):
    """The exception types a system brought (with how many are open), its rules over its datasets, and its webhook."""
    from ..sources import catalog, connections, workflows

    s = catalog.system(conn, name)
    if not s:
        raise HTTPException(404, f"no system {name!r}")
    ref = s.get("webhook_secret")
    try:
        ready = bool(connections.resolve(ref)) if ref else False
    except connections.ConnectionError_:
        ready = False
    return {"types": workflows.types_of(conn, name),
            "rules": [{**workflows.as_pack(r), "updated_by": r["updated_by"], "updated_at": r["updated_at"]}
                      for r in workflows.rules(conn, name)],
            "webhook": {"path": f"/hooks/{name}", "secret": ref, "ready": ready}}


class ExceptionTypeIn(BaseModel):
    label: str
    role: str | None = None
    process: str | None = None
    case_type: str | None = None
    audience: list[str] = Field(default_factory=lambda: ["ioc"])
    close_case_on_clear: bool = True
    description: str | None = None
    joins: bool = True
    by: str = "anonymous"


@router.put("/systems/{name}/exception-types/{type_name}")
def system_exception_type(name: str, type_name: str, body: ExceptionTypeIn, conn=Depends(get_conn)):
    """Define (or change) an exception type the system brings."""
    from ..sources import workflows

    values = body.model_dump(exclude={"by"})
    _do(conn, workflows.define_types, conn, name, [{"name": type_name, **values}], body.by)
    return next(t for t in workflows.types_of(conn, name) if t["name"] == type_name)


class RuleIn(BaseModel):
    dataset: str
    type: str
    when: dict[str, Any]
    clear_when: dict[str, Any] | None = None
    key: str | None = None
    refs: list[Any] = Field(default_factory=list)
    title: str | None = None
    priority: float | None = None
    value_usd: float | str | None = None
    enabled: bool = True
    by: str = "anonymous"


@router.put("/systems/{name}/rules/{rule}")
def system_rule(name: str, rule: str, body: RuleIn, conn=Depends(get_conn)):
    """Add or change a rule that raises exceptions from the system's loaded records; it runs now and after each load."""
    from ..sources import workflows

    row = _do(conn, workflows.set_rule, conn, name, rule, body.by, **body.model_dump(exclude={"by"}))
    ran = _do(conn, workflows.evaluate, conn, [row["dataset"]])
    return {**workflows.as_pack(row), "ran": ran.get(f"{name}/{rule}")}


@router.delete("/systems/{name}/rules/{rule}")
def system_rule_remove(name: str, rule: str, conn=Depends(get_conn)):
    """Remove a rule (the exceptions it raised stay until cleared or their case is done)."""
    from ..sources import workflows

    if not _do(conn, workflows.remove_rule, conn, name, rule):
        raise HTTPException(404, f"{name} has no rule {rule!r}")
    return {"removed": rule}


# ---- systems and connections -------------------------------------------------------------------

@router.post("/systems")
def add_system(body: Named, conn=Depends(get_conn)):
    """A system with no connection yet (add the one it needs: the project data folder, another folder, a server)."""
    from ..sources import catalog

    return _do(conn, catalog.add_system, conn, body.name, body.by, body.reason, files=False, **body.values)


@router.patch("/systems/{name}")
def edit_system(name: str, body: Change, conn=Depends(get_conn)):
    from ..sources import catalog

    return _do(conn, catalog.update_system, conn, name, body.by, body.reason, **body.values)


def _folder_exists(type_: str, config: dict) -> None:
    """A folder connection must point at a folder that is there (a typo otherwise only shows up later)."""
    from ..sources import connections

    if type_ == "folder":
        try:
            connections.Folder(config or {}, {}).check_path(Path(_data_dir()))
        except connections.ConnectionError_ as e:
            raise HTTPException(400, str(e)) from e


@router.post("/systems/{name}/connections")
def add_connection(name: str, body: NewConnection, conn=Depends(get_conn)):
    from ..sources import catalog

    _folder_exists(body.type, body.config)
    return _do(conn, catalog.add_connection, conn, name, body.name, body.type, body.by, body.config, body.secrets, body.reason)


@router.patch("/connections/{cid}")
def edit_connection(cid: int, body: Change, conn=Depends(get_conn)):
    from ..sources import catalog

    old = catalog.connection(conn, cid) or {}
    if "config" in body.values:
        _folder_exists(body.values.get("type", old.get("type")), body.values["config"])

    return _do(conn, catalog.update_connection, conn, cid, body.by, body.reason, **body.values)


@router.post("/connections/{cid}/test")
def test_connection(cid: int, rows: int = 5, conn=Depends(get_conn)):
    from ..sources import connections

    return _do(conn, connections.test, conn, cid, _data_dir(), min(max(rows, 1), 50))


@router.get("/connections/{cid}/discover")
def discover(cid: int, conn=Depends(get_conn)):
    from ..sources import catalog, connections

    try:
        return connections.discover(conn, cid, _data_dir())
    except catalog.CatalogError as e:
        raise HTTPException(404, str(e)) from e


class AddTable(BaseModel):
    yaml: str
    values: dict[str, Any] = Field(default_factory=dict)  # load_mode, schedule, expected_hours, owner, ...
    by: str = "anonymous"
    load: bool = True


@router.get("/connections/{cid}/tables")
def connection_tables(cid: int, conn=Depends(get_conn)):
    """What a connection holds, and which dataset (if any) reads each table: new ones first."""
    from ..sources import onboard

    try:
        return onboard.tables(conn, cid, _data_dir())
    except onboard.OnboardError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/connections/{cid}/tables/{table}")
def connection_table(cid: int, table: str, claude: bool = False, dataset: str | None = None,
                     record_type: str | None = None, conn=Depends(get_conn)):
    """A table on a connection: sample, columns, a date-format hint and a mapping suggested for its system."""
    from ..sources import onboard

    try:
        return onboard.preview(conn, deps.pack(), cid, table, _data_dir(), claude=claude, dataset=dataset,
                               record_type=record_type)
    except onboard.OnboardError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/connections/{cid}/tables/{table}")
def add_table(cid: int, table: str, body: AddTable, conn=Depends(get_conn)):
    """Save a table as a dataset on this connection with the mapping (approved), and load it."""
    from ..sources import catalog, connections, ingest, onboard

    try:
        return onboard.add(conn, deps.pack(), cid, table, body.yaml, body.by, _data_dir(), body.values, body.load)
    except (ingest.DatasetNotFound, ingest.DatasetUnavailable) as e:
        conn.rollback()
        raise HTTPException(404, str(e)) from e
    except connections.ConnectionError_ as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e
    except (onboard.OnboardError, catalog.CatalogError) as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e


# ---- datasets ----------------------------------------------------------------------------------

@router.post("/datasets")
def add_dataset(body: NewDataset, conn=Depends(get_conn)):
    from ..sources import catalog

    return _do(conn, catalog.add_dataset, conn, body.name, body.system, body.by, body.reason, **body.values)


@router.get("/datasets/{name}")
def dataset_detail(name: str, conn=Depends(get_conn)):
    """One dataset with everything the Sources page tabs show: connection, mapping versions (with YAML), runs,
    latest profiles, checks, its source's match statistics, health and change history."""
    from ..core.jsonsafe import plain
    from ..sources import catalog, connections, quality

    d = catalog.dataset(conn, name)
    if not d:
        raise HTTPException(404, f"no dataset {name!r}")
    vers = catalog.versions_for_dataset(conn, d)
    current = {(m["source"], m["record_type"]): m["id"] for m in catalog.current_mappings(conn)}
    return plain({
        "dataset": {k: v for k, v in d.items() if k == "connection_id" or not k.startswith(("connection_", "system_"))},
        "connection": catalog.connection(conn, d["connection_id"]) if d["connection_id"] else None,
        "secrets": connections.resolves(d["connection_secrets"] or {}),
        "mappings": [{**v, "current": current.get((v["source"], v["record_type"])) == v["id"]} for v in vers],
        "runs": catalog.runs(conn, name, 50),
        "profiles": quality.profiles(conn, name, 5),
        "checks": quality.checks(conn, name),
        "matching": quality.matching(conn, d["source"], 20),
        "health": (quality.health(conn, name) or [None])[0],
        "connection_display": connections.display(catalog.connection(conn, d["connection_id"]) or {}, _data_dir())
        if d["connection_id"] else None,
        "next_load": quality.next_load(d["schedule"]),
        "suggested_checks": quality.suggest_checks(conn, name),
        "work_items": [dict(r) for r in conn.execute(
            "select id, title, state, assignee, role, created_at from work_item where type = 'source_issue' "
            "and coalesce(payload->>'dataset', payload->'start'->>'dataset') = %s and state not in ('done', 'cancelled') "
            "order by id desc", (name,))],
        "reviews": [dict(r) for r in conn.execute(
            "select id, kind, created_at from review_item where subject = %s and kind in ('schema', 'check') "
            "and status = 'open'", (name,))],
        "history": catalog.audit(conn, "dataset", d["id"], 100),
        "retire_preview": catalog.retire_preview(conn, name),
    })


@router.patch("/datasets/{name}")
def edit_dataset(name: str, body: Change, conn=Depends(get_conn)):
    from ..sources import catalog

    return _do(conn, catalog.update_dataset, conn, name, body.by, body.reason, **body.values)


@router.post("/datasets/{name}/run")
def run_dataset(name: str, full: bool = False, conn=Depends(get_conn)):
    """Load one dataset now (its records become entities on the next rebuild)."""
    from ..sources import connections, ingest

    try:
        return ingest.ingest(conn, _data_dir(), exports=[name], full=full, trigger="api")
    except (ingest.DatasetNotFound, ingest.DatasetUnavailable) as e:
        conn.rollback()
        raise HTTPException(404, str(e)) from e
    except connections.ConnectionError_ as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e


@router.post("/datasets/{name}/pause")
def pause(name: str, body: Who, conn=Depends(get_conn)):
    from ..sources import catalog

    return _do(conn, catalog.pause, conn, name, body.by, body.reason)


@router.post("/datasets/{name}/resume")
def resume(name: str, body: Who, conn=Depends(get_conn)):
    from ..sources import catalog

    return _do(conn, catalog.resume, conn, name, body.by, body.reason)


@router.post("/datasets/{name}/retire")
def retire(name: str, body: Retire, conn=Depends(get_conn)):
    from ..sources import catalog

    return _do(conn, catalog.retire, conn, name, body.by, body.retract, body.reason)


@router.delete("/datasets/{name}")
def delete_dataset(name: str, by: str = "anonymous", reason: str | None = None, conn=Depends(get_conn)):
    """Undo adding a dataset: its records, its mapping (when nothing else uses it) and the dataset go."""
    from ..sources import catalog

    return _do(conn, catalog.delete_dataset, conn, name, by, reason)


@router.delete("/connections/{cid}")
def delete_connection(cid: int, by: str = "anonymous", reason: str | None = None, conn=Depends(get_conn)):
    from ..sources import catalog

    return _do(conn, catalog.delete_connection, conn, cid, by, reason)


@router.delete("/systems/{name}")
def delete_system(name: str, by: str = "anonymous", reason: str | None = None, conn=Depends(get_conn)):
    from ..sources import catalog

    return _do(conn, catalog.delete_system, conn, name, by, reason)


@router.post("/datasets/{name}/profile")
def profile(name: str, conn=Depends(get_conn)):
    from ..sources import quality

    return _do(conn, quality.profile_now, conn, name, _data_dir())


@router.post("/datasets/{name}/checks")
def add_check(name: str, body: NewCheck, conn=Depends(get_conn)):
    from ..sources import quality

    return _do(conn, quality.add_check, conn, name, body.kind, body.by, body.column, body.params, body.blocking, body.note)


@router.delete("/checks/{check_id}")
def remove_check(check_id: int, by: str = "anonymous", conn=Depends(get_conn)):
    from ..sources import quality

    _do(conn, quality.remove_check, conn, check_id, by)
    return {"removed": check_id}


# ---- mapping versions --------------------------------------------------------------------------

@router.post("/mappings")
def propose(body: Text, conn=Depends(get_conn)):
    """A new draft mapping version (approve it to use it)."""
    from ..sources import catalog

    return _do(conn, catalog.propose, conn, body.yaml, body.by, body.note)


@router.post("/mappings/{version_id}/approve")
def approve(version_id: int, body: Who, conn=Depends(get_conn)):
    from ..sources import catalog

    return _do(conn, catalog.approve, conn, version_id, body.by, body.reason)


@router.post("/mappings/{version_id}/discard")
def discard(version_id: int, body: Who, conn=Depends(get_conn)):
    """Set a draft aside without using it."""
    from ..sources import catalog

    return _do(conn, catalog.discard, conn, version_id, body.by, body.reason)


@router.post("/mappings/{version_id}/rollback")
def rollback(version_id: int, body: Who, conn=Depends(get_conn)):
    from ..sources import catalog

    return _do(conn, catalog.rollback, conn, version_id, body.by, body.reason)


@router.post("/impact")
def impact_preview(body: Impact, conn=Depends(get_conn)):
    """What a mapping (YAML or a stored version) would change against the one in use, before it's approved."""
    from ..sources import impact

    try:
        return impact.preview(conn, deps.pack(), body.yaml, body.version_id, body.limit)
    except impact.ImpactError as e:
        raise HTTPException(400, str(e)) from e


class MapperForm(BaseModel):
    yaml: str
    columns: list[str] | None = None
    dataset: str | None = None


class MapperYaml(BaseModel):
    form: dict[str, Any]
    yaml: str


@router.post("/mapper/form")
def mapper_form(body: MapperForm, conn=Depends(get_conn)):
    """A mapping as the column form; with a dataset, its columns from the latest profile, with samples."""
    from ..sources import catalog, mapper, quality

    columns, samples = body.columns, {}
    if body.dataset and (p := (quality.profiles(conn, body.dataset, 1) or [None])[0]):
        columns = columns or list(p["columns"])
        samples = {k: {"samples": v.get("samples"), "type": v.get("type"), "fill_pct": v.get("fill_pct")}
                   for k, v in p["columns"].items()}
    elif body.dataset and (d := catalog.dataset(conn, body.dataset)) and d["record_type"]:
        # loaded before profiles were kept: its columns from the last load, samples from stored records
        columns = columns or [c[0] for c in d["schema"] or []]
        recs = [r["payload"] for r in conn.execute(
            "select payload from source_record where source = %s and record_type = %s limit 3", (d["source"], d["record_type"]))]
        columns = columns or list(dict.fromkeys(k for r in recs for k in r))
        samples = {k: {"samples": [str(r[k]) for r in recs if r.get(k) not in (None, "")][:3]} for k in columns or []}
    try:
        return {**mapper.form(body.yaml, columns), "profile": samples, "warnings": ontology_warnings(conn, body.yaml)}
    except mapper.MapperError as e:
        raise HTTPException(400, str(e)) from e


def ontology_warnings(conn, text: str) -> list[str]:
    """What the mapping makes that the ontology doesn't declare or allow (#241)."""
    from ..core import ontology
    from ..sources import catalog, mapper

    current = None

    def ref_kind(ref: str) -> str | None:
        nonlocal current
        if current is None:
            current = {f"{m['source']}:{m['record_type']}": m["yaml"] for m in catalog.current_mappings(conn)}
        made = mapper.produces(current.get(ref, ""))
        return made[0][1] if made and not made[0][0] else None
    return ontology.check_mapping(conn, mapper.produces(text, ref_kind))


@router.post("/mapper/yaml")
def mapper_yaml(body: MapperYaml, conn=Depends(get_conn)):
    """The column form written over the mapping: everything the form doesn't show is kept."""
    from ..sources import catalog, mapper

    try:
        text = mapper.to_yaml(body.form, body.yaml)
    except (mapper.MapperError, catalog.CatalogError) as e:
        raise HTTPException(400, str(e)) from e
    return {"yaml": text, "warnings": ontology_warnings(conn, text)}


# ---- rules, matching, lineage --------------------------------------------------------------------

@router.get("/rules")
def rules_list(conn=Depends(get_conn)):
    from ..sources import rules

    rules.seed(conn, deps.pack())
    conn.commit()
    return rules.listing(conn)


@router.put("/rules/{scope}/{name}")
def set_rule(scope: str, name: str, body: Rule, conn=Depends(get_conn)):
    from ..sources import rules

    return _do(conn, rules.set_rule, conn, scope, name, body.config, body.by, body.reason)


@router.delete("/rules/{scope}/{name}")
def delete_rule(scope: str, name: str, by: str = "anonymous", conn=Depends(get_conn)):
    from ..sources import rules

    _do(conn, rules.delete_rule, conn, scope, name, by)
    return {"deleted": f"{scope}:{name}"}


@router.put("/systems/{name}/rank")
def set_rank(name: str, body: Rank, conn=Depends(get_conn)):
    from ..sources import rules

    return _do(conn, rules.set_rank, conn, name, body.rank, body.by, body.reason)


@router.get("/matching")
def matching(source: str | None = None, conn=Depends(get_conn)):
    from ..sources import quality

    return quality.matching(conn, source)


@router.get("/why/{fuzer_id}/{field}")
def why(fuzer_id: str, field: str, conn=Depends(get_conn)):
    """Why an entity's field says what it says: rule, records, mapping versions and runs."""
    from ..sources import impact

    try:
        return impact.why(conn, deps.pack(), fuzer_id, field)
    except impact.ImpactError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/rematch")
def rematch():
    """Re-derive entities after a mapping or rule change (a rebuild in the background)."""
    return deps.rebuilder.request()
