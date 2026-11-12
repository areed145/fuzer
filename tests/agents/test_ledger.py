"""P4.4: an agent step's ledger traces its answer to the skill and model versions that produced it."""

import pytest

from fuzer.agents import ledger
from fuzer.agents.capabilities import AGENTS

pytestmark = pytest.mark.unit


def test_every_agent_holds_versioned_skills():
    for a in AGENTS:
        v = ledger.skill_versions([a])
        assert v and all(len(h) == 12 for h in v.values()), a
    assert ledger.skill_versions(["nobody"]) == {}


def test_a_skill_version_changes_when_its_files_change(tmp_path, monkeypatch):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "SKILL.md").write_text("threshold 1")
    monkeypatch.setattr(ledger, "SKILLS", tmp_path)
    ledger._hash.cache_clear()
    before = ledger._skill_hash("s")
    (tmp_path / "s" / "SKILL.md").write_text("threshold 2")
    ledger._hash.cache_clear()
    assert ledger._skill_hash("s") != before
    ledger._hash.cache_clear()


def test_a_ledger_without_usage_still_says_who_worked():
    led = ledger.record("maintenance-integrity", {"answer": "x"})
    assert led["agents"] == ["maintenance-integrity"] and led["cost_usd"] == 0 and led["tools"] == []
