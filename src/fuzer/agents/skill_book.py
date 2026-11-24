"""The skills page (Ops IQ, Skills): every skill the agents load, read and changed by the engineers who own them.

A skill is a playbook (SKILL.md) an agent follows, versioned as data (skill_versions). This module is what the page
reads: one row per skill with the version the agents load, a draft waiting for its owner and where it fell short since
it went live; one skill with its live playbook, what its agents may call and its shortfalls; and a version's playbook
against the live one, line by line. Changing a skill goes through skill_versions and skill_gate, never here.

    listing(conn)               one row per skill, by area
    detail(conn, path)          one skill: live playbook, pack, tools, eval questions, shortfalls since live
    diff(conn, path, version)   a version against the live playbook: [{op: same|add|remove, text}]
"""

import difflib
import re

import yaml

from ..core import llm
from . import SKILLS, packs, shortfalls, skill_versions
from .capabilities import catalog


def _front(text: str) -> tuple[dict, str]:
    """A playbook's front matter (name, description) and its body."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                return yaml.safe_load(parts[1]) or {}, parts[2].lstrip("\n")
            except yaml.YAMLError:
                pass
    return {}, text


def _title(s: catalog.SkillDefinition) -> str:
    manifest = SKILLS / s.path / packs.MANIFEST
    if manifest.is_file():
        title = (yaml.safe_load(manifest.read_text()) or {}).get("title")
        if title:
            return str(title)
    return s.name.replace("-", " ").capitalize()


def _area(s: catalog.SkillDefinition) -> str:
    return s.path.split("/", 1)[0]


def _label(agent: str) -> str:
    """An agent's name as people read it (Maintenance & Integrity); the shared folder is every agent's."""
    if agent == "shared":
        return "Shared by every agent"
    return next((a.label for a in catalog.AGENT_CATALOG if a.id == agent), agent.replace("-", " ").capitalize())


def _versions(conn) -> dict[str, dict]:
    """Per skill: the live published version and the draft waiting, from skill_version."""
    out: dict[str, dict] = {}
    for r in conn.execute("select skill, version, status, created_by, created_at, published_by, published_at, note, "
                          "eval from skill_version where status in ('published', 'draft') order by skill, version"):
        e = out.setdefault(r["skill"], {"live": None, "draft": None})
        row = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(r).items() if k != "skill"}
        if r["status"] == "published":
            e["live"] = row
        else:
            e["draft"] = row  # the newest draft wins
    return out


def _reviews(conn) -> dict[str, int]:
    from ..ops import skill_review

    return {r["key"]: r["item_id"] for r in conn.execute(
        "select key, item_id from exception_record where type = %s and state = 'open'", (skill_review.SKILL_CHANGE,))}


def _questions(conn, path: str, live_version: int) -> list[dict]:
    from . import skill_gate

    try:
        return skill_gate.questions(conn, path, live_version)
    except ValueError:
        return []


def listing(conn) -> dict:
    """Every skill with its live version, a draft waiting and the shortfalls since it went live, grouped by area."""
    from ..ops import skill_review

    versions, reviews = _versions(conn), _reviews(conn)
    short = {s["skill"]: s for s in shortfalls.gather(conn)}
    rows = []
    for s in catalog.SKILL_CATALOG:
        v = versions.get(s.path, {"live": None, "draft": None})
        live = v["live"] or {"version": 0, "published_by": None, "published_at": None, "eval": {}}
        since = live.get("published_at")
        fell = [x for x in (short.get(s.path) or {}).get("shortfalls", []) if not since or x["at"] >= since]
        draft = v["draft"]
        rows.append({
            "skill": s.path, "name": s.name, "title": _title(s), "area": _area(s), "area_label": _label(_area(s)),
            "owner": s.owner, "owner_label": _label(s.owner), "agents": list(s.agents), "shared": s.shared,
            "purpose": s.purpose,
            "live": {"version": live["version"], "by": live.get("published_by"), "at": since},
            "draft": draft and {"version": draft["version"], "by": draft["created_by"], "at": draft["created_at"],
                                "note": draft["note"],
                                "review_case": reviews.get(skill_review.key_of(s.path, draft["version"]))},
            "shortfalls": len(fell),
            "questions": len(_questions(conn, s.path, live["version"])),
            "score": ((live.get("eval") or {}).get("candidate") or {}).get("score"),
        })
    conn.commit()
    return {"skills": rows, "ai": llm.availability("agents")}


def detail(conn, path: str) -> dict:
    """One skill: its live playbook (front matter apart), its pack, the tools its agents may call, the eval questions
    the gate asks, and where it fell short since its live version went live."""
    from .capabilities import tools_by_agent

    s = skill_versions._skill(path)
    live = skill_versions.live(conn, path)
    meta, body = _front(live["playbook"])
    pack = None
    if (SKILLS / s.path / packs.MANIFEST).is_file():
        manifest = yaml.safe_load((SKILLS / s.path / packs.MANIFEST).read_text()) or {}
        pack = {k: manifest.get(k) or [] for k in ("classes", "tools", "methods", "detectors", "briefs")}
    since = live.get("published_at")
    short = next((x for x in shortfalls.gather(conn) if x["skill"] == s.path), None)
    fell = [x for x in (short or {}).get("shortfalls", []) if not since or x["at"] >= since]
    tools = sorted({t for a in s.agents for t in tools_by_agent().get(a, [])})
    out = {
        "skill": s.path, "name": s.name, "title": _title(s), "area": _area(s), "area_label": _label(_area(s)),
        "owner": s.owner, "owner_label": _label(s.owner), "agents": [_label(a) for a in s.agents],
        "personas": list(s.personas), "shared": s.shared,
        "purpose": meta.get("description") or s.purpose, "data": s.data, "output": s.output, "boundary": s.boundary,
        "live": {"version": live["version"], "by": live.get("published_by"), "at": since},
        "playbook": live["playbook"], "body": body, "pack": pack, "tools": tools,
        "named_tools": sorted(set(re.findall(r"`([a-z_]+)(?:\(|`)", body)) & set(tools)),
        "questions": _questions(conn, s.path, live["version"]),
        "shortfalls": fell, "ai": llm.availability("agents"),
    }
    conn.commit()
    return out


def diff(conn, path: str, version: int) -> dict:
    """A version's playbook against the live one, line by line, with the counts of lines added and removed."""
    history = skill_versions.history(conn, path)
    chosen = next((v for v in history if v["version"] == version), None)
    if chosen is None:
        raise ValueError(f"{path} has no version {version}")
    live = skill_versions.live(conn, path)
    conn.commit()
    lines = []
    for op in difflib.ndiff(live["playbook"].splitlines(), chosen["playbook"].splitlines()):
        if op.startswith("? "):
            continue
        lines.append({"op": {"  ": "same", "+ ": "add", "- ": "remove"}[op[:2]], "text": op[2:]})
    return {"skill": path, "version": version, "against": live["version"], "lines": lines,
            "questions": len(_questions(conn, path, version)),
            "added": sum(x["op"] == "add" for x in lines), "removed": sum(x["op"] == "remove" for x in lines)}
