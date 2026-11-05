"""The project-schedules Delta table for #227: built from Okan's records, read by Fuzer's Delta connector, with
the mess the answer key lists, and a second version for the update step."""

import tempfile
from pathlib import Path

from fakeplant import schedules
from fuzer.sources import connectors


def test_schedules_delta_table(okan_data):
    lake = Path(tempfile.mkdtemp(prefix="fzlake"))
    out = schedules.write(okan_data, lake)
    assert out["version"] == 0
    c = connectors.find(lake, "project_schedules")
    assert c.kind == "delta" and "project_schedules" in connectors.datasets(lake)
    rows = list(c.rows())
    ids = [r["activity_id"] for r in rows]
    assert len(rows) == out["rows"] and len(ids) - len(set(ids)) == 1  # one duplicate key
    tar = [r for r in rows if r["project_id"] == "OK-TAR-2026" and r["work_order"]]
    assert tar and all(r["work_order"].startswith("5000") for r in tar)
    assert max(r["percent_complete"] for r in rows) == 112 and any(r["location"] == "FLARE-TIP-9" for r in rows)
    assert all(r["start"] is None or r["start"][2] == "/" for r in rows)  # dd/mm/yyyy

    again = schedules.write(okan_data, lake, update=True)
    assert again["version"] == 1
    v1 = list(connectors.find(lake, "project_schedules").rows())
    assert not any(r["activity_id"] == "OK-MOC-0233" for r in v1) and max(r["percent_complete"] for r in v1) == 100
