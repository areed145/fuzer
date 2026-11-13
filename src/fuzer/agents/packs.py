"""Specialist packs (P4.1): everything one specialist knows how to do, in one folder.

A pack is a skill folder whose SKILL.md (the playbook) sits beside a `pack.yaml` that names what the playbook relies
on, and an eval of questions with answer keys:

    title, owner      what it is called and the agent that loads it
    classes           the ISO 14224 equipment classes it covers (standards/iso14224.yaml), or a unit kind that has
                      no equipment class (`well`: a well is its own unit, as the detectors class it)
    tools             the registered tools the playbook tells the agent to call
    charts            the plot tools whose charts it shows (a subset of tools)
    methods           the registered methods (standards/methods.yaml) the playbook runs, through `calc` or, for a
                      method the registry gives its own tool, through that tool (which the pack must list)
    detectors         the registered detectors that raise its work
    briefs            the work types it briefs when a case opens on a subject of its classes (P4.3; optional)
    roles             the measurement roles it reads (core/roles.py), including every role its detectors read
    needs             for each method that reads the asset itself, the roles it needs measured there (P4.1b;
                      optional); a method that reads a neighbour (the scrubber's pressure, the driver's lube oil) or
                      a record (a curve, a plan) is left out rather than reported as blocked
    eval              the eval's name: each bundle holds its answer keys in evals/<name>.yaml (questions with
                      must_mention, specialists and charts keys), so nothing facility specific sits in the pack

Because the pack is a skill folder, the agent ledger's skill version (P4.4) covers the manifest and the eval too.

    shipped()          every pack under agents/skills, by skill path
    not_applicable()   the packs each shipped bundle says can't apply to its asset, with why (bundle.yaml
                       packs_not_applicable): conformance asks no questions of that bundle for them (#826)
    load(path)         one pack's manifest, with its playbook and each shipped bundle's eval questions read
    conformance(path)  what is wrong with it: [] when every name resolves and the playbook and manifest agree
    coverage(conn, pack, asset)
                       what it can and cannot run on one asset, from the measurement roles there (P1.4)
    cannot_do(conn, pack, assets)
                       a plain line for each method it cannot run on each of those assets of its classes, naming
                       the measurements that are missing (the brief puts these under could not check)
    on_site(conn, path)
                       every measured asset of its classes on this site with its coverage, and what keeps it from
                       working here: [] when it covers an asset and at least one of its methods runs
"""

import re

import yaml

from . import SKILLS

MANIFEST = "pack.yaml"
REQUIRED = ("title", "owner", "classes", "tools", "charts", "methods", "detectors", "roles", "eval")
UNIT_KINDS = ("well",)  # units classed by their own kind rather than an ISO 14224 class (core/detectors.classes_of)


def shipped() -> list[str]:
    """The skill paths of every pack, in name order."""
    return sorted(p.parent.relative_to(SKILLS).as_posix() for p in SKILLS.rglob(MANIFEST))


def summary(path: str) -> str:
    """What a pack covers, as its playbook's description says it (the SKILL.md front matter), else its title."""
    text = (SKILLS / path / "SKILL.md").read_text() if (SKILLS / path / "SKILL.md").is_file() else ""
    if text.startswith("---"):
        meta = yaml.safe_load(text.split("---", 2)[1]) or {}
        if meta.get("description"):
            return str(meta["description"]).strip()
    return str((yaml.safe_load((SKILLS / path / MANIFEST).read_text()) or {}).get("title") or path)


def not_applicable() -> dict[str, dict[str, str]]:
    """The packs each shipped bundle says can't apply to its asset, with why: {bundle: {pack path: reason}} (#826)."""
    from ..core import bundles

    return {b: got for b in bundles.shipped()
            if (got := bundles.read(bundles.find(b))["config"]["bundle"]["packs_not_applicable"])}


def unknown_exemptions() -> list[str]:
    """A bundle that marks a pack not applicable when no such pack is shipped, in words; [] when all are known."""
    known = set(shipped())
    return [f"the {b} bundle marks {p} not applicable, but no such pack is shipped"
            for b, got in not_applicable().items() for p in sorted(got) if p not in known]


def load(path: str) -> dict:
    """One pack: its manifest, with `path`, `playbook` (the SKILL.md text) and `questions` (each shipped bundle's
    eval questions, by bundle) and `not_applicable` (the bundles that say it can't apply there, with why) added."""
    from ..core import bundles

    exempt = {b: got[path] for b, got in not_applicable().items() if path in got}
    root = SKILLS / path
    pack = yaml.safe_load((root / MANIFEST).read_text()) or {}
    questions = {}
    for b in bundles.shipped() if pack.get("eval") else ():
        if b in exempt:
            continue
        keys = bundles.find(b) / "evals" / f"{pack['eval']}.yaml"
        if keys.is_file():
            questions[b] = yaml.safe_load(keys.read_text()) or []
    return {**pack, "path": path, "playbook": (root / "SKILL.md").read_text() if (root / "SKILL.md").is_file() else "",
            "questions": questions, "not_applicable": exempt}


def _diff(what: str, declared, used) -> list[str]:
    out = []
    if extra := sorted(set(declared) - set(used)):
        out.append(f"{what} declared but not in the playbook: {', '.join(extra)}")
    if missing := sorted(set(used) - set(declared)):
        out.append(f"{what} in the playbook but not declared: {', '.join(missing)}")
    return out


def conformance(path: str) -> list[str]:
    """Everything wrong with one pack, in words a pack author acts on; [] when it conforms."""
    from ..core import bundles, detectors, methods, roles
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
    if bad := sorted(set(pack["classes"]) - set(load_standard("iso14224").get("classes", {})) - set(UNIT_KINDS)):
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
    known = [m for m in pack["methods"] if methods.known(m)]
    by_tool = {m: methods.get(m).tool for m in known if methods.get(m).tool}  # run by their own tool, not `calc`
    if bad := sorted(m for m, t in by_tool.items() if t not in pack["tools"]):
        problems.append(f"methods whose tool it does not list: {', '.join(bad)}")
    problems += _diff("methods", [m for m in pack["methods"] if m not in by_tool],
                      re.findall(r'calc\("([a-z_]+)"', pack["playbook"]))

    found = detectors.registry()
    if bad := sorted(set(pack["detectors"]) - set(found)):
        problems.append(f"detectors not registered: {', '.join(bad)}")
    if bad := sorted(set(pack["roles"]) - set(roles.ROLES)):
        problems.append(f"roles not in the measurement role list: {', '.join(bad)}")
    read = {r for d in pack["detectors"] if d in found for r in found[d].roles}
    if bad := sorted(read - set(pack["roles"])):
        problems.append(f"roles its detectors read but it does not declare: {', '.join(bad)}")

    needs = pack.get("needs") or {}
    if bad := sorted(set(needs) - set(pack["methods"])):
        problems.append(f"needs for methods it does not list: {', '.join(bad)}")
    if bad := sorted({r for rs in needs.values() for r in rs or ()} - set(pack["roles"])):
        problems.append(f"needs roles it does not declare: {', '.join(bad)}")

    raised = {t for d in pack["detectors"] if d in found for t in found[d].raises}
    if bad := sorted(set(pack.get("briefs") or ()) - raised):
        problems.append(f"briefs work types none of its detectors raise: {', '.join(bad)}")

    questions = pack["questions"]
    for b in bundles.shipped():
        if b in (pack.get("not_applicable") or {}):
            if (bundles.find(b) / "evals" / f"{pack['eval']}.yaml").is_file():
                problems.append(f"the {b} bundle marks it not applicable but still holds evals/{pack['eval']}.yaml")
            continue
        if not questions.get(b):
            problems.append(f"no eval questions for the {b} bundle (evals/{pack['eval']}.yaml in the bundle)")
    for b, qs in questions.items():
        for i, q in enumerate(qs or [], 1):
            if not q.get("question") or not q.get("must_mention"):
                problems.append(f"{b} question {i} has no question or must_mention")
            if owner not in (q.get("specialists") or []):
                problems.append(f"{b} question {i} does not expect {owner} to answer")
    return problems


def coverage(conn, pack: dict, asset: str) -> dict:
    """What `pack` can and cannot run on `asset`: each method in its `needs` whose roles are all measured on the asset
    (or its instruments), and each one that is missing some, with the roles it lacks."""
    from ..graph import roles as measured
    from ..sources.timeseries import _unit_points

    on = set(measured.of(conn, [p["id"] for p in _unit_points(conn, asset, 100)]).values())
    can, cannot = [], []
    for method, need in (pack.get("needs") or {}).items():
        if missing := [r for r in need or () if r not in on]:
            cannot.append({"method": method, "missing": missing})
        else:
            can.append(method)
    return {"asset": asset, "roles": sorted(on), "can": can, "cannot": cannot}


def _label(conn, fid: str) -> str:
    r = conn.execute("select label from entity_label where fuzer_id = %s order by label limit 1", (fid,)).fetchone()
    return r["label"] if r else fid


def cannot_do(conn, pack: dict, assets) -> list[str]:
    """One line per method `pack` cannot run on each of `assets` that is of its classes."""
    from ..core import detectors, roles

    of_class = [a for a, c in detectors.classes_of(conn, assets).items() if c in set(pack["classes"])]
    out = []
    for a in of_class:
        for c in coverage(conn, pack, a)["cannot"]:
            what = " or ".join(roles.ROLES[r].label.lower() if r in roles.ROLES else r for r in c["missing"])
            out.append(f"{c['method']} on {_label(conn, a)}: no {what} measured there")
    return out


def on_site(conn, path: str) -> dict:
    """The pack against this site's register: each measured asset of its classes with its coverage, and what keeps
    the pack from working here."""
    from ..core import detectors

    pack = load(path)
    tags = [r["src"] for r in conn.execute(
        "select distinct c.src from edge c join entity s on s.fuzer_id = c.src and s.kind = 'tag' "
        "where c.rel = 'classified_as'")]
    if kinds := [k for k in pack["classes"] if k in UNIT_KINDS]:  # wells are units of their own, not classed tags
        tags += [r["fuzer_id"] for r in conn.execute(
            "select fuzer_id from entity where kind = any(%s) and merged_into is null and retired_at is null", (kinds,))]
    assets = sorted(a for a, c in detectors.classes_of(conn, tags).items() if c in set(pack["classes"]))
    covered = [{**cov, "label": _label(conn, a)} for a in assets if (cov := coverage(conn, pack, a))["roles"]]
    problems = []
    if not covered:
        problems.append(f"no measured asset of its classes ({', '.join(pack['classes'])}) on this site")
    elif pack.get("needs") and not any(c["can"] for c in covered):
        problems.append("none of its methods can run on any asset here")
    return {"assets": covered, "problems": problems}
