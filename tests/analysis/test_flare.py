"""P3.1e (#351): a flare above its own baseline is raised for the HSE specialist with the downtime, log entries and
detections around it, or with its cause unknown; on Okan, the gas plant curtailments of February and March 2025 and
one unexplained day in November 2025."""

import json

import pytest

from fuzer.analysis import flare
from fuzer.core import detectors
from fuzer.core.pack import load_pack

PACK = load_pack("okan")


@pytest.mark.unit
def test_an_episode_is_a_run_above_the_baseline():
    series = [(f"2025-01-01T00:{m:02d}", v) for m, v in zip(range(0, 60, 10), [3, 3, 40, 50, 3, 30])]
    runs = flare._runs(series, 9)
    assert [[t for t, _ in r] for r in runs] == [["2025-01-01T00:20", "2025-01-01T00:30"]]  # one reading is no run
    assert flare.FLARING.search("Flare back to purge") and not flare.FLARING.search("flareless")


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


def test_a_flare_event_is_raised_with_its_records_or_unknown(conn):
    from fuzer.workflow import engine

    engine.load_defaults(conn)
    conn.commit()
    assert not detectors.run(conn, PACK, "flare_events", until="2025-10-01T00:00")["raised"]
    out = detectors.run(conn, PACK, "flare_events", until="2025-11-09T00:00")
    assert [(r["attributed"]) for r in out["raised"]] == [False] and out["unknown"] == 1, out
    x = conn.execute("select * from exception_record where type = 'flare_event' and key like '%2025-11-08%'").fetchone()
    said = json.dumps(x["evidence"])
    assert "cause unknown" in x["title"] and "unknown: no downtime" in said and "not an emissions inventory" in said
    assert "an estimate" in said
    assert conn.execute("select role from work_item where id = %s", (x["item_id"],)).fetchone()["role"] == \
        "hse_specialist"
    curtailed = detectors.run(conn, PACK, "flare_events", until="2025-02-11T00:00")
    assert curtailed["raised"] and all(r["attributed"] for r in curtailed["raised"]), curtailed
    y = conn.execute("select evidence from exception_record where id = %s",
                     (curtailed["raised"][0]["exception_id"],)).fetchone()["evidence"]
    assert {c["kind"] for c in y["causes"]} >= {"downtime", "log_entry"} and "Escravos" in json.dumps(y)
    conn.commit()
