"""The capability registry in platform core: declaring a tool only records it, and a binder (the MCP server)
sees every tool whatever the import order."""

import pytest

from fuzer.core import capabilities as registry


@pytest.fixture
def clean(monkeypatch):
    import fuzer.agents.mcp_server  # noqa: F401  (declare the real tools first, so none land in the empty registry)

    monkeypatch.setattr(registry, "REGISTRY", {})
    monkeypatch.setattr(registry, "_binders", [])
    return registry


def test_a_binder_sees_tools_declared_before_and_after_it(clean):
    @clean.capability("a", kind="plot")
    def early():
        pass

    seen = []
    clean.bind(lambda c: seen.append(c.name) or clean.Capability(c.name, ("a", "b"), c.kind, c.fn))

    @clean.capability("all")
    def late():
        pass

    assert seen == ["early", "late"]
    assert clean.REGISTRY["early"].agents == ("a", "b") and clean.REGISTRY["early"].kind == "plot"
    assert clean.REGISTRY["late"].fn is late


def test_bad_declarations_fail(clean):
    with pytest.raises(ValueError, match="kind must be one of"):
        clean.capability(kind="write")

    @clean.capability()
    def tool():
        pass

    with pytest.raises(ValueError, match="declared twice"):
        clean.capability()(tool)


def test_the_mcp_server_binds_every_declared_tool():
    from fuzer.agents import capabilities, mcp_server

    assert capabilities.REGISTRY is registry.REGISTRY
    assert {"claim_work", "draft_work_item"} <= set(registry.REGISTRY)
    assert all(set(c.agents) <= set(capabilities.AGENTS) for c in registry.REGISTRY.values())
    assert mcp_server.claim_work is registry.REGISTRY["claim_work"].fn
