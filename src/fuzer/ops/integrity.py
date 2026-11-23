"""Work that the inspection source brings (P3.12, #1179): the integrity engineer's findings raised as routed work, with
no code on the asset.

Each item with wall thickness readings (a pipeline, or a vessel or piping tag) is read the way `analysis.integrity`
reads it, and the `inspection_findings` detector raises `inspection_due` for the engineer, keyed by the item, when

    retire_soon     the governing CML reaches its retirement thickness within `integrity.RETIRE_YEARS`
    overdue         the next inspection its wall supports (half the remaining life, at most 10 years) has passed
    plan_overdue    its condition inspection plan in the maintenance plans is past its next date
    due_sooner      its wall runs out of margin before the plan's next date

One case per item, holding every reason it has. The work clears when no reason holds any more: a later survey, or a
plan done or moved. Evidence only, with survey and plan dates; fitness for service stays with the engineer.
"""

from datetime import date, datetime, timedelta

from ..analysis import ili, integrity
from ..analysis.calcs import CalcError
from ..core import detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact

REASONS = {"retire_soon": "reaches retirement thickness soon", "overdue": "inspection overdue for its wall loss",
           "plan_overdue": "inspection plan overdue", "due_sooner": "wall runs out before the planned inspection"}


def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("inspection_due") or exception_type(
        "inspection_due", "Inspection or wall loss finding", role="role:engineer", audience=("ioc",), joins=False, need="obligation",
        description="A line or vessel whose wall is running out, or whose inspection is overdue for its wall loss or "
                    "its plan. Review the readings, plan the inspection or repair, and tell the IOC if the item "
                    "should be derated. It clears when a later survey or plan date takes the finding away.")


TYPE = _type().name


def _day(now) -> date | None:
    if now is None:
        return None
    if isinstance(now, str):
        now = datetime.fromisoformat(now)
    return now.date() if isinstance(now, datetime) else now


def findings(conn, now=None) -> list[dict]:
    """Every item with wall thickness readings that has at least one reason, as of `now` (else the asset's clock)."""
    from ..graph import operations

    day = _day(now) or date.fromisoformat(operations.as_of(conn)[:10])
    until = (day + timedelta(days=1)).isoformat()
    items = [r["fuzer_id"] for r in conn.execute(
        "select distinct m.fuzer_id from measurement m join entity e on e.fuzer_id = m.fuzer_id "
        "where m.quantity = 'wall_thickness' and m.at < %s and e.retired_at is null and e.merged_into is null "
        "order by 1", (until,))]
    out = []
    for fid in items:
        wall = integrity._line(conn, fid, until, None, day)
        try:
            plan = integrity.inspection_plan(conn, fid, until=until)
        except CalcError:
            plan = None
        why = []
        if wall and wall["remaining_years"] is not None and wall["remaining_years"] < integrity.RETIRE_YEARS:
            why.append("retire_soon")
        if wall and wall["overdue"]:
            why.append("overdue")
        if plan and plan["flag"] == "inspection_overdue":
            why.append("plan_overdue")
        elif plan and plan["flag"] == "due_sooner":
            why.append("due_sooner")
        if why:
            out.append({"id": fid, "item": (wall or {}).get("line") or plan["item"], "reasons": why, "wall": wall,
                        "plan": plan["plan"] if plan else None,
                        "plan_overdue_days": plan["overdue_days"] if plan else None, "at": day.isoformat()})
    return out


def _walls(conn, pack) -> bool:
    return conn.execute("select 1 from measurement where quantity = 'wall_thickness' limit 1").fetchone() is not None


@detectors.register("inspection_findings", title="A line or vessel running out of wall, or overdue for inspection",
                    kind=detectors.RULE, cadence="daily", raises=(TYPE,),
                    needs={"wall thickness readings": _walls},
                    eval="test:tests/ops/test_integrity_work.py::"
                         "test_an_item_running_out_of_wall_or_overdue_is_work_until_it_is_not",
                    found=lambda out: len(out["raised"]))
def watch_inspections(conn, by: str = "agent:integrity", now=None) -> dict:
    """Each item with wall thickness readings that reaches retirement within five years, is overdue for the
    inspection its wall loss supports, or whose condition inspection plan is overdue or too late for its wall, raised
    as `inspection_due` for the engineer (keyed by the item); open ones with no reason left are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (TYPE,))]
    found_now = findings(conn, now)
    raised, cleared, errors = [], [], []
    for f in found_now:
        w, p = f["wall"] or {}, f["plan"] or {}
        items = [fact("Item", f["item"]), fact("Findings", "; ".join(REASONS[r] for r in f["reasons"]))]
        if w:
            items += [fact("Wall loss rate", w["rate_mpy"], "mpy"), fact("Fastest CML", w["fastest_cml"]),
                      fact("Latest wall", w["latest_in"], "in"), at("Last survey", w["last_survey"])]
            if w["remaining_years"] is not None:
                items += [fact("Remaining life", w["remaining_years"], "years"),
                          fact("Retirement thickness", w["retirement_in"], "in"),
                          fact("Retirement basis", w["retirement_basis"])]
            if w["next_inspection_due"]:
                items.append(at("Next inspection its wall supports", w["next_inspection_due"]))
        if p:
            items += [fact("Inspection plan", f"{p['plan']}: {p['task']}")]
            items += [at("Plan next due", p["next_due"])] if p["next_due"] else []
            items += [fact("Plan overdue", f["plan_overdue_days"], "days")] if f["plan_overdue_days"] else []
        found = Finding(
            "integrity.inspection_due", DETERMINISTIC,
            method="Wall thickness readings per CML, the greater of the long and short term loss rate (API 570 7.1), "
                   "remaining life to the retirement thickness, and the condition inspection plan in the maintenance "
                   "plans (API 510 / API 570 intervals)",
            start=f["at"], data={"item": f["item"], "reasons": f["reasons"],
                                 "remaining_years": w.get("remaining_years"), "rate_mpy": w.get("rate_mpy")},
            items=tuple(items))
        bits = []
        if "retire_soon" in f["reasons"]:
            bits.append(f"{w['remaining_years']:g} years of wall left at {w['rate_mpy']:g} mpy")
        if "overdue" in f["reasons"]:
            bits.append(f"inspection due {w['next_inspection_due']} for its wall loss")
        if "plan_overdue" in f["reasons"]:
            bits.append(f"inspection plan {f['plan_overdue_days']} days overdue")
        if "due_sooner" in f["reasons"]:
            bits.append(f"wall runs out before the planned inspection on {p['next_due']}")
        title = f"{f['item']}: " + "; ".join(bits)
        priority = 0.8 if "retire_soon" in f["reasons"] else 0.6
        refs = [f["id"], *((p.get("id"),) if p else ())]
        try:
            x = raise_exception(conn, TYPE, f["item"], title, by, source="inspection", refs=refs, priority=priority,
                                finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{f['item']}: {e}")
            continue
        raised.append({"item": f["item"], "reasons": f["reasons"], "exception_id": x["id"], "item_id": x["item_id"],
                       "new": x["raised"]})
    for key in set(held) - {f["item"] for f in found_now}:
        clear_exception(conn, TYPE, key, by, reason="a later survey or plan date leaves no finding")
        cleared.append(key)
    conn.commit()
    return {"findings": len(found_now), "raised": raised, "cleared": cleared, "errors": errors}


# ---- in-line inspection anomalies (P3.12b) -----------------------------------------------------------------------

def _ili_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("ili_anomaly") or exception_type(
        "ili_anomaly", "Pig run anomaly over the screen", role="role:engineer", audience=("ioc",), joins=False,
        description="A metal loss anomaly from an in-line inspection run that crosses the strength or depth screen "
                    "now, or will at its growth rate within the horizon (modified ASME B31G). Assess fitness for "
                    "service, plan a dig or repair, and tell the IOC if the line's operating pressure must come down. "
                    "It clears when a later run no longer flags it.")


ILI_TYPE = _ili_type().name
FLAGGED = ("exceeds_now", f"exceeds_within_{int(ili.HORIZON_YEARS)}_years")


def _features(conn, pack) -> bool:
    return conn.execute("select 1 from edge where rel = 'found_on' and valid_to is null limit 1").fetchone() is not None


@detectors.register("ili_anomalies", title="A pig run anomaly that crosses the strength or depth screen",
                    kind=detectors.RULE, cadence="daily", raises=(ILI_TYPE,),
                    needs={"in-line inspection features": _features},
                    eval="test:tests/evals/test_ili_harrier.py::test_an_ili_anomaly_over_the_screen_is_work",
                    found=lambda out: len(out["raised"]))
def watch_ili(conn, by: str = "agent:integrity") -> dict:
    """Each metal loss anomaly of a line's latest in-line inspection run that crosses the modified B31G screen now
    or within the horizon at its growth rate, raised as `ili_anomaly` for the engineer (keyed by line and feature);
    open ones a later run no longer flags are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (ILI_TYPE,))]
    out = ili.anomalies(conn)
    flagged = [(x, a) for x in out["lines"] for a in x["anomalies"] if a["screen"] in FLAGGED]
    raised, cleared, errors, keys = [], [], [], set()
    for line, a in flagged:
        key = f"{line['line']} {a['feature']}"
        keys.add(key)
        now = a["screen"] == "exceeds_now"
        items = [fact("Line", line["line"]), fact("Anomaly", a["feature"]), fact("Segment", a["segment"]),
                 fact("Station", a["station_ft"], "ft"), fact("Depth", a["depth_pct"], "% of wall"),
                 fact("Length", a["length_in"], "in"), fact("Latest run", line["runs"][-1]["run"]),
                 at("Run date", line["runs"][-1]["date"]),
                 fact("MOP", line["mop_psig"], "psig"), fact("Safe pressure at the anomaly", a.get("safe_psig"), "psig"),
                 fact("ERF (MOP over safe pressure)", a.get("erf"))]
        if a.get("rate_pct_per_year") is not None:
            items += [fact("Growth since the run before", a["growth_pct_wall"], "% of wall"),
                      fact("Growth rate", a["rate_pct_per_year"], "% of wall a year"),
                      fact(f"Depth in {ili.HORIZON_YEARS:g} years", a["projected_depth_pct"], "% of wall"),
                      fact(f"ERF in {ili.HORIZON_YEARS:g} years", a.get("projected_erf"))]
            if a.get("within_tool_tolerance"):
                items.append(fact("Note", "the growth is within the tool's sizing tolerance"))
        found = Finding("integrity.ili_anomaly", DETERMINISTIC, method=out["method"],
                        start=line["runs"][-1]["date"], data={"line": line["line"], "feature": a["feature"],
                                                              "screen": a["screen"], "erf": a.get("erf")},
                        items=tuple(items))
        title = (f"{line['line']} {a['feature']} at {a['station_ft']:,.0f} ft: "
                 + (f"over the screen now ({a['depth_pct']:g}% of wall)" if now else
                    f"{a['depth_pct']:g}% of wall, over the screen within {ili.HORIZON_YEARS:g} years at its growth"))
        try:
            x = raise_exception(conn, ILI_TYPE, key, title, by, source="inspection", refs=[a["id"], line["id"]],
                                priority=0.9 if now else 0.7, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{key}: {e}")
            continue
        raised.append({"anomaly": key, "screen": a["screen"], "exception_id": x["id"], "item_id": x["item_id"],
                       "new": x["raised"]})
    for key in set(held) - keys:
        clear_exception(conn, ILI_TYPE, key, by, reason="the latest run no longer flags it")
        cleared.append(key)
    conn.commit()
    return {"flagged": len(flagged), "raised": raised, "cleared": cleared, "errors": errors}
