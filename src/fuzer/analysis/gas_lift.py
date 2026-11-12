"""Gas lift instability (P3.1c, #341): a gas-lifted well heading, its casing pressure surging and falling back as lift
gas slugs through the valve, which wastes lift gas and oil. Read from each well's casing pressure (and, alongside, its
wellhead pressure) by measurement role, so nothing here knows a field, a well or a valve design.

- `unstable(conn, until, days)`: each well with a casing pressure point whose casing pressure cycles regularly over
  the window (the same regularity test as the control loop screen, `loops.cycling`) with a swing of at least
  `MIN_SWING` of its level. The wellhead pressure's own cycling is reported beside it when the well has one.
- The `gas_lift_instability` detector raises each one as `gas_lift_instability` for the production engineer, and
  clears it when the casing settles. It flags candidates for review; no lift gas or choke change is proposed.
"""

from datetime import datetime

from ..core import detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact
from . import loops
from .calcs import read, window_of

MIN_SWING = 0.02  # the casing pressure's half swing, as a fraction of its level
DAYS = 3


def _pairs(conn) -> list[dict]:
    return conn.execute(
        """
        select c.asset as well, coalesce(l.label, c.asset) as label, c.point_id as casing,
               (select w.point_id from measurement_role w where w.asset = c.asset and w.role = 'wellhead_pressure'
                order by w.confidence desc, w.point_id limit 1) as wellhead
        from measurement_role c join entity e on e.fuzer_id = c.asset and e.kind = 'well' and e.retired_at is null
        left join lateral (select label from entity_label where fuzer_id = c.asset order by label limit 1) l on true
        where c.role = 'casing_pressure'
        order by 2
        """).fetchall()


def unstable(conn, until: str | None = None, days: int = DAYS, src=None) -> list[dict]:
    """The gas-lifted wells whose casing pressure heads over the window, the largest swing first."""
    from ..sources import timeseries

    pairs = _pairs(conn)
    ids = {pid for r in pairs for pid in (r["casing"], r["wellhead"]) if pid}
    pts = {pid: p for pid in sorted(ids) if (p := timeseries._point(conn, pid))}
    if not pts:
        return []
    src = src or timeseries.provider()
    start, end = window_of(conn, days, until)
    vals = read(pts, start, end, loops.STEP_MINUTES, src)
    out = []
    for r in pairs:
        if r["casing"] not in pts:
            continue
        series = [v for _, v in sorted(vals[r["casing"]].items())]
        c = loops.cycling(series, loops.STEP_MINUTES)
        level = sorted(x for x in series if x is not None)[len(series) // 2] if series else None
        if not (c and loops._cycles(c) and level and c["amplitude"] / abs(level) >= MIN_SWING):
            continue
        w = loops.cycling([v for _, v in sorted(vals[r["wellhead"]].items())], loops.STEP_MINUTES) \
            if r["wellhead"] in pts else None
        out.append({"well": r["well"], "label": r["label"], "casing_point": pts[r["casing"]]["point"],
                    "casing_point_id": r["casing"], "uom": pts[r["casing"]].get("uom"), "level": round(level, 2),
                    "swing": round(c["amplitude"] / abs(level), 3), "provider": getattr(src, "name", None),
                    "wellhead_cycles": bool(w and loops._cycles(w)),
                    "window": [start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")], **c})
    return sorted(out, key=lambda r: -r["swing"])


def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("gas_lift_instability") or exception_type(
        "gas_lift_instability", "Gas lift heading", role="role:production_engineer", audience=("ioc",), joins=False,
        description="A gas-lifted well whose casing pressure surges and falls back regularly, as a well heading on "
                    "lift does. A candidate for the production engineer to review, not a diagnosis; no lift gas or "
                    "choke change is proposed. It clears when the casing settles.")


INSTABILITY_TYPE = _type().name


@detectors.register("gas_lift_instability", title="A gas-lifted well heading: its casing pressure surging regularly",
                    kind=detectors.STATISTICAL, cadence="daily", raises=(INSTABILITY_TYPE,), classes=("well",),
                    roles=("casing_pressure", "wellhead_pressure"),
                    needs={"casing pressure on a well": "select 1 from measurement_role where role = 'casing_pressure' "
                                                       "limit 1"},
                    eval="test:tests/analysis/test_gas_lift.py::test_a_heading_well_is_raised_until_its_casing_settles",
                    found=lambda out: len(out["raised"]))
def watch(conn, until: str | None = None, days: int = DAYS, by: str = "agent:gas_lift", src=None) -> dict:
    """Each heading well raised as `gas_lift_instability` for the production engineer (keyed by the well), with the
    casing pressure's period and swing; open ones whose casing has settled are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (INSTABILITY_TYPE,))]
    found_rows = unstable(conn, until, days, src)
    raised, errors = [], []
    for r in found_rows:
        period = f"{r['period_min']:g} minutes"
        found = Finding(
            "gas_lift.instability", DETERMINISTIC, method="loop_oscillation", start=r["window"][0], end=r["window"][1],
            data={"r": r["r"], "period_min": r["period_min"], "swing": r["swing"]},
            items=(fact("Well", r["label"]), fact("Casing pressure", r["casing_point"]), fact("Heads every", period),
                   fact("Swing, % of its level", round(100 * r["swing"], 1)),
                   fact("Casing pressure level", f"{r['level']:g} {r['uom'] or ''}".strip()),
                   fact("Wellhead pressure cycles with it", r["wellhead_cycles"]),
                   at("From", r["window"][0]), at("To", r["window"][1]),
                   fact("What this is", "A candidate for review, not a diagnosis. No lift gas or choke change is "
                                        "proposed."),
                   fact("Data", "synthetic values from the fake plant, not plant data"
                        if r["provider"] == "synthetic" else None)))
        try:
            x = raise_exception(conn, INSTABILITY_TYPE, r["label"], f"{r['label']} heading on gas lift: casing "
                                f"pressure swings {100 * r['swing']:.0f} % every {period}", by, source="gas_lift",
                                refs=[r["well"]], priority=0.5, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{r['label']}: {e}")
            continue
        raised.append({"well": r["label"], "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
    cleared = []
    for key in set(held) - {r["label"] for r in found_rows}:
        clear_exception(conn, INSTABILITY_TYPE, key, by, reason="the casing pressure has settled")
        cleared.append(key)
    conn.commit()
    return {"at": until or datetime.now().isoformat(timespec="minutes"), "wells": len(found_rows), "raised": raised,
            "cleared": cleared, "errors": errors}
