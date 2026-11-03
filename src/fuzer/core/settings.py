"""Settings (#131): every tunable declared once here, with its type, default, range and description; values
set by people live in the database, per project or for all projects, with a history of who changed what
and why.

    settings.get(conn, "sre.handoff.max_usd")             # this project's value, else the global one, else the default
    settings.set(conn, "sre.handoff.max_usd", 8, by="ana", reason="more diagnoses during the turnaround")

The project is FUZER_PROJECT (or FUZER_PACK until projects land). Values are cached for a few seconds, so
calling get in a loop doesn't hit the database each time.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Any

from psycopg.types.json import Jsonb

CACHE_SECONDS = 5.0
GLOBAL = "global"


class SettingError(ValueError):
    pass


@dataclass(frozen=True)
class Setting:
    key: str
    type: str  # int, float, bool, str, choice, secret (a reference such as "env:FUZER_PI_PASSWORD")
    default: Any
    description: str
    group: str
    min: float | None = None
    max: float | None = None
    choices: tuple = ()
    restart: bool = False  # takes effect when the process restarts, not on the next read
    scopes: tuple = (GLOBAL, "project")

    def parse(self, value: Any) -> Any:
        """The value as this setting's type, or SettingError saying why it isn't one."""
        try:
            if self.type == "int":
                v = int(value) if not isinstance(value, bool) else None
                if v is None or (isinstance(value, float) and value != v):
                    raise ValueError
            elif self.type == "float":
                v = float(value)
            elif self.type == "bool":
                if isinstance(value, bool):
                    v = value
                elif str(value).lower() in ("true", "1", "yes", "on"):
                    v = True
                elif str(value).lower() in ("false", "0", "no", "off"):
                    v = False
                else:
                    raise ValueError
            elif self.type == "secret":
                v = str(value)
                if not v.startswith("env:"):
                    raise SettingError(f"{self.key} is a secret: give a reference such as env:NAME, not the value")
            else:
                v = str(value)
        except (TypeError, ValueError) as e:
            if isinstance(e, SettingError):
                raise
            raise SettingError(f"{self.key} must be {self.type}, not {value!r}") from None
        if self.choices and v not in self.choices:
            raise SettingError(f"{self.key} must be one of {', '.join(map(str, self.choices))}")
        if self.min is not None and v < self.min:
            raise SettingError(f"{self.key} must be at least {self.min:g}")
        if self.max is not None and v > self.max:
            raise SettingError(f"{self.key} must be at most {self.max:g}")
        return v


REGISTRY: dict[str, Setting] = {}


def register(*settings: Setting) -> None:
    for s in settings:
        if s.key in REGISTRY and REGISTRY[s.key] != s:
            raise SettingError(f"{s.key} is declared twice")
        REGISTRY[s.key] = s


register(
    Setting("sre.handoff.min_detections", "int", 3, "Detections an upset needs before it is handed off (a lone "
            "detection is evidence, not an upset worth a person's time)", "SRE", min=1),
    Setting("sre.handoff.max_diagnoses", "int", 10, "Upsets the troubleshooter diagnoses per hand-off run", "SRE",
            min=0, max=100),
    Setting("sre.handoff.max_usd", "float", 5.0, "Claude spend cap per hand-off run (USD)", "SRE", min=0),
)


def project() -> str | None:
    """The current project (#131): FUZER_PROJECT, else the pack name while projects are new."""
    return os.environ.get("FUZER_PROJECT") or os.environ.get("FUZER_PACK")


def scope_of(project_name: str | None) -> str:
    return f"project:{project_name}" if project_name else GLOBAL


def _setting(key: str) -> Setting:
    if key not in REGISTRY:
        raise SettingError(f"no setting {key!r}")
    return REGISTRY[key]


_cache: dict[tuple, tuple[float, Any]] = {}


def get(conn, key: str, project_name: str | None = None) -> Any:
    """The project's value, else the global value, else the default."""
    s = _setting(key)
    p = project_name or project()
    hit = _cache.get((key, p))
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    rows = {r["scope"]: r["value"] for r in conn.execute(
        "select scope, value from setting where key = %s and scope = any(%s)", (key, [scope_of(p), GLOBAL]))}
    v = rows.get(scope_of(p), rows.get(GLOBAL, s.default)) if p else rows.get(GLOBAL, s.default)
    _cache[(key, p)] = (time.monotonic(), v)
    return v


def set(conn, key: str, value: Any, *, by: str, reason: str | None = None, project_name: str | None = None) -> dict:  # noqa: A001
    """Set a value (for one project, or globally without a project), checked against the registry."""
    s = _setting(key)
    scope = scope_of(project_name)
    if scope != GLOBAL and "project" not in s.scopes:
        raise SettingError(f"{key} is global only")
    v = s.parse(value)
    old = conn.execute("select value from setting where scope = %s and key = %s", (scope, key)).fetchone()
    conn.execute(
        """
        insert into setting (scope, key, value, updated_by) values (%s, %s, %s, %s)
        on conflict (scope, key) do update set value = excluded.value, updated_by = excluded.updated_by, updated_at = now()
        """, (scope, key, Jsonb(v), by))
    conn.execute("insert into setting_history (scope, key, old, new, by, reason) values (%s, %s, %s, %s, %s, %s)",
                 (scope, key, Jsonb(old["value"]) if old else None, Jsonb(v), by, reason))
    conn.commit()
    _cache.clear()
    return {"key": key, "scope": scope, "value": v, "was": old["value"] if old else None, "restart": s.restart}


def reset(conn, key: str, *, by: str, reason: str | None = None, project_name: str | None = None) -> dict:
    """Back to the next level down (a project to the global value, global to the default)."""
    _setting(key)
    scope = scope_of(project_name)
    old = conn.execute("delete from setting where scope = %s and key = %s returning value", (scope, key)).fetchone()
    if old:
        conn.execute("insert into setting_history (scope, key, old, new, by, reason) values (%s, %s, %s, null, %s, %s)",
                     (scope, key, Jsonb(old["value"]), by, reason))
    conn.commit()
    _cache.clear()
    return {"key": key, "scope": scope, "was": old["value"] if old else None, "now": get(conn, key, project_name)}


def listing(conn, group: str | None = None, project_name: str | None = None) -> list[dict]:
    """Every setting with its default, the global and project values, the one in effect, and who set it."""
    p = project_name or project()
    stored: dict[tuple, dict] = {(r["key"], r["scope"]): r for r in conn.execute(
        "select key, scope, value, updated_by, updated_at from setting where scope = any(%s)", ([GLOBAL, scope_of(p)],))}
    out = []
    for s in sorted(REGISTRY.values(), key=lambda s: (s.group, s.key)):
        if group and s.group.lower() != group.lower():
            continue
        g, pr = stored.get((s.key, GLOBAL)), stored.get((s.key, scope_of(p))) if p else None
        top = pr or g
        out.append({"key": s.key, "group": s.group, "type": s.type, "description": s.description, "default": s.default,
                    "global": g["value"] if g else None, "project": pr["value"] if pr else None,
                    "value": top["value"] if top else s.default, "from": "project" if pr else "global" if g else "default",
                    "updated_by": top["updated_by"] if top else None,
                    "updated_at": top["updated_at"].isoformat(timespec="seconds") if top else None,
                    **({"min": s.min} if s.min is not None else {}), **({"max": s.max} if s.max is not None else {}),
                    **({"choices": list(s.choices)} if s.choices else {}), "restart": s.restart})
    return out


def history(conn, key: str, limit: int = 50) -> list[dict]:
    _setting(key)
    return [{**r, "at": r["at"].isoformat(timespec="seconds")} for r in conn.execute(
        "select scope, old, new, by, reason, at from setting_history where key = %s order by id desc limit %s",
        (key, limit))]


@dataclass
class Overrides:
    """Settings for a test, without a database: `with settings.Overrides({"sre.handoff.max_usd": 1}):`."""

    values: dict = field(default_factory=dict)

    def __enter__(self):
        for k, v in self.values.items():
            _cache[(k, project())] = (float("inf"), _setting(k).parse(v))
        return self

    def __exit__(self, *exc):
        _cache.clear()
