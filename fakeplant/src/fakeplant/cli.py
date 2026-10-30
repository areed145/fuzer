"""Command line: fakeplant generate | demo."""

import argparse
import json
import os
from pathlib import Path

from . import lake, offshore

# All fake output lives here, one folder per asset (git-ignored).
DATA = Path(__file__).resolve().parents[2] / "data"


def main(argv=None):
    p = argparse.ArgumentParser(prog="fakeplant", description="Fake source exports for Fuzer.")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write fake source exports")
    g.add_argument("--out", default=None, help="output folder (default: fakeplant/data/<asset>)")
    d = sub.add_parser("demo", help="reset the Fuzer database, generate, ingest and rebuild")
    d.add_argument("--data", dest="out", default=None, help="output folder (default: fakeplant/data/<asset>)")
    d.add_argument("--pack", default=None, help="asset pack (default: $FUZER_PACK, or onshore_demo for --asset onshore)")
    for x in (g, d):
        x.add_argument("--seed", type=int, default=7)
        x.add_argument("--asset", choices=["offshore", "onshore"], default="offshore",
                       help="which fake asset to make (onshore pairs with the onshore_demo pack)")
        x.add_argument("--size", choices=list(offshore.SIZES), default="small",
                       help="small: hand-checked test asset; medium: about 1/10 of a real offshore asset")
        x.add_argument("--areas", type=int, help="override number of process areas (max 90)")
        x.add_argument("--trains", type=int, help="override equipment trains per area (1-10)")
        x.add_argument("--wells", type=int, help="override number of wells")
        x.add_argument("--logs", type=int, help="override number of operator log entries")
        x.add_argument("--format", choices=["csv", "parquet"], default="csv",
                       help="parquet writes each table as a lake-style folder of Parquet part files")
    args = p.parse_args(argv)
    args.out = args.out or str(DATA / args.asset)

    if args.cmd == "generate":
        _print(generate(args))
    else:
        from fuzer import db, ingest, pipeline
        from fuzer.pack import load_pack

        pack = load_pack(args.pack or ("onshore_demo" if args.asset == "onshore" else None))
        with db.connect() as conn:
            db.reset(conn)
            _print({"generated": generate(args)})
            _print({"ingested": ingest.ingest(conn, args.out)})
            _print(pipeline.rebuild(conn, pack))
        if Path(args.out).resolve() != Path(os.environ.get("FUZER_DATA_DIR") or "data").resolve():
            print(f"\nPoint Fuzer at this data with FUZER_DATA_DIR={args.out} (in .env) for ingest, eval and jobs.")


def make(out, asset: str = "offshore", size: str = "small") -> dict:
    """A fake plant in `out` with default settings (used by `fuzer up`)."""
    return generate(argparse.Namespace(out=str(out), asset=asset, seed=7, size=size, areas=None, trains=None,
                                       wells=None, logs=None, format="csv"))


def generate(args) -> dict:
    out = Path(args.out)
    if args.asset == "onshore":
        from . import onshore

        stats = onshore.generate(out)
    else:
        stats = offshore.generate(out, args.seed, size=args.size, areas=args.areas, trains=args.trains,
                                  wells=args.wells, logs=args.logs)
    if args.format == "parquet":
        stats["parquet_tables"] = lake.to_parquet(out)
    else:
        lake.clear_lake_folders(out)
    return stats


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))
