"""The sweep scorer for the work detectors (P3.6a, #844): each detector run day by day over a bundle's history, as a
schedule runs it, and its raises scored against the events the bundle injected for it, so a detector change that
starts missing what it should find, or finds what is not there, fails the check.

Each bundle declares, in `evals/detections.yaml`, per detector: the stretch to sweep (`since`, `until`, `every_days`;
dates may be relative, `today-30d`; or `at: [dates]`), the events it should raise (`events: [{key, from, to}]`: a
raise whose key holds `key`, made by a run between `from` and `to`), or `not_here: "<why>"` when it can't arise on the bundle's asset
type. `deliver: {path, datasets, incremental, rebuild}` lands the next delivery in the bundle's history first, for a
detector that reads what it brings; such entries come last in the file, since what they land stays.

- A raise is a new exception of one of the detector's own work types (or those its entry lists under `types`, for a
  detector that raises some work no injected event defines) (a key raised again after it was cleared counts
  again; one held open does not). It is true when it matches an event, a false alarm when it matches none.
- `recall` is the events raised over the events declared; `precision` the true raises over every raise; false alarms
  a week over the swept stretch.
- `store` keeps each detector's scores in `ops_eval` as `sweep:<detector>`, so a detector whose eval is
  `replay:sweep:<detector>` shows them on the Detection page; `gate` reports the scores under their floor.

The detector is called with the time arguments its signature takes: `since` and `until` (or `start` and `end`) as the
stretch since the previous run, else `until`, `at` (a date) or `now` alone.
"""

import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from ..core import clock

FILE = "detections.yaml"
# Work detectors scored some other way than a sweep of a bundle's history, and why
SCORED_ELSEWHERE = {
    "opportunities": "the ops eval recomputes each gas lift gain by hand (ops_eval.CEILINGS, worst_gain_error)",
    "watch": "an agent in shadow that raises nothing: its proposals are scored on replay against the injected events "
             "no rule caught (P3.3b)",
    "deviation_reviews": "a rule over the operating model a project adopts, not over plant data: no bundle's history "
                         "has a deviation past its review (tests/evals/test_conformance.py)",
}
GATES = {"recall": 1.0, "precision": 0.5}  # the floors a swept detector keeps unless its entry sets its own


def plan(bundle: Path) -> dict[str, dict]:
    """The bundle's declared detections, by detector, dates resolved."""
    from ..sources.relative_dates import resolve_all

    path = Path(bundle) / "evals" / FILE
    if not path.exists():
        return {}
    return resolve_all((yaml.safe_load(path.read_text()) or {}).get("detectors") or {})


def _when(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def times(entry: dict) -> list[datetime]:
    """The run times: every `every_days` from `since` to `until` (or the dates listed under `at`, for a detector too
    slow to run every week), and each event's own end, so a short event is never stepped over."""
    since, until = _when(entry["since"]), _when(entry["until"])
    if "at" in entry:
        out = {_when(t) for t in entry["at"]}
    else:
        step = timedelta(days=entry.get("every_days", 7))
        out, t = set(), since
        while t <= until:
            out.add(t)
            t += step
    out |= {_when(e["to"]) for e in entry.get("events", []) if since <= _when(e["to"]) <= until}
    return sorted(out)


def _args(fn, prev: datetime, t: datetime) -> dict:
    p = inspect.signature(fn).parameters
    if "since" in p and "until" in p:
        return {"since": prev.isoformat(timespec="minutes"), "until": t.isoformat(timespec="minutes")}
    if "start" in p and "end" in p:
        return {"start": prev.isoformat(timespec="minutes"), "end": t.isoformat(timespec="minutes")}
    if "until" in p:
        return {"until": t.isoformat(timespec="minutes")}
    if "at" in p:
        return {"at": t.date().isoformat()}
    if "now" in p:
        return {"now": clock.aware(t)}  # a date without a zone is the plant's wall clock
    return {}


def _new(conn, types: tuple, after: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "select id, type, key from exception_record where type = any(%s) and id > %s order by id",
        (list(types), after))]


def _last_id(conn) -> int:
    return conn.execute("select coalesce(max(id), 0) as n from exception_record").fetchone()["n"]


def _keys(e: dict) -> list[str]:
    """What an event's raise may be keyed by: one key, or several for one cause (a loop's transmitter, its controller's
    measurement and its output)."""
    return e["key"] if isinstance(e["key"], list) else [e["key"]]


def score(entry: dict, raises: list[dict]) -> dict:
    """Each raise ({key, at}) matched to the declared events; recall, precision and false alarms a week."""
    events = entry.get("events", [])
    found, true, false = set(), [], []
    for r in raises:
        hit = [i for i, e in enumerate(events) if any(k in r["key"] for k in _keys(e))
               and _when(e["from"]) <= r["at"] <= _when(e["to"])]
        found |= set(hit)
        (true if hit else false).append(r)
    weeks = max((_when(entry["until"]) - _when(entry["since"])).days / 7, 1 / 7)
    return {"events": len(events), "found": len(found), "raises": len(raises), "true": len(true),
            "false": len(false),
            "recall": round(len(found) / len(events), 3) if events else None,
            "precision": round(len(true) / len(raises), 3) if raises else None,
            "false_alarms_per_week": round(len(false) / weeks, 2),
            "missed": [e["key"] for i, e in enumerate(events) if i not in found],
            "false_alarms": [{"key": r["key"], "at": r["at"].isoformat(timespec="minutes")} for r in false[:20]]}


def land(conn, pack, bundle: Path, how: dict) -> None:
    """The next delivery in the bundle's history (`deliver: {path, datasets, incremental, rebuild}`), landed as the
    source's load takes it, for a detector that reads what it brings (a bypass register, a changed limit)."""
    from ..ops import pipeline
    from ..sources import ingest

    ingest.ingest(conn, Path(bundle) / how["path"], how.get("datasets") or None, snapshot=not how.get("incremental"),
                  trigger="sensor")
    conn.commit()
    if how.get("rebuild"):
        pipeline.rebuild(conn, pack)
        conn.commit()


def run_one(conn, pack, name: str, entry: dict, say=None, bundle: Path | None = None) -> dict:
    """Sweep one detector over its stretch and score it, first landing the delivery it names (from `bundle`)."""
    from ..core import detectors

    say = say or (lambda msg: None)
    d = detectors.get(name)
    if "not_here" in entry:
        return {"detector": name, "not_here": entry["not_here"]}
    after = _last_id(conn)  # a load runs some detectors itself (the source checks): what it raises counts at the first run
    if "deliver" in entry:
        land(conn, pack, bundle, entry["deliver"])
    ts = times(entry)
    raises, prev = [], _when(entry["since"]) - timedelta(days=entry.get("every_days", 7))
    for t in ts:
        out = detectors.run(conn, pack, name, **_args(d.fn, prev, t))
        if isinstance(out, dict) and out.get("status") in (detectors.OFF, detectors.NOT_APPLICABLE):
            return {"detector": name, "status": out["status"], "note": out.get("note")}
        conn.commit()
        new = _new(conn, tuple(entry.get("types") or d.raises), after)
        raises += [{**r, "at": t} for r in new]
        say(f"{name} {t:%Y-%m-%d}: {len(new)} raised")
        prev, after = t, _last_id(conn)
    s = score(entry, raises)
    return {"detector": name, "since": _when(entry["since"]).isoformat(timespec="minutes"),
            "until": _when(entry["until"]).isoformat(timespec="minutes"), "runs": len(ts), **s,
            "gates": {**GATES, **(entry.get("gates") or {})}}


def run(conn, pack, bundle: Path, only: list[str] | None = None, say=None) -> dict[str, dict]:
    """Sweep every detector the bundle declares (or `only` those)."""
    return {name: run_one(conn, pack, name, entry, say, bundle) for name, entry in plan(bundle).items()
            if not only or name in only}


def gate(res: dict) -> list[str]:
    """A swept detector's scores under their floors."""
    bad = []
    for k, floor in (res.get("gates") or {}).items():
        v = res.get(k)
        if v is not None and v < floor:
            bad.append(f"{res['detector']} {k} {v} < {floor}")
    return bad


def store(conn, res: dict) -> None:
    """Keep a detector's sweep scores, read back by the Detection page as `sweep:<detector>`."""
    if "recall" not in res and "precision" not in res:
        return
    conn.execute("insert into ops_eval (detector, since, until, scores) values (%s, %s, %s, %s)",
                 (f"sweep:{res['detector']}", res["since"], res["until"], json.dumps(res, default=str)))
    conn.commit()
