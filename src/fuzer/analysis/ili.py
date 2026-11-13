"""In-line inspection anomalies (#366): how each anomaly a pig run found has grown since the run before, and the
pressure the line could hold at its metal loss against the line's MOP. Screening only: fitness for service stays
with the integrity engineer.

    anomalies(conn, line=None)   per line with ILI features: each run, and each metal loss anomaly of the latest run
                                 with its growth from the previous run, its remaining strength now and at the growth
                                 rate HORIZON_YEARS on, and whether it crosses the screen

Anomalies are matched between runs on the same line by type and station (within MATCH_M, after the vendor's girth
weld correction). Remaining strength is modified ASME B31G (0.85 dL): failure pressure from the segment's wall and
material grade (SMYS of the API 5L grade) and the line's diameter, with the safe pressure at the class location's
design factor. A segment with no recorded grade gets no strength, never an assumed grade. Growth between two runs
is within the tool's sizing tolerance when it is under TOLERANCE_PCT of wall, and the result says so.
"""

import math
import re
from datetime import date

from ..graph import positions
from .integrity import OD_MM, YEAR, _fields, _num

METAL_LOSS = "metal loss"
MATCH_M = 1.5  # an anomaly in the next run within this many metres, of the same type, is the same anomaly
HORIZON_YEARS = 5.0  # growth is projected this far ahead of the latest run
DEPTH_LIMIT = 80.0  # % of wall: an anomaly this deep crosses the screen whatever its pressure
TOLERANCE_PCT = 10.0  # % of wall: a typical MFL tool's depth sizing tolerance; growth under it may be sizing
FLOW_STRESS_ADD_MPA = 69.0  # modified B31G flow stress: SMYS + 69 MPa (10 ksi)
SMYS_MPA = {"B": 241.0, "X42": 290.0, "X46": 317.0, "X52": 359.0, "X56": 386.0, "X60": 414.0, "X65": 448.0,
            "X70": 483.0, "X80": 552.0}  # API 5L
DESIGN_FACTOR = {"1": 0.72, "2": 0.60, "3": 0.50, "4": 0.40}  # ASME B31.8 class locations


def smys(grade: str | None) -> float | None:
    """SMYS (MPa) of an API 5L grade written as recorded ("API 5L X65", "X65", "Grade B"); None when unrecorded."""
    m = re.search(r"\bX\s?(\d{2})\b|\bGRADE\s+B\b|\bGR\.?\s*B\b", (grade or "").upper())
    if not m:
        return None
    return SMYS_MPA.get(f"X{m.group(1)}" if m.group(1) else "B")


def failure_pressure(depth_pct: float, length_mm: float, wall_mm: float, od_mm: float, smys_mpa: float) -> float:
    """Modified ASME B31G (0.85 dL) failure pressure in bar of a metal loss `depth_pct` of wall deep and `length_mm`
    long, on a pipe of `wall_mm` wall and `od_mm` outside diameter."""
    z = length_mm ** 2 / (od_mm * wall_mm)
    m = math.sqrt(1 + 0.6275 * z - 0.003375 * z * z) if z <= 50 else 0.032 * z + 3.3
    a = 0.85 * depth_pct / 100
    return 2 * (smys_mpa + FLOW_STRESS_ADD_MPA) * wall_mm / od_mm * (1 - a) / (1 - a / m) * 10


def _date(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _match(latest: list[dict], previous: list[dict]) -> dict[str, dict]:
    """Latest feature id -> the previous run's feature it is: same type, nearest station within MATCH_M, each once."""
    pairs = sorted((abs(_num(a["station_m"]) - _num(b["station_m"])), a["id"], b["id"])
                   for a in latest for b in previous
                   if a["feature_type"] == b["feature_type"] and _num(a["station_m"]) is not None
                   and _num(b["station_m"]) is not None and abs(_num(a["station_m"]) - _num(b["station_m"])) <= MATCH_M)
    prev = {b["id"]: b for b in previous}
    out, used = {}, set()
    for _, a, b in pairs:
        if a not in out and b not in used:
            out[a] = prev[b]
            used.add(b)
    return out


def _line(conn, fid: str) -> dict | None:
    along = positions.along(conn, fid)
    if not along["features"]:
        return None
    f = _fields(conn, fid)
    label = conn.execute("select label from entity_label where fuzer_id = %s", (fid,)).fetchone()
    dia, mop = _num(f.get("diameter_in")), _num(f.get("mop_barg"))
    od = OD_MM.get(round(dia), dia * 25.4) if dia else None
    segs = {s["id"]: s for s in along["segments"]}
    runs: dict[str, dict] = {}
    for x in along["features"]:
        r = runs.setdefault(x["run_id"] or "unknown run", {"run": x["run_id"] or "unknown run",
                                                            "date": _date(x["run_date"]), "features": []})
        r["features"].append(x)
    order = sorted(runs.values(), key=lambda r: r["date"] or date.min)
    latest, previous = order[-1], (order[-2] if len(order) > 1 else None)
    years = ((latest["date"] - previous["date"]).days / YEAR
             if previous and latest["date"] and previous["date"] else None)
    matched = _match(latest["features"], previous["features"]) if previous else {}
    anomalies = []
    for x in latest["features"]:
        if x["feature_type"] != METAL_LOSS:
            continue
        seg = segs.get(x["segment"]) or {}
        d, length, wall = _num(x["depth_pct"]), _num(x["length_mm"]), _num(seg.get("wall_thickness_mm"))
        grade = smys(seg.get("grade"))
        df = DESIGN_FACTOR.get(str(seg.get("class_location") or "").strip())
        a = {"id": x["id"], "feature": x["label"], "station_m": _num(x["station_m"]), "clock": x["clock"],
             "segment": seg.get("label"), "depth_pct": d, "length_mm": length, "wall_mm": wall,
             "grade": seg.get("grade") or None, "class_location": seg.get("class_location"), "design_factor": df}
        before = matched.get(x["id"])
        rate = None
        if before is not None and years and years > 0 and d is not None and _num(before["depth_pct"]) is not None:
            grew = d - _num(before["depth_pct"])
            rate = max(0.0, grew / years)
            a |= {"previous": before["label"], "previous_depth_pct": _num(before["depth_pct"]),
                  "growth_pct_wall": round(grew, 1), "rate_pct_per_year": round(rate, 2),
                  "rate_mm_per_year": round(rate * wall / 100, 3) if wall else None,
                  "within_tool_tolerance": abs(grew) < TOLERANCE_PCT}
        elif previous:
            a["new_since"] = previous["run"]
        missing = [n for n, v in (("material grade", grade), ("wall thickness", wall), ("diameter", od),
                                  ("MOP", mop), ("class location", df), ("depth", d), ("length", length)) if not v]
        ahead = min(100.0, d + rate * HORIZON_YEARS) if d is not None and rate is not None else None
        a["projected_depth_pct"] = round(ahead, 1) if ahead is not None else None
        if missing:
            a |= {"failure_barg": None, "safe_barg": None, "erf": None, "projected_erf": None,
                  "why": f"no {' or '.join(missing)} recorded, so no strength is calculated"}
        else:
            pf = failure_pressure(d, length, wall, od, grade)
            safe = pf * df
            a |= {"failure_barg": round(pf, 1), "safe_barg": round(safe, 1), "erf": round(mop / safe, 3)}
            if ahead is not None:
                a["projected_erf"] = round(mop / (failure_pressure(ahead, length, wall, od, grade) * df), 3)
        now = (a.get("erf") or 0) >= 1 or (d or 0) >= DEPTH_LIMIT
        later = (a.get("projected_erf") or 0) >= 1 or (ahead or 0) >= DEPTH_LIMIT
        a["screen"] = ("exceeds_now" if now else f"exceeds_within_{int(HORIZON_YEARS)}_years" if later
                       else "no_strength" if missing else "within_screen")
        anomalies.append(a)
    gone = [{"feature": b["label"], "station_m": _num(b["station_m"]), "type": b["feature_type"]}
            for b in (previous["features"] if previous else [])
            if b["id"] not in {m["id"] for m in matched.values()}]
    rank = {"exceeds_now": 0, f"exceeds_within_{int(HORIZON_YEARS)}_years": 1, "no_strength": 2, "within_screen": 3}
    anomalies.sort(key=lambda a: (rank[a["screen"]], -(a.get("erf") or 0), a["station_m"] or 0))
    worst = anomalies[0]["screen"] if anomalies else "no_metal_loss"
    return {"line": label["label"] if label else fid, "id": fid, "diameter_in": dia, "od_mm": od, "mop_barg": mop,
            "runs": [{"run": r["run"], "date": r["date"].isoformat() if r["date"] else None,
                      "features": len(r["features"]),
                      "metal_loss": sum(x["feature_type"] == METAL_LOSS for x in r["features"])} for r in order],
            "years_between_runs": round(years, 2) if years else None, "anomalies": anomalies,
            "not_in_latest_run": gone, "verdict": worst,
            **({} if previous else {"growth": "one run on record, so no growth rate"})}


def anomalies(conn, line: str | None = None) -> dict:
    """Each line's ILI anomalies: growth since the previous run, remaining strength against MOP, and the screen."""
    ids = [r["fuzer_id"] for r in conn.execute(
        "select distinct e.fuzer_id, l.label from edge g join entity e on e.fuzer_id = g.dst "
        "left join entity_label l on l.fuzer_id = e.fuzer_id "
        "where g.rel = 'found_on' and g.valid_to is null and e.kind = 'pipeline' order by l.label")]
    if line:
        ids = [i for i in ids if i == line or (conn.execute(
            "select 1 from entity_label where fuzer_id = %s and lower(label) = lower(%s)", (i, line)).fetchone())]
    lines = [x for x in (_line(conn, i) for i in ids) if x]
    horizon = f"exceeds_within_{int(HORIZON_YEARS)}_years"
    flagged = [a for x in lines for a in x["anomalies"] if a["screen"] in ("exceeds_now", horizon)]
    base = {"thresholds": {"match_m": MATCH_M, "horizon_years": HORIZON_YEARS, "depth_limit_pct": DEPTH_LIMIT,
                           "tool_tolerance_pct": TOLERANCE_PCT, "design_factor": DESIGN_FACTOR},
            "method": "modified ASME B31G (0.85 dL), flow stress SMYS + 69 MPa, safe pressure at the class location's "
                      "design factor (ASME B31.8)",
            "source": "in-line inspection features, PODS route segments (wall, grade, class location), GIS pipelines "
                      "(diameter, MOP)"}
    if not lines:
        return base | {"lines": [], "verdict": "no_ili", "summary": {"lines": 0},
                       "note": "No in-line inspection features recorded on a known line."}
    verdict = ("exceeds_now" if any(a["screen"] == "exceeds_now" for a in flagged) else horizon if flagged
               else "no_strength" if any(a["screen"] == "no_strength" for x in lines for a in x["anomalies"])
               else "within_screen")
    return base | {
        "lines": lines, "verdict": verdict,
        "summary": {"lines": len(lines), "anomalies": sum(len(x["anomalies"]) for x in lines), "flagged": len(flagged),
                    "no_strength": sum(a["screen"] == "no_strength" for x in lines for a in x["anomalies"])},
        "note": (f"An anomaly crosses the screen when the MOP over its safe pressure (ERF) reaches 1 or it is "
                 f"{DEPTH_LIMIT:g}% of wall deep, now or at its growth rate {HORIZON_YEARS:g} years on. Growth under "
                 f"{TOLERANCE_PCT:g}% of wall between two runs may be tool sizing. Anomalies on a segment with no "
                 "recorded grade get no strength. Screening only: fitness for service stays with the integrity "
                 "engineer.")}
