"""P8.4: the onboarding journey on one page, on the third bundle. Stood up from its seed bundle and loaded, Harrier's
page says the operating model is still to adopt and nobody holds its roster's roles, and what each gap prevents;
adopting the standard turns that step ready."""

from fuzer.api import onboarding
from fuzer.workflow import engine
from fuzer.workflow import operating_model as om


def test_the_journey_says_what_is_ready_blocked_and_what_each_gap_prevents(harrier_db):
    engine.load_defaults(harrier_db)
    j = onboarding.journey(harrier_db)
    steps = {s["key"]: s for s in j["steps"]}
    assert list(steps) == ["bundle", "sources", "source_packs", "operating_model", "context", "roster", "loop"]
    assert j["project"] == "harrier" and steps["bundle"]["status"] == "ready" and steps["bundle"]["facility"] == "HRPT"
    sources = steps["sources"]
    assert sources["status"] == "ready" and sources["loaded"] >= 25 and sources["held"] == 0
    assert "ec_well_tests" in sources["never_loaded"]  # a pipeline exports no well tests: listed, not a gap
    assert steps["source_packs"]["status"] == "ready"  # Harrier names none beyond the built-in sources
    model = steps["operating_model"]
    assert model["status"] == "to do" and not model["applied"]
    assert any("isn't applied" in g["what"] for g in model["gaps"])
    context = steps["context"]
    assert context["questions"] > 0 and all(g["prevents"] for g in context["gaps"])
    assert any(g["what"].endswith("criticality not assessed") for g in context["gaps"])
    roster = steps["roster"]
    assert roster["status"] == "blocked" and roster["filled"] == 0 and roster["seats"] == 6
    assert any("nobody holds operator" == g["what"] for g in roster["gaps"])
    assert steps["loop"]["command"] == "fuzer eval --loop"

    om.adopt(harrier_db, by="test")
    after = {s["key"]: s for s in onboarding.journey(harrier_db)["steps"]}
    assert after["operating_model"]["status"] == "ready" and after["operating_model"]["unnamed"] == 0
