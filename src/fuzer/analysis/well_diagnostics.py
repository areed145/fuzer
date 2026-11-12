"""Well water cut, GOR and test staleness (#339): breakthrough and gas screening for well review, and whether the tests
everything else rests on are recent enough to trust.

    diagnose(conn, well)       one well's tests: water cut against estimated cumulative oil, GOR over time, and the
                               first test where water cut stepped up (a breakthrough suggestion) or GOR climbed
    staleness(conn, max_days)  every producing well's latest test and its age at the data's latest day, asset-wide

Cumulative oil is estimated from the tests (the rate held between tests, trapezoid), since no per-well allocation is
loaded; it is labelled as such everywhere. A step or a climb is a pattern to look at, not a mechanism: coning, a
channel, a casing leak or a test error stay questions for the engineer.
"""

from datetime import date, datetime
from statistics import median

from ..graph import operations as ops
from . import calcs

WC_STEP = 15.0  # water cut points above the median of the previous tests that reads as a step
GOR_RISE = 1.3  # the latest tests' median GOR against the first tests' that reads as a climb
WINDOW = 3  # tests before a water cut step that set its level
GOR_WINDOW = 6  # tests at each end that set a GOR level (GOR on a test scatters far more than water cut)
STALE_DAYS = 90  # a well whose latest test is older than this at the data's latest day is stale
ESTIMATED = "estimated from well tests (rate held between tests); no per-well allocation is loaded"


def _label(conn, fid: str) -> str:
    r = conn.execute("select label from entity_label where fuzer_id = %s", (fid,)).fetchone()
    return r["label"] if r and r["label"] else fid


def diagnose(conn, well: str) -> dict:
    """One well's water cut against estimated cumulative oil and its GOR over time, with any step or climb found."""
    try:
        tests = calcs.well_tests(conn, well)
    except calcs.CalcError as e:
        return {"well_id": well, "well": _label(conn, well), "verdict": "too_little_data", "reason": str(e)}
    cum, points = 0.0, []
    for i, t in enumerate(tests):
        if i:
            prev = tests[i - 1]
            days = (t["at"] - prev["at"]).days
            cum += days * ((prev.get("oil_rate") or 0) + (t.get("oil_rate") or 0)) / 2
        points.append({"at": t["at"].date().isoformat(), "record": t["record"], "oil_sm3d": t.get("oil_rate"),
                       "water_cut_pct": t.get("water_cut"), "gor": t.get("gor"), "cum_oil_sm3": round(cum)})
    step = None
    wcs = [p["water_cut_pct"] for p in points]
    for i in range(WINDOW, len(points)):
        before = [w for w in wcs[i - WINDOW:i] if w is not None]
        if wcs[i] is not None and before and wcs[i] - median(before) >= WC_STEP:
            step = {"at": points[i]["at"], "record": points[i]["record"], "cum_oil_sm3": points[i]["cum_oil_sm3"],
                    "water_cut_before_pct": round(median(before), 1), "water_cut_after_pct": wcs[i],
                    "oil_before_sm3d": points[i - 1]["oil_sm3d"], "oil_after_sm3d": points[i]["oil_sm3d"]}
            break
    gors = [p["gor"] for p in points if p["gor"] is not None]
    climb = None
    if len(gors) >= 2 * GOR_WINDOW:
        first, last = median(gors[:GOR_WINDOW]), median(gors[-GOR_WINDOW:])
        if first and last / first >= GOR_RISE:
            climb = {"first_gor": first, "latest_gor": last, "ratio": round(last / first, 2)}
    return {"well_id": well, "well": _label(conn, well), "tests": len(points), "points": points,
            "water_cut_step": step, "gor_climb": climb,
            "verdict": "water_cut_step" if step else "gor_climb" if climb else "no_pattern",
            "cumulative": {"oil_sm3": round(cum), "basis": ESTIMATED},
            "thresholds": {"water_cut_step_points": WC_STEP, "gor_rise": GOR_RISE, "tests_for_a_level": WINDOW,
                           "tests_for_a_gor_level": GOR_WINDOW},
            "source": "well tests (oil rate, water cut, GOR)",
            "note": "A step or a climb is a pattern for the engineer, not a mechanism. Cumulative oil is " + ESTIMATED
                    + "."}


def staleness(conn, max_days: int = STALE_DAYS) -> dict:
    """Every producing well's latest test and its age at the data's latest day; stale beyond `max_days`."""
    today = date.fromisoformat(ops.as_of(conn))
    rows = conn.execute(
        """
        select e.fuzer_id as id, coalesce(l.label, e.fuzer_id) as label, max(m.at) as latest, count(distinct m.record) as tests
        from entity e left join entity_label l using (fuzer_id)
        left join measurement m on m.fuzer_id = e.fuzer_id and m.method = 'well test'
        where e.kind = 'well' and e.retired_at is null and e.merged_into is null
        group by e.fuzer_id, l.label order by 2
        """).fetchall()
    wells = []
    for r in rows:
        latest = r["latest"].date() if isinstance(r["latest"], datetime) else r["latest"]
        age = (today - latest).days if latest else None
        wells.append({"id": r["id"], "well": r["label"], "tests": r["tests"],
                      "latest_test": latest.isoformat() if latest else None, "age_days": age,
                      "status": "no_test_on_file" if latest is None else "stale" if age > max_days else "current"})
    wells.sort(key=lambda w: (w["status"] == "current", -(w["age_days"] or 10**6), w["well"]))
    ages = [w["age_days"] for w in wells if w["age_days"] is not None]
    count = {s: sum(1 for w in wells if w["status"] == s) for s in ("current", "stale", "no_test_on_file")}
    return {"as_of": today.isoformat(), "max_days": max_days, "wells": wells, "summary": count | {
                "wells": len(wells), "stale_share_pct": round(100 * count["stale"] / len(wells), 1) if wells else None,
                "median_age_days": median(ages) if ages else None, "oldest_days": max(ages) if ages else None},
            "source": "well tests (latest test date per well)",
            "note": f"A test older than {max_days} days at {today} is stale: rates, forecasts and allocation built on it "
                    "carry that age. A well with no test on file (a gas producer metered another way, a new well) is listed, not counted as current."}


def screen(conn, max_days: int = STALE_DAYS) -> dict:
    """Across the field: the wells whose water cut stepped up or whose GOR climbed, and the test staleness."""
    ids = [r["fuzer_id"] for r in conn.execute(
        "select distinct m.fuzer_id from measurement m join entity e on e.fuzer_id = m.fuzer_id and e.kind = 'well' "
        "and e.retired_at is null and e.merged_into is null where m.method = 'well test'")]
    found = []
    for fid in ids:
        d = diagnose(conn, fid)
        if d["verdict"] in ("water_cut_step", "gor_climb"):
            found.append({k: d[k] for k in ("well_id", "well", "verdict", "water_cut_step", "gor_climb", "tests")}
                         | {"latest": d["points"][-1]})
    found.sort(key=lambda d: (d["verdict"] != "water_cut_step", d["well"]))
    return {"wells_screened": len(ids), "patterns": found, "staleness": staleness(conn, max_days),
            "thresholds": {"water_cut_step_points": WC_STEP, "gor_rise": GOR_RISE, "stale_days": max_days},
            "source": "well tests", "note": "Patterns are suggestions for the engineer, not mechanisms."}
