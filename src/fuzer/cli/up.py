"""`fuzer up`: one command from nothing to the web UI.

1. Data: the exports in $FUZER_DATA_DIR. With nothing configured, the Okan field demo (made if needed).
2. Load: ingest, and rebuild only when something changed, or when cached LLM readings are newer
   than the register (a read that stopped before its rebuild). A second start is quick.
3. Readers: the configured LLM provider reads whatever it hasn't read yet (operator logs, P&ID
   drawings, document sections), then the register is rebuilt with what it found.
4. Web UI: built when missing or older than its source.
5. Jobs: Dagster (schedules and sensors: SRE scans, studies, refreshes, day close) on
   http://127.0.0.1:3000, its log in .dagster/dagster.log; `--no-jobs` leaves it out.
6. Agent worker: `fuzer work worker`, which runs the workflow's agent steps (drafting daily reports,
   specialists' proposals), its log in .fuzer/worker.log. It needs a configured agents provider:
   without one it isn't started, so the steps wait instead of failing over to a person. `--no-worker`
   leaves it out.
7. Serve on http://127.0.0.1:8000. Ctrl+C stops all of them.

`--fresh` starts over: empties the database and, for the fake plant, makes new data. LLM
readings are kept (they are keyed by the text they read), so a fresh start doesn't pay to read the
same logs and drawings again. `--demo okan` selects the Okan field and its pack.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..core import db
from ..ops import pipeline
from ..resources import ROOT
from ..sources import ingest

UI = ROOT / "ui"
DEFAULT_DEMO = "okan"


def say(msg: str) -> None:
    print(f"  {msg}", flush=True)


def _has_exports(folder: Path) -> bool:
    return folder.is_dir() and any(ingest.dataset(folder, name) for name in ingest.export_names())


def _fake_plant():
    try:
        from fakeplant import cli as fake
    except ImportError:
        return None
    return fake


def data_folder(fresh: bool) -> Path:
    folder = Path(os.environ["FUZER_DATA_DIR"]) if os.environ.get("FUZER_DATA_DIR") else None
    fake = _fake_plant()
    if folder and _has_exports(folder) and not (fresh and fake and folder.resolve().is_relative_to(fake.DATA)):
        return folder
    if fake is None:
        raise SystemExit(f"No source exports found in {folder or 'data'}. Put exports there, or set FUZER_DATA_DIR.")
    asset = "okan"
    folder = folder or fake.DATA / asset
    say(f"Making the fake {asset} plant in {folder} ...")
    fake.make(folder)
    return folder


def load(conn, pack, folder: Path, fresh: bool) -> None:
    if fresh:
        say("Emptying the database (LLM readings are kept) ...")
        kept = conn.execute("select to_regclass('llm_extraction') as t").fetchone()["t"]
        if kept:
            conn.execute("create temp table keep_llm as select * from llm_extraction")
        db.reset(conn)
        if kept:
            conn.execute("insert into llm_extraction select * from keep_llm on conflict do nothing")
            conn.execute("drop table keep_llm")
            conn.commit()
    db.init_schema(conn)
    from ..sources import catalog

    if seeded := catalog.seed(conn):  # built-in mappings that changed (never over a person's edit)
        say(f"Sources: {', '.join(f'{len(v)} {k}' for k, v in seeded.items())} from the built-in mappings")
    stats = ingest.ingest(conn, folder)
    changed = stats["inserted"] + stats["updated"] + stats["removed"]
    empty = not conn.execute("select 1 from entity limit 1").fetchone()
    say(f"Data: {stats['records']:,} records read, {changed:,} changed" + (
        f"; {len(stats['unmapped'])} tables without a mapping (Review > New tables after "
        "fuzer suggest-mappings)" if stats.get("unmapped") else ""))
    if changed or empty or pipeline.readings_newer(conn):
        say("Building the register ...")
        r = pipeline.rebuild(conn, pack)
        say(f"Register: {r['entities']:,} entities, {r['edges']:,} links")


def read_with_claude(conn, pack) -> None:
    """Have the configured LLM provider read everything it hasn't read yet: operator logs (links and events), P&ID drawings
    (tags and piping), document sections (failure modes) and OEM datasheets (curves, for review); then
    rebuild once with what it found.
    Readings are cached by the text they read, so only new or changed records cost anything."""
    from ..core import llm
    from ..core.pricing import cost
    from ..graph import llm_links
    from ..sources import llm_curves, llm_docs, llm_pids

    readers = [("operator log entries", "entries", llm_links.run), ("drawings", "sheets", llm_pids.run),
               ("document sections", "sections", llm_docs.run), ("datasheets", "datasheets", llm_curves.run)]
    todo = [(label, key, run, n) for label, key, run in readers if (n := run(conn, pack, dry_run=True)[key])]
    if not todo:
        return
    what = ", ".join(f"{n:,} {label}" for label, _, _, n in todo)
    if not llm.configured():
        say(f"Not read by {llm.provider()}: {what}. Configure the provider API key and run fuzer up again.")
        return
    say(f"{llm.provider()} ({llm.model()}) is reading {what}. This can take a few minutes ...")
    stored, spent = 0, 0.0
    for label, _, run, _ in todo:
        out = run(conn, pack)
        stored += out.get("stored", 0)
        spent += cost(out.get("model", llm_links.DEFAULT_MODEL), out.get("input_tokens", 0),
                      out.get("output_tokens", 0), out.get("cache_read_tokens", 0), 0, llm.provider())[0]
        for err in out.get("errors", [])[:3]:
            say(f"Could not read some {label}: {err}")
    say(f"{llm.provider()} read {stored:,} item(s) for about ${spent:.2f}")
    if pipeline.readings_newer(conn):
        say(f"Rebuilding the register with what {llm.provider()} found ...")
        pipeline.rebuild(conn, pack)


def build_ui() -> None:
    index = UI / "dist" / "index.html"
    sources = [p for p in (UI / "src").rglob("*") if p.is_file()] + [UI / "index.html", UI / "package.json"]
    if index.exists() and index.stat().st_mtime >= max(p.stat().st_mtime for p in sources if p.exists()):
        return
    npm = shutil.which("npm")
    if not npm:
        say("Web UI not built and npm isn't installed (install Node.js); the API still runs.")
        return
    say("Building the web UI ...")
    if not (UI / "node_modules").exists():
        subprocess.run([npm, "install", "--silent"], cwd=UI, check=True)
    subprocess.run([npm, "run", "build", "--silent"], cwd=UI, check=True, stdout=subprocess.DEVNULL)


def use_demo(name: str):
    """Point everything at one fake plant: its asset pack and its data folder."""
    from ..core.pack import load_pack

    fake = _fake_plant()
    if fake is None or name not in fake.PACKS:
        raise SystemExit(f"Unknown demo {name!r}" + (f"; one of {', '.join(fake.PACKS)}" if fake else
                                                     " (the fakeplant package isn't installed)"))
    os.environ["FUZER_PROJECT"] = name  # this process and the jobs and worker it starts serve the demo's project
    os.environ["FUZER_DATA_DIR"] = str(fake.DATA / name)
    return load_pack(fake.PACKS[name])


def other_asset(conn, pack) -> str | None:
    """The field already loaded, when it isn't this pack's (two assets in one database get mixed up)."""
    row = conn.execute("select source_key from source_record where source = 'sap' and record_type = 'floc' "
                       "and coalesce(payload->>'parent', '') = '' limit 1").fetchone()
    return row["source_key"] if row and row["source_key"] != pack.facility else None


def dagster_command(port: int) -> list[str]:
    """`dagster dev` on the Fuzer jobs, with its state in $DAGSTER_HOME (default .dagster) and the same
    database as this process (embedded Postgres is started first, so the child connects to it)."""
    home = Path(os.environ.setdefault("DAGSTER_HOME", str(Path(".dagster").resolve())))
    home.mkdir(exist_ok=True)  # run history, schedules and sensor state live here
    # Dagster warns on every start without an instance config; an empty one keeps its default (local) storage.
    (home / "dagster.yaml").touch(exist_ok=True)
    os.environ.setdefault("FUZER_DATABASE_URL", db.database_url())
    return [sys.executable, "-m", "dagster", "dev", "-m", "fuzer.ops.jobs", "-p", str(port)]


def start_jobs(port: int) -> subprocess.Popen:
    cmd = dagster_command(port)
    log = Path(os.environ["DAGSTER_HOME"]) / "dagster.log"
    say(f"Jobs (Dagster) on http://127.0.0.1:{port}, log in {log}")
    with open(log, "ab") as out:
        return subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)


def worker_command(pack) -> list[str]:
    """`fuzer work worker` for this project (it inherits FUZER_PROJECT), on the same database as this process."""
    os.environ.setdefault("FUZER_DATABASE_URL", db.database_url())
    return [sys.executable, "-c", "from fuzer.cli import main; main()", "work", "worker"]


def start_worker(pack) -> subprocess.Popen | None:
    """The agent worker, unless the agents provider is not configured."""
    from ..core import llm

    if not llm.configured("agents"):
        say("Agent worker not started: configure the agents provider API key and run fuzer up again.")
        return None
    cmd = worker_command(pack)
    log = Path(".fuzer").resolve() / "worker.log"
    log.parent.mkdir(exist_ok=True)
    say(f"Agent worker (workflow agent steps), log in {log}")
    with open(log, "ab") as out:
        return subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()  # dagster dev stops its webserver and daemon on SIGTERM; the worker just exits
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def up(pack, host: str = "127.0.0.1", port: int = 8000, fresh: bool = False, demo: str | None = None,
       jobs: bool = True, jobs_port: int = 3000, worker: bool = True) -> None:
    if not demo and not os.environ.get("FUZER_DATA_DIR") and not os.environ.get("FUZER_PROJECT") and _fake_plant():
        demo = DEFAULT_DEMO  # nothing configured: the Okan field demo
    print("Starting Fuzer" + (f": {demo} demo" if demo else ""), flush=True)
    if demo:
        pack = use_demo(demo)
    if pack is None:
        from ..core.pack import resolve
        from ..core import projects

        with db.connect() as conn:
            try:
                pack = resolve(conn)
            except projects.ProjectError as e:
                raise SystemExit(str(e)) from None
    if not fresh:
        with db.connect() as conn:
            db.init_schema(conn)
            if loaded := other_asset(conn, pack):
                raise SystemExit(f"The database holds another asset ({loaded}). Run with --fresh to replace it "
                                 f"with {pack.facility}.")
    folder = data_folder(fresh)
    os.environ["FUZER_DATA_DIR"] = str(folder)
    with db.connect() as conn:
        load(conn, pack, folder, fresh)
        read_with_claude(conn, pack)
    build_ui()
    proc = start_jobs(jobs_port) if jobs else None
    agents = start_worker(pack) if worker else None
    print(f"\nFuzer is running: http://{host}:{port}   (Ctrl+C to stop)\n", flush=True)
    import uvicorn

    try:
        uvicorn.run("fuzer.api:app", host=host, port=port, log_level="warning")
    finally:
        stop_process(agents)
        stop_process(proc)
