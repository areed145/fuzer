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

from . import models

CACHE_SECONDS = 5.0
OVERRIDE = "override"  # a cache entry set by Overrides (tests), not read from the database
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
    env: str | None = None  # the environment variable it replaces: still read for one release, with a warning

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


EFFORTS = ("low", "medium", "high", "xhigh", "max")
LLM_PROVIDERS = ("anthropic", "openai", "azure_openai", "ollama")

register(
    Setting("llm.provider", "choice", "anthropic", "Provider used for reader calls", "LLM", choices=LLM_PROVIDERS),
    Setting("llm.base_url", "str", "", "OpenAI-compatible endpoint (empty uses the provider default)", "LLM"),
    Setting("llm.api_key", "secret", "env:ANTHROPIC_API_KEY", "Environment variable holding the reader provider API key",
            "LLM"),
    Setting("llm.model", "str", models.DEFAULT_MODEL, "Model for log links, P&IDs, documents, curves, mapping "
            "suggestions and daily report drafts", "LLM", env="FUZER_LLM_MODEL"),
    Setting("llm.effort", "choice", "medium", "Effort for the readers", "LLM", choices=EFFORTS, env="FUZER_LLM_EFFORT"),
    Setting("agents.provider", "choice", "anthropic", "Provider used for assistant and specialist agents", "Agents",
            choices=LLM_PROVIDERS, restart=True),
    Setting("agents.base_url", "str", "", "OpenAI-compatible endpoint (empty uses the provider default)", "Agents",
            restart=True),
    Setting("agents.api_key", "secret", "env:ANTHROPIC_API_KEY", "Environment variable holding the agents provider API key",
            "Agents", restart=True),
    Setting("agents.model", "str", models.DEFAULT_MODEL, "Model for the assistant's orchestrator", "Agents",
            env="FUZER_AGENT_MODEL", restart=True),
    Setting("agents.effort", "choice", "high", "Effort for the assistant's orchestrator", "Agents", choices=EFFORTS,
            env="FUZER_AGENT_EFFORT", restart=True),
    Setting("agents.specialist_model", "str", "", "Model for the specialists (empty: the orchestrator's)",
            "Agents", env="FUZER_AGENT_SPECIALIST_MODEL", restart=True),
    Setting("agents.specialist_effort", "choice", "medium", "Effort for the specialists", "Agents", choices=EFFORTS,
            env="FUZER_AGENT_SPECIALIST_EFFORT", restart=True),
    Setting("agents.fallbacks", "bool", True, "On a policy decline, let the API re-run the request on a fallback model",
            "Agents", env="FUZER_AGENT_FALLBACKS", restart=True),
    Setting("assistant.retention_days", "int", 0, "Days the assistant keeps a conversation after its last message, "
            "with its images and answers (0: keep them)", "Agents", min=0),
    Setting("assistant.concurrent_questions", "int", 1, "Questions the web assistant can run at once per server "
            "process; prevents overlapping model/tool workloads", "Agents", min=1, max=8),
    Setting("assistant.timeout_seconds", "int", 300, "Time limit for a web assistant question, including specialists",
            "Agents", min=30, max=900),
    Setting("daily_report.close_cron", "str", "0 7 * * *", "When the day is closed and its report drafted (cron)",
            "Schedule", restart=True, env="FUZER_DAY_CLOSE_CRON"),
    Setting("work.lease_seconds", "int", 900, "How long an agent holds a workflow task before another may take it",
            "Schedule", min=30, env="FUZER_WORK_LEASE_SECONDS"),
    Setting("db.work_mem", "str", "256MB", "Postgres work_mem for the rebuild's big queries", "Database",
            env="FUZER_WORK_MEM"),
    Setting("sre.handoff.min_detections", "int", 3, "Detections an upset needs before it is handed off (a lone "
            "detection is evidence, not an upset worth a person's time)", "SRE", min=1),
    Setting("sre.handoff.max_diagnoses", "int", 10, "Upsets the troubleshooter diagnoses per hand-off run", "SRE",
            min=0, max=100),
    Setting("sre.handoff.max_usd", "float", 5.0, "Claude spend cap per hand-off run (USD)", "SRE", min=0),
    Setting("sre.step_minutes", "int", 10, "Minutes between the values the detectors read", "SRE", min=1, max=60),
    Setting("sre.lookback_hours", "int", 24, "Hours before the window that a step is judged against", "SRE", min=1,
            max=168),
    Setting("sre.learned_minutes", "int", 60, "Minutes a value must stay outside a learned normal range (one no "
            "source set) to count", "SRE", min=0),
    Setting("sre.spe_minutes", "int", 120, "Minutes a multivariate anomaly lasts before it is raised", "SRE", min=10),
    Setting("sre.merge_minutes", "int", 30, "The same finding again within this many minutes carries on the one "
            "before", "SRE", min=0),
    Setting("sre.drift_rise", "float", 0.05, "Share of its starting value a drift must climb before it is raised",
            "SRE", min=0, max=1),
    Setting("sre.group_gap_hours", "float", 2.0, "A detection joins an upset whose latest detection started within "
            "this many hours", "SRE", min=0.1, max=48),
    Setting("sre.hops", "int", 4, "Units this many links apart in the plant model count as connected (upsets and "
            "correlation studies)", "SRE", min=1, max=10),
    Setting("signals.flat_hours", "float", 2.0, "Hours with no change at all, while the unit runs, before a reading "
            "is frozen", "Signals", min=0.5, max=48),
    Setting("signals.stuck_hours", "float", 6.0, "Hours a reading moves far less than usual (under stuck_ratio x), "
            "while the unit runs, before it is stuck", "Signals", min=0.5, max=72),
    Setting("signals.stuck_ratio", "float", 0.1, "A stuck reading's step-to-step spread is under this share of its "
            "usual", "Signals", min=0.01, max=0.9),
    Setting("signals.noisy_hours", "float", 1.0, "Hours a reading moves far more than usual (over noisy_ratio x) "
            "before it is noisy", "Signals", min=0.25, max=48),
    Setting("signals.noisy_ratio", "float", 4.0, "A noisy reading's step-to-step spread is over this many times its "
            "usual", "Signals", min=1.5, max=50),
    Setting("signals.range_minutes", "int", 30, "Minutes at or past the end of its instrument range before a reading "
            "is out of range", "Signals", min=10, max=1440),
    Setting("signals.redundant_hours", "float", 2.0, "Hours two redundant transmitters stay further apart than usual "
            "before one is drifting", "Signals", min=0.5, max=72),
    Setting("signals.baseline_days", "int", 7, "Days before the window that set a reading's usual behaviour "
            "(instrument_health)", "Signals", min=1, max=90),
    Setting("signals.merge_minutes", "int", 240, "The same instrument finding again within this many minutes is one "
            "finding", "Signals", min=0, max=1440),
    Setting("signals.step_z", "float", 6.0, "A step: the median moves this many times its usual window-to-window "
            "move", "Signals", min=2, max=50),
    Setting("signals.var_ratio", "float", 4.0, "A variance change: the jitter either side differs by this "
            "factor", "Signals", min=1.5, max=50),
    Setting("signals.drift_z", "float", 4.0, "A drift: the fitted trend moves this many times the spread around "
            "it", "Signals", min=2, max=50),
    Setting("alarms.flood_per_10min", "int", 10, "More alarms than this in 10 minutes is a flood (EEMUA 191)", "Alarms",
            min=2, max=100),
    Setting("alarms.target_per_hour", "float", 6.0, "Alarms per hour an operator can handle; more is overloaded",
            "Alarms", min=0.5, max=60),
    Setting("alarms.standing_hours", "float", 24.0, "Hours an alarm stays on before it is standing", "Alarms", min=1,
            max=720),
    Setting("alarms.chatter", "int", 3, "Alarms on one point, each within a minute of the last clearing, that make "
            "it chattering", "Alarms", min=2, max=50),
    Setting("alarms.failed_start_minutes", "int", 30, "A start followed by a stop within this many minutes is a "
            "failed start", "Alarms", min=1, max=1440),
    Setting("opportunities.gas_lift_breakeven", "float", 0.5, "Sm3/d of oil per kSm3/d of lift gas below which "
            "more gas isn't worth it", "Opportunities", min=0, max=10),
    Setting("opportunities.stale_days", "int", 90, "Days without a well test before the test is stale",
            "Opportunities", min=7, max=730),
    Setting("opportunities.material", "float", 0.5, "A decided opportunity comes back when its gain moves by this "
            "share", "Opportunities", min=0.05, max=10),
    Setting("daily_report.mover_pct", "float", 10.0, "A well test this many percent from the well's previous one is "
            "a mover in the daily report", "Reports", min=1, max=100),
    Setting("studies.max_points", "int", 2000, "Points an interactive correlation or clustering study reads at most",
            "Studies", min=10, max=100000),
    Setting("studies.max_values", "int", 15_000_000, "Values (points x steps) a study reads at once; bigger ones run "
            "in the background", "Studies", min=10000),
    Setting("studies.workers", "int", 8, "Threads reading a study's points in parallel", "Studies", min=1, max=64),
    # Sources (#132). Since #225 these only seed a connection in the source catalog (fuzer sources connections):
    # the first time a URL is set, it becomes a connection and pi_points / pi_af / adx_series move onto it.
    Setting("sources.pi.url", "str", "", "PI Web API base URL; seeds a pi_web_api connection in the source catalog (#225)",
            "Sources", restart=True, env="FUZER_PI_WEB_API_URL"),
    Setting("sources.pi.data_server", "str", "", "PI Data Archive to read points from (empty: the first)", "Sources",
            env="FUZER_PI_DATA_SERVER"),
    Setting("sources.pi.af_server", "str", "", "PI AF server (empty: the first)", "Sources", env="FUZER_PI_AF_SERVER"),
    Setting("sources.pi.af_database", "str", "", "PI AF database (empty: the server's first)", "Sources",
            env="FUZER_PI_AF_DATABASE"),
    Setting("sources.pi.user", "str", "", "PI Web API user for basic auth (empty: no auth)", "Sources",
            env="FUZER_PI_USER"),
    Setting("sources.pi.password", "secret", "env:FUZER_PI_PASSWORD", "Where the PI Web API password is kept",
            "Sources"),
    Setting("sources.pi.verify_tls", "bool", True, "Check the PI Web API's TLS certificate", "Sources",
            env="FUZER_PI_VERIFY_TLS"),
    Setting("sources.adx.url", "str", "", "Azure Data Explorer cluster URL; seeds an adx connection in the source catalog (#225)", "Sources",
            restart=True, env="FUZER_ADX_URL"),
    Setting("sources.adx.database", "str", "", "ADX database", "Sources", env="FUZER_ADX_DATABASE"),
    Setting("sources.adx.catalog", "str", "SeriesCatalog", "ADX table listing the series", "Sources",
            env="FUZER_ADX_CATALOG"),
    Setting("sources.adx.token", "secret", "env:FUZER_ADX_TOKEN", "Where the ADX bearer token is kept", "Sources"),
    Setting("alerts.webhook", "secret", "env:FUZER_ALERT_WEBHOOK", "Where the URL failed scheduled runs are posted "
            "to (Teams, Slack or any JSON webhook) is kept", "Schedule"),
    Setting("access.session_hours", "int", 8, "Absolute signed-in session lifetime in hours", "Access",
            min=1, max=24, scopes=(GLOBAL,)),
    Setting("access.api_concurrency", "int", 4, "Maximum simultaneous authenticated API requests per server process; "
            "bounds memory pressure on the single-instance demo, not a commercial quota", "Access",
            min=1, max=32, scopes=(GLOBAL,)),
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


_warned: set[str] = set()


def _stored(conn, key: str, p: str | None) -> dict:
    if conn is None:  # open a short connection; without a database, the variable or the default
        from . import db

        if not os.environ.get("FUZER_DATABASE_URL") and db._server is None:
            return {}  # don't start the embedded database just to read a setting (an import, a test)
        try:
            with db.connect() as c:
                return _stored(c, key, p)
        except Exception:
            return {}
    try:
        return {r["scope"]: r["value"] for r in conn.execute(
            "select scope, value from setting where key = %s and scope = any(%s)", (key, [scope_of(p), GLOBAL]))}
    except Exception:  # a database from before #131: no setting table yet
        conn.rollback()
        return {}


def secret(conn, key: str) -> str | None:
    """A secret setting's value, read from where its reference points (env:NAME); None when it isn't there."""
    if _setting(key).type != "secret":
        raise SettingError(f"{key} isn't a secret")
    ref = get(conn, key) or ""
    return os.environ.get(ref[4:]) or None if ref.startswith("env:") else None


def get(conn, key: str, project_name: str | None = None) -> Any:
    """The project's value, else the global value, else the old environment variable (for one release),
    else the default. `conn` None opens a short connection."""
    s = _setting(key)
    p = project_name or project()
    hit = _cache.get((key, p))  # the stored rows, not the value: the environment and defaults are read fresh
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        rows = hit[1]
        if OVERRIDE in rows:
            return rows[OVERRIDE]
    else:
        rows = _stored(conn, key, p)
        _cache[(key, p)] = (time.monotonic(), rows)
    if p and scope_of(p) in rows:
        v = rows[scope_of(p)]
    elif GLOBAL in rows:
        v = rows[GLOBAL]
    elif s.env and os.environ.get(s.env):
        v = s.parse(os.environ[s.env])
        if s.env not in _warned:
            _warned.add(s.env)
            import warnings

            warnings.warn(f"{s.env} is deprecated: use the setting {key} (fuzer settings set {key} ...)",
                          DeprecationWarning, stacklevel=2)
    else:
        v = s.default
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
    """Every setting with its default, the global and project values, the one in effect, and who set it;
    groups in the order they are declared, and a secret's reference with whether it resolves."""
    p = project_name or project()
    stored: dict[tuple, dict] = {(r["key"], r["scope"]): r for r in conn.execute(
        "select key, scope, value, updated_by, updated_at from setting where scope = any(%s)", ([GLOBAL, scope_of(p)],))}
    out, groups = [], list(dict.fromkeys(s.group for s in REGISTRY.values()))
    for s in sorted(REGISTRY.values(), key=lambda s: groups.index(s.group)):
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
                    **({"choices": list(s.choices)} if s.choices else {}), "restart": s.restart,
                    "scopes": list(s.scopes),
                    **({"resolves": bool(os.environ.get(str(top["value"] if top else s.default or "")[4:]))}
                       if s.type == "secret" else {})})
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
            _cache[(k, project())] = (float("inf"), {OVERRIDE: _setting(k).parse(v)})
        return self

    def __exit__(self, *exc):
        _cache.clear()
