"""Separator and export quality drift (P3.1d, #342): a lab analyte at a sample point (oil in water off a separator,
BS&W in the export oil, water in the sales gas) whose latest samples have all moved off its own recent level. Read
from the lab results landed as measurements on equipment, by sample point and analyte, so nothing here knows a
field, a product or a specification.

- `drifts(conn, until)`: each series (what was sampled, where, which analyte) with at least `MIN_SAMPLES` results,
  checked against its own baseline (the up to `BASE` samples before the last `LAST`). It drifts when every one of
  the last `LAST` lies past the baseline's range on the same side, and their median is off the baseline's median by
  at least `MIN_SHIFT` of it and `SCATTER` times the baseline's own scatter. One odd sample is never a drift.
- No claim of on or off specification is made: export specifications and discharge limits are not loaded, so the
  finding says how far the analyte moved against itself, not against a limit.
- The `quality_drift` detector raises each one as `quality_drift` for the engineer, keyed by the sample point and
  analyte. It holds while the latest samples' median stays past the range of the samples before the drift began (so
  a step that lasts stays raised after it becomes the new normal), and clears when they come back within it.
"""

from datetime import datetime, timedelta
from statistics import median

from ..core import detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact

LAST = 3  # the latest samples that must all have moved
BASE = 12  # the samples before them that set the baseline
MIN_BASE = 4
MIN_SAMPLES = LAST + MIN_BASE
MIN_SHIFT = 0.10  # of the baseline median
SCATTER = 4.0  # times the baseline's median absolute deviation
STALE_DAYS = 120  # a series with no sample this long is no longer sampled, so not drifting
ONE_OFF = ("wall_thickness",)  # measurements on equipment that are inspection readings, not lab samples


def _series(conn, until: datetime) -> dict[tuple, list[dict]]:
    rows = conn.execute(
        """
        select m.fuzer_id as subject, coalesce(l.label, m.fuzer_id) as label, m.location, m.quantity, m.at, m.value,
               m.unit, m.qualifier, m.method
        from measurement m join entity e on e.fuzer_id = m.fuzer_id and e.kind <> 'well' and e.retired_at is null
        left join lateral (select label from entity_label where fuzer_id = m.fuzer_id order by label limit 1) l on true
        where m.at < %s and m.location is not null and coalesce(m.method, '') <> 'well test'
          and m.quantity <> all(%s)
        order by m.fuzer_id, m.location, m.quantity, m.at
        """, (until, list(ONE_OFF))).fetchall()
    out: dict[tuple, list[dict]] = {}
    for r in rows:
        out.setdefault((r["subject"], r["location"], r["quantity"]), []).append(r)
    return out


def _check(samples: list[dict], until: datetime) -> dict | None:
    if len(samples) < MIN_SAMPLES or (until - samples[-1]["at"]).days > STALE_DAYS:
        return None
    last, base = samples[-LAST:], samples[-LAST - BASE:-LAST]
    if any(s["qualifier"] for s in last) or len(base) < MIN_BASE:
        return None  # a result at a detection limit has no level to compare
    vals = [s["value"] for s in base]
    ref = median(vals)
    mad = median(abs(v - ref) for v in vals)
    now = median(s["value"] for s in last)
    shift = now - ref
    side = 1 if shift > 0 else -1
    past = all((s["value"] > max(vals)) if side > 0 else (s["value"] < min(vals)) for s in last)
    if not (past and ref and abs(shift) >= MIN_SHIFT * abs(ref) and abs(shift) >= SCATTER * mad):
        return None
    return {"direction": "up" if side > 0 else "down", "level": round(now, 3), "baseline": round(ref, 3),
            "change": round(shift / abs(ref), 3), "baseline_samples": len(base), "since": last[0]["at"],
            "last_sample": last[-1]["at"], "low": min(vals), "high": max(vals)}


def _persists(samples: list[dict], since: datetime, direction: str) -> bool:
    """Whether a drift that began at `since` still holds: the latest samples' median past the range of the samples
    before it, on the side it moved."""
    base = [s["value"] for s in samples if s["at"] < since][-BASE:]
    last = [s["value"] for s in samples[-LAST:] if s["at"] >= since]
    if len(base) < MIN_BASE or len(last) < LAST:
        return False
    now = median(last)
    return now > max(base) if direction == "up" else now < min(base)


def _end(conn, until: str | None) -> datetime:
    from ..graph import operations

    return datetime.fromisoformat(until) if until else datetime.fromisoformat(operations.as_of(conn)) + timedelta(days=1)


def key_of(r: dict) -> str:
    return f"{r['label']} · {r['sample_point']} · {r['analyte']}"


def drifts(conn, until: str | None = None, held: dict | None = None) -> list[dict]:
    """The sample point analytes whose latest samples have moved off their own baseline, the largest change first.
    `held` ({key: {"since", "direction"}}, the drifts already raised) are judged only against the samples before
    each began: kept, marked `held`, while the latest samples stay past that range, else left out (come back)."""
    end = _end(conn, until)
    out = []
    for (subject, location, quantity), samples in _series(conn, end).items():
        s = samples[-1]
        row = {"subject": subject, "label": s["label"], "sample_point": location, "analyte": quantity,
               "unit": s["unit"], "method": s["method"], "samples": len(samples)}
        if (h := (held or {}).get(key_of(row))) and h.get("since") and h.get("direction"):
            if _persists(samples, datetime.fromisoformat(h["since"]), h["direction"]):  # else back: cleared
                out.append({**row, "held": True, "direction": h["direction"], "last_sample": s["at"],
                            "since": datetime.fromisoformat(h["since"]),
                            "level": round(median(x["value"] for x in samples[-LAST:]), 3)})
        elif c := _check(samples, end):
            out.append({**row, **c})
    return sorted(out, key=lambda r: -abs(r.get("change", 0)))


def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("quality_drift") or exception_type(
        "quality_drift", "Product or water quality drift", role="role:engineer", audience=("ioc",), joins=False,
        description="A lab analyte at a sample point (oil in water, BS&W, water in gas) whose latest samples have all "
                    "moved off its own recent level. Measured against its own history, not a specification: no on "
                    "or off spec claim is made without a loaded limit. It clears when the samples come back.")


DRIFT_TYPE = _type().name


@detectors.register("quality_drift", title="A lab analyte at a sample point drifting off its own baseline",
                    kind=detectors.RULE, cadence="daily", raises=(DRIFT_TYPE,),
                    needs={"lab results at a sample point": "select 1 from measurement m join entity e on "
                                                            "e.fuzer_id = m.fuzer_id and e.kind <> 'well' where "
                                                            "m.location is not null and m.quantity <> "
                                                            "'wall_thickness' limit 1"},
                    eval="test:tests/analysis/test_quality_drift.py::test_a_drifting_analyte_is_raised_until_it_comes_back",
                    found=lambda out: len(out["raised"]))
def watch(conn, until: str | None = None, by: str = "agent:quality") -> dict:
    """Each drift raised as `quality_drift` for the engineer (keyed by subject, sample point and analyte), with the
    level against its baseline; open ones whose samples have come back are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    held = {r["key"]: {"since": r["since"], "direction": r["direction"]} for r in conn.execute(
        "select key, evidence->>'since' as since, evidence->>'direction' as direction from exception_record "
        "where type = %s and state = 'open'", (DRIFT_TYPE,))}
    found_rows = drifts(conn, until, held)
    raised, errors = [], []
    for r in found_rows:
        key = key_of(r)
        if r.get("held"):
            continue  # still past the samples before it began: left open as first raised
        since = r["since"].isoformat(timespec="minutes")
        unit = f" {r['unit']}" if r["unit"] else ""
        found = Finding(
            "quality.drift", DETERMINISTIC, method="Each sample point analyte's latest samples against its own baseline",
            start=since, end=r["last_sample"].isoformat(timespec="minutes"),
            data={"analyte": r["analyte"], "change": r["change"], "direction": r["direction"], "since": since},
            items=(fact("Sampled on", r["label"]), fact("Sample point", r["sample_point"]), fact("Analyte", r["analyte"]),
                   fact(f"Last {LAST} samples, median", f"{r['level']:g}{unit}"),
                   fact(f"The {r['baseline_samples']} samples before, median", f"{r['baseline']:g}{unit}"),
                   fact("Their range", f"{r['low']:g} to {r['high']:g}{unit}"),
                   fact("Change, % of the baseline", round(100 * r["change"], 1)),
                   at("Moving since", since), at("Latest sample", r["last_sample"].isoformat(timespec="minutes")),
                   fact("Method", r["method"]),
                   fact("Specification", "None loaded: this is a change against its own history, not an off "
                                         "specification claim.")))
        try:
            x = raise_exception(conn, DRIFT_TYPE, key, f"{r['analyte']} {r['direction']} {abs(100 * r['change']):.0f} % "
                                f"at {r['sample_point']} ({r['label']})", by, source="quality", refs=[r["subject"]],
                                priority=0.5, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{key}: {e}")
            continue
        raised.append({"key": key, "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
    cleared = []
    for key in set(held) - {key_of(r) for r in found_rows}:
        clear_exception(conn, DRIFT_TYPE, key, by, reason="the latest samples are back within the range before it")
        cleared.append(key)
    conn.commit()
    return {"drifts": len(found_rows), "held": sum(1 for r in found_rows if r.get("held")), "raised": raised,
            "cleared": cleared, "errors": errors}
