"""Load Okan once into an empty register, including a database with an existing schema."""

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
        db.init_schema(conn)
        roots = {r["source_key"] for r in conn.execute(
            "select source_key from source_record where source = 'sap' and record_type = 'floc' "
            "and coalesce(payload->>'parent', '') = ''"
        )}
        if roots - {"OKAN"}:
            raise RuntimeError("Okan bootstrap refuses a database holding another asset; no data was reset")
        marker = conn.execute("select to_regclass('public.fuzer_demo_bootstrap') as t").fetchone()["t"]
        if marker is None:
            populated = conn.execute("select 1 from source_record limit 1").fetchone()
            entities = conn.execute("select 1 from entity limit 1").fetchone()
            if populated or entities:
                if roots == {"OKAN"} and entities:
                    print("Existing Okan register found; leaving data and reviewer decisions unchanged.", flush=True)
                    return
                raise RuntimeError("Okan bootstrap requires an empty register or a built Okan register; "
                                   "existing data was left unchanged")
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

        print("Loading the Okan sample into the empty register...", flush=True)
        catalog.seed(conn)
        stats = ingest.ingest(conn, folder)
        if not stats["records"]:
            raise RuntimeError("Okan ingest read zero records; check source catalog connections and data files")
        built = pipeline.rebuild(conn, load_pack("okan"))
        if not built["entities"]:
            raise RuntimeError("Okan rebuild produced no entities; bootstrap remains incomplete")
        conn.execute("update fuzer_demo_bootstrap set complete = true where id = 1")
        conn.commit()
        print("Okan sample loaded.", flush=True)
    finally:
        conn.rollback()
        conn.execute("select pg_advisory_unlock(%s)", (LOCK,))
        conn.commit()


def main() -> None:
    if not os.environ.get("FUZER_DATABASE_URL"):
        raise SystemExit("FUZER_DATABASE_URL must point to the Render Okan test database")
    folder = Path(os.environ.get("FUZER_DATA_DIR", "fakeplant/data/okan"))
    with db.connect() as conn:
        seed(conn, folder)


if __name__ == "__main__":
    main()
