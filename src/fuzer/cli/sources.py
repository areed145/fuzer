"""`fuzer sources ...` (#225): the source catalog from the command line. Every change is audited with --by."""

import os
from pathlib import Path

import yaml

ACTIONS = {
    "list": "datasets in the catalog and tables in the data folder no dataset covers, with their state",
    "show": "one dataset: system, connection, mapping versions, recent runs and changes",
    "preview": "sample rows, schema drift and what the rows would become",
    "seed": "import the built-in mapping files (never overwrites what a person changed)",
    "export": "write the catalog as YAML (mapping files and systems.yaml), for review and other environments",
    "import": "a mapping file becomes a draft version; systems.yaml updates systems and connections",
    "approve": "make a draft mapping version the one in use",
    "rollback": "put an older mapping version back in use (as a new version)",
    "run": "load datasets now (all active ones, or the named ones)",
    "pause": "stop loading a dataset; its records stay",
    "resume": "start loading a paused dataset again",
    "retire": "stop loading a dataset for good: --retract removes its records, --keep keeps them as history",
    "edit": "change a dataset (--set field=value): load_mode, drift_policy, schedule, key, location, ...",
    "add-dataset": "add a dataset to a system (--set field=value)",
    "add-system": "add a system of record (--set field=value)",
    "edit-system": "change a system (--set field=value): owner, timezone, decimal, date_format, rank, ...",
    "add-connection": "add a connection to a system: NAME TYPE (--config k=v, --secret k=env:NAME)",
    "edit-connection": "change a connection by id (--config k=v, --secret k=env:NAME, --set enabled=false)",
    "connections": "the connections, and whether their secrets resolve",
    "test": "test a connection by id: reach, authenticate, list, read a few rows",
    "discover": "what a connection can see, and what no dataset covers yet",
    "runs": "load history (of one dataset, or all)",
    "audit": "catalog change history",
}


def add_parser(sub) -> None:
    so = sub.add_parser("sources", aliases=["source"],
                        help="the source catalog (#225): systems, connections, datasets, mappings, runs")
    so.add_argument("action", choices=list(ACTIONS), help="; ".join(f"{k}: {v}" for k, v in ACTIONS.items()))
    so.add_argument("names", nargs="*", help="dataset, system, connection id or file names, as the action needs")
    so.add_argument("--data", default=None, help="the project's data folder (default: $FUZER_DATA_DIR or data)")
    so.add_argument("--rows", type=int, default=5, help="preview, test: sample rows")
    so.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE", help="field values (YAML scalars)")
    so.add_argument("--config", action="append", default=[], metavar="KEY=VALUE", help="connection config")
    so.add_argument("--secret", action="append", default=[], metavar="KEY=env:NAME", help="connection secret reference")
    so.add_argument("--out", default=None, help="export: folder to write the files to (default: print them)")
    so.add_argument("--full", action="store_true", help="run: read every dataset again, even unchanged ones")
    so.add_argument("--retract", action="store_true", help="retire: remove its records")
    so.add_argument("--keep", action="store_true", help="retire: keep its records as history")
    so.add_argument("--dry-run", action="store_true", help="retire: only show what would go")
    so.add_argument("--reason", default=None, help="why (kept in the audit log)")
    so.add_argument("--by", default=os.environ.get("USER", "cli"))


def _pairs(items: list[str]) -> dict:
    out = {}
    for item in items:
        k, sep, v = item.partition("=")
        if not sep:
            raise SystemExit(f"expected FIELD=VALUE, got {item!r}")
        out[k.strip()] = yaml.safe_load(v) if v.strip() else None
    return out


def _need(args, n: int, what: str) -> list[str]:
    if len(args.names) < n:
        raise SystemExit(f"sources {args.action} needs {what}")
    return args.names


def run(conn, pack, data: Path, args) -> object:
    from ..core.jsonsafe import plain
    from ..sources import catalog, connections, ingest, preview

    a, by, why = args.action, args.by, args.reason
    if a != "seed":
        catalog.ensure(conn)
    try:
        if a == "list":
            return preview.listing(conn, data)
        if a == "show":
            return show(conn, _need(args, 1, "a dataset")[0])
        if a == "preview":
            return preview.preview(conn, pack, data, _need(args, 1, "a dataset")[0], args.rows)
        if a == "seed":
            out = catalog.seed(conn)
        elif a == "export":
            files = catalog.export(conn, args.names or None)
            if not args.out:
                return "\n---\n".join(f"# {k}\n{v}" for k, v in files.items())
            Path(args.out).mkdir(parents=True, exist_ok=True)
            for k, v in files.items():
                (Path(args.out) / k).write_text(v)
            return {"written": sorted(files), "to": args.out}
        elif a == "import":
            out = [catalog.import_(conn, Path(f).read_text(), by, why) for f in _need(args, 1, "a file")]
        elif a == "approve":
            out = catalog.approve(conn, int(_need(args, 1, "a version id")[0]), by, why)
        elif a == "rollback":
            out = catalog.rollback(conn, int(_need(args, 1, "a version id")[0]), by, why)
        elif a == "run":
            return ingest.ingest(conn, data, args.names or None, full=args.full)
        elif a in ("pause", "resume"):
            out = getattr(catalog, a)(conn, _need(args, 1, "a dataset")[0], by, why)
        elif a == "retire":
            name = _need(args, 1, "a dataset")[0]
            if args.dry_run:
                return catalog.retire_preview(conn, name)
            if args.retract == args.keep:
                raise SystemExit("retire needs --retract (remove its records) or --keep (keep them as history)")
            out = catalog.retire(conn, name, by, retract=args.retract, reason=why)
        elif a == "edit":
            out = catalog.update_dataset(conn, _need(args, 1, "a dataset")[0], by, why, **_pairs(args.set))
        elif a == "add-dataset":
            name, system = _need(args, 2, "a dataset name and its system")[:2]
            out = catalog.add_dataset(conn, name, system, by, why, **_pairs(args.set))
        elif a == "add-system":
            out = catalog.add_system(conn, _need(args, 1, "a system name")[0], by, why, **_pairs(args.set))
        elif a == "edit-system":
            out = catalog.update_system(conn, _need(args, 1, "a system name")[0], by, why, **_pairs(args.set))
        elif a == "add-connection":
            system, name, type_ = _need(args, 3, "a system, a connection name and its type")[:3]
            out = catalog.add_connection(conn, system, name, type_, by, _pairs(args.config),
                                         {k: str(v) for k, v in _pairs(args.secret).items()}, why)
        elif a == "edit-connection":
            cid = int(_need(args, 1, "a connection id")[0])
            old = catalog.connection(conn, cid) or {}
            vals = _pairs(args.set)
            if args.config:
                vals["config"] = {**(old.get("config") or {}), **_pairs(args.config)}
            if args.secret:
                vals["secrets"] = {**(old.get("secrets") or {}), **{k: str(v) for k, v in _pairs(args.secret).items()}}
            out = catalog.update_connection(conn, cid, by, why, **vals)
        elif a == "connections":
            return [{**plain(c), "secrets": connections.resolves(c["secrets"])} for c in catalog.connections(
                conn, args.names[0] if args.names else None)]
        elif a == "test":
            out = connections.test(conn, int(_need(args, 1, "a connection id")[0]), data, args.rows)
        elif a == "discover":
            return connections.discover(conn, int(_need(args, 1, "a connection id")[0]), data)
        elif a == "runs":
            return catalog.runs(conn, args.names[0] if args.names else None)
        elif a == "audit":
            return catalog.audit(conn, args.names[0] if args.names else None)
    except catalog.CatalogError as e:
        conn.rollback()
        raise SystemExit(str(e)) from None
    conn.commit()
    return plain(out)


def show(conn, name: str) -> dict:
    from ..core.jsonsafe import plain
    from ..sources import catalog, connections

    d = catalog.dataset(conn, name)
    if not d:
        raise SystemExit(f"no dataset {name!r} (fuzer sources list shows them)")
    vers = [v for s, t in catalog.produces(d) for v in catalog.versions(conn, s, t)]
    return plain({
        **{k: v for k, v in d.items() if not k.startswith("connection_") and k != "schema"},
        "connection": {"id": d["connection_id"], "name": d["connection_name"], "type": d["connection_type"],
                       "secrets": connections.resolves(d["connection_secrets"] or {})},
        "mappings": [{k: v[k] for k in ("id", "source", "record_type", "status", "author", "note", "created_at",
                                        "approved_by")} for v in vers],
        "runs": catalog.runs(conn, name, 10),
        "changes": catalog.audit(conn, "dataset", d["id"], 20),
    })
