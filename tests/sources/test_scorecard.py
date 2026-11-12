"""P2.6: every dataset has four quality measures with a trend, and a seeded drop in one export is one case for its
owner, not one per record, that clears when the export recovers."""

import csv
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.sources import quality, scorecard

pytestmark = pytest.mark.sequence


def _open(conn) -> dict[str, dict]:
    return {r["key"]: r for r in conn.execute(
        "select x.key, x.title, w.assignee, w.role from exception_record x left join work_item w on w.id = x.item_id "
        "where x.type = 'source_quality_drop' and x.state = 'open'")}


def _degraded(data: Path, name: str, share: float = 0.6) -> Path:
    """A copy of the export with most of its columns emptied on most rows: a delivery gone wrong upstream."""
    folder = Path(tempfile.mkdtemp(prefix="fzdrop"))
    src = next(data.glob(f"{name}.csv"))
    with src.open(newline="") as f:
        rows = list(csv.DictReader(f))
    cols = list(rows[0])
    blank = cols[1:]  # keep the key
    for i, r in enumerate(rows):
        if i < share * len(rows):
            r.update({c: "" for c in blank})
    with (folder / src.name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    for other in data.iterdir():  # the rest of the delivery as it was
        if other.name != src.name and other.is_file():
            shutil.copy(other, folder / other.name)
    return folder


def test_a_seeded_drop_in_one_export_is_one_case_for_its_owner(okan_db):
    from fuzer import api

    conn, data = okan_db.conn, Path(okan_db.data)
    t = scorecard.trend(conn, "sap_equipment")
    assert t and t[-1]["completeness"] and t[-1]["validity"] is not None  # recorded by the rebuild
    assert not _open(conn)
    with TestClient(api.app) as client:
        r = client.patch("/sources/systems/sap", json={"values": {"owner": "bo"}, "by": "ana", "reason": "SAP's owner"})
        assert r.status_code == 200, r.text
        before = client.get("/sources/datasets/sap_equipment/scorecard").json()
        assert [m["key"] for m in before["measures"]] == list(scorecard.MEASURES)

        quality.profile_now(conn, "sap_equipment", _degraded(data, "sap_equipment"))
        out = scorecard.sync(conn)
        conn.commit()
        assert out["raised"] == 1
        cases = _open(conn)
        assert list(cases) == ["sap_equipment"] and cases["sap_equipment"]["assignee"] == "bo"
        assert cases["sap_equipment"]["title"].startswith("sap_equipment: completeness fell from")
        after = client.get("/sources/datasets/sap_equipment/scorecard").json()
        comp = next(m for m in after["measures"] if m["key"] == "completeness")
        assert comp["dropped"] and comp["now"] < comp["best"] - scorecard.DROP and len(comp["trend"]) >= 2
        page = client.get("/sources/datasets/sap_equipment").json()  # the case is on the dataset's page
        assert [w["title"] for w in page["work_items"]] == [cases["sap_equipment"]["title"]]

    quality.profile_now(conn, "sap_equipment", data)  # the next delivery is whole again
    assert scorecard.sync(conn)["cleared"] == 1 and not _open(conn)
    conn.commit()


def test_kestrel_datasets_are_scored(request):
    conn = request.getfixturevalue("kestrel")
    scored = {r["name"] for r in conn.execute("select distinct d.name from source_score s "
                                              "join source_dataset d on d.id = s.dataset_id")}
    assert len(scored) >= 5 and not _open(conn)
