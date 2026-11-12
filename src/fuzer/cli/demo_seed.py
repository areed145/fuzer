"""Load Okan once into an empty register, including a database with an existing schema, and bring
new built-in mappings and changed exports to one that is already built."""

import os
from pathlib import Path

from ..core import bundles, db, projects
from ..core.pack import current_pack, load_pack
from ..ops import pipeline
from ..resources import BUNDLES
from ..sources import catalog, ingest

LOCK = 0x465A444D  # session lock: held across the ingest and rebuild commits


def refresh(conn, folder: Path, pack=None) -> None:
    """A deploy brings new exports and mappings in the image; an existing register needs them too.

    Failing here must not take the application down, so it reports and carries on with what's
    loaded. It isn't retried: the failure may have left a load part applied, so Sources shows what
    was recorded, and the next deploy, or a deliberate `fuzer ingest`, runs it again.
    """
    try:
        seeded = catalog.seed(conn)
        stats = ingest.ingest(conn, folder)
        changed = stats["inserted"] + stats["updated"] + stats["removed"]
        for outcome in ("held", "paused", "missing"):
            if stats[outcome]:
                print(f"Data refresh {outcome}: {', '.join(stats[outcome])}", flush=True)
        if seeded or changed:
            pack = pack or load_pack("okan")
            built = pipeline.rebuild(conn, pack)
            # what follows every register build (the work board, plan and SAP notification intake), so work in the
            # new records reaches the IOC's queue at deploy, not at the next scheduled register run
            notified = pipeline.follow_register(conn, pack)["notifications"]
            conn.commit()
            print(f"Data refreshed: {changed} records changed, {built['entities']} entities, "
                  f"{notified.get('raised', 0)} notifications raised.", flush=True)
        else:
            print("Data unchanged.", flush=True)
    except Exception as e:
        conn.rollback()
        print(f"Data refresh failed; running on the loaded register. Inspect Sources before retrying: {e}", flush=True)


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
                    print("Existing Okan register found; keeping settings and reviewer decisions.", flush=True)
                    refresh(conn, folder)
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
                print("Okan demo already initialized; keeping settings and reviewer decisions.", flush=True)
                refresh(conn, folder)
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


def register_projects(conn, folder: Path = BUNDLES, by: str = "deploy") -> list[str]:
    """Make Okan (already seeded) the database's first project, then add every other bundle in fakeplant/bundles
    beside it, each in its own schema, and load its exports there. Returns the projects added. A bundle that
    fails is reported and skipped: the application still starts with the projects that are ready."""
    if not (folder / "okan" / "bundle.yaml").is_file():
        print(f"No seed bundles in {folder}; serving Okan only.", flush=True)
        return []
    try:
        print(f"Project okan: {bundles.init(conn, folder / 'okan', by=by, upgrade=True)['status']}", flush=True)
    except bundles.BundleError as e:
        conn.rollback()
        print(f"Okan isn't registered as a project, so no other project is added: {e}", flush=True)
        return []
    added = []
    for path in sorted(p for p in folder.iterdir() if p.name != "okan" and (p / "bundle.yaml").is_file()):
        try:
            out = bundles.init(conn, path, by=by, upgrade=True)
            print(f"Project {out['project']}: {out['status']} ({out.get('schema')})", flush=True)
            with projects.use(conn, out["project"]), db.connect() as c:
                refresh(c, Path(out["data"]), current_pack(c))
            added.append(out["project"])
        except Exception as e:
            conn.rollback()
            print(f"Project {path.name} isn't registered: {e}", flush=True)
    return added


def main() -> None:
    if not os.environ.get("FUZER_DATABASE_URL"):
        raise SystemExit("FUZER_DATABASE_URL must point to the Render Okan test database")
    folder = Path(os.environ.get("FUZER_DATA_DIR", "fakeplant/data/okan"))
    with db.connect() as conn:
        seed(conn, folder)


if __name__ == "__main__":
    main()
