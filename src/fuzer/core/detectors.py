"""The detector registry (P1.2): everything that looks for trouble in the data, declared once where it is written.

    @detectors.register("drifts", title="A slow climb in what wears and fouls", kind=detectors.STATISTICAL,
                        roles=("vibration", "bearing temperature"), needs={"historian points": POINTS},
                        cadence="daily", raises=("upset",), params=("sre.drift_rise",), eval="replay:sre:drifts",
                        window=True)
    def drifts(conn, pack, since, until, src): ...

A detector says what kind it is (a rule, a statistic, a model against a curve, an agent), the equipment classes and
measurement roles it applies to, what it needs before it can run (`needs`: a name a person reads, and a query that
returns a row when it is there, or a function of `(conn, pack)`), how often it runs, the work type it raises, the
settings that tune it and where it is scored (`replay:<name>` in the replay harness, `test:<node>` on the fake plant).

`run` is how a schedule calls one: a detector whose needs are missing reports "not applicable: missing X" and does
not run, and every run is kept in `detector_run`, so the Detection page and docs/detectors.md read from one place.
Adding a detector is one registered function and its test.
"""

import importlib
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field

RULE, STATISTICAL, MODEL, AGENTIC = "rule", "statistical", "model", "agentic"
KINDS = {RULE: "Rule", STATISTICAL: "Statistical", MODEL: "Model", AGENTIC: "Agentic"}
CADENCES = {"30min": "Every 30 minutes", "6h": "Every 6 hours", "daily": "Once a day", "request": "On request"}
RAN, NOT_APPLICABLE, FAILED = "ran", "not_applicable", "failed"

# Where detectors are written. A module here is imported when the registry is first read.
MODULES = ("fuzer.ops.sre", "fuzer.analysis.limits", "fuzer.analysis.trips", "fuzer.analysis.rotating",
           "fuzer.analysis.records", "fuzer.sources.alerts", "fuzer.ops.opportunities")

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
    roles: tuple = ()              # the measurement roles it reads
    needs: dict = field(default_factory=dict)  # name -> query returning a row when present, or fn(conn, pack)
    params: tuple = ()             # the settings that tune it
    eval: str | None = None        # replay:<detector> or test:<node>
    window: bool = False           # a window function the SRE scan and the replay harness call
    found: Callable | None = None  # how many findings a run's result holds

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
    """Every detector, in the order declared."""
    global _loaded
    if not _loaded:
        for m in MODULES:
            importlib.import_module(m)
        _loaded = True
    return _registry


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


def run(conn, pack, name: str, **kw):
    """Run one detector as a schedule does: its own result, or {"status": "not_applicable", "note": "not applicable:
    missing X"} when the asset lacks what it needs. Either way the run is kept."""
    d = get(name)
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


def describe(d: Detector) -> dict:
    """A detector as the reference page and the Detection page read it."""
    return {"name": d.name, "title": d.title, "kind": d.kind, "kind_label": KINDS[d.kind], "cadence": d.cadence,
            "cadence_label": CADENCES[d.cadence], "raises": list(d.raises), "classes": list(d.classes),
            "roles": list(d.roles), "needs": list(d.needs), "params": list(d.params), "eval": d.eval, "about": d.doc}


def listing(conn, pack) -> list[dict]:
    """Every detector on this asset: what it is, whether it applies here and its last run."""
    runs = last_runs(conn)
    out = []
    for d in registry().values():
        lacking = missing(conn, pack, d)
        out.append({**describe(d), "applicable": not lacking, "note": note(lacking) if lacking else None,
                    "last_run": runs.get(d.name)})
    return out
