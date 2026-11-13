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
    assert list(steps) == ["bundle", "sources", "source_packs", "operating_model", "context", "roster", "go_live", "loop"]
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


def test_go_live_says_what_a_shift_needs_without_testing_or_calling_anything(harrier_db, monkeypatch):
    """R.4: references and settings only. Unconfigured sign-in and no owner block a shift; alerts, links and a spend
    cap are to do; set, the step is ready and says set is not tested."""
    from fuzer.core import settings

    for name in ("FUZER_MICROSOFT_CLIENT_ID", "FUZER_MICROSOFT_CLIENT_SECRET", "FUZER_PUBLIC_URL",
                 "FUZER_ALERT_WEBHOOK", "RENDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FUZER_AUTH_MODE", "microsoft")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
    settings.set(harrier_db, "agents.case_cap_usd", 0, by="test", reason="no cap", project_name="harrier")
    live = {s["key"]: s for s in onboarding.journey(harrier_db)["steps"]}["go_live"]
    whats = {g["what"] for g in live["gaps"]}
    assert live["status"] == "blocked" and all(g["prevents"] and g["link"] for g in live["gaps"])
    assert {"Microsoft sign-in isn't configured", "no owner", "no failed-run alert channel",
            "notifications have no link back", "agent spend per case has no cap"} <= whats

    monkeypatch.setenv("FUZER_MICROSOFT_CLIENT_ID", "test-client")
    monkeypatch.setenv("FUZER_MICROSOFT_CLIENT_SECRET", "test-only-not-a-real-secret")
    monkeypatch.setenv("FUZER_PUBLIC_URL", "https://fuzer.example.com")
    monkeypatch.setenv("FUZER_ALERT_WEBHOOK", "https://hooks.example.com/test")
    harrier_db.execute("insert into access_account (id, tenant_id, object_id, display_name, enabled, owner) "
                       "values ('golive', gen_random_uuid()::text, gen_random_uuid()::text, 'Owner', true, true)")
    settings.set(harrier_db, "notify.app_url", "https://fuzer.example.com", by="test", reason="links",
                 project_name="harrier")
    settings.set(harrier_db, "agents.case_cap_usd", 2.0, by="test", reason="cap", project_name="harrier")
    live = {s["key"]: s for s in onboarding.journey(harrier_db)["steps"]}["go_live"]
    assert live["status"] == "ready" and live["gaps"] == [] and "not tested" in live["summary"]
    harrier_db.execute("delete from access_account where id = 'golive'")
    harrier_db.commit()
