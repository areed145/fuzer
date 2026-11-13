"""P3.4 (#951): the agentic watch's proposals of one pattern, confirmed real three times, become a deterministic rule
with its replay score; a person publishes it, it runs as the promoted_rules window detector, and the watch stops asking
about what it catches. On Okan: 20-K-2300A's discharge temperature climbing before its trips (three in 2024 to fit on,
one in July 2025 held out to score)."""

import json
from datetime import datetime

import pytest

from fuzer.core import detectors
from fuzer.core.pack import load_pack
from fuzer.evals import watch_eval
from fuzer.ops import promote, watch

PACK = load_pack("okan")
FIT = [("2024-06-11T12:00", "2024-06-11T15:00"), ("2024-07-02T11:30", "2024-07-02T14:30"),
       ("2024-07-24T10:45", "2024-07-24T13:45")]
HELD_OUT = ("2025-07-16T00:00", "2025-07-17T00:00")
PATTERN = "CO:discharge_temperature:high"
T = datetime.fromisoformat


@pytest.mark.unit
def test_a_rule_is_fitted_with_margin_under_its_examples():
    assert promote.fit([{"shift_z": 7.8, "minutes": 110}, {"shift_z": -6.5, "minutes": 100}]) == {"z": 5.2, "minutes": 50}
    assert promote.fit([{"shift_z": 2.1, "minutes": 20}]) == {"z": 2.0, "minutes": 30}  # never below the floors
    with pytest.raises(promote.PromoteError, match="how far"):
        promote.fit([{"shift_z": None, "minutes": 10}])
    assert detectors.get("promoted_rules").window and "promoted_rules" in [d.name for d in detectors.window()]


def recorder(shown: list):
    """A stand-in model that proposes nothing and keeps the stretches it was shown."""
    def ask(prompt: str) -> dict:
        shown.extend(json.loads(prompt.split("Evidence:\n", 1)[1])["unexplained_stretches"])
        return {"answer": '```json\n{"proposals": []}\n```', "usage": {"cost_usd": 0.01}}
    return ask


@pytest.mark.integration
def test_a_confirmed_pattern_becomes_a_published_rule_and_the_watch_stops_spending_on_it(okan_db, okan_data):
    conn = okan_db.conn
    pid = conn.execute("select m.point_id from measurement_role m join entity_label l on l.fuzer_id = m.asset "
                       "where m.role = 'discharge_temperature' and l.label = '20-K-2300A'").fetchone()["point_id"]
    name = conn.execute("select label from entity_label where fuzer_id = %s", (pid,)).fetchone()["label"]
    area = next(a for a in watch.areas(conn) if pid in watch._plant(conn, a["id"])[1])
    try:
        # the watch proposed the climb three times (as its model would, citing the point)
        for a, b in FIT:
            day = T(a[:10])
            watch.keep(conn, {"area": area["id"], "subject": name, "start": T(a), "end": T(b), "evidence": [pid],
                              "why": "discharge temperature climbing with no change in load", "cited": True},
                       day, day.replace(hour=23, minute=59))
        conn.commit()
        assert promote.patterns(conn) == []  # nothing confirmed yet

        # on replay the fake plant's events confirm all three, and each reads into one pattern
        verdicts = watch_eval.judge(conn, okan_data, "2024-06-01T00:00", "2024-08-01T00:00")
        assert len(verdicts["confirmed"]) == 3 and not verdicts["rejected"], verdicts
        p = next(x for x in promote.patterns(conn) if x["pattern"] == PATTERN)
        assert p["confirmed"] == 3 and p["ready"] and p["rule"] is None and p["smallest_z"] > 5

        # the watch drafts a rule, fitted on the three and scored on the held-out trip
        rule = promote.draft(conn, PATTERN, pack=PACK, data_dir=okan_data, replay=HELD_OUT)
        spec, score = rule["spec"], rule["score"]
        assert (spec["role"], spec["class"], spec["direction"]) == ("discharge_temperature", "CO", "high")
        assert spec["z"] < p["smallest_z"] and spec["minutes"] >= promote.MIN_MINUTES
        assert score["kinds"] == ["discharge_temperature"] and score["events"] == 1 and score["recall"] == 1.0, score
        with pytest.raises(promote.PromoteError, match="already has a rule"):
            promote.draft(conn, PATTERN)

        # a person publishes it, never an agent; then it runs as a window detector like any other
        assert detectors.missing(conn, PACK, detectors.get("promoted_rules")) == ["a published rule"]
        with pytest.raises(promote.PromoteError, match="never an agent"):
            promote.publish(conn, rule["id"], "agent:watch")
        assert promote.publish(conn, rule["id"], "ana", "the climb precedes every trip")["status"] == "published"
        a, b = T("2025-07-16T10:00"), T("2025-07-16T18:00")
        found = detectors.get("promoted_rules").fn(conn, PACK, a, b, None)
        assert name in {d["point"] for d in found} and all(d["key"] == f"rule:{rule['id']}" for d in found)

        # the watch: before, it asks about the stretch on the compressor; with the rule's detection stored, it doesn't
        before, after = [], []
        watch.propose(conn, area, a, b, recorder(before))
        from fuzer.analysis import limits
        from fuzer.graph.topology import topology

        unit, adj = limits.units(conn, [pid, *(pt["id"] for s in before for pt in s["points"])]), topology(conn)
        near = {unit[pid], *adj.get(unit[pid], ())}  # the compressor and its train
        on_unit = [s for s in before if any(unit.get(pt["id"]) in near for pt in s["points"])
                   and any(d["start"] <= T(s["end"]) and T(s["start"]) <= d["end"] for d in found)]
        assert on_unit, ("a stretch on the compressor's train the area's own model can't explain", unit[pid], before)
        for d in found:
            conn.execute('insert into detection (point_id, unit_id, detector, key, start, "end", magnitude) '
                         "values (%s, %s, %s, %s, %s, %s, %s)", (d["point_id"], d["unit_id"], d["detector"], d["key"],
                                                                 d["start"], d["end"], d["magnitude"]))
        _, usd = watch.propose(conn, area, a, b, recorder(after))
        assert not [s for s in after if s in on_unit], (before, after)  # the rule covers it: not asked about
        assert usd == 0 or after  # a question is asked only about what is left
    finally:
        conn.rollback()
        conn.execute("delete from detection where detector = 'promoted_rules'")
        conn.execute("delete from promoted_rule")
        conn.execute("delete from watch_proposal")
        conn.commit()
