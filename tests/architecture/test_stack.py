"""The approved stack (stack.json, docs/stack.md): a manifest may only name dependencies the list approves.

Don't add a name to stack.json to get past this. A new dependency needs Adam's go-ahead first (AGENTS.md,
"Stack and shared modules"); once agreed, it goes in stack.json with what it is for.
"""

import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APPROVED = json.loads((ROOT / "stack.json").read_text())["python"]
FUZER = tomllib.loads((ROOT / "pyproject.toml").read_text())
FAKEPLANT = tomllib.loads((ROOT / "fakeplant" / "pyproject.toml").read_text())


def names(requirements: list[str]) -> set[str]:
    """Distribution names, without extras, versions or markers."""
    return {re.match(r"[A-Za-z0-9._-]+", r).group(0).lower() for r in requirements}


MANIFESTS = {
    "fuzer": FUZER["project"]["dependencies"],
    "fuzer[openai]": FUZER["project"]["optional-dependencies"]["openai"],
    "dev": FUZER["dependency-groups"]["dev"],
    "docs": FUZER["dependency-groups"]["docs"],
    "fakeplant": FAKEPLANT["project"]["dependencies"],
}


@pytest.mark.unit
def test_every_dependency_group_is_listed():
    groups = {"fuzer", "fakeplant"}
    groups |= {f"fuzer[{extra}]" for extra in FUZER["project"].get("optional-dependencies", {})}
    groups |= set(FUZER.get("dependency-groups", {}))
    assert groups == set(MANIFESTS) == set(APPROVED)


@pytest.mark.unit
@pytest.mark.parametrize("group", sorted(MANIFESTS))
def test_dependencies_are_the_approved_ones(group):
    used, listed = names(MANIFESTS[group]), set(APPROVED[group])
    assert not used - listed, (
        f"Not in the approved stack ({group}): {sorted(used - listed)}. Use what the stack already has, "
        "or ask Adam before adding a dependency."
    )
    assert not listed - used, f"stack.json lists {sorted(listed - used)} under {group}, which nothing declares."
    assert all(len(purpose) > 3 for purpose in APPROVED[group].values())
