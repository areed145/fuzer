"""Detectors tune from dispositions (P6.3, #944).

P6.3a, close-outs per detector and class (#945): each case is put down to the registered detector that raised it and
to the equipment class of what it is about, and its close-out (P6.1) counted there. A false alarm and a duplicate are
both noise: the detector raised work nobody needed. A real problem and a data problem are not counted against it.

    detector_of(finding_detector, work_type)   the registered detector behind a case, or None
    closeouts(conn)                            per detector: cases, how they closed, and the same per class

P6.3b, a tuning proposal when the noise rate passes its threshold: `propose` looks at each window detector that says
which way its parameters turn it quieter (`quieter` in the registry). Where its cases closed out on one equipment class
(or on the whole asset, for a parameter read the same for every class) reach `tuning.min_cases` and their noise rate
reaches `tuning.noise_rate`, it replays the detector over the windows those cases came from, at its current value and
at quieter values in turn, held in this process only (settings.Overrides), never written. It proposes the smallest
change that stops raising at least one of the noisy cases and, if it can, still raises every real one. The proposal
is `detector_tuning` work for the engineer: the change, the replay before and after on those cases, and the real
cases the change would have missed. One open proposal per detector, parameter and class.

    propose(conn, pack, src=None)   the proposals raised (or already open)

P6.3c, deciding it: `accept` sets the value through `detectors.tune`, the fixed limit-change gate, so only a named
person can; it lands in the settings history with the case as its reason, the detector's next version
(`detectors.version`). `set_aside` leaves the detector as it is, with a reason. Either closes the case.

The detector behind a case is the finding's own (`sre.<name>`, or a registry name), else the one registered detector
that raises the case's work type. A work type several detectors raise, with a finding that names none of them, is
put down to no detector.
"""

from datetime import datetime, timedelta

from ..core import detectors, settings
from ..core.exceptions import TYPES, exception_type, raise_exception
from ..core.findings import DETERMINISTIC, Finding, fact

NOISE = ("false_alarm", "duplicate")
COUNTED = ("real", "false_alarm", "duplicate", "data_problem")
NO_CLASS = ""  # what a case about nothing classified is counted under


def detector_of(finding: str | None, work_type: str | None, registry: dict | None = None) -> str | None:
    reg = registry if registry is not None else detectors.registry()
    if finding:
        name = finding.removeprefix("sre.")
        if name in reg:
            return name
    raisers = [n for n, d in reg.items() if work_type in d.raises]
    return raisers[0] if len(raisers) == 1 else None


def tally(rows: list[str | None]) -> dict:
    """Dispositions (None while open or not closed out) as counts and rates. The noise rate is false alarms and
    duplicates over the cases closed out."""
    out = {"cases": len(rows), **{k: sum(r == k for r in rows) for k in COUNTED}}
    closed = sum(out[k] for k in COUNTED)
    noise = out["false_alarm"] + out["duplicate"]
    return {**out, "closed": closed, "noise": noise,
            "confirmed_rate": out["real"] / closed if closed else None,
            "false_alarm_rate": out["false_alarm"] / closed if closed else None,
            "noise_rate": noise / closed if closed else None}


def cases(conn) -> list[dict]:
    """Every case an exception opened, once (its first exception: one joined later shares the case), with the
    registered detector behind it, its equipment class and its disposition."""
    rows = conn.execute(
        """
        select distinct on (r.item_id) r.item_id, r.type, r.evidence -> 'finding' ->> 'detector' as finding,
               r.evidence -> 'finding' ->> 'start' as started, r.subjects, d.disposition
        from exception_record r left join case_disposition d on d.item_id = r.item_id
        where r.item_id is not null
        order by r.item_id, r.id
        """).fetchall()
    reg = detectors.registry()
    out = [{**r, "detector": detector_of(r["finding"], r["type"], reg)} for r in rows]
    out = [r for r in out if r["detector"]]
    cls = detectors.classes_of(conn, {s for r in out for s in r["subjects"] or ()})
    for r in out:
        r["class"] = next((cls[s] for s in r["subjects"] or () if s in cls), NO_CLASS)
    return out


def closeouts(conn) -> dict[str, dict]:
    """Per registered detector: how its cases closed out, and the same for each equipment class (`by_class`)."""
    by: dict[str, dict[str, list]] = {}
    for r in cases(conn):
        by.setdefault(r["detector"], {}).setdefault(r["class"], []).append(r["disposition"])
    return {name: {**tally([x for v in per.values() for x in v]),
                   "by_class": [{"class": c, **tally(v)} for c, v in sorted(per.items())]}
            for name, per in by.items()}


def empty() -> dict:
    return {**tally([]), "by_class": []}


# ---- P6.3b: tuning proposals ------------------------------------------------------------------------

STEPS = (1.25, 1.5, 2.0)  # each value tried: the current one times (or over) these, within the setting's limits
WINDOW = timedelta(hours=6)  # a case is replayed over the scan window it was raised in
MATCH = timedelta(minutes=30)  # a replayed detection this close to the case's start, on its point, raises it again


def _type():
    return TYPES.get("detector_tuning") or exception_type(
        "detector_tuning", "Detector tuning to review", role="role:engineer", audience=("ioc",), joins=False,
        description="A detector whose cases on one equipment class keep closing as false alarms or duplicates, with "
                    "a quieter value for one of its parameters, replayed on those cases before and after, and the "
                    "real cases the change would have missed. Accept it to tune the detector, or set it aside.")


TUNING = _type().name


def key_of(detector: str, param: str, cls: str | None) -> str:
    return f"{detector}:{param}@{cls or 'all'}"


def values(param: str, current: float, way: str) -> list[float]:
    """The quieter values to try, smallest change first, within the setting's limits, then its usual value when that
    is quieter still."""
    s = settings._setting(param)
    out = []
    for f in STEPS:
        v = current * f if way == "up" else current / f
        v = min(v, s.max) if s.max is not None else v
        v = max(v, s.min) if s.min is not None else v
        v = round(v) if s.type == "int" else round(v, 3)
        if v != current and v not in out:
            out.append(v)
    quieter = s.default > current if way == "up" else s.default < current
    if quieter and s.default not in out and (not out or abs(s.default - current) > abs(out[-1] - current)):
        out.append(s.default)  # its usual value, last: a detector turned well below it may only settle there
    return out


def _start(at: str | None) -> datetime | None:
    return datetime.fromisoformat(at).replace(tzinfo=None) if at else None


def _window(at: datetime) -> tuple[datetime, datetime]:
    a = at.replace(minute=0, second=0, microsecond=0)
    a -= timedelta(hours=a.hour % 6)
    return a, a + WINDOW


def replay(conn, pack, d: detectors.Detector, rows: list[dict], src, trial: dict | None = None) -> set[int]:
    """The cases (of `rows`) the detector raises again when replayed over their windows, with the `trial` values."""
    windows: dict[tuple, list[dict]] = {}
    for r in rows:
        windows.setdefault(_window(r["start"]), []).append(r)
    again: set[int] = set()
    with settings.Overrides(trial or {}):
        for (a, b), cases_in in windows.items():
            found = d.fn(conn, pack, a, b, src)
            for r in cases_in:
                if any(f.get("point_id") in r["subjects"] and abs(f["start"] - r["start"]) <= MATCH for f in found):
                    again.add(r["item_id"])
    return again


def _score(rows: list[dict], raised: set[int]) -> dict:
    return {"noise": sum(r["item_id"] in raised for r in rows if r["disposition"] in NOISE),
            "real": sum(r["item_id"] in raised for r in rows if r["disposition"] == "real")}


def _open(conn) -> set[str]:
    return {r["key"] for r in conn.execute("select key from exception_record where type = %s and state = 'open'",
                                            (TUNING,))}


def proposal(conn, pack, d: detectors.Detector, param: str, cls: str | None, rows: list[dict], src) -> dict | None:
    """Try quieter values of one parameter on these closed cases; the change to propose, or None if none helps."""
    current = detectors.param(conn, param, cls)
    full = f"{param}{settings.QUALIFIER}{cls}" if cls else param
    rows = sorted((r for r in rows if r["start"]), key=lambda r: r["start"], reverse=True)
    keep, seen = [], set()
    for r in rows:  # the newest cases, within the replay's cap on windows
        w = _window(r["start"])
        if w not in seen and len(seen) >= settings.get(conn, "tuning.max_windows"):
            continue
        seen.add(w)
        keep.append(r)
    before = replay(conn, pack, d, keep, src)
    was = _score(keep, before)
    best = None
    for v in values(param, current, d.quieter[param]):
        after = replay(conn, pack, d, keep, src, {full: v})
        now = _score(keep, after)
        if now["noise"] >= was["noise"]:
            continue  # quieter on paper, but raises every noisy case it did
        missed = [r["item_id"] for r in keep if r["disposition"] == "real" and r["item_id"] in before - after]
        best = {"value": v, "after": now, "missed": missed}
        if not missed:
            break  # the smallest change that loses no real case
    if not best:
        return None
    return {"detector": d.name, "param": param, "class": cls, "current": current, "proposed": best["value"],
            "cases": len(keep), "windows": len(seen), "before": was, "after": best["after"], "missed": best["missed"],
            "noise_rate": tally([r["disposition"] for r in rows])["noise_rate"]}


def _raise(conn, p: dict) -> dict:
    where = f" on {p['class']}" if p["class"] else ""
    title = f"Tune {p['detector']}{where}: {p['param']} {p['current']} to {p['proposed']}"
    found = Finding(
        "tuning.proposal", DETERMINISTIC, method="Close-outs per detector and class, replayed at a quieter value",
        data=p, items=(fact("Detector", p["detector"]), fact("Equipment class", p["class"] or "All classes"),
                       fact("Parameter", p["param"]), fact("Now", p["current"]), fact("Proposed", p["proposed"]),
                       fact("Noise rate", round(p["noise_rate"] * 100), "%"),
                       fact("Noisy cases raised, before", p["before"]["noise"]),
                       fact("Noisy cases raised, after", p["after"]["noise"]),
                       fact("Real cases raised, before", p["before"]["real"]),
                       fact("Real cases raised, after", p["after"]["real"]),
                       fact("Real cases it would miss", ", ".join(map(str, p["missed"])) or "none")))
    return raise_exception(conn, TUNING, key_of(p["detector"], p["param"], p["class"]), title, "fuzer",
                           source="tuning", refs=[], priority=0.5, finding=found)


def propose(conn, pack, src=None) -> list[dict]:
    """Raise a tuning proposal for each detector and class whose noise rate passed its threshold (see the notes)."""
    from ..sources.timeseries import provider

    rate, least = settings.get(conn, "tuning.noise_rate"), settings.get(conn, "tuning.min_cases")
    rows = [{**r, "start": _start(r["started"])} for r in cases(conn) if r["disposition"] in COUNTED]
    held, out = _open(conn), []
    for d in detectors.window():
        if not d.quieter or not detectors.on(conn, d.name):
            continue
        mine = [r for r in rows if r["detector"] == d.name]
        for param in d.quieter:
            groups = {}
            for r in mine:
                groups.setdefault((r["class"] or None) if param in d.by_class else None, []).append(r)
            for cls, group in groups.items():
                t = tally([r["disposition"] for r in group])
                if t["closed"] < least or (t["noise_rate"] or 0) < rate or key_of(d.name, param, cls) in held:
                    continue
                p = proposal(conn, pack, d, param, cls, group, src or provider())
                if p:
                    out.append({**p, "exception": _raise(conn, p)})
                    held.add(key_of(d.name, param, cls))
            if any(o["detector"] == d.name for o in out):
                break  # one parameter at a time: the next is tried once this one is decided
    return out


# ---- P6.3c: deciding a proposal ---------------------------------------------------------------------

def _proposal(conn, exception_id: int) -> dict:
    r = conn.execute("select id, key, item_id, state, evidence from exception_record where id = %s and type = %s",
                     (exception_id, TUNING)).fetchone()
    if not r:
        raise LookupError(f"no tuning proposal {exception_id}")
    if r["state"] != "open":
        raise ValueError(f"tuning proposal {exception_id} is already decided")
    return {**r, "p": {k: v for k, v in r["evidence"].items() if k != "finding"}}


def accept(conn, exception_id: int, by: str, note: str | None = None, project_name: str | None = None) -> dict:
    """Tune the detector as proposed: set through `detectors.tune`, the fixed limit-change gate, so only a named
    person can, and the change is the detector's next version in the settings history, with the case as its reason.
    The proposal's case closes."""
    r = _proposal(conn, exception_id)
    p = r["p"]
    why = f"Tuning proposal, case {r['item_id']}: noise {round(p['noise_rate'] * 100)}% on {p['cases']} closed cases, " \
          f"{p['before']['noise']} to {p['after']['noise']} noisy cases raised on replay" + (f". {note}" if note else "")
    changed = detectors.tune(conn, p["detector"], p["param"], p["proposed"], cls=p["class"], by=by, reason=why,
                             project_name=project_name or settings.project())
    clear(conn, r["key"], by, f"Accepted: {p['param']} set to {p['proposed']}" + (f". {note}" if note else ""))
    return {**changed, "version": detectors.version(conn, p["detector"], project_name)}


def set_aside(conn, exception_id: int, by: str, reason: str) -> dict:
    """Leave the detector as it is; the case closes with the reason. The proposal can come again once more cases close."""
    if len((reason or "").strip()) < 3:
        raise ValueError("say why the proposal is set aside")
    r = _proposal(conn, exception_id)
    clear(conn, r["key"], by, f"Set aside: {reason.strip()}")
    return {"id": exception_id, "set_aside": True}


def clear(conn, key: str, by: str, reason: str) -> None:
    from ..core.exceptions import clear_exception

    clear_exception(conn, TUNING, key, by, reason=reason)
