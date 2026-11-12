"""The source pack contract (P1.5): what one system type brings to Fuzer, and the conformance every pack passes.

A source pack is one YAML file (`fuzer_source_pack: 1`, see `_catalog_packs` for its full layout):

    system:           the system type: name, kind, owner, timezone, date format, webhook secret reference
    connections:      how Fuzer reaches it: a connection template (secrets as references, never values)
    datasets:         what it exports, how each loads, the mapping in use and its quality checks
    exception_types:  the work types it raises, each with the role that owns it (they appear on Routing)
    exception_rules:  when a record raises that work
    detectors:        the registered detectors it brings, switched on when the pack is installed (Detection)
    fixture:          a folder of sample exports, relative to the pack file: the fake source the conformance
                      suite loads, and what a project without the real system can try it on

Write-back adapters, where a system accepts one, join the contract with the write-back queue (P5.7).

`install` puts a pack on a project in one step: its datasets on Sources, its work types on Routing and its
detectors on Detection. `conformance` is the suite every pack must pass, against a database it may write to:
the pack installs, installs again with nothing changed, its fixture loads through its own mappings with no
dataset held, failed or missing, and each detector it names is registered.
"""

from pathlib import Path

import yaml

from ._catalog_common import CatalogError

VERSION = 1


class PackError(CatalogError):
    pass


def read(path) -> tuple[str, dict, Path | None]:
    """A pack file: its text, the parsed pack and its fixture folder (None without one)."""
    path = Path(path)
    if not path.is_file():
        raise PackError(f"no source pack at {path}")
    text = path.read_text()
    try:
        pack = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PackError(f"{path} isn't YAML: {e}") from None
    if not isinstance(pack, dict) or pack.get("fuzer_source_pack") != VERSION:
        raise PackError(f"{path} is not a source pack (fuzer_source_pack: {VERSION})")
    fixture = pack.get("fixture")
    return text, pack, (path.parent / fixture).resolve() if fixture else None


def detectors_of(pack: dict) -> list[str]:
    """The detectors a pack names, each checked against the registry."""
    from ..core import detectors

    names = pack.get("detectors") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise PackError("detectors must be a list of registered detector names")
    known = detectors.registry()
    unknown = [n for n in names if n not in known]
    if unknown:
        raise PackError(f"the pack names detectors nobody registered: {', '.join(unknown)}")
    return names


def install(conn, text: str, by: str, reason: str | None = None, config: dict | None = None, data_dir=None,
            project_name: str | None = None) -> dict:
    """Install a pack on the project in one step: system, connections, datasets, mappings and checks (Sources),
    work types and rules (Routing), and its detectors switched on (Detection). Safe to repeat."""
    from ..core import detectors
    from . import _catalog_packs

    pack = yaml.safe_load(text or "") or {}
    names = detectors_of(pack) if isinstance(pack, dict) else []  # every name known before anything is written
    report = _catalog_packs.import_pack(conn, text, by, reason, config=config, data_dir=data_dir)
    switched = []
    for name in names:
        if detectors.on(conn, name, project_name):
            report.setdefault("unchanged", []).append(f"detector {name}")
            continue
        detectors.switch(conn, name, True, by=by, reason=reason or "installed with its source pack",
                         project_name=project_name)
        switched.append(name)
    if switched:
        report["detectors"] = switched
    return report


def conformance(conn, path, *, by: str = "conformance") -> dict:
    """Run the conformance suite for the pack at `path` on this connection's project. Returns
    {pack, system, problems, installed, loaded}; a pack conforms when `problems` is empty."""
    from . import ingest

    problems: list[str] = []
    try:
        text, pack, fixture = read(path)
    except PackError as e:
        return {"pack": str(path), "system": None, "problems": [str(e)], "installed": {}, "loaded": {}}
    system = (pack.get("system") or {}).get("name")
    out = {"pack": str(path), "system": system, "problems": problems, "installed": {}, "loaded": {}}
    datasets = [d for d in pack.get("datasets") or [] if isinstance(d, dict)]
    if not datasets:
        problems.append("the pack brings no datasets")
    for d in datasets:
        if not d.get("mappings") and not d.get("reader"):
            problems.append(f"dataset {d.get('name')!r} has no mapping or reader")
    named = {d.get("name") for d in datasets}
    for r in pack.get("exception_rules") or []:
        if isinstance(r, dict) and r.get("dataset") not in named:
            problems.append(f"rule {r.get('name')!r} reads dataset {r.get('dataset')!r}, which the pack doesn't bring")
    if fixture is None:
        problems.append("the pack has no fixture: a folder of sample exports to load")
    elif not fixture.is_dir():
        problems.append(f"its fixture folder {fixture} is missing")
    if problems:
        return out
    config = {c["name"]: {"path": str(fixture)} for c in pack.get("connections") or []
              if isinstance(c, dict) and c.get("type") == "folder"}
    try:
        out["installed"] = install(conn, text, by, config=config, data_dir=fixture)
        again = install(conn, text, by, config=config, data_dir=fixture)
    except CatalogError as e:
        problems.append(f"it doesn't install: {e}")
        return out
    if set(again) - {"unchanged"}:
        problems.append(f"installing it again changed {sorted(set(again) - {'unchanged'})}")
    try:
        loaded = ingest.ingest(conn, fixture, exports=sorted(named), trigger="api")
    except Exception as e:  # a failed load is recorded on the dataset's runs; here it is a problem with the pack
        conn.rollback()
        problems.append(f"its fixture doesn't load: {type(e).__name__}: {e}")
        return out
    out["loaded"] = {k: loaded.get(k) for k in ("loaded", "held", "missing", "records")}
    for outcome in ("held", "missing"):
        if loaded.get(outcome):
            problems.append(f"its fixture's datasets {outcome}: {', '.join(map(str, loaded[outcome]))}")
    if not loaded.get("records"):
        problems.append("its fixture loaded no records")
    return out
