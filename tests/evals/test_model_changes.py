"""Change control and rollout (P8.3): a process change goes from proposal to live on two assets (Kestrel and Harrier
in one database) with a test run on closed cases and a recorded review, and one asset rolls back cleanly. A local
deviation is promoted to the standard the same way."""

from pathlib import Path

import pytest

from fuzer.core import bundles, db, projects
from fuzer.workflow import conformance, engine
from fuzer.workflow import model_changes as mc
from fuzer.workflow import operating_model as om
from fuzer.workflow.definitions import Forbidden

BUNDLES = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles"

FAST = 'fuzer:sla="PT2H"'


def _process(conn, key: str) -> str:
    return om.snapshot(conn)["processes"][key]


def _in(conn, project: str):
    with projects.use(conn, project), db.connect() as there:
        return {"process": _process(there, "iow_breach"), "standing": conformance.standing(there)}


def test_a_process_change_goes_live_on_two_assets_and_one_rolls_back(kestrel_db):
    conn = kestrel_db
    bundles.init(conn, BUNDLES / "harrier", by="test")
    for project in ("kestrel", "harrier"):
        with projects.use(conn, project), db.connect() as there:
            engine.set_person(there, "ana", ["engineer"])
            engine.set_person(there, "olga", ["ops_lead", "workflow_admin"])
            there.commit()
    om.adopt(conn, by="olga")
    with projects.use(conn, "harrier"), db.connect() as there:
        om.adopt(there, by="olga")
    before = _process(conn, "iow_breach")
    assert 'fuzer:sla="PT4H"' in before

    # proposed: the IOW breach's first step due in two hours, not four
    with pytest.raises(mc.ChangeError, match="changes nothing"):
        mc.propose(conn, {"processes": {"iow_breach": before}}, "no change", "ana")
    p = mc.propose(conn, {"processes": {"iow_breach": before.replace('fuzer:sla="PT4H"', FAST, 1)}},
                   "A breach is looked at within two hours", "ana")
    assert (p["version"], p["base_version"], p["state"]) == (2, 1, "proposed")
    assert p["changes"] == [{"part": "processes", "key": "iow_breach", "change": "changed"}]

    # reviewed only after a test run, by someone else, who holds workflow_admin
    with pytest.raises(mc.ChangeError, match="test-run"):
        mc.review(conn, "standard", 2, "olga", True, "fine")
    run = mc.test_run(conn, "standard", 2)
    assert run["problems"] == [] and "iow_breach" in run["types"] and run["summary"]["cases"] == len(run["cases"])
    with pytest.raises(Forbidden):
        mc.review(conn, "standard", 2, "ana", True, "my own change")
    with pytest.raises(mc.ChangeError):
        mc.rollout(conn, "kestrel", "standard", 2, "olga")  # not published yet
    r = mc.review(conn, "standard", 2, "olga", True, "Two hours is what the IOW owners already do")
    assert (r["state"], r["reviewed_by"]) == ("approved", "olga")
    assert mc.publish(conn, "standard", 2, "olga")["state"] == "published"
    assert mc.latest(conn) == 2

    # live on both assets
    for project in ("kestrel", "harrier"):
        out = mc.rollout(conn, project, "standard", 2, "olga")
        assert (out["from_name"], out["from_version"], out["state"]) == ("standard", 1, "live")
        there = _in(conn, project)
        assert FAST in there["process"]
        s = there["standing"]
        assert (s["version"], s["current"], s["applied"]) == (2, True, True), s
        assert s["unnamed"] == 0 and out["applied"]["overwritten"] == []
    engine.load_defaults(conn)  # as every start of the API does: the shipped process doesn't come back over it
    assert FAST in _process(conn, "iow_breach")
    kestrel = _in(conn, "kestrel")["standing"]  # its named shift times survive the rollout
    assert kestrel["verdict"] == "justified" and [d["key"] for d in kestrel["deviations"]] == ["handover.shift_times"]

    # Harrier rolls back; Kestrel stays on v2
    back = mc.rollback(conn, "harrier", "olga", "The terminal's IOW owners work days only")
    assert back["to"] == {"name": "standard", "version": 1}
    harrier = _in(conn, "harrier")
    assert harrier["process"] == before
    assert (harrier["standing"]["version"], harrier["standing"]["applied"]) == (1, True)
    assert harrier["standing"]["unnamed"] == 0
    assert FAST in _in(conn, "kestrel")["process"]
    with pytest.raises(mc.ChangeError, match="no rollout"):
        mc.rollback(conn, "harrier", "olga", "again")
    states = {(h["project"], h["state"]) for h in mc.history(conn)}
    assert states == {("kestrel", "live"), ("harrier", "rolled_back")}

    # a deviation that proved better is proposed as the standard
    promoted = mc.promote(conn, "settings", "handover.shift_times", "Kestrel's shift times suit every asset", "ana")
    assert (promoted["version"], promoted["base_version"]) == (3, 2) and "promoted from kestrel" in promoted["summary"]
    assert [v["version"] for v in mc.versions(conn, "standard")] == [1, 2, 3]
