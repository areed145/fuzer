"""Detectors tune from dispositions on the fake plant (P6.3, #944): a detector made noisy on Okan, its cases closed out
against the plant's injected events, produces one tuning proposal (P6.3b) that is quieter on replay and still raises
the real cases."""

from datetime import datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from fuzer.core import detectors, settings, work
from fuzer.core.pack import load_pack
from fuzer.evals import replay
from fuzer.ops import disposition, sre, tuning
from fuzer.sources import process_model, timeseries

PACK = load_pack("okan")
DAY = datetime(2024, 11, 4)  # Train C's trips, a week the replay scores (tests/ops/test_sre.py)
WINDOWS = [(DAY + timedelta(hours=h), DAY + timedelta(hours=h + 6)) for h in range(0, 48, 6)]


@pytest.fixture(scope="module")
def noisy(okan_db, okan_data):
    """Steps at a sensitivity far below its usual 6, each detection a case closed out as the plant's events say:
    real where it finds an injected upset on its own unit, a false alarm where nothing explains it."""
    conn, src = okan_db.conn, timeseries.SyntheticProvider(okan_data)
    spec = process_model.read(okan_data / "process_model.json")
    events = replay.truth(spec, WINDOWS[0][0], WINDOWS[-1][1])
    unit_of = {p: u for p, (u, _) in spec["points"].items()}
    settings.set(conn, "signals.step_z", 2.0, by="test", reason="made noisy for the test")
    labelled: dict[tuple, list] = {}
    for a, b in WINDOWS:
        for d in sre.steps(conn, PACK, a, b, src):
            d["detected_at"] = b
            u = unit_of.get(d["point"])
            if any(replay._hits(d, u, e, "own") for e in events if e["upset"]):
                labelled.setdefault((a, b), []).append((d, "real"))
            elif not any(replay._hits(d, u, e) for e in events):
                labelled.setdefault((a, b), []).append((d, "false_alarm"))
    real = [w for w, ds in labelled.items() if any(x == "real" for _, x in ds)]
    noisy = sorted((w for w in labelled if w not in real), key=lambda w: -len(labelled[w]))
    keep = (real + noisy)[:3]  # a few windows, so the proposal's replay stays short
    made = []
    for w in keep:
        for i, (d, closed_as) in enumerate(labelled[w]):
            item = work.open_case(conn, "manual", f"Step on {d['point']}", by="fuzer")["id"]
            conn.execute("insert into exception_record (type, key, title, subjects, evidence, item_id) "
                         "values ('upset', %s, %s, %s, %s, %s)",
                         (f"test-step-{item}", f"Step on {d['point']}", [x for x in (d["unit_id"], d["point_id"]) if x],
                          Jsonb({"finding": {"detector": "sre.steps", "start": d["start"].isoformat()}}), item))
            disposition.record(conn, PACK, item, "ana", closed_as,
                               **({"failure_mode": "VIB", "cause": "3", "activity": "9"} if closed_as == "real" else {}))
            made.append((item, closed_as))
    conn.commit()
    yield {"conn": conn, "src": src, "cases": made}
    settings.reset(conn, "signals.step_z", by="test", reason="put back")


def test_a_noisy_detector_gets_one_proposal_and_accepting_it_is_quieter_on_replay_with_every_real_case(noisy):
    conn, src, made = noisy["conn"], noisy["src"], noisy["cases"]
    assert sum(x == "false_alarm" for _, x in made) >= 5 and any(x == "real" for _, x in made), made
    assert tuning.closeouts(conn)["steps"]["noise_rate"] >= 0.5

    [p] = tuning.propose(conn, PACK, src)
    assert (p["detector"], p["param"], p["class"], p["current"]) == ("steps", "signals.step_z", None, 2.0)
    assert p["proposed"] > 2.0 and p["after"]["noise"] < p["before"]["noise"], p
    assert p["after"]["real"] == p["before"]["real"] and p["missed"] == [], p
    ex = conn.execute("select type, state, title, item_id, evidence from exception_record where id = %s",
                      (p["exception"]["id"],)).fetchone()
    assert ex["type"] == "detector_tuning" and ex["state"] == "open" and ex["item_id"]
    assert ex["title"] == f"Tune steps: signals.step_z 2.0 to {p['proposed']}"
    assert ex["evidence"]["missed"] == [] and ex["evidence"]["proposed"] == p["proposed"]
    assert settings.get(conn, "signals.step_z") == 2.0  # tried in the replay only, never written
    assert tuning.propose(conn, PACK, src) == []  # one open proposal per detector, parameter and class
    assert detectors.get("steps").quieter == {"signals.step_z": "up"}
    conn.commit()

    # P6.3c: a person accepts through the limit-change gate; the change is the detector's next version, and the
    # replay of its closed cases at the new value raises fewer noisy cases and every real one it raised before.
    from fuzer.api import settings as settings_api
    from fuzer.core import gates

    was = detectors.version(conn, "steps")
    with pytest.raises(gates.GateRefused):
        tuning.accept(conn, p["exception"]["id"], "agent:tuning")  # an agent never tunes a detector
    conn.rollback()
    [shown] = [x for x in settings_api.detector_tuning_open(conn) if x["id"] == p["exception"]["id"]]
    assert (shown["detector"], shown["proposed"], shown["missed"]) == ("steps", p["proposed"], [])

    out = settings_api.detector_tuning_accept(p["exception"]["id"], settings_api.TuningAccept(by="ana"), conn)
    assert out["value"] == p["proposed"] and out["version"] == was + 1
    assert settings.get(conn, "signals.step_z") == p["proposed"]
    [h] = settings.history(conn, "signals.step_z", 1)
    assert h["by"] == "ana" and f"case {p['exception']['item_id']}" in h["reason"]
    ex = conn.execute("select state, item_id from exception_record where id = %s", (p["exception"]["id"],)).fetchone()
    assert ex["state"] != "open"
    assert conn.execute("select state from work_item where id = %s", (ex["item_id"],)).fetchone()["state"] == "done"
    with pytest.raises(ValueError, match="already decided"):
        tuning.accept(conn, p["exception"]["id"], "ana")

    rows = [{**r, "start": tuning._start(r["started"])} for r in tuning.cases(conn)
            if r["detector"] == "steps" and r["disposition"] in tuning.COUNTED]
    again = tuning.replay(conn, PACK, detectors.get("steps"), rows, src)  # at the value now stored
    real = {r["item_id"] for r in rows if r["disposition"] == "real"}
    noisy_cases = {r["item_id"] for r in rows if r["disposition"] in tuning.NOISE}
    assert len(again & noisy_cases) < p["before"]["noise"] and len(again & real) == p["before"]["real"]
    conn.commit()
