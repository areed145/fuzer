"""P3.6a (#844): the sweep scorer runs each work detector over a bundle's history as a schedule runs it, and scores
its raises against the events the bundle declares for it (`evals/detections.yaml`): recall, precision and false
alarms a week, kept in `ops_eval` as `sweep:<detector>` so the Detection page shows them, under per-detector floors."""

from datetime import datetime
from pathlib import Path

import pytest

from fuzer.core import clock, detectors
from fuzer.evals import sweep

BUNDLES = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles"


@pytest.mark.unit
def test_raises_are_scored_against_the_declared_events():
    entry = {"since": "2025-01-01", "until": "2025-03-01", "every_days": 14,
             "events": [{"key": "K-1", "from": "2025-01-10", "to": "2025-01-20"},
                        {"key": "K-2", "from": "2025-02-01", "to": "2025-02-03"}]}
    ts = sweep.times(entry)
    assert ts[0] == datetime(2025, 1, 1) and datetime(2025, 2, 3) in ts  # an event's end is never stepped over
    raises = [{"key": "site · K-1", "at": datetime(2025, 1, 15)}, {"key": "K-1", "at": datetime(2025, 2, 26)},
              {"key": "K-9", "at": datetime(2025, 1, 29)}]
    s = sweep.score(entry, raises)
    assert (s["recall"], s["precision"], s["true"], s["false"], s["missed"]) == (0.5, 0.333, 1, 2, ["K-2"])
    assert s["false_alarms_per_week"] == round(2 / (59 / 7), 2)  # K-1 raised again after its window is false
    res = {"detector": "x", **s, "gates": {**sweep.GATES, "precision": 0.3}}
    assert sweep.gate(res) == ["x recall 0.5 < 1.0"]
    assert sweep.times({**entry, "at": ["2025-01-05"]}) == [datetime(2025, 1, 5), datetime(2025, 1, 20),
                                                            datetime(2025, 2, 3)]


@pytest.mark.unit
def test_a_detector_gets_the_time_arguments_its_signature_takes():
    a, b = datetime(2025, 1, 1), datetime(2025, 1, 8)
    assert sweep._args(lambda conn, pack, since, until: 0, a, b) == {"since": "2025-01-01T00:00",
                                                                      "until": "2025-01-08T00:00"}
    assert sweep._args(lambda conn, pack, start, end: 0, a, b)["end"] == "2025-01-08T00:00"
    assert sweep._args(lambda conn, until=None: 0, a, b) == {"until": "2025-01-08T00:00"}
    assert sweep._args(lambda conn, pack, at=None: 0, a, b) == {"at": "2025-01-08"}
    assert sweep._args(lambda conn, now=None: 0, a, b) == {"now": clock.aware(b)}
    assert sweep._args(lambda conn: 0, a, b) == {}


@pytest.mark.unit
@pytest.mark.parametrize("bundle", sorted(p.name for p in BUNDLES.iterdir() if (p / "evals" / sweep.FILE).exists()))
def test_each_declared_detection_names_a_registered_detector_and_its_events(bundle):
    names = detectors.registry()
    for name, e in sweep.plan(BUNDLES / bundle).items():
        assert name in names, f"{bundle}: no detector {name!r}"
        if "not_here" in e:
            assert e["not_here"].strip(), f"{bundle}: {name} says why it can't arise"
            continue
        assert e["since"] and e["until"] and (e.get("events") or e.get("gates")), name
        for ev in e.get("events", []):
            assert set(ev) == {"key", "from", "to"} and sweep._when(ev["from"]) <= sweep._when(ev["to"]), (name, ev)
        assert set(e.get("gates") or {}) <= set(sweep.GATES), name


CHEAP = ["quality_drift", "well_tests", "flare_events"]  # seconds each; the whole sweep is the e2e tier's


@pytest.mark.integration
def test_the_cheap_detectors_find_okans_injected_events_and_the_detection_page_shows_the_score(okan_db):
    from fuzer.core.pack import load_pack

    conn = okan_db.conn
    res = sweep.run(conn, load_pack("okan"), BUNDLES / "okan", CHEAP)
    for r in res.values():
        assert not sweep.gate(r), r
        assert r["recall"] == 1.0 and r["runs"] > 10, r
        sweep.store(conn, r)
    assert res["flare_events"]["precision"] == 1.0
    score = detectors.replay_score(conn, detectors.get("quality_drift"))
    assert score["recall"] == 1.0 and score["precision"] == res["quality_drift"]["precision"]
    assert detectors.replay_score(conn, detectors.get("records_gaps")) is None  # not swept yet: no score


@pytest.mark.e2e
def test_every_swept_detector_on_okan_keeps_its_floor(okan_db):
    from fuzer.core.pack import load_pack

    res = sweep.run(okan_db.conn, load_pack("okan"), BUNDLES / "okan")
    bad = [b for r in res.values() for b in sweep.gate(r)]
    assert not bad, bad
    assert all(r.get("recall") is not None or "not_here" in r for r in res.values()), res
