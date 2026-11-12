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
                "roles": [*p["roles"][1:], "no_such_role"],
                "questions": {"okan": [{"question": "q", "must_mention": ["x"], "specialists": ["orchestrator"]}]}}

    monkeypatch.setattr(packs, "load", broken)
    said = "\n".join(packs.conformance(COMPRESSION))
    for words in ("tools not registered: no_such_tool", "methods not in the registry: nope",
                  "methods in the playbook but not declared: compressor_performance", "detectors not registered: nope",
                  "roles not in the measurement role list: no_such_role", "classes not in ISO 14224: ZZ", "charts that are not plot tools",
                  "no eval questions for the kestrel bundle", "okan question 1 does not expect maintenance-integrity"):
        assert words in said, words


def test_the_pack_is_one_skill_so_its_version_covers_the_manifest_and_eval():
    assert COMPRESSION in ledger.skill_versions(["maintenance-integrity"])
