"""Specialist packs (P4.1): everything one specialist knows how to do, in one folder.

A pack is a skill folder whose SKILL.md (the playbook) sits beside a `pack.yaml` that names what the playbook relies
on, and an eval of questions with answer keys:

    title, owner      what it is called and the agent that loads it
    classes           the ISO 14224 equipment classes it covers (standards/iso14224.yaml)
    tools             the registered tools the playbook tells the agent to call
    charts            the plot tools whose charts it shows (a subset of tools)
    methods           the registered methods (standards/methods.yaml) the playbook runs through `calc`
    detectors         the registered detectors that raise its work
    roles             the measurement roles it reads
    eval              the eval's name: each bundle holds its answer keys in evals/<name>.yaml (questions with
                      must_mention, specialists and charts keys), so nothing facility specific sits in the pack

Because the pack is a skill folder, the agent ledger's skill version (P4.4) covers the manifest and the eval too.

    shipped()          every pack under agents/skills, by skill path
    load(path)         one pack's manifest, with its playbook and each shipped bundle's eval questions read
    conformance(path)  what is wrong with it: [] when every name resolves and the playbook and manifest agree
"""

import re

import yaml

from . import SKILLS

MANIFEST = "pack.yaml"
REQUIRED = ("title", "owner", "classes", "tools", "charts", "methods", "detectors", "roles", "eval")


def shipped() -> list[str]:
    """The skill paths of every pack, in name order."""
    return sorted(p.parent.relative_to(SKILLS).as_posix() for p in SKILLS.rglob(MANIFEST))


def load(path: str) -> dict:
    """One pack: its manifest, with `path`, `playbook` (the SKILL.md text) and `questions` (each shipped bundle's
    eval questions, by bundle) added."""
    from ..core import bundles

    root = SKILLS / path
    pack = yaml.safe_load((root / MANIFEST).read_text()) or {}
    questions = {}
    for b in bundles.shipped() if pack.get("eval") else ():
        keys = bundles.find(b) / "evals" / f"{pack['eval']}.yaml"
        if keys.is_file():
            questions[b] = yaml.safe_load(keys.read_text()) or []
    return {**pack, "path": path, "playbook": (root / "SKILL.md").read_text() if (root / "SKILL.md").is_file() else "",
            "questions": questions}


def _diff(what: str, declared, used) -> list[str]:
    out = []
    if extra := sorted(set(declared) - set(used)):
        out.append(f"{what} declared but not in the playbook: {', '.join(extra)}")
    if missing := sorted(set(used) - set(declared)):
        out.append(f"{what} in the playbook but not declared: {', '.join(missing)}")
    return out


def conformance(path: str) -> list[str]:
    """Everything wrong with one pack, in words a pack author acts on; [] when it conforms."""
    from ..core import bundles, detectors, methods
    from ..core.capabilities import REGISTRY
    from ..core.taxonomy import load_standard
    from .capabilities import AGENTS, SKILL_CATALOG, tools_by_agent

    pack = load(path)
    problems = [f"no {k} in {MANIFEST}" for k in REQUIRED if not pack.get(k)]
    if problems:
        return problems
    if not pack["playbook"]:
        problems.append("no SKILL.md beside the manifest")
    owner = pack["owner"]
    skill = next((s for s in SKILL_CATALOG if s.path == path), None)
    if owner not in AGENTS:
        problems.append(f"owner {owner} is not an agent")
    elif skill is None or skill.owner != owner:
        problems.append(f"the skill {path} is not in the catalogue owned by {owner}")
    if bad := sorted(set(pack["classes"]) - set(load_standard("iso14224").get("classes", {}))):
        problems.append(f"classes not in ISO 14224: {', '.join(bad)}")

    given = set(tools_by_agent().get(owner, ()))
    if bad := sorted(t for t in pack["tools"] if t not in REGISTRY):
        problems.append(f"tools not registered: {', '.join(bad)}")
    elif bad := sorted(set(pack["tools"]) - given):
        problems.append(f"tools {owner} is not given: {', '.join(bad)}")
    named = set(re.findall(r"`([a-z_]+)(?:\(|`)", pack["playbook"])) & set(REGISTRY)
    problems += _diff("tools", pack["tools"], named)
    if bad := sorted(set(pack["charts"]) - set(pack["tools"])):
        problems.append(f"charts not among its tools: {', '.join(bad)}")
    if bad := sorted(c for c in pack["charts"] if c in REGISTRY and REGISTRY[c].kind != "plot"):
        problems.append(f"charts that are not plot tools: {', '.join(bad)}")

    if bad := sorted(m for m in pack["methods"] if not methods.known(m)):
        problems.append(f"methods not in the registry: {', '.join(bad)}")
    problems += _diff("methods", pack["methods"], re.findall(r'calc\("([a-z_]+)"', pack["playbook"]))

    found = detectors.registry()
    if bad := sorted(set(pack["detectors"]) - set(found)):
        problems.append(f"detectors not registered: {', '.join(bad)}")
    # Until the measurement role registry (P1.4) lands, a role is known when one of the pack's detectors reads it.
    read = {r for d in pack["detectors"] if d in found for r in found[d].roles}
    if bad := sorted(set(pack["roles"]) - read):
        problems.append(f"roles none of its detectors read: {', '.join(bad)}")

    questions = pack["questions"]
    for b in bundles.shipped():
        if not questions.get(b):
            problems.append(f"no eval questions for the {b} bundle (evals/{pack['eval']}.yaml in the bundle)")
    for b, qs in questions.items():
        for i, q in enumerate(qs or [], 1):
            if not q.get("question") or not q.get("must_mention"):
                problems.append(f"{b} question {i} has no question or must_mention")
            if owner not in (q.get("specialists") or []):
                problems.append(f"{b} question {i} does not expect {owner} to answer")
    return problems
