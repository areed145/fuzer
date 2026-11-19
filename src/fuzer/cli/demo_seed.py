"""Load the deployment's first seed bundle once into an empty register, including a database with an existing
schema, and bring new built-in mappings and changed exports to one that is already built. Which bundle is the
deployment's choice (`python -m fuzer.cli.deploy --bundle <folder>`); nothing here names a facility."""

import os
import re
from pathlib import Path

from ..core import bundles, db, projects
from ..core.pack import current_pack
from ..ops import pipeline
from ..resources import BUNDLES
from ..sources import catalog, ingest

LOCK = 0x465A444D  # session lock: held across the ingest and rebuild commits
# The exports moved to US oilfield units (oil_sm3 is oil_bbl, whp_barg whp_psig, station_m station_ft): a column
# named for a metric unit in a dataset's last-loaded schema is that move, not drift for the steward to review.
METRIC_COLUMN = re.compile(r"(_(sm3d?|ksm3d?|sm3_sm3|mj_sm3|barg|bara|bar|mm|mm_y|m|km|kw|gj|rm3|kg|degc)|^kp)$", re.IGNORECASE)


def _units_moved(conn) -> list[str]:
    """Forget the last-loaded schema of each dataset still on metric column names, so its US export loads instead
    of being held as drift. Idempotent: once loaded, no metric name is left to match."""
    moved = [r["name"] for r in conn.execute("select name, schema from source_dataset where schema is not null")
             if any(METRIC_COLUMN.search(str(c[0])) for c in r["schema"] or [])]
    if moved:
        conn.execute("update source_dataset set schema = null where name = any(%s)", (moved,))
        print(f"US oilfield units: {len(moved)} datasets reload on their new column names", flush=True)
    return moved


def refresh(conn, folder: Path, pack=None) -> None:
    """A deploy brings new exports and mappings in the image; an existing register needs them too.

    Failing here must not take the application down, so it reports and carries on with what's
    loaded. It isn't retried: the failure may have left a load part applied, so Sources shows what
    was recorded, and the next deploy, or a deliberate `fuzer ingest`, runs it again.
    """
    try:
        seeded = catalog.seed(conn)
        _units_moved(conn)
        from ..core import access
        from ..sources import demo_hold

        held = demo_hold.seed(conn) if access.demo_enabled() else None  # a held load for the steward to fix (#1145)
        stats = ingest.ingest(conn, folder)
        if held:
            from ..graph import data_work

            data_work.sync(conn)  # its case in the steward's queue now, not at the next rebuild
            conn.commit()
            print(f"Demo: {held} held for the data steward to review.", flush=True)
        changed = stats["inserted"] + stats["updated"] + stats["removed"]
        for outcome in ("held", "paused", "missing"):
            if stats[outcome]:
                print(f"Data refresh {outcome}: {', '.join(stats[outcome])}", flush=True)
        if seeded or changed:
            pack = pack or current_pack(conn)
            built = pipeline.rebuild(conn, pack)
            # what follows every register build (the work board, plan and SAP notification intake), so work in the
            # new records reaches the IOC's queue at deploy, not at the next scheduled register run
            notified = pipeline.follow_register(conn, pack)["notifications"]
            conn.commit()
            print(f"Data refreshed: {changed} records changed, {built['entities']} entities, "
                  f"{notified.get('raised', 0)} notifications raised.", flush=True)
        else:
            # the plan and the work board still follow the register: a deploy can bring a new way of reading the
            # same records (the planning system's schedules on Plan, P5.18), and nothing else would apply it until
            # the data next changed
            pack = pack or current_pack(conn)
            planned = pipeline.follow_register(conn, pack)["plan"]
            conn.commit()
            print(f"Data unchanged; the plan follows the register: {planned.get('added', 0)} orders added, "
                  f"{(planned.get('planning_system') or {}).get('activities', 0)} planning-system activities.", flush=True)
    except Exception as e:
        conn.rollback()
        print(f"Data refresh failed; running on the loaded register. Inspect Sources before retrying: {e}", flush=True)


def seed(conn, bundle: Path) -> dict:
    """Load the bundle's exports into an empty register as the database's first project (once), else refresh it.
    Returns the bundle as read."""
    b = bundles.read(bundle)
    pack, folder, facility = b["pack"], Path(b["config"].get("data") or ""), b["pack"].facility
    if not (folder / "sap_floc.csv").is_file():
        raise RuntimeError(f"{facility} exports are missing from {folder}")
    conn.execute("select pg_advisory_lock(%s)", (LOCK,))
    try:
        db.init_schema(conn)
        if (first := projects.active(conn)) and first != b["name"]:
            raise RuntimeError(f"this database's first project is {first}, not {b['name']}; no data was reset")
        roots = {r["source_key"] for r in conn.execute(
            "select source_key from source_record where source = 'sap' and record_type = 'floc' "
            "and coalesce(payload->>'parent', '') = ''"
        )}
        if roots - {facility}:
            raise RuntimeError(f"{facility} bootstrap refuses a database holding another asset; no data was reset")
        marker = conn.execute("select to_regclass('public.fuzer_demo_bootstrap') as t").fetchone()["t"]
        if marker is None:
            populated = conn.execute("select 1 from source_record limit 1").fetchone()
            entities = conn.execute("select 1 from entity limit 1").fetchone()
            if populated or entities:
                if roots == {facility} and entities:
                    print(f"Existing {facility} register found; keeping settings and reviewer decisions.", flush=True)
                    bundles.init(conn, bundle, by="deploy", upgrade=True)
                    refresh(conn, folder, pack)
                    return b
                raise RuntimeError(f"{facility} bootstrap requires an empty register or a built {facility} register; "
                                   "existing data was left unchanged")
            conn.execute("create table fuzer_demo_bootstrap (id integer primary key, complete boolean not null)")
            conn.execute("insert into fuzer_demo_bootstrap values (1, false)")
            conn.commit()
        else:
            row = conn.execute("select complete from fuzer_demo_bootstrap where id = 1").fetchone()
            if row is None:
                raise RuntimeError(f"{facility} bootstrap marker is invalid")
            if row["complete"]:
                print(f"{facility} demo already initialized; keeping settings and reviewer decisions.", flush=True)
                bundles.init(conn, bundle, by="deploy", upgrade=True)
                refresh(conn, folder, pack)
                return b

        bundles.init(conn, bundle, by="deploy", upgrade=True)  # the project first: its pack serves what follows
        print(f"Loading the {facility} sample into the empty register...", flush=True)
        catalog.seed(conn)
        stats = ingest.ingest(conn, folder)
        if not stats["records"]:
            raise RuntimeError(f"{facility} ingest read zero records; check source catalog connections and data files")
        built = pipeline.rebuild(conn, pack)
        if not built["entities"]:
            raise RuntimeError(f"{facility} rebuild produced no entities; bootstrap remains incomplete")
        conn.execute("update fuzer_demo_bootstrap set complete = true where id = 1")
        conn.commit()
        print(f"{facility} sample loaded.", flush=True)
        return b
    finally:
        conn.rollback()
        conn.execute("select pg_advisory_unlock(%s)", (LOCK,))
        conn.commit()


def register_projects(conn, first: str, folder: Path = BUNDLES, by: str = "deploy") -> list[str]:
    """Add every shipped bundle in `folder` other than the database's first project (`first`, already seeded)
    beside it, each in its own schema, and load its exports there. Returns the projects added. A bundle that
    fails is reported and skipped: the application still starts with the projects that are ready."""
    if projects.active(conn) != first:
        print(f"{first} isn't registered as the first project, so no other project is added.", flush=True)
        return []
    added = []
    for path in sorted(p for p in folder.iterdir() if p.name != first and (p / "bundle.yaml").is_file()):
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


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="Load a seed bundle's exports into the test database once.")
    p.add_argument("--bundle", required=True, help="the bundle folder (with bundle.yaml)")
    args = p.parse_args(argv)
    if not os.environ.get("FUZER_DATABASE_URL"):
        raise SystemExit("FUZER_DATABASE_URL must point to the test database")
    with db.connect() as conn:
        seed(conn, Path(args.bundle))


if __name__ == "__main__":
    main()
