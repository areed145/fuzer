"""`fuzer up`: one command from nothing to the web UI.

1. Data: the exports in $FUZER_DATA_DIR. With nothing configured, the Okan field demo (made if needed).
2. Load: ingest, and rebuild only when something changed (a second start is quick).
3. Claude: with ANTHROPIC_API_KEY set, Claude reads whatever it hasn't read yet (operator logs,
   P&ID drawings, document sections), then the register is rebuilt with what it found.
4. Web UI: built when missing or older than its source.
5. Serve on http://127.0.0.1:8000.

`--fresh` starts over: empties the database and, for the fake plant, makes new data. Claude's
readings are kept (they are keyed by the text they read), so a fresh start doesn't pay to read the
same logs and drawings again. `--demo okan` (or offshore, onshore) picks a fake plant and its pack.
"""

import os
import shutil
import subprocess
from pathlib import Path

from . import db, ingest, pipeline

ROOT = Path(__file__).resolve().parents[2]
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
    asset = fake.ASSET_FOR_PACK.get(os.environ.get("FUZER_PACK") or "", "offshore")
    folder = folder or fake.DATA / asset
    say(f"Making the fake {asset} plant in {folder} ...")
    fake.make(folder, asset)
    return folder


def load(conn, pack, folder: Path, fresh: bool) -> None:
    if fresh:
        say("Emptying the database (Claude's readings are kept) ...")
        kept = conn.execute("select to_regclass('llm_extraction') as t").fetchone()["t"]
        if kept:
            conn.execute("create temp table keep_llm as select * from llm_extraction")
        db.reset(conn)
        if kept:
            conn.execute("insert into llm_extraction select * from keep_llm on conflict do nothing")
            conn.execute("drop table keep_llm")
            conn.commit()
    db.init_schema(conn)
    stats = ingest.ingest(conn, folder)
    changed = stats["inserted"] + stats["updated"] + stats["removed"]
    empty = not conn.execute("select 1 from entity limit 1").fetchone()
    say(f"Data: {stats['records']:,} records read, {changed:,} changed" + (
        f"; {len(stats['unmapped'])} tables without a mapping (Review > New tables after "
        "fuzer suggest-mappings)" if stats.get("unmapped") else ""))
    if changed or empty:
        say("Building the register ...")
        r = pipeline.rebuild(conn, pack)
        say(f"Register: {r['entities']:,} entities, {r['edges']:,} links")


def read_with_claude(conn, pack) -> None:
    """Have Claude read everything it hasn't read yet: operator logs (links and events), P&ID drawings
    (tags and piping) and document sections (failure modes); then rebuild once with what it found.
    Readings are cached by the text they read, so only new or changed records cost anything."""
    from . import llm_docs, llm_links, llm_pids
    from .pricing import cost

    readers = [("operator log entries", "entries", llm_links.run), ("drawings", "sheets", llm_pids.run),
               ("document sections", "sections", llm_docs.run)]
    todo = [(label, key, run, n) for label, key, run in readers if (n := run(conn, pack, dry_run=True)[key])]
    if not todo:
        return
    what = ", ".join(f"{n:,} {label}" for label, _, _, n in todo)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        say(f"Not read by Claude yet: {what}. Add ANTHROPIC_API_KEY to .env and run fuzer up again.")
        return
    say(f"Claude ({llm_links.DEFAULT_MODEL}) is reading {what}. This can take a few minutes ...")
    stored, spent = 0, 0.0
    for label, _, run, _ in todo:
        out = run(conn, pack)
        stored += out.get("stored", 0)
        spent += cost(out.get("model", llm_links.DEFAULT_MODEL), out.get("input_tokens", 0),
                      out.get("output_tokens", 0), out.get("cache_read_tokens", 0))[0]
        for err in out.get("errors", [])[:3]:
            say(f"Could not read some {label}: {err}")
    say(f"Claude read {stored:,} item(s) for about ${spent:.2f}")
    if stored:
        say("Rebuilding the register with what Claude found ...")
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
    from .pack import load_pack

    fake = _fake_plant()
    if fake is None or name not in fake.PACKS:
        raise SystemExit(f"Unknown demo {name!r}" + (f"; one of {', '.join(fake.PACKS)}" if fake else
                                                     " (the fakeplant package isn't installed)"))
    os.environ["FUZER_PACK"] = fake.PACKS[name]
    os.environ["FUZER_DATA_DIR"] = str(fake.DATA / name)
    return load_pack(fake.PACKS[name])


def other_asset(conn, pack) -> str | None:
    """The field already loaded, when it isn't this pack's (two assets in one database get mixed up)."""
    row = conn.execute("select source_key from source_record where source = 'sap' and record_type = 'floc' "
                       "and coalesce(payload->>'parent', '') = '' limit 1").fetchone()
    return row["source_key"] if row and row["source_key"] != pack.facility else None


def up(pack, host: str = "127.0.0.1", port: int = 8000, fresh: bool = False, demo: str | None = None) -> None:
    if not demo and not os.environ.get("FUZER_DATA_DIR") and not os.environ.get("FUZER_PACK") and _fake_plant():
        demo = DEFAULT_DEMO  # nothing configured: the Okan field demo
    print("Starting Fuzer" + (f": {demo} demo" if demo else ""), flush=True)
    if demo:
        pack = use_demo(demo)
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
    print(f"\nFuzer is running: http://{host}:{port}   (Ctrl+C to stop)\n", flush=True)
    import uvicorn

    uvicorn.run("fuzer.api:app", host=host, port=port, log_level="warning")
