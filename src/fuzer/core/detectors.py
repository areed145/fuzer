"""The detector registry (P1.2): everything that looks for trouble in the data, declared once where it is written.

    @detectors.register("drifts", title="A slow climb in what wears and fouls", kind=detectors.STATISTICAL,
                        roles=("vibration", "bearing_temperature"), needs={"historian points": POINTS},
                        cadence="daily", raises=("upset",), params=("sre.drift_rise",), eval="replay:sre:drifts",
                        window=True)
    def drifts(conn, pack, since, until, src): ...

A detector says what kind it is (a rule, a statistic, a model against a curve, an agent), the equipment classes and
measurement roles it applies to, what it needs before it can run (`needs`: a name a person reads, and a query that
returns a row when it is there, or a function of `(conn, pack)`), how often it runs, the work type it raises, the
settings that tune it and where it is scored (`replay:<name>` in the replay harness, `test:<node>` on the fake plant).

`run` is how a schedule calls one: a detector switched off for the asset reports "off", one whose needs are missing
reports "not applicable: missing X", neither runs, and every run is kept in `detector_run`, so the Detection page and
docs/detectors.md read from one place. Adding a detector is one registered function and its test.

On and off, and thresholds for one equipment class, are settings (`detectors.on@<name>`, `<param>@<class>`), so each
change is versioned and attributed in the settings history. A detector lists in `by_class` the parameters it reads
per equipment class, through `param`; only those take a class value.
"""

import importlib
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import roles as measurement_roles
from . import settings

RULE, STATISTICAL, MODEL, AGENTIC = "rule", "statistical", "model", "agentic"
KINDS = {RULE: "Rule", STATISTICAL: "Statistical", MODEL: "Model", AGENTIC: "Agentic"}
CADENCES = {"5min": "Every 5 minutes", "30min": "Every 30 minutes", "hourly": "Every hour", "6h": "Every 6 hours",
            "daily": "Once a day", "request": "On request"}
RAN, NOT_APPLICABLE, FAILED, OFF = "ran", "not_applicable", "failed", "off"
ON = "detectors.on"  # the setting that switches detectors; detectors.on@<name> for one

# Where detectors are written. A module here is imported when the registry is first read.
MODULES = ("fuzer.ops.sre", "fuzer.analysis.limits", "fuzer.analysis.trips", "fuzer.analysis.rotating",
           "fuzer.analysis.records", "fuzer.sources.alerts", "fuzer.ops.opportunities", "fuzer.ops.safeguards")

POINTS = "select 1 from edge where rel = 'has_timeseries' limit 1"  # the need most detectors share


class DetectorError(ValueError):
    """A detector declared wrongly, or one nobody registered."""


@dataclass(frozen=True)
class Detector:
    name: str
    title: str                     # what it looks for, in the words a person reads
    kind: str                      # rule, statistical, model or agentic
    fn: Callable
    cadence: str                   # a key of CADENCES
    raises: tuple = ()             # the work types it raises
    classes: tuple = ()            # the equipment classes it applies to; none means any asset with the inputs
    roles: tuple = ()              # the measurement roles it reads (keys of core/roles.py)
    needs: dict = field(default_factory=dict)  # name -> query returning a row when present, or fn(conn, pack)
    params: tuple = ()             # the settings that tune it
    eval: str | None = None        # replay:<detector> or test:<node>
    window: bool = False           # a window function the SRE scan and the replay harness call
    found: Callable | None = None  # how many findings a run's result holds
    by_class: tuple = ()           # the params it reads per equipment class (through `param`)

    @property
    def doc(self) -> str:
        return " ".join((self.fn.__doc__ or "").split())


_registry: dict[str, Detector] = {}
_loaded = False


def register(name: str, *, title: str, kind: str, cadence: str, **kw):
    """Declare the function below as a detector."""
    if kind not in KINDS:
        raise DetectorError(f"{name}: a detector's kind is one of {', '.join(KINDS)}, not {kind!r}")
    if cadence not in CADENCES:
        raise DetectorError(f"{name}: a detector's cadence is one of {', '.join(CADENCES)}, not {cadence!r}")
    if not title.strip():
        raise DetectorError(f"{name}: a detector says what it looks for")
    if unknown := set(kw.get("roles", ())) - set(measurement_roles.ROLES):
        raise DetectorError(f"{name}: {', '.join(sorted(unknown))} isn't a measurement role (core/roles.py)")
    if not set(kw.get("by_class", ())) <= set(kw.get("params", ())):
        raise DetectorError(f"{name}: a parameter read per equipment class is one of its params")

    def wrap(fn):
        held = _registry.get(name)
        if held and (held.fn.__module__, held.fn.__qualname__) != (fn.__module__, fn.__qualname__):
            raise DetectorError(f"two detectors are called {name!r}")
        _registry[name] = Detector(name=name, title=title, kind=kind, fn=fn, cadence=cadence, **kw)
        return fn
    return wrap


def unregister(name: str) -> None:
    _registry.pop(name, None)


def registry() -> dict[str, Detector]:
    """Every detector, by module in MODULES order and then in the order declared, however the modules came to be
    imported."""
    global _loaded
    if not _loaded:
        for m in MODULES:
            importlib.import_module(m)
        _loaded = True
    at = {m: i for i, m in enumerate(MODULES)}
    order = sorted(enumerate(_registry.items()), key=lambda x: (at.get(x[1][1].fn.__module__, len(at)), x[0]))
    return {name: d for _, (name, d) in order}


def get(name: str) -> Detector:
    try:
        return registry()[name]
    except KeyError:
        raise DetectorError(f"no detector {name!r} in the registry") from None


def window() -> list[Detector]:
    """The window detectors, which the SRE scan runs over each stretch of time and the replay harness scores."""
    return [d for d in registry().values() if d.window]


def missing(conn, pack, d: Detector) -> list[str]:
    """The needs this asset does not meet, by name."""
    out = []
    for name, need in d.needs.items():
        with conn.transaction():  # a need's query failing must not take the caller's work with it
            there = need(conn, pack) if callable(need) else conn.execute(need).fetchone()
        if not there:
            out.append(name)
    return out


def note(lacking: list[str]) -> str:
    return "not applicable: missing " + ", ".join(lacking)


def record(conn, name: str, status: str, said: str | None = None, found: int | None = None,
           seconds: float | None = None, since=None, until=None) -> None:
    conn.execute("insert into detector_run (detector, status, note, found, seconds, since, until) "
                 "values (%s, %s, %s, %s, %s, %s, %s)", (name, status, said, found, seconds, since, until))


def on(conn, name: str, project_name: str | None = None) -> bool:
    """Whether the detector runs on this asset (the project's detectors.on@<name>, else the global ones)."""
    return bool(settings.get(conn, ON, project_name, qualifier=name))


def switch(conn, name: str, turn_on: bool, *, by: str, reason: str | None = None, project_name: str | None = None) -> dict:
    """Switch a detector on or off for one asset (or for all, without a project); kept in the settings history."""
    get(name)
    return settings.set(conn, f"{ON}{settings.QUALIFIER}{name}", bool(turn_on), by=by, reason=reason,
                        project_name=project_name)


def run(conn, pack, name: str, **kw):
    """Run one detector as a schedule does: its own result, {"status": "off"} when it is switched off on this asset,
    or {"status": "not_applicable", "note": "not applicable: missing X"} when the asset lacks what it needs. Either
    way the run is kept."""
    d = get(name)
    if not on(conn, name):
        record(conn, name, OFF, "switched off", since=kw.get("since"), until=kw.get("until"))
        return {"status": OFF, "note": "switched off"}
    if lacking := missing(conn, pack, d):
        record(conn, name, NOT_APPLICABLE, note(lacking), since=kw.get("since"), until=kw.get("until"))
        return {"status": NOT_APPLICABLE, "missing": lacking, "note": note(lacking)}
    if "pack" in inspect.signature(d.fn).parameters:
        kw["pack"] = pack
    t0 = time.perf_counter()
    try:
        out = d.fn(conn, **kw)
    except Exception as e:
        conn.rollback()
        record(conn, name, FAILED, str(e)[:300], seconds=time.perf_counter() - t0)
        conn.commit()
        raise
    record(conn, name, RAN, found=d.found(out) if d.found else None, seconds=round(time.perf_counter() - t0, 2),
           since=kw.get("since"), until=kw.get("until"))
    return out


def last_runs(conn) -> dict[str, dict]:
    """Each detector's latest run."""
    return {r["detector"]: dict(r) for r in conn.execute(
        "select distinct on (detector) detector, status, note, found, seconds, since, until, at from detector_run "
        "order by detector, id desc")}


# ---- equipment classes ---------------------------------------------------------------------------
# A unit's class is the taxonomy class its tag (or the equipment installed there) is classified as, by its code
# (ISO14224:CO.CE is CO), else the unit's own kind (a well). Whatever taxonomy the pack uses, the code is the class.
_UNIT_CLASS = """
select u.id as unit, coalesce(cl.label, e.kind) as label
from unnest(%s::text[]) u(id)
join entity e on e.fuzer_id = u.id
left join lateral (
    (select l.label from edge c join entity_label l on l.fuzer_id = c.dst
     where c.src = u.id and c.rel = 'classified_as' limit 1)
    union all
    (select l.label from edge i join edge c on c.src = i.src and c.rel = 'classified_as'
     join entity_label l on l.fuzer_id = c.dst
     where i.dst = u.id and i.rel = 'installed_at' and i.valid_to is null limit 1)
    limit 1) cl on true
"""


def _code(label: str) -> str:
    return label.partition(":")[2].partition(".")[0] if ":" in label else label


def classes_of(conn, units) -> dict[str, str]:
    """Each unit's equipment class code."""
    units = sorted({u for u in units if u})
    if not units:
        return {}
    return {r["unit"]: _code(r["label"]) for r in conn.execute(_UNIT_CLASS, (units,))}


def asset_classes(conn) -> list[dict]:
    """The equipment classes on this asset, with how many tags (or wells) are in each and the class's name."""
    rows = conn.execute(
        """
        with tagged as (
            select c.src, split_part(l.label, ':', 1) as std, split_part(split_part(l.label, ':', 2), '.', 1) as code
            from edge c join entity_label l on l.fuzer_id = c.dst join entity s on s.fuzer_id = c.src and s.kind = 'tag'
            where c.rel = 'classified_as' and l.label like '%%:%%')
        select t.code, count(distinct t.src) as units, max(g.value #>> '{}') as name
        from tagged t left join entity_label cl on cl.label = t.std || ':' || t.code
        left join golden g on g.fuzer_id = cl.fuzer_id and g.field = 'description'
        group by t.code
        union all
        select 'well', count(*), 'Wells' from entity where kind = 'well' having count(*) > 0
        order by 1
        """).fetchall()
    return [{"code": r["code"], "name": r["name"] or r["code"], "units": r["units"]} for r in rows]


def param(conn, key: str, cls: str | None = None):
    """A detector's parameter for one equipment class: the asset's value for that class, else its usual value."""
    return settings.get(conn, key, qualifier=cls)


def tune(conn, name: str, key: str, value, *, cls: str | None = None, by: str, reason: str | None = None,
         project_name: str | None = None, reset: bool = False) -> dict:
    """Set (or with `reset`, clear) one of a detector's parameters on one asset, for one equipment class or all of
    them; kept in the settings history. Changing a detector parameter is a fixed gate: a named person only."""
    from . import gates

    gates.require_person(conn, "limit_change", by, ref=f"detector:{name}:{key}")
    d = get(name)
    if key not in d.params:
        raise DetectorError(f"{name} is not tuned by {key}; its parameters are {', '.join(d.params) or 'none'}")
    if cls is not None:
        if key not in d.by_class:
            raise DetectorError(f"{name} reads {key} the same for every class, so it takes no class value")
        if not project_name:
            raise DetectorError("a value for one equipment class is set on one asset: pick a project")
        if cls not in {c["code"] for c in asset_classes(conn)}:
            raise DetectorError(f"no equipment class {cls!r} on this asset")
    full = f"{key}{settings.QUALIFIER}{cls}" if cls else key
    if reset:
        return settings.reset(conn, full, by=by, reason=reason, project_name=project_name)
    return settings.set(conn, full, value, by=by, reason=reason, project_name=project_name)


# ---- scores and listing ---------------------------------------------------------------------------

def replay_score(conn, d: Detector) -> dict | None:
    """The detector's latest replay score (recall, precision, false alarms a week, over which window), when it is
    scored by replay and has been."""
    if not (d.eval or "").startswith("replay:"):
        return None
    try:
        with conn.transaction():  # a database without the eval table yet
            r = conn.execute("select since, until, scores, run_at from ops_eval where detector = %s "
                             "order by id desc limit 1", (d.eval.partition(":")[2],)).fetchone()
    except Exception:
        return None
    if not r:
        return None
    s = r["scores"] or {}
    return {"recall": s.get("recall"), "precision": s.get("precision"),
            "false_alarms_per_week": s.get("false_alarms_per_week"), "upsets": s.get("upsets"),
            "since": r["since"], "until": r["until"], "at": r["run_at"].isoformat(timespec="seconds")}


def describe(d: Detector) -> dict:
    """A detector as the reference page and the Detection page read it."""
    return {"name": d.name, "title": d.title, "kind": d.kind, "kind_label": KINDS[d.kind], "cadence": d.cadence,
            "cadence_label": CADENCES[d.cadence], "raises": list(d.raises), "classes": list(d.classes),
            "roles": list(d.roles), "needs": list(d.needs), "params": list(d.params), "by_class": list(d.by_class),
            "eval": d.eval, "about": d.doc}


def _params(conn, d: Detector, project_name: str | None, rows: dict) -> list[dict]:
    out = []
    for k in d.params:
        r = rows[k]
        out.append({"key": k, "description": r["description"], "type": r["type"], "value": r["value"],
                    "default": r["default"], "from": r["from"], "updated_by": r["updated_by"],
                    "updated_at": r["updated_at"], **{x: r[x] for x in ("min", "max") if x in r},
                    "by_class": k in d.by_class,
                    "classes": [c for c in settings.qualified(conn, k, project_name) if c["scope"] == "project"]
                    if k in d.by_class else []})
    return out


def listing(conn, pack, project_name: str | None = None) -> list[dict]:
    """Every detector on this asset: what it is, whether it is on and applies here, its parameters with any class
    values, its last run and its replay score."""
    p = project_name or settings.project()
    runs = last_runs(conn)
    rows = {r["key"]: r for r in settings.listing(conn, project_name=p)}
    switched = {c["qualifier"]: c for c in settings.qualified(conn, ON, p)}
    out = []
    for d in registry().values():
        lacking = missing(conn, pack, d)
        out.append({**describe(d), "on": on(conn, d.name, p), "switched": switched.get(d.name),
                    "applicable": not lacking, "note": note(lacking) if lacking else None,
                    "last_run": runs.get(d.name), "score": replay_score(conn, d),
                    "settings": _params(conn, d, p, rows)})
    return out
