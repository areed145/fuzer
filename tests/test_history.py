"""Source record history: updates and removals keep the previous version."""

import csv
import os
import tempfile
from pathlib import Path

import pytest

from fakeplant import offshore
from fuzer import db, ingest, pipeline, queries
from fuzer.pack import load_pack

PACK = load_pack()


@pytest.fixture(scope="module")
def setup():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzdata"))
    offshore.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c, data


def edit_csv(path: Path, key_col: str, key: str, changes: dict | None):
    rows = list(csv.DictReader(path.open()))
    out = [r if r[key_col] != key else {**r, **changes} for r in rows if changes is not None or r[key_col] != key]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(out)


def test_unchanged_reload_writes_nothing(setup):
    conn, data = setup
    stats = ingest.ingest(conn, data)
    assert stats["inserted"] == stats["updated"] == stats["removed"] == 0
    assert conn.execute("select count(*) as n from source_record_history").fetchone()["n"] == 0


def test_update_and_removal_are_kept(setup):
    conn, data = setup
    equnr = next(csv.DictReader((data / "sap_equipment.csv").open()))["equnr"]
    edit_csv(data / "sap_equipment.csv", "equnr", equnr, {"manufacturer": "Corrected Maker", "model": "NEW-1"})
    last_log = list(csv.DictReader((data / "oplogs.csv").open()))[-1]["entry_id"]
    edit_csv(data / "oplogs.csv", "entry_id", last_log, None)
    stats = ingest.ingest(conn, data)
    assert stats["updated"] == 1 and stats["removed"] == 1
    pipeline.rebuild(conn, PACK)

    fid = conn.execute("select fuzer_id from crosswalk where source = 'sap' and record_type = 'equipment' "
                       "and source_key = %s", (equnr,)).fetchone()["fuzer_id"]
    changes = queries.record_history(conn, PACK, fid)
    assert changes[0]["change"] == "updated"
    assert changes[0]["fields"]["manufacturer"]["to"] == "Corrected Maker"
    assert set(changes[0]["fields"]) == {"manufacturer", "model"}

    removed = conn.execute("select change from source_record_history where source = 'oplog' and source_key = %s",
                           (last_log,)).fetchone()
    assert removed["change"] == "removed"
