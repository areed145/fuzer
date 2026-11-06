"""Initialize a dedicated, empty database with the Okan sample exactly once."""

import os
from pathlib import Path

from ..core import db
from ..core.pack import load_pack
from ..ops import pipeline
from ..sources import catalog, ingest

LOCK = 0x465A444D  # session lock: held across the ingest and rebuild commits


def seed(conn, folder: Path) -> None:
    if not (folder / "process_model.json").is_file() or not (folder / "sap_floc.csv").is_file():
        raise RuntimeError(f"Okan exports are missing from {folder}")
    conn.execute("select pg_advisory_lock(%s)", (LOCK,))
    try:
        marker = conn.execute("select to_regclass('public.fuzer_demo_bootstrap') as t").fetchone()["t"]
        if marker is None:
            existing = conn.execute(
                "select tablename from pg_tables where schemaname = 'public' limit 1"
            ).fetchone()
            if existing:
                raise RuntimeError("Okan bootstrap requires a dedicated empty database; existing tables found")
            conn.execute("create table fuzer_demo_bootstrap (id integer primary key, complete boolean not null)")
            conn.execute("insert into fuzer_demo_bootstrap values (1, false)")
            conn.commit()
        else:
            row = conn.execute("select complete from fuzer_demo_bootstrap where id = 1").fetchone()
            if row is None:
                raise RuntimeError("Okan bootstrap marker is invalid")
            if row["complete"]:
                print("Okan demo already initialized; leaving database unchanged.", flush=True)
                return

        print("Loading the Okan sample into the dedicated demo database...", flush=True)
        db.init_schema(conn)
        catalog.seed(conn)
        ingest.ingest(conn, folder)
        pipeline.rebuild(conn, load_pack("okan"))
        conn.execute("update fuzer_demo_bootstrap set complete = true where id = 1")
        conn.commit()
        print("Okan sample loaded.", flush=True)
    finally:
        conn.rollback()
        conn.execute("select pg_advisory_unlock(%s)", (LOCK,))
        conn.commit()


def main() -> None:
    if not os.environ.get("FUZER_DATABASE_URL"):
        raise SystemExit("FUZER_DATABASE_URL must point to a dedicated empty demo database")
    folder = Path(os.environ.get("FUZER_DATA_DIR", "fakeplant/data/okan"))
    with db.connect() as conn:
        seed(conn, folder)


if __name__ == "__main__":
    main()
