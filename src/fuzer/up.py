"""`fuzer up`: one command from nothing to the web UI.

1. Data: the exports in $FUZER_DATA_DIR. With none there and the fake plant installed, it makes one.
2. Load: ingest, and rebuild only when something changed (a second start is quick).
3. Drawings: with ANTHROPIC_API_KEY set, Claude reads up to MAX_AUTO_DRAWINGS new drawings.
4. Web UI: built when missing or older than its source.
5. Serve on http://127.0.0.1:8000.

`--fresh` starts over: empties the database and, for the fake plant, makes new data.
"""

import os
import shutil
import subprocess
from pathlib import Path

from . import db, ingest, pipeline

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
MAX_AUTO_DRAWINGS = 5


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
    folder = folder or fake.DATA / "offshore"
    say(f"Making a fake plant in {folder} ...")
    asset = "onshore" if "onshore" in (os.environ.get("FUZER_PACK") or "") else "offshore"
    fake.make(folder, asset)
    return folder


def load(conn, pack, folder: Path, fresh: bool) -> None:
    if fresh:
        say("Emptying the database ...")
        db.reset(conn)
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


def read_drawings(conn, pack) -> None:
    from . import llm_pids

    todo = llm_pids.run(conn, pack, dry_run=True)["sheets"]
    if not todo:
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        say(f"{todo} drawing(s) not read yet: add ANTHROPIC_API_KEY to .env and run fuzer up again.")
        return
    if todo > MAX_AUTO_DRAWINGS:
        say(f"{todo} drawings not read yet; that's more than fuzer up reads on its own. "
            "Run fuzer llm-link --source pids --dry-run to see the cost.")
        return
    say(f"Claude is reading {todo} drawing(s) ...")
    out = llm_pids.run(conn, pack)
    if out.get("stored"):
        say(f"Read {out['stored']}: {out['tags_linked']} tags linked, {out['findings']} findings")
        pipeline.rebuild(conn, pack)
    for err in out.get("errors", []):
        say(f"Could not read: {err}")


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


def up(pack, host: str = "127.0.0.1", port: int = 8000, fresh: bool = False) -> None:
    print("Starting Fuzer", flush=True)
    folder = data_folder(fresh)
    os.environ["FUZER_DATA_DIR"] = str(folder)
    with db.connect() as conn:
        load(conn, pack, folder, fresh)
        read_drawings(conn, pack)
    build_ui()
    print(f"\nFuzer is running: http://{host}:{port}   (Ctrl+C to stop)\n", flush=True)
    import uvicorn

    uvicorn.run("fuzer.api:app", host=host, port=port, log_level="warning")
