"""Command line: fuzer ingest | rebuild | serve | mcp | jobs | eval | reset ... (fake data: `fakeplant`)."""

import argparse
import json

from . import db, ingest, pipeline
from .pack import load_pack


def main(argv=None):
    p = argparse.ArgumentParser(prog="fuzer")
    p.add_argument("--pack", default=None, help="asset pack (default: $FUZER_PACK or offshore_demo)")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("ingest", help="load source exports into Postgres")
    i.add_argument("--data", default="data/fake")
    i.add_argument("--full", action="store_true", help="read every dataset again, even unchanged ones")

    sub.add_parser("rebuild", help="rerun resolve / golden / graph")

    sm = sub.add_parser("suggest-mappings", help="draft mappings for tables that have none (review queue)")
    sm.add_argument("--data", default="data/fake")
    sm.add_argument("--table", action="append", help="only this table (repeatable); default: every unmapped table")
    sm.add_argument("--claude", action="store_true", help="ask Claude about columns rules can't name (costs money)")

    s = sub.add_parser("serve", help="run the API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    lk = sub.add_parser("llm-link", help="have Claude read operator logs and scanned P&IDs, then rebuild")
    lk.add_argument("--source", choices=["logs", "pids", "docs", "all"], default="all")
    lk.add_argument("--limit", type=int, help="only this many entries (try a small sample first)")
    lk.add_argument("--model", default=None, help="Claude model id (default claude-opus-5-5 or $FUZER_LLM_MODEL)")
    lk.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    lk.add_argument("--per-request", type=int, default=20, help="log entries per request")
    ps = sub.add_parser("pid-score", help="score image reading against the fake data's answer key")
    ps.add_argument("--truth", default="data/fake/pid_truth.json")
    lk.add_argument("--workers", type=int, default=4, help="parallel requests")
    lk.add_argument("--batch", action="store_true", help="use the Batches API: half price, results within ~1 hour")
    lk.add_argument("--force", action="store_true", help="re-read entries that already have a result")
    lk.add_argument("--dry-run", action="store_true", help="show how much would be sent, call nothing")
    lk.add_argument("--no-rebuild", action="store_true")

    m = sub.add_parser("mcp", help="run the MCP server for AI agents (stdio by default)")
    m.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8001)

    j = sub.add_parser("jobs", help="run the Dagster UI with schedules and sensors (needs the 'jobs' extra)")
    j.add_argument("--port", type=int, default=3000)

    sub.add_parser("bench", help="time the main read queries against the current database")
    ev = sub.add_parser("eval", help="score search, document links, section failure modes and log reading")
    ev.add_argument("--keys", default="data/fake/answer_keys", help="folder of answer-key JSON files")

    sub.add_parser("reset", help="drop and recreate all tables")

    args = p.parse_args(argv)
    pack = load_pack(args.pack)

    if args.cmd == "serve":
        import uvicorn

        db.database_url()  # start embedded Postgres before uvicorn
        uvicorn.run("fuzer.api:app", host=args.host, port=args.port)
    elif args.cmd == "jobs":
        import os
        import subprocess
        import sys
        from pathlib import Path

        home = Path(os.environ.setdefault("DAGSTER_HOME", str(Path(".dagster").resolve())))
        home.mkdir(exist_ok=True)  # run history, schedules and sensor state live here
        db.database_url()  # with no FUZER_DATABASE_URL, start embedded Postgres before Dagster forks
        os.environ.setdefault("FUZER_DATABASE_URL", db.database_url())
        sys.exit(subprocess.call([sys.executable, "-m", "dagster", "dev", "-m", "fuzer.jobs", "-p", str(args.port)]))
    elif args.cmd == "mcp":
        from . import mcp_server

        mcp_server.main(http=args.http, host=args.host, port=args.port)
    else:
        with db.connect() as conn:
            if args.cmd != "reset":
                db.init_schema(conn)  # idempotent; picks up new tables without a reset
            if args.cmd == "reset":
                db.reset(conn)
            elif args.cmd == "suggest-mappings":
                from . import suggest

                _print(suggest.run(conn, pack, args.data, claude=args.claude, datasets=args.table))
            elif args.cmd == "ingest":
                _print(ingest.ingest(conn, args.data, full=args.full))
            elif args.cmd == "llm-link":
                from . import llm_links, llm_pids

                opts = {k: v for k, v in {"model": args.model, "effort": args.effort}.items() if v}
                common = {"limit": args.limit, "workers": args.workers, "batch": args.batch, "force": args.force,
                          "dry_run": args.dry_run, **opts}
                result = {}
                if args.source in ("logs", "all"):
                    result["logs"] = llm_links.run(conn, pack, per_request=args.per_request, **common)
                if args.source in ("pids", "all"):
                    result["pids"] = llm_pids.run(conn, pack, **common)
                if args.source in ("docs", "all"):
                    from . import llm_docs
                    result["docs"] = llm_docs.run(conn, pack, **common)
                _print(result)
                if not (args.dry_run or args.no_rebuild) and any(r.get("stored") for r in result.values()):
                    _print(pipeline.rebuild(conn, pack))
            elif args.cmd == "pid-score":
                import json as _json

                from . import llm_pids

                _print(llm_pids.score(conn, _json.loads(open(args.truth).read())))
            elif args.cmd == "eval":
                from . import evaluate
                _print(evaluate.run(conn, pack, args.keys))
            elif args.cmd == "bench":
                _print(_bench(conn, pack))
            elif args.cmd == "rebuild":
                _print(pipeline.rebuild(conn, pack))


def _bench(conn, pack) -> dict:
    """Best of three timings (ms) for the queries the UI and agents use most, on whatever is loaded."""
    import time

    from . import graph, queries

    def best(fn):
        times = []
        for _ in range(3):
            t = time.perf_counter()
            fn()
            times.append((time.perf_counter() - t) * 1000)
        return round(min(times), 1)

    big = conn.execute("select e.dst as id from edge e group by e.dst order by count(*) desc limit 1").fetchone()
    tag = conn.execute("select fuzer_id from entity where kind = 'tag' limit 1").fetchone()
    pumps = queries.resolve_ref(conn, pack, "ISO14224:PU")
    sizes = {r["kind"]: r["n"] for r in conn.execute(
        "select kind, count(*) as n from entity where retired_at is null group by kind")}
    out = {"entities": sum(sizes.values()), "edges": conn.execute("select count(*) as n from edge").fetchone()["n"]}
    checks = {
        "search_common_word": lambda: queries.search(conn, pack, "pump"),
        "search_tag": lambda: queries.search(conn, pack, "20-P-1001B"),
        "entity_hub": lambda: queries.entity(conn, big["id"], pack=pack),
        "entity_tag": lambda: queries.entity(conn, tag["fuzer_id"], pack=pack),
        "neighborhood_depth_2": lambda: queries.neighborhood(conn, tag["fuzer_id"], depth=2),
        "trace_flow": lambda: graph.trace(conn, tag["fuzer_id"]),
        "ontology": lambda: queries.ontology(conn),
        "incidents": lambda: queries.incidents(conn),
        "review_list": lambda: queries.reviews(conn, limit=500),
    }
    if pumps:
        checks |= {"members_class": lambda: queries.members(conn, pumps),
                   "maintenance_class": lambda: queries.maintenance(conn, pumps)}
    out["ms"] = {name: best(fn) for name, fn in checks.items()}
    return out


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


if __name__ == "__main__":
    main()
