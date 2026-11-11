"""Integrity rollups (#515): what an inspection engineer works out from the CML readings Fuzer already holds.

- `corrosion_circuit(ref)`: a pipeline's corrosion circuit (the in-service lines in the same service, API 570's
  grouping by like material, service and damage mechanism) with each line's corrosion rate, remaining life and
  next inspection date. The rate per CML is the greater of the long-term rate (first to last reading) and the
  short-term rate (the last two readings), as API 570 7.1 asks, so a rate that sped up recently is not averaged
  away. Remaining life runs to the retirement thickness: the one passed in, else the pressure design thickness
  from the line's MOP and size (Barlow, ASME B31.4 / B31.8) on an assumed grade the result names. The next
  inspection is at half the remaining life, at most 10 years, after the last survey. Flags `retire_soon` (a line
  reaches retirement within 5 years), `inspection_overdue` (a line's next inspection date has passed) and
  `localised` (a line corroding more than twice the circuit's median: a changed condition, not the circuit).

A vessel or piping tag has no circuit on record, so its circuit is itself, and the result says so.
"""

from datetime import date, datetime

from .calcs import CalcError, _label, _r, result

YEAR = 365.25
# Pressure design thickness (Barlow): t = P D / (2 S F). The grade isn't in the register, so it is assumed and named.
SMYS_MPA = 359.0  # API 5L X52
DESIGN_FACTOR = 0.72  # ASME B31.4 / B31.8 class 1
OD_MM = {2: 60.3, 3: 88.9, 4: 114.3, 6: 168.3, 8: 219.1, 10: 273.1, 12: 323.9, 14: 355.6, 16: 406.4, 18: 457.0,
         20: 508.0, 24: 610.0}
RETIRE_YEARS = 5.0
MAX_INTERVAL = 10.0  # years, API 570


def _fields(conn, fid: str) -> dict:
    rows = conn.execute("select field, value #>> '{}' as v from golden where fuzer_id = %s", (fid,)).fetchall()
    return {r["field"]: r["v"] for r in rows}


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def design_thickness(mop_barg: float | None, diameter_in: float | None) -> float | None:
    """Barlow pressure design thickness in mm at the MOP, on the assumed grade and design factor."""
    if not mop_barg or not diameter_in:
        return None
    od = OD_MM.get(round(diameter_in), diameter_in * 25.4)
    return mop_barg / 10 * od / (2 * SMYS_MPA * DESIGN_FACTOR)


def cml_rate(values: list[dict]) -> dict | None:
    """One CML's rates (mm/year, positive = loss): long-term first to last, short-term over the last two readings,
    and the governing one (the greater)."""
    pts = [(datetime.fromisoformat(v["at"][:19]), float(v["value"])) for v in values]
    if len(pts) < 2:
        return None

    def rate(a, b):
        yrs = (b[0] - a[0]).days / YEAR
        return (a[1] - b[1]) / yrs if yrs >= 0.08 else None

    lt, st = rate(pts[0], pts[-1]), rate(pts[-2], pts[-1])
    rates = [x for x in (lt, st) if x is not None]
    if not rates:
        return None
    return {"long_term": lt, "short_term": st, "governing": max(rates), "latest_mm": pts[-1][1],
            "latest_at": pts[-1][0].date().isoformat()}


def _line(conn, fid: str, until: str | None, min_required_mm: float | None, at: date) -> dict | None:
    from ..graph import measurements as m

    series = m.for_entity(conn, fid, "wall_thickness", until=until)["series"]
    f = _fields(conn, fid)
    tmin = min_required_mm or design_thickness(_num(f.get("mop_barg")), _num(f.get("diameter_in")))
    cmls = []
    for s in series:
        r = cml_rate(s["values"])
        if r is None:
            continue
        life = (r["latest_mm"] - tmin) / r["governing"] if tmin and r["governing"] > 0 else None
        cmls.append({"cml": s["location"], "record": s["latest"]["record"], "remaining_years": life, **r})
    if not cmls:
        return None
    worst = max(cmls, key=lambda c: c["governing"])
    lives = [c["remaining_years"] for c in cmls if c["remaining_years"] is not None]
    shortest = min(lives) if lives else None
    last = max(c["latest_at"] for c in cmls)
    interval = min(max(shortest, 0) / 2, MAX_INTERVAL) if shortest is not None else None
    due = (date.fromisoformat(last).toordinal() + int(interval * YEAR)) if interval is not None else None
    return {"id": fid, "line": _label(conn, fid), "service": f.get("service"),
            "rate_mm_per_year": _r(worst["governing"], 3), "long_term_mm_per_year": _r(worst["long_term"], 3),
            "short_term_mm_per_year": _r(worst["short_term"], 3), "fastest_cml": worst["cml"],
            "latest_mm": _r(worst["latest_mm"], 2), "retirement_mm": _r(tmin, 2),
            "retirement_basis": ("given" if min_required_mm else "Barlow at MOP, assumed grade") if tmin else None,
            "remaining_years": _r(shortest, 1), "last_survey": last,
            "next_inspection_years": _r(interval, 1),
            "next_inspection_due": date.fromordinal(due).isoformat() if due else None,
            "overdue": bool(due and date.fromordinal(due) < at), "cmls": len(cmls),
            "records": [c["record"] for c in sorted(cmls, key=lambda c: -c["governing"])[:2]]}


def corrosion_circuit(conn, fid: str, min_required_mm: float | None = None, until: str | None = None) -> dict:
    from ..graph import operations

    kind = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()
    if kind is None:
        raise CalcError("unknown entity")
    at = date.fromisoformat((until or operations.as_of(conn))[:10])
    service = _fields(conn, fid).get("service") if kind["kind"] == "pipeline" else None
    members = [fid]
    if service:
        members = [r["fuzer_id"] for r in conn.execute(
            """
            select e.fuzer_id from entity e
            join golden s on s.fuzer_id = e.fuzer_id and s.field = 'service' and s.value #>> '{}' = %s
            left join golden st on st.fuzer_id = e.fuzer_id and st.field = 'status'
            where e.kind = 'pipeline' and e.retired_at is null and e.merged_into is null
              and coalesce(st.value #>> '{}', 'in service') = 'in service'
            order by 1
            """, (service,)).fetchall()] or [fid]
    lines = [x for x in (_line(conn, m, until, min_required_mm, at) for m in members) if x]
    if not lines:
        raise CalcError("no wall thickness readings on that item" + (f" or its {service} circuit" if service else ""))
    lines.sort(key=lambda x: -(x["rate_mm_per_year"] or 0))
    rates = sorted(x["rate_mm_per_year"] for x in lines)
    median = rates[len(rates) // 2]
    soon = [x for x in lines if x["remaining_years"] is not None and x["remaining_years"] < RETIRE_YEARS]
    overdue = [x for x in lines if x["overdue"]]
    localised = [x for x in lines if len(lines) >= 3 and x["rate_mm_per_year"] > 2 * max(median, 0.01)]
    flag = "retire_soon" if soon else "inspection_overdue" if overdue else "localised" if localised else None
    me = next((x for x in lines if x["id"] == fid), None)
    assumed = any(x["retirement_basis"] == "Barlow at MOP, assumed grade" for x in lines)
    return result(lines[0]["rate_mm_per_year"], median, flag,
                  (f"the {service} corrosion circuit: {len(lines)} in-service line(s) in that service" if service else
                   "no circuit on record for this item, so it stands alone")
                  + "; per CML the greater of the long-term (first to last) and short-term (last two readings) wall "
                  "loss rate (API 570 7.1); value is the circuit's fastest line, expected its median; remaining life to "
                  + ("the given retirement thickness" if min_required_mm else
                     f"the Barlow pressure design thickness at each line's MOP (assumed API 5L X52, design factor "
                     f"{DESIGN_FACTOR}: confirm against the line's design)" if assumed else
                     "a retirement thickness, which isn't on record here: pass min_required_mm from the design")
                  + f"; next inspection at half the remaining life, at most {MAX_INTERVAL:g} years, after the last "
                  f"survey; retire soon under {RETIRE_YEARS:g} years",
                  [fid, *dict.fromkeys(r for x in lines[:3] for r in x["records"])], unit="mm/year",
                  circuit=service or _label(conn, fid), at=at.isoformat(), line=me, lines=lines[:15],
                  retire_soon=[x["line"] for x in soon], inspection_overdue=[x["line"] for x in overdue],
                  localised=[x["line"] for x in localised], retirement_assumed=assumed and not min_required_mm,
                  shortest_remaining_years=min((x["remaining_years"] for x in lines
                                                if x["remaining_years"] is not None), default=None))
