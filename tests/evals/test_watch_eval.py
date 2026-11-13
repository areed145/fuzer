"""P3.3b (#931): the agentic watch scored in shadow on replay. Its proposals are matched to the bundle's injected upsets
that no rule caught, and the share matched and the precision are kept per bundle for the Detection page. A scripted
model tests the wiring; a live score needs a provider key."""

import json
from datetime import datetime

import pytest

from fuzer.core import detectors
from fuzer.core.pack import load_pack
from fuzer.evals import watch_eval

T = datetime.fromisoformat
SPEC = {"units": {"train:A": {"type": "train"}, "K-1": {"type": "compressor", "train": "train:A"},
                  "P-1": {"type": "pump"}, "V-1": {"type": "vessel"}},
        "points": {"K1VT": ["K-1", "vibration"], "P1FT": ["P-1", "flow"], "V1LT": ["V-1", "liquid_level"]},
        "events": [{"unit": "K-1", "kind": "vibration", "start": "2024-03-08T02:00", "end": "2024-03-08T20:00"},
                   {"unit": "P-1", "kind": "trip", "start": "2024-03-09T04:00", "end": "2024-03-09T06:00"},
                   {"unit": "V-1", "kind": "setpoint", "at": "2024-03-09T10:00"}]}
SINCE, UNTIL = T("2024-03-08T00:00"), T("2024-03-10T00:00")


def prop(names, start=None, end=None, cited=True):
    return {"subject": names[0], "names": set(names), "start": start and T(start), "end": end and T(end),
            "since": SINCE, "until": UNTIL, "cited": cited}


@pytest.mark.unit
def test_proposals_are_matched_to_the_upsets_no_rule_caught():
    found = [{"point": "P1FT", "start": T("2024-03-09T04:10"), "end": T("2024-03-09T05:00")}]  # the rules caught the trip
    props = [prop(["K1VT"], "2024-03-08T09:00", "2024-03-08T12:00"),  # the vibration, which nothing caught
             prop(["K-1", "fz-x"], "2024-03-08T18:00"),  # named by its unit, inside the upset
             prop(["P-1"], "2024-03-09T04:30", "2024-03-09T05:30"),  # the trip: right, but a rule had it
             prop(["V1LT"], "2024-03-09T10:00", cited=False),  # a routine setpoint change: not an upset
             prop(["K1VT"], "2024-03-09T12:00")]  # the right machine a day late
    res = watch_eval.score(SPEC, props, found, [], SINCE, UNTIL)
    assert (res["upsets"], res["caught"], res["uncaught"]) == (2, 1, 1)
    assert (res["matched_uncaught"], res["matched_caught"], res["false_proposals"]) == (2, 1, 2)
    assert (res["matched"], res["precision"], res["recall"], res["cited"]) == (0.4, 0.6, 1.0, 0.8)
    assert not res["passes"]  # under half match what no rule caught

    # an exception raised on the vibrating machine during it catches it too: then nothing it proposed is new
    raised = [{"names": {"K-1"}, "at": T("2024-03-08T03:00")}]
    res = watch_eval.score(SPEC, props, found, raised, SINCE, UNTIL)
    assert (res["uncaught"], res["matched_uncaught"], res["matched_caught"], res["recall"]) == (0, 0, 3, None)

    res = watch_eval.score(SPEC, props[:2], [], [], SINCE, UNTIL)
    assert res["matched"] == 1.0 and res["passes"]
    assert watch_eval.score(SPEC, [], [], [], SINCE, UNTIL)["matched"] is None


def scripted(prompt: str) -> dict:
    """A stand-in model: proposes the top point of each stretch its area's model can't explain."""
    ev = json.loads(prompt.split("Evidence:\n", 1)[1])
    props = [{"subject": s["points"][0]["name"], "start": s["start"], "end": s["end"], "why": "unexplained",
              "ruled_out": "nothing in the log", "evidence": [s["points"][0]["id"]]} for s in ev["unexplained_stretches"][:2]]
    return {"answer": "```json\n" + json.dumps({"proposals": props}) + "\n```", "usage": {"cost_usd": 0.01}}


@pytest.mark.integration
def test_the_watch_is_scored_on_replay_and_shown_per_bundle(okan_db, okan_data):
    conn, pack = okan_db.conn, load_pack("okan")
    try:
        res = watch_eval.run(conn, pack, "okan", "2024-03-08T00:00", "2024-03-10T00:00", okan_data, ask=scripted)
        assert res["status"] == "shadow" and res["model"] == "scripted" and res["proposals"], res
        assert res["upsets"] >= 1  # 20-K-2200B's vibration climbing to train B's trip
        assert res["matched_uncaught"] + res["matched_caught"] + res["false_proposals"] == res["proposals"]
        assert res["caught"] + res["uncaught"] == res["upsets"]
        watch_eval.store(conn, res)
        row = next(d for d in detectors.listing(conn, pack) if d["name"] == "watch")
        shadow = row["score"]["shadow"]
        assert [b["bundle"] for b in shadow] == ["okan"] and shadow[0]["matched"] == res["matched"]
        assert shadow[0]["precision"] == res["precision"] and shadow[0]["model"] == "scripted"
    finally:
        conn.rollback()
        conn.execute("delete from ops_eval where detector = %s", (watch_eval.KEY,))
        conn.execute("delete from watch_proposal where since >= '2024-03-08' and since < '2024-03-10'")
        conn.commit()
