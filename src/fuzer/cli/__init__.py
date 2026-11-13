"""Command line: fuzer ingest | rebuild | serve | mcp | jobs | limits | eval | reset ... (fake data: `fakeplant`)."""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from ..core import bundles, db, projects
from ..core.pack import load_pack, resolve
from ..ops import pipeline
from ..resources import ROOT
from ..sources import ingest


def parser() -> argparse.ArgumentParser:
    """The command line, as argparse builds it (docs/cli.md is generated from it)."""
    p = argparse.ArgumentParser(prog="fuzer")
    p.add_argument("--project", default=None,
                   help="project (#131): picks the pack, data folder, mappings and time-series provider "
                        "(default: $FUZER_PROJECT; `fuzer projects list` shows them)")
    p.add_argument("--pack", default=None, help="a packaged asset pack to use instead of the project's")
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("up", help="start everything: data, register, drawings, web UI (the one command you need)")
    u.add_argument("--fresh", action="store_true", help="start over: empty the database (and make new fake data)")
    u.add_argument("--port", type=int, default=8000)
    u.add_argument("--demo", choices=bundles.shipped(), help="a shipped seed bundle to serve, with its exports")
    u.add_argument("--no-jobs", action="store_true", help="don't start Dagster (schedules: SRE scans, studies, refreshes)")
    u.add_argument("--jobs-port", type=int, default=3000, help="port for the Dagster UI")
    u.add_argument("--no-worker", action="store_true",
                   help="don't start the agent worker (runs workflow agent steps such as daily report drafts)")


    i = sub.add_parser("ingest", help="load source exports into Postgres")
    i.add_argument("--data", default=None, help="exports folder (default: $FUZER_DATA_DIR or data)")
    i.add_argument("--full", action="store_true", help="read every dataset again, even unchanged ones")

    sub.add_parser("rebuild", help="rerun resolve / golden / graph")

    sm = sub.add_parser("suggest-mappings", help="draft mappings for tables that have none (review queue)")
    sm.add_argument("--data", default=None, help="folder of new tables (default: $FUZER_DATA_DIR or data)")
    sm.add_argument("--table", action="append", help="only this table (repeatable); default: every unmapped table")
    sm.add_argument("--claude", action="store_true", help="ask Claude about columns rules can't name (costs money)")

    from . import sources as sources_cli

    sources_cli.add_parser(sub)

    s = sub.add_parser("serve", help="run the API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    lk = sub.add_parser("llm-link", help="have the configured LLM read operator logs and scanned P&IDs, then rebuild")
    lk.add_argument("--source", choices=["logs", "pids", "docs", "curves", "all"], default="all")
    lk.add_argument("--limit", type=int, help="only this many entries (try a small sample first)")
    lk.add_argument("--model", default=None, help="model id (default: the setting llm.model)")
    lk.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    lk.add_argument("--per-request", type=int, default=20, help="log entries per request")
    lk.add_argument("--twins", action="store_true",
                    help="pids: read the scans of sheets that also come as DEXPI, to compare with the exact file")
    ps = sub.add_parser("pid-score", help="score image reading against the fake data's answer key")
    ps.add_argument("--truth", default=None, help="answer key (default: pid_truth.json in the exports folder)")
    ps.add_argument("--record", action="store_true", help="save the scores to evals/runs/ as the new baseline")
    ps.add_argument("--gate", action="store_true",
                    help="fail under an element's floor, or on a drop since the last recorded run of the same model")
    lk.add_argument("--workers", type=int, default=4, help="parallel requests")
    lk.add_argument("--batch", action="store_true", help="use the Batches API: half price, results within ~1 hour")
    lk.add_argument("--force", action="store_true", help="re-read entries that already have a result")
    lk.add_argument("--dry-run", action="store_true", help="show how much would be sent, call nothing")
    lk.add_argument("--no-rebuild", action="store_true")

    m = sub.add_parser("mcp", help="run the MCP server for AI agents (stdio by default)")
    m.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8001)
    m.add_argument("--as", dest="serve_as", metavar="ACCOUNT", default=None,
                   help="answer as this account (its id or user:<id>), seeing the restricted documents its groups see; "
                        "without it, restricted documents are left out")

    j = sub.add_parser("jobs", help="run the Dagster UI with schedules and sensors")
    j.add_argument("--port", type=int, default=3000)

    for name, text in (("ask", "ask the operations assistant one question"),
                       ("chat", "talk to the operations assistant (follow-up questions keep the context)")):
        a = sub.add_parser(name, help=text)
        if name == "ask":
            a.add_argument("question")
        a.add_argument("--model", default=None, help="orchestrator model (default: the setting agents.model); "
                       "specialists use agents.specialist_model")
        a.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None,
                       help="orchestrator effort (default: the setting agents.effort); specialists use agents.specialist_effort")
        a.add_argument("--json", action="store_true", help="print the answer, tools used and cost as JSON")
        a.add_argument("--as", dest="serve_as", metavar="ACCOUNT", default=None,
                       help="answer as this account (its id or user:<id>), seeing the restricted documents its groups "
                            "see; without it, restricted documents are left out")

    w = sub.add_parser("work", help="work engine (#97): load the starting processes, run the agent worker, fire timers")
    w.add_argument("action", choices=["load", "worker", "tick"],
                   help="load: import and publish definitions/*.bpmn; worker: run agent steps with the assistant's "
                        "specialists; tick: fire due timers and escalate overdue tasks once")

    lm = sub.add_parser("limits", help="operating limits (#101): learn normal ranges, check limits, scan for excursions")
    lm.add_argument("action", choices=["learn", "issues", "scan"],
                    help="learn: normal ranges per point and operating mode from history; issues: limits that look "
                         "wrong or are missing; scan: record excursions and open work for IOW breaches past their time")
    lm.add_argument("--since", default=None, help="start (default: learn 90 days, scan 6 hours before --until)")
    lm.add_argument("--until", default=None, help="end (default: the latest daily report, else today)")
    lm.add_argument("--step", type=int, default=None, help="minutes between values (default: learn 60, scan 10)")

    sr = sub.add_parser("sre", help="agentic SRE (#96): detect upsets in the process data since the last run")
    sr.add_argument("--since", default=None, help="start (default: where the last run ended, less an hour)")
    sr.add_argument("--until", default=None, help="end (default: now, or the end of a fake plant's data)")
    sr.add_argument("--handoff", action="store_true",
                    help="then prioritise, diagnose (Claude, capped) and hand off the new upsets to the workflow")
    sr.add_argument("--max-diagnoses", type=int, default=10, help="Claude diagnoses per run (0: none)")

    st = sub.add_parser("settings", help="settings (#131): list, get, set, reset, or show a setting's history")
    st.add_argument("action", choices=["list", "get", "set", "reset", "history"])
    st.add_argument("key", nargs="?")
    st.add_argument("value", nargs="?")
    st.add_argument("--group", default=None, help="with list: only this group (SRE, Claude, ...)")
    st.add_argument("--project", default=None, help="set or reset for this project (default: all projects)")
    st.add_argument("--by", default=os.environ.get("USER", "cli"), help="who is changing it (default: $USER)")
    st.add_argument("--reason", default=None, help="why, kept in the history")

    au = sub.add_parser("audit", help="the project's audit record (R.5): export a window, or show what it covers")
    au.add_argument("action", choices=["export", "coverage"])
    au.add_argument("--since", type=_when, default=None, help="start of the window, ISO date or time (default: "
                    "30 days before --until)")
    au.add_argument("--until", type=_when, default=None, help="end of the window, exclusive (default: now)")
    au.add_argument("--format", choices=["csv", "json"], default="csv", help="default csv")
    au.add_argument("--out", type=Path, default=None, help="write here instead of standard output")
    au.add_argument("--by", default=os.environ.get("USER", "cli"), help="who is exporting (default: $USER); the export is itself recorded")

    om = sub.add_parser("operating-model", help="the operating model as a package (P8.1): export, apply, adopt, "
                        "deviations, conformance, name a deviation")
    om.add_argument("action", choices=["export", "apply", "adopt", "deviations", "conformance", "name", "unname",
                                       "applied", "standing", "fleet"])
    om.add_argument("package", nargs="?", default=None,
                    help="a package folder, or `standard` (export: the folder to write; deviations and conformance: "
                    "default the model the project's bundle follows, apply: default `standard`)")
    om.add_argument("--part", help="with name and unname: roles, work_types, settings, processes or forms")
    om.add_argument("--key", help="with name and unname: the role, work type, setting, process or form")
    om.add_argument("--reason", help="with name: why this project differs there")
    om.add_argument("--owner", help="with name: the role or person who owns the difference")
    om.add_argument("--review-by", default=None, help="with name: when it is next reviewed (YYYY-MM-DD; default a year)")
    om.add_argument("--days", type=int, default=28, help="with standing and fleet: the days of work to read (P8.2)")
    om.add_argument("--name", default="standard", help="with export: the model's name")
    om.add_argument("--version", type=int, default=1, help="with export: the model's version")
    om.add_argument("--by", default=os.environ.get("USER", "cli"), help="who is applying it (default: $USER)")

    mc = sub.add_parser("model-change", help="change control for the standard (P8.3): propose, promote, test-run, "
                        "review, publish, rollout, rollback, versions, history")
    mc.add_argument("action", choices=["propose", "promote", "test-run", "review", "publish", "rollout", "rollback",
                                       "versions", "history"])
    mc.add_argument("--version", type=int, help="with test-run, review, publish and rollout: the version")
    mc.add_argument("--changes", help="with propose: a JSON file of {part: {key: value, or null to remove}}")
    mc.add_argument("--part", help="with promote: the part of this project's deviation")
    mc.add_argument("--key", help="with promote: the key of this project's deviation")
    mc.add_argument("--summary", help="with propose and promote: what it changes and why")
    mc.add_argument("--approve", action="store_true", help="with review: approve (default: reject)")
    mc.add_argument("--note", help="with review: why")
    mc.add_argument("--target", help="with rollout, rollback and history: the project (default: this one)")
    mc.add_argument("--reason", help="with rollback: why")
    mc.add_argument("--by", default=os.environ.get("USER", "cli"), help="who is acting (default: $USER)")

    sub.add_parser("onboarding", help="the onboarding journey (P8.4): each step ready, blocked or to do, and what "
                   "each gap prevents")

    bd = sub.add_parser("board", help="the work board (#99): refresh it, or show the top cards")
    bd.add_argument("action", choices=["refresh", "show"])
    bd.add_argument("--limit", type=int, default=20)

    rp = sub.add_parser("report", help="daily operations report (#98): close a day and draft it")
    rp.add_argument("--day", default=None, help="the production day (default: the latest DAY001)")
    rp.add_argument("--claude", action="store_true", help="have Claude draft it from the facts (costs money)")

    sub.add_parser("bench", help="time the main read queries against the current database")
    ev = sub.add_parser("eval", help="score search, document links, section failure modes and log reading")
    ev.add_argument("--keys", default=None, help="folder of answer-key JSON files (default: answer_keys in the exports folder)")
    ev.add_argument("--agents", action="store_true",
                    help="also ask the operations assistant the questions in assistant.json (Claude; costs money)")
    ev.add_argument("--memory", action="store_true",
                    help="score the assistant's memory with assistant_memory.json: learn from teaching answers, then "
                         "ask later questions (Claude; costs money; removes what it stored)")
    ev.add_argument("--domain", action="append", default=None,
                    help="with --agents: only the questions in this domain (repeatable), e.g. --domain charts")
    ev.add_argument("--out", default=None, help="with --agents or --packs: also write the full results (answers) to this JSON file")
    ev.add_argument("--record", action="store_true",
                    help="with --agents, --packs or --ops --full: keep the scores in evals/runs/ for docs/eval-results.md")
    ev.add_argument("--ops", action="store_true",
                    help="replay history through the operations detectors and score them against the fake "
                         "plant's injected events (exits 1 if recall fell since the last run of the same period)")
    ev.add_argument("--detector", default="limits", help="with --ops: the detector to replay (default: limits)")
    ev.add_argument("--full", action="store_true",
                    help="with --ops: score everything scheduled (SRE, grouping, diagnosis, daily reports, opportunities) "
                         "and fail if a score is under its floor or fell since the last run")
    ev.add_argument("--live", action="store_true", help="with --full: diagnose with the real Claude (costs money)")
    ev.add_argument("--packs", action="store_true",
                    help="ask each specialist pack's questions for this project's bundle and say which packs pass "
                         "(P4.2; Claude; costs money; exits 1 if any pack falls short); --specialist picks packs")
    ev.add_argument("--specialist", action="append", default=None,
                    help="with --packs: only this pack, by skill path (repeatable)")
    ev.add_argument("--loop", action="store_true",
                    help="drive every type of work arrive to counted and say which step failed for which type "
                         "(P1.7; writes cases, so run it on a scratch copy; exits 1 if any type fails)")
    ev.add_argument("--sweep", action="store_true",
                    help="run each work detector over the bundle's history and score its raises against the events "
                         "its evals/detections.yaml declares (P3.6a; writes cases, so run it on a scratch copy; exits "
                         "1 if a score is under its floor); --detector picks one")
    ev.add_argument("--watch", action="store_true",
                    help="scan --since to --until with the rules, read it with the agentic watch and score its "
                         "proposals against the fake plant's injected events no rule caught (P3.3b; needs a model "
                         "provider and costs money; exits 1 if under half match)")
    ev.add_argument("--roles", action="store_true",
                    help="score the measurement roles proposed for each point against the fake plant's process model "
                         "(P1.4; exits 1 under 90%% right or if a wrong role was confident)")
    ev.add_argument("--since", default=None, help="with --ops: start (default: 28 days before --until)")
    ev.add_argument("--until", default=None, help="with --ops: end (default: the latest daily report)")

    sub.add_parser("reset", help="drop and recreate all tables")
    ini = sub.add_parser("init", help="initialise this database for a facility from a seed bundle (#376); "
                                      "doesn't ingest or rebuild")
    ini.add_argument("--bundle", required=True, help="the bundle folder (with bundle.yaml) or its bundle.yaml")
    ini.add_argument("--upgrade", action="store_true",
                     help="replace the stored project config of the same project with the bundle's")
    ini.add_argument("--by", default=os.environ.get("USER", "cli"), help="who the stored project is recorded as set by")

    pj = sub.add_parser("projects", help="projects (#131): list them, show one, or save one to the database")
    pj.add_argument("action", choices=["list", "show", "save"])
    pj.add_argument("name", nargs="?")
    pj.add_argument("--by", default=os.environ.get("USER", "cli"))

    return p


NEEDS_NO_PACK = {"init", "ingest", "projects", "settings"}  # these work on a database that holds no project yet


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    name = args.project or projects.current()
    if name or args.cmd not in ("up", "reset"):
        with db.connect() as conn:
            # no project named: the database's first project (the one `fuzer up` or `fuzer init` stored), so a
            # command in another shell serves what `fuzer up` serves, with its data folder
            name = name or projects.active(conn)
            try:
                if name:
                    projects.activate(conn, name)
            except projects.ProjectError as e:
                raise SystemExit(str(e)) from None
    pack = load_pack(args.pack) if args.pack else None  # else the project's, read from the database below
    data = Path(getattr(args, "data", None) or data_dir())

    if args.cmd == "up":
        from . import up

        up.up(pack, port=args.port, fresh=args.fresh, demo=args.demo, jobs=not args.no_jobs, jobs_port=args.jobs_port,
              worker=not args.no_worker)
    elif args.cmd == "serve":
        import uvicorn

        db.database_url()  # start embedded Postgres before uvicorn
        uvicorn.run("fuzer.api:app", host=args.host, port=args.port)
    elif args.cmd == "jobs":
        import subprocess
        import sys

        from .up import dagster_command

        sys.exit(subprocess.call(dagster_command(args.port)))
    elif args.cmd in ("ask", "chat"):
        _serve_as(args.serve_as)
        _assistant(args)
    elif args.cmd == "mcp":
        from ..agents import mcp_server

        _serve_as(args.serve_as)
        mcp_server.main(http=args.http, host=args.host, port=args.port)
    elif args.cmd == "work":
        from ..agents import worker
        from ..workflow import engine

        with db.connect() as conn:
            db.init_schema(conn)
            if args.action == "load":
                print(json.dumps(engine.load_defaults(conn), default=str, indent=2))
            elif args.action == "tick":
                print(json.dumps(engine.tick(conn)))
        if args.action == "worker":
            worker.run(db.connect, worker.assistant_agents())
    else:
        with db.connect() as conn:
            if args.cmd != "reset":
                db.init_schema(conn)  # idempotent; picks up new tables without a reset
                if not args.pack:
                    try:
                        pack = resolve(conn)  # the pack of the project this code serves (#376)
                    except projects.ProjectError as e:
                        if args.cmd not in NEEDS_NO_PACK:
                            raise SystemExit(str(e)) from None
            if args.cmd == "reset":
                db.reset(conn)
            elif args.cmd == "init":

                try:
                    _print(bundles.init(conn, Path(args.bundle), by=args.by, upgrade=args.upgrade))
                except bundles.BundleError as e:
                    raise SystemExit(str(e)) from None
            elif args.cmd in ("sources", "source"):
                from . import sources as sources_cli

                out = sources_cli.run(conn, pack, data, args)
                print(out) if isinstance(out, str) else _print(out)
            elif args.cmd == "suggest-mappings":
                from ..sources import suggest

                _print(suggest.run(conn, pack, data, claude=args.claude, datasets=args.table))
            elif args.cmd == "ingest":
                from ..sources import catalog

                seeded = catalog.seed(conn)  # built-in mapping changes since the last run (never over a person's edit)
                _print({**ingest.ingest(conn, data, full=args.full), **({"seeded": seeded} if seeded else {})})
            elif args.cmd == "llm-link":
                from ..graph import llm_links
                from ..sources import llm_pids

                opts = {k: v for k, v in {"model": args.model, "effort": args.effort}.items() if v}
                common = {"limit": args.limit, "workers": args.workers, "batch": args.batch, "force": args.force,
                          "dry_run": args.dry_run, **opts}
                result = {}
                if args.source in ("logs", "all"):
                    result["logs"] = llm_links.run(conn, pack, per_request=args.per_request, **common)
                if args.source in ("pids", "all"):
                    result["pids"] = llm_pids.run(conn, pack, twins=args.twins, **common)
                if args.source in ("docs", "all"):
                    from ..sources import llm_docs
                    result["docs"] = llm_docs.run(conn, pack, **common)
                if args.source in ("curves", "all"):  # OEM datasheets -> curve review items
                    from ..sources import llm_curves
                    result["curves"] = llm_curves.run(conn, pack, **common)
                _print(result)
                if not (args.dry_run or args.no_rebuild) and any(r.get("stored") for r in result.values()):
                    _print(pipeline.rebuild(conn, pack))
            elif args.cmd == "pid-score":
                import json as _json

                from ..sources import llm_pids

                truth = Path(args.truth or data / "pid_truth.json")
                if not truth.is_file():
                    raise SystemExit(f"No P&ID answer key at {truth}. It comes with fake data "
                                     "(uv run fakeplant generate); set FUZER_DATA_DIR or pass --truth.")
                res = llm_pids.score(conn, _json.loads(truth.read_text()))
                _print(res)
                from ..evals import runs

                model = conn.execute("select max(model) as m from llm_extraction where source = 'pid'").fetchone()["m"]
                last = next((r for r in reversed(runs.load()) if r["kind"] == "pids" and r.get("model") == model), None)
                bad = llm_pids.gate(res, last) if args.gate else []
                if args.record and not bad:
                    _say(f"recorded {runs.save(runs.pids(res, model))}")
                if bad:
                    raise SystemExit("P&ID benchmark failed: " + "; ".join(bad))
            elif args.cmd == "eval" and args.ops:
                _ops_eval(conn, pack, args, data)
            elif args.cmd == "eval" and args.packs:
                _packs_eval(conn, args)
            elif args.cmd == "eval" and args.loop:
                from ..evals import loop

                res = loop.run(conn)
                for r in res["types"]:
                    _say(f"{r['type']}: " + (f"failed at {r['failed']}: {r['why']}" if r["failed"] else "completed")
                         + (f" (arrived from {r['arrived']})" if r.get("arrived") else ""))
                if res["failed"]:
                    raise SystemExit("loop eval failed: " + "; ".join(res["failed"]))
            elif args.cmd == "eval" and args.sweep:
                from ..evals import sweep

                folder = bundles.find(resolve(conn).name)
                if not folder:
                    raise SystemExit("this project has no bundle folder, so no detections to score against")
                only = [args.detector] if args.detector != "limits" else None
                bad = []
                for res in sweep.run(conn, pack, folder, only, say=_say).values():
                    sweep.store(conn, res)
                    _say(f"{res['detector']}: " + (f"not here ({res['not_here']})" if "not_here" in res else
                         res.get("note") or f"recall {res['recall']}, precision {res['precision']}, "
                         f"{res['false']} false of {res['raises']} raised, missed {res['missed'] or 'none'}"))
                    bad += sweep.gate(res)
                if bad:
                    raise SystemExit("sweep eval under its floor: " + "; ".join(bad))
            elif args.cmd == "eval" and args.watch:
                from ..evals import watch_eval

                if not (args.since and args.until):
                    raise SystemExit("--watch needs --since and --until (a few days around the bundle's upsets)")
                res = watch_eval.run(conn, pack, resolve(conn).name, args.since, args.until, data, say=_say)
                if res["status"] != "shadow":
                    raise SystemExit(f"watch eval not run: {res['note']}")
                watch_eval.store(conn, res)
                _say(f"{res['proposals']} proposals, {res['matched_uncaught']} on what no rule caught "
                     f"(matched {res['matched']}), precision {res['precision']}, {res['uncaught']} of {res['upsets']} "
                     f"upsets uncaught, ${res['usd']}")
                if not res["passes"]:
                    raise SystemExit("watch eval: under half its proposals match what no rule caught")
            elif args.cmd == "eval" and args.roles:
                from ..evals import roles as roles_eval

                if not (data / "process_model.json").is_file():
                    raise SystemExit(f"No process_model.json in {data}: the roles eval needs a fake plant's data folder")
                res = roles_eval.score(conn, data)
                for m in res["misses"]:
                    _say(f"{m['point']}: {m['got']} ({m['confidence']}), should be {m['want']}")
                _say(f"{res['right']} of {res['points']} roles right ({res['accuracy']:.1%}), "
                     f"{res['confident_misses']} wrong and confident")
                if (res["accuracy"] or 0) < 0.9 or res["confident_misses"]:
                    raise SystemExit("roles eval failed")
            elif args.cmd == "eval":
                from ..evals import evaluate
                keys = Path(args.keys or data / "answer_keys")
                if not keys.is_dir():
                    raise SystemExit(f"No answer keys at {keys}. Set FUZER_DATA_DIR or pass --keys.")
                if args.memory:
                    if not (keys / "assistant_memory.json").is_file():
                        raise SystemExit(f"No assistant_memory.json in {keys}; regenerate the exports.")
                    cases = json.loads((keys / "assistant_memory.json").read_text())
                    res = evaluate.memory(conn, pack, cases, say=_say)
                    if args.out:
                        Path(args.out).write_text(json.dumps(res, indent=2, default=str))
                    _print({**res, "results": [{k: v for k, v in r.items() if k != "answer"} for r in res["results"]]})
                elif args.agents:
                    if not (keys / "assistant.json").is_file():
                        raise SystemExit(f"No assistant.json in {keys}.")
                    cases = json.loads((keys / "assistant.json").read_text())
                    if args.domain:
                        cases = [c for c in cases if c.get("domain") in args.domain]
                        if not cases:
                            raise SystemExit(f"No questions in {', '.join(args.domain)}.")
                    res = evaluate.assistant(cases, say=_say)
                    if args.out:
                        Path(args.out).write_text(json.dumps(res, indent=2, default=str))
                    if args.record:
                        from ..core import settings
                        from ..evals import runs

                        m = {k: settings.get(conn, f"agents.{k}") for k in ("model", "effort", "specialist_effort")}
                        _say(f"recorded {runs.save(runs.assistant(res, m))}")
                    _print({**res, "results": [{k: v for k, v in r.items() if k != "answer"} for r in res["results"]]})
                else:
                    _print(evaluate.run(conn, pack, keys))
            elif args.cmd == "bench":
                _print(_bench(conn, pack))
            elif args.cmd == "rebuild":
                _print(pipeline.rebuild(conn, pack))
            elif args.cmd == "limits":
                _limits(conn, pack, args)
            elif args.cmd == "report":
                from ..graph import operations
                from ..ops import daily_report
                from ..workflow import engine

                engine.load_defaults(conn)
                day = args.day or operations.as_of(conn)
                _print(daily_report.close_day(conn, pack, day))
                if args.claude:
                    _print(daily_report.redraft(conn, day))
                print(daily_report.get(conn, day)["markdown"])
            elif args.cmd == "projects":
                _projects(conn, args)
            elif args.cmd == "settings":
                _settings(conn, args)
            elif args.cmd == "audit":
                _audit(conn, args)
            elif args.cmd == "operating-model":
                from ..workflow import operating_model as om

                if args.action == "export":
                    if not args.package:
                        raise SystemExit("export: give the folder to write")
                    _print(om.export(conn, Path(args.package), args.name, args.version))
                elif args.action == "apply":
                    _print(om.apply(conn, args.package or "standard", args.by))
                elif args.action == "adopt":
                    _print(om.adopt(conn, args.by))
                elif args.action == "deviations":
                    _print(om.deviations(conn, args.package or om.conformance_package(conn)))
                elif args.action == "conformance":
                    _print(om.conformance(conn, args.package))
                elif args.action == "name":
                    _print(om.name(conn, args.part, args.key, args.reason, args.owner, args.by, args.review_by))
                elif args.action == "unname":
                    _print({"unnamed": om.unname(conn, args.part, args.key)})
                elif args.action in ("standing", "fleet"):
                    from ..workflow import conformance as standards

                    _print(standards.standing(conn, args.days) if args.action == "standing"
                           else standards.fleet(None, args.days))
                else:
                    _print(om.applied(conn))
            elif args.cmd == "model-change":
                _model_change(conn, args)
            elif args.cmd == "onboarding":
                from ..api import onboarding

                _print(onboarding.journey(conn))
            elif args.cmd == "board":
                from ..ops import board

                _print(board.refresh(conn, pack) if args.action == "refresh" else board.cards(conn, per_page=args.limit))
            elif args.cmd == "sre":
                from ..ops import sre

                _print(sre.scan(conn, pack, args.since, args.until))
                if args.handoff:
                    from ..ops import sre_handoff
                    from ..workflow import engine

                    engine.load_defaults(conn)
                    _print(sre_handoff.handoff(conn, pack, max_diagnoses=args.max_diagnoses))
                    from ..ops import board

                    _print({"board": board.refresh(conn, pack)})


def _limits(conn, pack, args) -> None:
    from datetime import datetime, timedelta

    from ..analysis import limits
    from ..graph import operations

    if args.action == "issues":
        found = limits.issues(conn, pack)
        _print({"issues": len(found), "by_fault": dict(Counter(i["fault"] for i in found)), "items": found})
        return
    until = datetime.fromisoformat(args.until or f"{operations.as_of(conn)}T23:59")
    back = timedelta(days=90) if args.action == "learn" else timedelta(hours=6)
    since = args.since or (until - back).isoformat(timespec="minutes")
    if args.action == "learn":
        _print(limits.learn(conn, pack, since, until.isoformat(timespec="minutes"), args.step or 60))
    else:
        from ..workflow import engine

        engine.load_defaults(conn)
        _print(limits.scan(conn, pack, since, until.isoformat(timespec="minutes"), args.step or 10))


def _ops_eval(conn, pack, args, data: Path) -> None:
    from datetime import datetime, timedelta

    from ..evals import replay
    from ..graph import operations
    from ..sources import process_model

    if not (data / "process_model.json").is_file():  # the fake plant's own folder for this pack
        own = ROOT / "fakeplant" / "data" / pack.name
        data = own if (own / "process_model.json").is_file() else data
    if args.full and not (args.since or args.until) and (data / "process_model.json").is_file():
        since, until_s = replay.default_window(process_model.read(data))
        until = datetime.fromisoformat(until_s)
        _say(f"window {since} to {until_s}: the first injected trip 90 days or more into the data "
             "(pass --since/--until for another)")
    else:
        until = datetime.fromisoformat(args.until or f"{operations.as_of(conn)}T00:00")
        since = args.since or (until - timedelta(days=28)).isoformat(timespec="minutes")
    if args.full:
        from ..evals import ops_eval

        if not (data / "process_model.json").is_file():
            raise SystemExit("no process_model.json: set FUZER_DATA_DIR to the fake plant's folder")
        res = ops_eval.run(conn, pack, data, since, until.isoformat(timespec="minutes"), live=args.live, say=_say)
        prev = replay.store(conn, {"detector": "full" + ("-live" if args.live else ""), "since": since,
                                   "until": until.isoformat(timespec="minutes"), "recall": res["sre"]["recall"],
                                   "by_kind": res["sre"]["by_kind"], **res})
        _print(res)
        if args.record:
            from ..evals import runs

            _say(f"recorded {runs.save(runs.ops(res, since, until.isoformat(timespec='minutes'), args.live))}")
        bad = ops_eval.gate(res) + [f"recall fell: {k}" for k in prev["recall_dropped"]]
        if bad:
            raise SystemExit("Ops eval failed: " + "; ".join(bad))
        return
    try:
        res = replay.run(conn, pack, args.detector, since, until.isoformat(timespec="minutes"), data_dir=data, say=_say)
    except ValueError as e:
        raise SystemExit(f"{e}. Set FUZER_DATA_DIR to the fake plant's folder.")
    check = replay.store(conn, res)
    _print({**res, **check})
    if check["recall_dropped"]:
        raise SystemExit(f"Recall fell since the last run: {', '.join(check['recall_dropped'])}")


def _packs_eval(conn, args) -> None:
    """`fuzer eval --packs`: each specialist pack's answer keys for this project's bundle, asked and scored (P4.2)."""
    from ..core import settings
    from ..core.pack import resolve
    from ..evals import packs_eval, runs

    bundle = resolve(conn).name
    try:
        res = packs_eval.score(bundle, args.specialist, say=_say)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    for path, why in res["not_applicable"].items():
        _say(f"{path}: not applicable on {bundle} ({why})")
    for path, p in res["packs"].items():
        _say(f"{path}: {'passed' if p['passed'] else 'short: ' + '; '.join(p['short'])}")
    if args.record:
        m = {k: settings.get(conn, f"agents.{k}") for k in ("model", "effort", "specialist_effort")}
        _say(f"recorded {runs.save(runs.packs(res, m))}")
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=2, default=str))
    _say(f"{res['passed']} of {res['asked']} packs passed on {bundle}")
    if res["passed"] < res["asked"]:
        raise SystemExit("pack eval failed")


def _assistant(args) -> None:
    import asyncio

    from .. import agents
    from ..core import llm
    if not llm.configured("agents"):
        raise SystemExit("Configure the agents provider API key for the operations assistant.")
    db.database_url()  # start embedded Postgres up front if it's in use
    with db.connect() as conn:
        db.init_schema(conn)
    team = agents.team(agents.client(args.model), effort=args.effort)

    def show(out: dict) -> None:
        if args.json:
            print(json.dumps({k: v for k, v in out.items() if k != "session"}, indent=2, default=str))
            return
        print(out["answer"])
        u = out["usage"]
        print(f"\n— {out['seconds']} s · {u['model_calls']} model calls · ${u['cost_usd']:.2f}"
              + (f" · specialists: {', '.join(out['specialists'])}" if out["specialists"] else ""))
        print("  " + " · ".join(f"{name}: {m['calls']} calls ${m['cost_usd']:.2f}"
                                + (" (unpriced: shown at Opus 5.5 rates)" if m.get("unpriced") else "")
                                for name, m in u["models"].items()))

    async def run():
        if args.cmd == "ask":
            show(await agents.ask(args.question, team))
            return
        session = team.create_session()
        print("Operations assistant. Ask about the plant; an empty line quits.\n")
        while (q := (await asyncio.to_thread(input, "> ")).strip()):
            show(await agents.ask(q, team, session))
            print()

    asyncio.run(run())


def _serve_as(account: str | None) -> None:
    """Restricted documents (R.5, #29): a local MCP server or CLI assistant answers as an account, or as no one."""
    from ..core import access, capabilities

    capabilities.serve_as(capabilities.ANONYMOUS if not account else
                          account if account.startswith(access.ACTOR_PREFIX) else access.ACTOR_PREFIX + account)


def data_dir() -> str:
    """Where source exports land: the project's data folder (from its bundle), else ./data."""
    return projects.field("data") or "data"


def _bench(conn, pack) -> dict:
    """Best of three timings (ms) for the queries the UI and agents use most, on whatever is loaded."""
    import time

    from ..graph import edges, queries

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
        "trace_flow": lambda: edges.trace(conn, tag["fuzer_id"]),
        "ontology": lambda: queries.ontology(conn),
        "incidents": lambda: queries.incidents(conn),
        "review_list": lambda: queries.reviews(conn, limit=500),
    }
    if pumps:
        checks |= {"members_class": lambda: queries.members(conn, pumps),
                   "maintenance_class": lambda: queries.maintenance(conn, pumps)}
    out["ms"] = {name: best(fn) for name, fn in checks.items()}
    return out


def _projects(conn, args) -> None:
    if args.action == "list":
        cur = projects.current()
        _print([{"name": n, "current": n == cur, **{k: v for k, v in projects.config(conn, n).items() if k != "name"}}
                for n in projects.names(conn)])
        return
    name = args.name or projects.current()
    if not name:
        raise SystemExit(f"projects {args.action} needs a name (fuzer projects list shows them)")
    try:
        cfg = projects.config(conn, name)
        _print(cfg if args.action == "show" else projects.save(conn, name, cfg, args.by))
    except projects.ProjectError as e:
        raise SystemExit(str(e)) from None


def _when(value: str):
    from datetime import datetime

    from ..core import clock

    return clock.aware(datetime.fromisoformat(value))


def _audit(conn, args) -> None:
    from datetime import UTC, datetime, timedelta

    from ..core import access, audit

    if args.action == "coverage":
        _print(audit.coverage(conn))
        return
    project = projects.current(conn)
    until = args.until or datetime.now(UTC)
    since = args.since or until - timedelta(days=30)
    try:
        rows = audit.record(conn, project, since, until)
    except audit.AuditError as e:
        raise SystemExit(str(e)) from None
    text = audit.to_csv(rows) if args.format == "csv" else json.dumps(rows, default=str, indent=1)
    access.event(conn, f"cli:{args.by}", "audit_exported", project=project, since=since.isoformat(),
                 until=until.isoformat(), rows=len(rows), format=args.format)
    conn.commit()
    if args.out:
        args.out.write_text(text)
        print(f"{len(rows)} audit records for {project} written to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def _model_change(conn, args) -> None:
    from ..core import projects
    from ..workflow import model_changes as mc

    target = args.target or projects.current(conn)
    if args.action == "propose":
        _print(mc.propose(conn, json.loads(Path(args.changes).read_text()), args.summary or "", args.by))
    elif args.action == "promote":
        _print(mc.promote(conn, args.part, args.key, args.summary or "", args.by))
    elif args.action == "test-run":
        _print(mc.test_run(conn, "standard", args.version))
    elif args.action == "review":
        _print(mc.review(conn, "standard", args.version, args.by, args.approve, args.note or ""))
    elif args.action == "publish":
        _print(mc.publish(conn, "standard", args.version, args.by))
    elif args.action == "rollout":
        _print(mc.rollout(conn, target, "standard", args.version, args.by))
    elif args.action == "rollback":
        _print(mc.rollback(conn, target, args.by, args.reason or ""))
    elif args.action == "versions":
        _print(mc.versions(conn))
    else:
        _print(mc.history(conn, args.target))


def _settings(conn, args) -> None:
    from ..core import settings

    if args.action != "list" and not args.key:
        raise SystemExit(f"settings {args.action} needs a key (fuzer settings list shows them)")
    try:
        if args.action == "list":
            _print(settings.listing(conn, args.group, args.project))
        elif args.action == "get":
            _print({"key": args.key, "value": settings.get(conn, args.key, args.project)})
        elif args.action == "set":
            if args.value is None:
                raise SystemExit("settings set needs a value")
            _print(settings.set(conn, args.key, args.value, by=args.by, reason=args.reason, project_name=args.project))
        elif args.action == "reset":
            _print(settings.reset(conn, args.key, by=args.by, reason=args.reason, project_name=args.project))
        else:
            _print(settings.history(conn, args.key))
    except settings.SettingError as e:
        raise SystemExit(str(e)) from None


def _say(msg: str) -> None:
    """A progress line on stderr, so stdout stays the JSON result."""
    import sys
    from datetime import datetime

    print(f"{datetime.now():%H:%M:%S} {msg}", file=sys.stderr, flush=True)


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


if __name__ == "__main__":
    main()
