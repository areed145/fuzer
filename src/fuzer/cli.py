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
    g.add_argument("--scale", type=int, default=1)

    i = sub.add_parser("ingest", help="load source exports into Postgres")
    i.add_argument("--data", default="data/fake")

    sub.add_parser("rebuild", help="rerun resolve / golden / graph")

    d = sub.add_parser("demo", help="reset, generate, ingest and rebuild")
    d.add_argument("--data", default="data/fake")

    s = sub.add_parser("serve", help="run the API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    m = sub.add_parser("mcp", help="run the MCP server for AI agents (stdio by default)")
    m.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8001)

    sub.add_parser("reset", help="drop and recreate all tables")

    args = p.parse_args(argv)
    pack = load_pack(args.pack)

    if args.cmd == "generate":
        _print(fakedata.generate(args.out, args.seed, args.scale))
    elif args.cmd == "serve":
        import uvicorn

        db.database_url()  # start embedded Postgres before uvicorn
        uvicorn.run("fuzer.api:app", host=args.host, port=args.port)
    elif args.cmd == "mcp":
        from . import mcp_server

        mcp_server.main(http=args.http, host=args.host, port=args.port)
    else:
        with db.connect() as conn:
            if args.cmd == "reset":
                db.reset(conn)
            elif args.cmd == "ingest":
                db.init_schema(conn)
                _print({"records": ingest.ingest(conn, args.data)})
            elif args.cmd == "rebuild":
                _print(pipeline.rebuild(conn, pack))
            elif args.cmd == "demo":
                db.reset(conn)
                _print({"generated": fakedata.generate(args.data)})
                _print({"ingested": ingest.ingest(conn, args.data)})
                _print(pipeline.rebuild(conn, pack))


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


if __name__ == "__main__":
    main()
