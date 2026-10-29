"""Command line: fuzer demo | generate | ingest | rebuild | serve | reset."""

import argparse
import json

from . import db, fakedata, ingest, pipeline
from .pack import load_pack


def main(argv=None):
    p = argparse.ArgumentParser(prog="fuzer")
    p.add_argument("--pack", default="offshore_demo")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="write fake source exports")
    g.add_argument("--out", default="data/fake")
    g.add_argument("--seed", type=int, default=7)
    _size_args(g)

    i = sub.add_parser("ingest", help="load source exports into Postgres")
    i.add_argument("--data", default="data/fake")

    sub.add_parser("rebuild", help="rerun resolve / golden / graph")

    d = sub.add_parser("demo", help="reset, generate, ingest and rebuild")
    d.add_argument("--data", default="data/fake")
    d.add_argument("--seed", type=int, default=7)
    _size_args(d)

    s = sub.add_parser("serve", help="run the API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    lk = sub.add_parser("llm-link", help="have Claude read operator logs (links + events), then rebuild")
    lk.add_argument("--limit", type=int, help="only this many entries (try a small sample first)")
    lk.add_argument("--model", default=None, help="Claude model id (default claude-opus-5-5 or $FUZER_LLM_MODEL)")
    lk.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    lk.add_argument("--per-request", type=int, default=20, help="log entries per request")
    lk.add_argument("--workers", type=int, default=4, help="parallel requests")
    lk.add_argument("--batch", action="store_true", help="use the Batches API: half price, results within ~1 hour")
    lk.add_argument("--force", action="store_true", help="re-read entries that already have a result")
    lk.add_argument("--dry-run", action="store_true", help="show how much would be sent, call nothing")
    lk.add_argument("--no-rebuild", action="store_true")

    m = sub.add_parser("mcp", help="run the MCP server for AI agents (stdio by default)")
    m.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8001)

    sub.add_parser("reset", help="drop and recreate all tables")

    args = p.parse_args(argv)
    pack = load_pack(args.pack)

    if args.cmd == "generate":
        _print(fakedata.generate(args.out, args.seed, **_size_kwargs(args)))
    elif args.cmd == "serve":
        import uvicorn

        db.database_url()  # start embedded Postgres before uvicorn
        uvicorn.run("fuzer.api:app", host=args.host, port=args.port)
    elif args.cmd == "mcp":
        from . import mcp_server

        mcp_server.main(http=args.http, host=args.host, port=args.port)
    else:
        with db.connect() as conn:
            if args.cmd != "reset":
                db.init_schema(conn)  # idempotent; picks up new tables without a reset
            if args.cmd == "reset":
                db.reset(conn)
            elif args.cmd == "ingest":
                _print({"records": ingest.ingest(conn, args.data)})
            elif args.cmd == "llm-link":
                from . import llm_links

                opts = {k: v for k, v in {"model": args.model, "effort": args.effort}.items() if v}
                result = llm_links.run(conn, pack, limit=args.limit, per_request=args.per_request,
                                       workers=args.workers, batch=args.batch, force=args.force,
                                       dry_run=args.dry_run, **opts)
                _print(result)
                if not (args.dry_run or args.no_rebuild) and result.get("stored"):
                    _print(pipeline.rebuild(conn, pack))
            elif args.cmd == "rebuild":
                _print(pipeline.rebuild(conn, pack))
            elif args.cmd == "demo":
                db.reset(conn)
                _print({"generated": fakedata.generate(args.data, args.seed, **_size_kwargs(args))})
                _print({"ingested": ingest.ingest(conn, args.data)})
                _print(pipeline.rebuild(conn, pack))


def _size_args(p):
    p.add_argument("--size", choices=list(fakedata.SIZES), default="small",
                   help="small: hand-checked test asset; medium: about 1/10 of a real offshore asset")
    p.add_argument("--areas", type=int, help="override number of process areas (max 90)")
    p.add_argument("--trains", type=int, help="override equipment trains per area (1-10)")
    p.add_argument("--wells", type=int, help="override number of wells")
    p.add_argument("--logs", type=int, help="override number of operator log entries")


def _size_kwargs(args) -> dict:
    return {"size": args.size, "areas": args.areas, "trains": args.trains, "wells": args.wells, "logs": args.logs}


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


if __name__ == "__main__":
    main()
