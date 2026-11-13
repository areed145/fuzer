"""Detectors tune from dispositions (P6.3, #944).

P6.3a, close-outs per detector and class (#945): each case is put down to the registered detector that raised it and
to the equipment class of what it is about, and its close-out (P6.1) counted there. A false alarm and a duplicate are
both noise: the detector raised work nobody needed. A real problem and a data problem are not counted against it.

    detector_of(finding_detector, work_type)   the registered detector behind a case, or None
    closeouts(conn)                            per detector: cases, how they closed, and the same per class

The detector behind a case is the finding's own (`sre.<name>`, or a registry name), else the one registered detector
that raises the case's work type. A work type several detectors raise, with a finding that names none of them, is
put down to no detector.
"""

from ..core import detectors

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
               r.subjects, d.disposition
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
