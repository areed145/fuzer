"""The full ops eval (#103b) on the fake client: every scheduled job scored over Okan's 4-7 November
2024 (CI scores the whole week), the gate passing; a score under its floor fails it."""

import os
import tempfile
from pathlib import Path

import pytest

from fakeplant import okan
from fuzer import ops_eval
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.graph import pipeline
from fuzer.sources import ingest

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def setup():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzokan"))
    okan.generate(data)
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATA_DIR", str(data))
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c, data
    mp.undo()


def test_full_eval_on_the_fake_client(setup):
    conn, data = setup
    res = ops_eval.run(conn, PACK, data, "2024-11-04T00:00", "2024-11-07T00:00")  # CI scores the whole week
    assert res["sre"]["recall"] == 1.0 and res["grouping"]["groups_per_trip"] == 1
    d = res["diagnosis"]
    assert d["client"] == "scripted" and d["diagnosed"] >= 1 and d["claude_usd"] == 0.0
    assert d["scored"] >= 1 and d["first_cause_named"] >= 0.5
    r = res["daily_report"]
    assert r["days"] == 3 and r["all_checks_ok"] == 1.0 and r["trip_days_led_by_the_trip"] == 1.0
    assert r["with_operating_instructions"] == 0
    assert res["opportunities"]["gas_lift_reallocations"] >= 1 and res["opportunities"]["worst_gain_error"] < 0.02
    assert ops_eval.gate(res) == []
    worse = {**res, "sre": {**res["sre"], "recall": 0.5}}
    assert ops_eval.gate(worse) == ["sre.recall 0.5 < 0.9"]
    assert ops_eval.INSTRUCTION.search("Set the choke to 40% on J07-03")
    assert not ops_eval.INSTRUCTION.search("Lowering the separator by 2 bar would gain 168 Sm3/d")
