"""P4.1: the specialist pack format, and the conformance every pack must pass."""

import pytest

from fuzer.agents import ledger, packs

pytestmark = pytest.mark.unit

COMPRESSION = "maintenance-integrity/compression"


def test_compression_is_the_reference_pack():
    assert COMPRESSION in packs.shipped()
    p = packs.load(COMPRESSION)
    assert p["owner"] == "maintenance-integrity" and p["classes"] == ["CO"]
    assert {"surge", "rod_load", "valve_health", "protection", "seal_gas"} <= set(p["methods"])
    assert {"okan", "kestrel"} <= set(p["questions"])


@pytest.mark.parametrize("path", packs.shipped())
def test_every_shipped_pack_conforms(path):
    assert packs.conformance(path) == []


def test_conformance_names_what_is_wrong(monkeypatch):
    real = packs.load

    def broken(path):
        p = real(path)
        return {**p, "tools": [*p["tools"], "plot_choke", "no_such_tool"], "methods": [*p["methods"][1:], "nope"],
                "detectors": ["nope"], "classes": ["ZZ"], "charts": ["calc"],
                "roles": [*p["roles"][1:], "no_such_role"], "briefs": ["source_issue"],
                "questions": {"okan": [{"question": "q", "must_mention": ["x"], "specialists": ["orchestrator"]}]}}

    monkeypatch.setattr(packs, "load", broken)
    said = "\n".join(packs.conformance(COMPRESSION))
    for words in ("tools not registered: no_such_tool", "methods not in the registry: nope",
                  "methods in the playbook but not declared: compressor_performance", "detectors not registered: nope",
                  "roles not in the measurement role list: no_such_role",
                  "briefs work types none of its detectors raise: source_issue", "classes not in ISO 14224: ZZ", "charts that are not plot tools",
                  "no eval questions for the kestrel bundle", "okan question 1 does not expect maintenance-integrity"):
        assert words in said, words


def test_the_pack_is_one_skill_so_its_version_covers_the_manifest_and_eval():
    assert COMPRESSION in ledger.skill_versions(["maintenance-integrity"])


def test_the_capabilities_page_lists_each_pack_from_its_manifest():
    from fuzer.agents import capabilities

    page = capabilities.catalogue()
    p = packs.load(COMPRESSION)
    section = page.split(f"### {p['title']}", 1)[1].split("###", 1)[0].split("## ", 1)[0]
    assert f"`{COMPRESSION}`" in section and "API 618" in section  # methods with the standards they cite
    assert all(f"`{d}`" in section for d in p["detectors"]) and all(f"`{r}`" in section for r in p["roles"])


def test_a_method_with_its_own_tool_needs_that_tool_not_a_calc_call(monkeypatch):
    alarms = "field-operations-facilities/alarms-and-instruments"
    assert packs.conformance(alarms) == []  # alarm_performance runs through alarm_analysis, not calc
    real = packs.load
    monkeypatch.setattr(packs, "load", lambda path: {**real(path), "tools": [
        t for t in real(path)["tools"] if t != "alarm_analysis"]})
    assert "methods whose tool it does not list: alarm_performance" in packs.conformance(alarms)
