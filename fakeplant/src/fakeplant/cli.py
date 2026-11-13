"""Command line: fakeplant generate | demo | drawings | journal | historian."""

import argparse
import json
import os
from pathlib import Path

from . import lake, okan

# All fake output lives here, one folder per asset (git-ignored).
DATA = Path(__file__).resolve().parents[2] / "data"
PACKS = {"okan": "okan"}


def main(argv=None):
    p = argparse.ArgumentParser(prog="fakeplant", description="Fake source exports for Fuzer.")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write fake source exports")
    g.add_argument("--out", default=None, help="output folder (default: fakeplant/data/okan)")
    d = sub.add_parser("demo", help="reset the Fuzer database, generate, ingest and rebuild")
    d.add_argument("--data", dest="out", default=None, help="output folder (default: fakeplant/data/okan)")
    d.add_argument("--pack", choices=["okan"], default="okan", help="asset pack")
    sc = sub.add_parser("schedules", help="Okan project schedules as a Delta table, a source with no mapping (#227)")
    sc.add_argument("--out", default="fakeplant/data/okan_lake", help="lake folder (the table is <out>/project_schedules)")
    sc.add_argument("--okan", default="fakeplant/data/okan", help="the Okan exports it is built from")
    sc.add_argument("--update", action="store_true", help="write version 1: progress moves, a finish slips, a row goes")
    sc.add_argument("--fixed", action="store_true", help="without the copied row (the planner's fix for the held load)")
    dw = sub.add_parser("drawings", help="draw a hand-written bundle's smart P&IDs and record where each tag sits")
    dw.add_argument("bundle", help="the bundle folder, e.g. fakeplant/bundles/kestrel")
    jr = sub.add_parser("journal", help="write a hand-written bundle's DCS alarm and event journal from its process model")
    jr.add_argument("bundle", help="the bundle folder, e.g. fakeplant/bundles/kestrel")
    jr.add_argument("--days", type=int, default=100, help="days of journal up to today")
    h = sub.add_parser("historian", help="serve a fake PI Web API and ADX over the fake plant's points")
    h.add_argument("--data", dest="out", default=None, help="exports folder (default: fakeplant/data/okan)")
    h.add_argument("--host", default="127.0.0.1")
    h.add_argument("--port", type=int, default=8100)
    for x in (g, d):
        x.add_argument("--format", choices=["csv", "parquet"], default="csv",
                       help="parquet writes each table as a lake-style folder of Parquet part files")
    args = p.parse_args(argv)
    args.out = getattr(args, "out", None) or str(DATA / "okan")

    if args.cmd == "generate":
        _print(generate(args))
    elif args.cmd == "drawings":
        from . import drawings

        _print(drawings.render(Path(args.bundle)))
    elif args.cmd == "journal":
        from . import journal

        _print(journal.for_bundle(Path(args.bundle), args.days))
    elif args.cmd == "schedules":
        from . import schedules

        if not (Path(args.okan) / "sap_work_orders.csv").is_file():
            raise SystemExit(f"No Okan exports in {args.okan}; run `fakeplant generate` first.")
        _print(schedules.write(Path(args.okan), Path(args.out), args.update, args.fixed))
    elif args.cmd == "historian":
        from . import historian

        if not Path(args.out).is_dir():
            raise SystemExit(f"No exports in {args.out}; run `fakeplant generate` first.")
        historian.main(args.out, args.host, args.port)
    else:
        from fuzer.core import db
        from fuzer.core.pack import load_pack
        from fuzer.ops import pipeline
        from fuzer.sources import ingest

        pack = load_pack(args.pack)
        with db.connect() as conn:
            db.reset(conn)
            _print({"generated": generate(args)})
            _print({"ingested": ingest.ingest(conn, args.out)})
            _print(pipeline.rebuild(conn, pack))
        if Path(args.out).resolve() != Path(os.environ.get("FUZER_DATA_DIR") or "data").resolve():
            print(f"\nPoint Fuzer at this data with FUZER_DATA_DIR={args.out} (in .env) for ingest, eval and jobs.")


def make(out) -> dict:
    """A fake plant in `out` with default settings (used by `fuzer up`)."""
    return generate(argparse.Namespace(out=str(out), format="csv"))


def generate(args) -> dict:
    out = Path(args.out)
    stats = okan.generate(out)
    if args.format == "parquet":
        stats["parquet_tables"] = lake.to_parquet(out)
    else:
        lake.clear_lake_folders(out)
    return stats


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))
