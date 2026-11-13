"""Sparse, manual measurements: EC well tests, LIMS lab results at sample points and inspection wall
thickness readings at CMLs. Each generator takes its own Random, so adding them never changes the
rest of an asset. Units vary the way they do in real exports (older surveys in inches, the odd
dewpoint in degF, results below the detection limit as "<5")."""

import random
from datetime import date, timedelta

# kind of sample point -> analytes: (analyte, unit, base value, spread, method)
ANALYTES = {
    "oil": [("bsw", "% vol", 0.3, 0.12, "ASTM D4007 centrifuge"), ("salt", "PTB", 8.0, 2.5, "ASTM D3230"),
            ("density", "kg/m3", 862.0, 3.0, "ASTM D5002")],
    "water": [("oil_in_water", "mg/L", 18.0, 6.0, "IR, ISO 9377-2")],
    "gas": [("h2s", "ppmv", 12.0, 3.0, "ASTM D4810 stain tube"), ("co2", "% mol", 2.1, 0.2, "GC, GPA 2261"),
            ("water_dewpoint", "degC", -8.0, 1.5, "chilled mirror")],
    # the inlet separator's gas, at its dew point there (fakeplant/calibration.py)
    "gas_composition": [("relative_density", "-", 0.746, 0.004, "GC, GPA 2261 / GPA 2172"),
                        ("molar_mass", "g/mol", 21.6, 0.12, "GC, GPA 2261 / GPA 2172")],
    "well": [("chloride", "mg/L", 18000.0, 900.0, "Mohr titration"), ("bsw", "% vol", 25.0, 4.0, "ASTM D4007 centrifuge")],
}
DETECTION_LIMIT = {"oil_in_water": 5.0, "h2s": 1.0}


def _days(start: date, end: date, every: int, rng: random.Random):
    d = start + timedelta(days=rng.randint(0, every - 1))
    while d <= end:
        yield d
        d += timedelta(days=every + rng.randint(-2, 2))


def lab_results(rng: random.Random, points: list[dict], start: date, end: date, every: int = 14) -> list[dict]:
    """points: {"tag" or "well", "sample_point", "kind" (ANALYTES), optional "step": {analyte: (from
    date, factor)}} for a planted change (oil in water rising after a water breakthrough)."""
    rows = []
    for p in points:
        for day in _days(start, end, every, rng):
            sample = f"S{day:%y%m%d}-{len(rows):05d}"
            at = f"{day.isoformat()}T{rng.choice(['08', '09', '14'])}:{rng.randint(0, 59):02d}:00"
            for analyte, unit, base, spread, method in ANALYTES[p["kind"]]:
                step = p.get("step", {}).get(analyte)
                v = base * (step[1] if step and day >= step[0] else 1) + rng.gauss(0, spread)
                if analyte == "water_dewpoint" and rng.random() < 0.2:  # a contract lab that reports in degF
                    v, unit = v * 9 / 5 + 32, "degF"
                result = f"{v:.3g}" if abs(v) < 100 else f"{v:.0f}"
                if (lim := DETECTION_LIMIT.get(analyte)) and v < lim:
                    result = f"<{lim:g}"
                rows.append({"sample_id": sample, "analyte": analyte, "tag": p.get("tag", ""), "well": p.get("well", ""),
                             "sample_point": p["sample_point"], "sampled_at": at, "result": result, "unit": unit,
                             "method": method})
    return rows


def thickness_readings(rng: random.Random, assets: list[dict], surveys: list[int]) -> list[dict]:
    """assets: {"tag" or "line_id", "nominal_mm", "cmls", optional "rate_mm_y"}; one reading per CML
    per survey year. Surveys before 2021 were done by a contractor that reported in inches.

    Optional per asset: "faster" = (date, rate_mm_y), the wall loss rate from that date on (readings
    taken after it lose the extra metal), and "follow_up" = an ISO date for one extra survey of every
    CML, as an inspector books after a change in service. Both draw from their own Random, so an asset
    with them leaves every other reading unchanged."""
    rows = []
    for a in assets:
        ref = a.get("tag") or a["line_id"]
        extra = random.Random(ref)  # the follow-up survey's noise, apart from `rng`
        for c in range(1, a["cmls"] + 1):
            rate = a.get("rate_mm_y", rng.uniform(0.02, 0.12)) * rng.uniform(0.7, 1.3)
            start = a["nominal_mm"] * rng.uniform(0.98, 1.04)

            def faster(on: date) -> float:  # extra metal lost by `on` once the faster rate applies
                return (a["faster"][1] - rate) * (on - a["faster"][0]).days / 365.25 \
                    if a.get("faster") and on > a["faster"][0] else 0.0  # noqa: B023

            for year in surveys:
                t = start - rate * (year - surveys[0]) + rng.gauss(0, 0.05)
                inches = year < 2021
                day = f"{year}-{rng.randint(4, 9):02d}-{rng.randint(1, 28):02d}"
                t -= faster(date.fromisoformat(day))
                rows.append({"reading_id": f"TM-{ref}-{c:02d}-{year}", "tag": a.get("tag", ""),
                             "line_id": a.get("line_id", ""), "cml": f"CML-{c:02d}", "survey_date": day,
                             "thickness": f"{t / 25.4:.3f}" if inches else f"{t:.2f}", "unit": "in" if inches else "mm",
                             "nominal": f"{a['nominal_mm']:.2f}", "method": "UT" if c % 4 else "profile RT"})
            if a.get("follow_up"):
                on = date.fromisoformat(a["follow_up"])
                # surveys are placed by year, as above; the faster rate adds what it took since it began
                t = start - rate * (on.year - surveys[0]) - faster(on) + extra.gauss(0, 0.05)
                rows.append({"reading_id": f"TM-{ref}-{c:02d}-{on:%Y%m}", "tag": a.get("tag", ""),
                             "line_id": a.get("line_id", ""), "cml": f"CML-{c:02d}", "survey_date": on.isoformat(),
                             "thickness": f"{t:.2f}", "unit": "mm", "nominal": f"{a['nominal_mm']:.2f}",
                             "method": "UT" if c % 4 else "profile RT"})
    return rows


def well_tests(rng: random.Random, wells: list[str], first: date, months: int, lifted: set[str] = frozenset()) -> list[dict]:
    """Monthly EC well tests: declining oil rate, rising water cut."""
    rows = []
    for w in wells:
        rate, wc = rng.uniform(40, 160), rng.uniform(5, 50)
        decline, rise = rng.uniform(0.004, 0.015), rng.uniform(0.2, 0.8)
        for m in range(months):
            y, mo = first.year + (first.month - 1 + m) // 12, (first.month - 1 + m) % 12 + 1
            day = date(y, mo, rng.randint(3, 26)).isoformat()
            rows.append({"test_id": f"WT-{w}-{day}", "well": w, "test_date": day,
                         "oil_sm3d": f"{rate * (1 - decline) ** m * rng.uniform(0.95, 1.05):.1f}",
                         "water_cut_pct": f"{min(95, wc + rise * m + rng.gauss(0, 1)):.1f}",
                         "gor_sm3_sm3": f"{rng.uniform(90, 160):.0f}",
                         "lift_gas_ksm3d": f"{rng.uniform(20, 60):.1f}" if w in lifted else "",
                         "whp_barg": f"{rng.uniform(14, 30):.1f}"})
    return rows


def nominal_wall_mm(diameter_in: float) -> float:
    """A rough schedule wall for a line size."""
    return round(5.5 + 0.55 * diameter_in, 2)
