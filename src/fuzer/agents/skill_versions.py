"""Skill versions as data (P6.4b, #805): a published playbook overrides the one shipped in the repository.

A skill's version 0 is its folder under agents/skills, as shipped. A change is drafted as version 1, 2, ... with the
cases behind it and the eval questions drawn from them, and a person publishes it. The agents then load the live
version: the latest published one, or the shipped folder when nothing is published. Rolling back is one action: the
live version is marked rolled back and the one live before it, or the shipped folder, is live again.

    shipped(path)                                    the playbook in the repository
    live(conn, path)                                 the version the agents load: {version, playbook, ...}
    history(conn, path)                              every version newest first, ending with the shipped one
    draft(conn, path, playbook, by, cases, questions, note)  a new draft version; it cannot name a tool the
                                                     skill's agents do not have
    publish(conn, path, version, by)                 a person makes a draft live
    set_aside(conn, path, version, by, note)         a person turns a draft down
    rollback(conn, path, by)                         a person takes the live version back one step
    roots(conn=None)                                 the folder each skill with a live published version loads from
    trying(path, playbook)                           agents built inside load this playbook (the eval gate's candidate)

A version changes the playbook (SKILL.md) only. The pack manifest, its tools, methods and roles stay as shipped, so a
playbook change cannot give an agent a tool, and conformance (packs.conformance) still holds against the shipped pack.
"""

import contextlib
import hashlib
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from psycopg.types.json import Jsonb

from . import SKILLS
from .capabilities import catalog

log = logging.getLogger(__name__)
CACHE = Path(tempfile.gettempdir()) / "fuzer-skills"  # published playbooks as skill folders, by content hash


class VersionRefused(PermissionError):
    status = 403


def _skill(path: str) -> catalog.SkillDefinition:
    s = next((s for s in catalog.SKILL_CATALOG if s.path == path), None)
    if s is None:
        raise ValueError(f"no skill {path!r}")
    return s


def shipped(path: str) -> str:
    return (SKILLS / _skill(path).path / "SKILL.md").read_text()


def _person(by: str, what: str) -> None:
    from ..core import gates

    if gates.machine(by):
        raise VersionRefused(f"{by} is not a person: only the skill's owner {what}")


def _row(r) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(r).items()}


def live(conn, path: str) -> dict:
    r = conn.execute("select version, playbook, published_by, published_at from skill_version "
                     "where skill = %s and status = 'published'", (_skill(path).path,)).fetchone()
    return _row(r) if r else {"version": 0, "playbook": shipped(path), "published_by": None, "published_at": None}


def history(conn, path: str) -> list[dict]:
    rows = conn.execute("select version, status, playbook, cases, questions, note, created_by, created_at, "
                        "published_by, published_at, eval from skill_version where skill = %s order by version desc",
                        (_skill(path).path,)).fetchall()
    on = any(r["status"] == "published" for r in rows)
    return [_row(r) for r in rows] + [{"version": 0, "status": "retired" if on else "published", "playbook": shipped(path),
                                       "note": "as shipped", "cases": [], "questions": []}]


def _tools(text: str) -> set[str]:
    from .capabilities import REGISTRY

    return set(re.findall(r"`([a-z_]+)(?:\(|`)", text)) & set(REGISTRY)


def draft(conn, path: str, playbook: str, by: str, cases: list | None = None, questions: list | None = None,
          note: str | None = None) -> dict:
    """A new draft version of a skill's playbook. It may not tell the agent to call a tool the skill's agents lack."""
    from .capabilities import tools_by_agent

    s = _skill(path)
    if not playbook.strip():
        raise ValueError("a playbook cannot be empty")
    allowed = {t for a in s.agents for t in tools_by_agent().get(a, [])}
    extra = sorted(_tools(playbook) - allowed)
    if extra:
        raise ValueError(f"the playbook names tools the {s.owner} agent does not have: {', '.join(extra)}")
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"skill:{s.path}",))
        n = conn.execute("select coalesce(max(version), 0) + 1 as n from skill_version where skill = %s",
                         (s.path,)).fetchone()["n"]
        row = conn.execute(
            "insert into skill_version (skill, version, playbook, cases, questions, note, created_by) "
            "values (%s, %s, %s, %s, %s, %s, %s) returning version, status, created_by, created_at",
            (s.path, n, playbook, Jsonb(cases or []), Jsonb(questions or []), note, by)).fetchone()
    return {"skill": s.path, **_row(row)}


def check_publish(conn, path: str, version: int, by: str) -> None:
    """Refuse a publish before anything is spent on it: not a person, or not a draft."""
    s = _skill(path)
    _person(by, "publishes a skill version")
    cur = conn.execute("select status from skill_version where skill = %s and version = %s",
                       (s.path, version)).fetchone()
    if cur is None:
        raise ValueError(f"{s.path} has no version {version}")
    if cur["status"] != "draft":
        raise ValueError(f"version {version} is {cur['status']}, not a draft")


def publish(conn, path: str, version: int, by: str, evaluation: dict | None = None) -> dict:
    """A person makes a draft live; the version live before it is retired."""
    s = _skill(path)
    _person(by, "publishes a skill version")
    with conn.transaction():
        cur = conn.execute("select status from skill_version where skill = %s and version = %s for update",
                           (s.path, version)).fetchone()
        if cur is None:
            raise ValueError(f"{s.path} has no version {version}")
        if cur["status"] != "draft":
            raise ValueError(f"version {version} is {cur['status']}, not a draft")
        conn.execute("update skill_version set status = 'retired' where skill = %s and status = 'published'", (s.path,))
        conn.execute("update skill_version set status = 'published', published_by = %s, published_at = now(), "
                     "eval = coalesce(%s::jsonb, eval) where skill = %s and version = %s",
                     (by, Jsonb(evaluation) if evaluation is not None else None, s.path, version))
    _forget()
    _reviewed(conn, s.path, version, by, "the draft was published")
    return live(conn, path) | {"skill": s.path}


def set_aside(conn, path: str, version: int, by: str, note: str | None = None) -> dict:
    """A person turns a draft down; it stays in the skill's history as refused, with their note."""
    s = _skill(path)
    _person(by, "sets a draft aside")
    with conn.transaction():
        r = conn.execute("update skill_version set status = 'refused', note = concat_ws(' ', note, %s::text) "
                         "where skill = %s and version = %s and status = 'draft' returning version",
                         (f"(set aside by {by}{': ' + note if note else ''})", s.path, version)).fetchone()
        if r is None:
            raise ValueError(f"{s.path} has no draft version {version}")
    _reviewed(conn, s.path, version, by, f"the draft was set aside{': ' + note if note else ''}")
    return {"skill": s.path, "version": version, "status": "refused"}


def _reviewed(conn, path: str, version: int, by: str, reason: str) -> None:
    """The draft's work for the skill's owner (P6.4c), cleared now a person has decided it."""
    from ..ops import skill_review

    skill_review.close_review(conn, path, version, by, reason)
    conn.commit()


def _forget() -> None:
    global _read
    _read = None


def rollback(conn, path: str, by: str) -> dict:
    """One action: the live version is rolled back and the one live before it (or the shipped folder) is live."""
    s = _skill(path)
    _person(by, "rolls a skill back")
    with conn.transaction():
        cur = conn.execute("update skill_version set status = 'rolled_back', note = concat_ws(' ', note, %s::text) "
                           "where skill = %s and status = 'published' returning version",
                           (f"(rolled back by {by})", s.path)).fetchone()
        if cur is None:
            raise ValueError(f"{s.path} is on its shipped playbook: there is nothing to roll back")
        conn.execute(
            "update skill_version set status = 'published' where skill = %s and version = ("
            "  select max(version) from skill_version where skill = %s and version < %s and status = 'retired'"
            "  and published_at is not null)", (s.path, s.path, cur["version"]))
    _forget()
    return live(conn, path) | {"skill": s.path, "rolled_back": cur["version"]}


def _folder(path: str, playbook: str) -> Path:
    """The skill's shipped folder with its playbook replaced, under a name from the content so it is never stale."""
    digest = hashlib.sha256(f"{path}\0{playbook}".encode()).hexdigest()[:16]
    root = CACHE / digest / path
    if not (root / "SKILL.md").is_file():
        tmp = CACHE / f".{digest}.{os.getpid()}"
        shutil.copytree(SKILLS / path, tmp / path, ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
        (tmp / path / "SKILL.md").write_text(playbook)
        try:
            (CACHE / digest).parent.mkdir(parents=True, exist_ok=True)
            tmp.rename(CACHE / digest)
        except OSError:  # another process wrote the same content first
            shutil.rmtree(tmp, ignore_errors=True)
    return root


_read: tuple[float, dict] | None = None
CACHE_SECONDS = 10  # building a team builds every agent; read the live versions once for all of them


_trying: dict[str, Path] = {}


@contextlib.contextmanager
def trying(path: str, playbook: str):
    """Agents built inside load `playbook` for `path` instead of its live version: how the eval gate (P6.4d) asks the
    candidate the same questions as the live version. Nothing is stored."""
    s = _skill(path)
    _trying[s.path] = _folder(s.path, playbook)
    try:
        yield
    finally:
        _trying.pop(s.path, None)


def roots(conn=None) -> dict[str, Path]:
    """The folder each skill with a live published version loads from; a skill not listed loads as shipped. With no
    connection and no database configured, nothing is published (an import, a unit test). Without a connection the
    answer may be up to CACHE_SECONDS old. A playbook being tried (`trying`) wins."""
    return {**_published(conn), **_trying}


def _published(conn=None) -> dict[str, Path]:
    global _read
    if conn is None:
        from ..core import db

        if not os.environ.get("FUZER_DATABASE_URL") and db._server is None:
            return {}
        if _read and time.monotonic() - _read[0] < CACHE_SECONDS:
            return _read[1]
        try:
            with db.connect() as c:
                out = _published(c)
        except Exception:
            log.warning("could not read published skill versions; agents load the shipped skills", exc_info=True)
            return {}
        _read = (time.monotonic(), out)
        return out
    try:
        rows = conn.execute("select skill, playbook from skill_version where status = 'published'").fetchall()
    except Exception:  # a database from before P6.4b
        conn.rollback()
        return {}
    known = {s.path for s in catalog.SKILL_CATALOG}
    return {r["skill"]: _folder(r["skill"], r["playbook"]) for r in rows if r["skill"] in known}
