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


WELLS, GAS = "wells-subsurface/wells-and-lift", "field-operations-facilities/gas-treating"


@pytest.fixture
def fourth(tmp_path, monkeypatch):
    """The shipped bundles plus a fourth, `plover`, with no wells: Harrier's asset pack and answer keys under a new
    name. Returns a writer for its manifest's packs_not_applicable list."""
    from fuzer import resources

    for b in ("okan", "kestrel", "harrier"):
        (tmp_path / b).symlink_to(resources.BUNDLES / b)
    plover = tmp_path / "plover"
    plover.mkdir()
    (plover / "pack.yaml").symlink_to(resources.BUNDLES / "harrier" / "pack.yaml")
    (plover / "evals").symlink_to(resources.BUNDLES / "harrier" / "evals")
    monkeypatch.setattr(resources, "BUNDLES", tmp_path)

    def manifest(exempt):
        import yaml

        (plover / "bundle.yaml").write_text(yaml.safe_dump(
            {"bundle": 1, "name": "plover", "pack": "pack.yaml", "packs_not_applicable": exempt}))

    return manifest


def test_a_fourth_bundle_with_no_wells_conforms_without_questions_for_packs_it_marks_not_applicable(fourth):
    fourth([{"pack": WELLS, "reason": "a gathering hub has no wells"}, {"pack": GAS, "reason": "it treats no gas"}])
    assert packs.unknown_exemptions() == []
    for path in packs.shipped():
        assert packs.conformance(path) == [], path
    assert packs.load(WELLS)["not_applicable"] == {"harrier": "a pipeline and terminal has no wells",
                                                   "plover": "a gathering hub has no wells"}
    assert "plover" not in packs.load(WELLS)["questions"]


def test_a_pack_the_bundle_does_not_exempt_still_needs_its_questions(fourth):
    fourth([{"pack": GAS, "reason": "it treats no gas"}])
    assert "no eval questions for the plover bundle (evals/wells-and-lift.yaml in the bundle)" in \
        packs.conformance(WELLS)
    assert packs.conformance(GAS) == []


def test_an_exemption_names_a_shipped_pack_and_says_why(fourth):
    from fuzer.core import bundles

    fourth([{"pack": "wells-subsurface/no-such-pack", "reason": "none here"}])
    assert packs.unknown_exemptions() == [
        "the plover bundle marks wells-subsurface/no-such-pack not applicable, but no such pack is shipped"]
    fourth([{"pack": WELLS}])
    with pytest.raises(bundles.BundleError, match="needs a pack and a reason"):
        packs.not_applicable()


def test_a_bundle_that_exempts_a_pack_holds_no_answer_keys_for_it(fourth):
    fourth([{"pack": WELLS, "reason": "a gathering hub has no wells"}, {"pack": "maintenance-integrity/compression",
                                                                      "reason": "said so to test"}])
    assert "the plover bundle marks it not applicable but still holds evals/compression.yaml" in \
        packs.conformance(COMPRESSION)


def test_every_shipped_exemption_names_a_shipped_pack():
    assert packs.unknown_exemptions() == []


@pytest.mark.unit
def test_the_orchestrator_routes_by_the_packs_each_specialist_holds():
    """Each pack sits under its owner in the routing list, in its playbook's own words, so a question a pack covers
    (a meter's allocation factor, a trip's proof test) goes to that specialist rather than being answered as a lookup."""
    from fuzer import agents

    text = agents.routing()
    blocks = {}
    for line in text.splitlines():
        if line.startswith("- "):
            owner = line[2:].split(":", 1)[0]
            blocks[owner] = []
        else:
            blocks[owner].append(line.strip())
    for path in packs.shipped():
        assert f"- Pack: {packs.summary(path)}" in blocks[path.split("/", 1)[0]], path
    assert packs.summary("production-surveillance/metering-and-balances").startswith("Metering and balances - ")
    assert "spare" in packs.summary("maintenance-integrity/reliability")
    assert "covers is that specialist's" in agents.ORCHESTRATOR
