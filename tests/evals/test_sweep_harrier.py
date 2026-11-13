"""P3.6b (#844): every work detector that can arise on Harrier, swept over its history and scored against the events its
evals/detections.yaml declares, keeps its floor. Cheap enough to run on every change (about half a minute)."""

from pathlib import Path

import pytest

from fuzer.core import detectors
from fuzer.core import pack as packs
from fuzer.evals import sweep

BUNDLE = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "harrier"


@pytest.mark.integration
def test_every_swept_detector_on_harrier_keeps_its_floor(harrier_db):
    conn = harrier_db
    res = sweep.run(conn, packs.current_pack(conn), BUNDLE)
    bad = [b for r in res.values() for b in sweep.gate(r)]
    assert not bad, bad
    scored = [r for r in res.values() if "not_here" not in r]
    assert scored and all(r.get("recall") == 1.0 for r in scored), [(r["detector"], r.get("missed")) for r in scored]
    for r in scored:
        sweep.store(conn, r)
    assert detectors.replay_score(conn, detectors.get(scored[0]["detector"]))["recall"] == 1.0
