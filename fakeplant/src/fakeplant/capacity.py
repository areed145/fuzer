"""Okan's stage capacities and the paper trail an old asset keeps them in.

The hidden truth is one rating per flow stage, dated. Three registers hold it the way a 1998 field
does: design values from the original datasheets (bbl/d, MMscf/d), a yearly production engineering
statement, and an MOC register of re-rates. They disagree, as they do on a real asset: the 2025
statement predates a derate, and an open MOC proposes a rate nobody approved.

`constrain` then holds each day's allocated oil to the truth, so the limits bind in the history
instead of labelling it. Its own Random and post-processing leave every other export untouched:
logs stay byte-identical, so cached readings of them stay valid.

Water is the story: as the field's water cut rises the HP separator's liquid limit costs more oil,
and a sand derate in May 2025 brings the day forward when it takes over from the wells.
"""

import math
import random

Row = dict[str, str]

MANIFOLDS = [f"OKJ{j:02d}-PM" for j in range(1, 25)]
DESIGN = [  # tag or node, quantity, value, unit, document (1998 datasheets, two later additions)
    ("10-V-2001", "liquid_capacity", 100000, "bbl/d", "OKA-PP-DS-2001"),
    ("10-V-2002", "liquid_capacity", 107000, "bbl/d", "OKA-PP-DS-2002"),
    ("30-V-2002", "liquid_capacity", 44000, "bbl/d", "OKA-ST1-DS-2002"),
    ("40-V-2002", "liquid_capacity", 50000, "bbl/d", "OKA-ST2-DS-2002"),
    ("50-V-2001", "liquid_capacity", 1500, "Sm3/d", "OKA-NWP-DS-2001"),
    ("20-V-2001", "gas_capacity", 170, "MMscf/d", "OKG-20-DS-2001"),
    ("10-FT-3001", "oil_capacity", 75000, "bbl/d", "OKA-PP-DS-3001"),
]
STATEMENT = {  # year -> rating; a blank in a year's sheet is a line nobody revisited
    2024: [("10-V-2001", "liquid_capacity", 14500, "Sm3/d"), ("20-K-2300A", "gas_capacity", 45.9, "MMscf/d"),
           ("20-K-2300B", "gas_capacity", 1300, "kSm3/d"), ("20-K-2300C", "gas_capacity", 1300, "kSm3/d"),
           ("ESCRAVOS-TERM", "oil_capacity", 66000, "bbl/d"), ("EGP", "export_gas_capacity", 2100, "kSm3/d"),
           *[(m, "liquid_capacity", 1200, "Sm3/d") for m in MANIFOLDS]],
    2025: [("10-V-2001", "liquid_capacity", 14500, "Sm3/d"), ("20-K-2300A", "gas_capacity", 1300, "kSm3/d"),
           ("20-K-2300B", "gas_capacity", 1300, "kSm3/d"), ("20-K-2300C", "gas_capacity", 45.9, "MMscf/d"),
           ("ESCRAVOS-TERM", "oil_capacity", 10500, "Sm3/d"), ("EGP", "export_gas_capacity", 74.2, "MMscf/d")],
}
ISSUED = {2024: "2024-01-15", 2025: "2025-02-03"}
MOC = [
    ("MOC-2011-007", "2011-06-01", "closed", "10-V-2001", "liquid_capacity", 14500, "Sm3/d",
     "Re-rate after the 2011 tie-in; internals revised, retention time rechecked."),
    ("MOC-2025-014", "2025-05-19", "closed", "10-V-2001", "liquid_capacity", 13400, "Sm3/d",
     "Temporary derate pending sand jetting at the next shutdown. Sand on the weir reduces retention."),
    ("MOC-2025-031", "2025-11-20", "open", "10-V-2001", "liquid_capacity", 15500, "Sm3/d",
     "Proposed re-rate with new internals. Not approved; no change to operating limits."),
]
TRUTH_HP = [("2024-01-01", 14500.0), ("2025-05-19", 13400.0)]
TRUTH = {"terminal": 10500.0, "compression": 3900.0, "inlet": 170 * 28.3168, "egp": 2100.0}


def _hold(tests: list[dict], days: list[str]) -> list[tuple[float, float]]:
    """Each day's field water cut and rate, from every well's latest test so far."""
    by_day: dict[str, list[dict]] = {}
    for t in tests:
        by_day.setdefault(t["test_date"], []).append(t)
    latest, out, last = {}, [], None
    dates = sorted(by_day)
    i = 0
    for day in days:
        while i < len(dates) and dates[i] <= day:
            for t in by_day[dates[i]]:
                latest[t["well"]] = (float(t["oil_sm3d"]), float(t["water_cut_pct"]))
            i += 1
        oil = sum(q for q, _ in latest.values())
        water = sum(q * c / (100 - c) for q, c in latest.values())
        last = (water / (oil + water) if oil else 0.0, oil)
        out.append(last)
    return out


def bounds(day: str, potential: float, gas: float, export: float, cut: float) -> dict[str, float]:
    """Facility oil each binding-capable stage allows that day."""
    hp = next(v for start, v in reversed(TRUTH_HP) if start <= day)
    return {"wells": potential, "hp_separator": hp * (1 - cut), "terminal": TRUTH["terminal"],
            "compression": TRUTH["compression"] * potential / gas if gas else math.inf,
            "gas_inlet": TRUTH["inlet"] * potential / gas if gas else math.inf,
            "egp": TRUTH["egp"] * potential / export if export else math.inf}


def constrain(daily: list[Row], tests: list[dict]) -> list[dict]:
    """Hold allocated oil to the day's limit in place; returns the answer key. A day of lost oil
    below the limit is deferment against it, not a lower limit."""
    days = [d["report_date"] for d in daily]
    truth = []
    for d, (cut, _) in zip(daily, _hold(tests, days)):
        potential = float(d["oil_potential_sm3"])
        limits = bounds(d["report_date"], potential, float(d["gas_ksm3"]), float(d["gas_export_ksm3"]), cut)
        stage = min(limits, key=limits.get)
        d["oil_sm3"] = f"{max(0, math.floor(min(float(d['oil_sm3']), limits[stage]))):.0f}"
        truth.append({"date": d["report_date"], "binding": stage, "limit_sm3d": round(limits[stage], 1)})
    return truth


def registers(seed: int) -> dict[str, list[Row]]:
    rng = random.Random(seed + 20)
    design = [{"reading_id": f"DS-{doc}", "tag": tag, "node": "", "quantity": quantity, "value": str(value), "unit": unit, "effective": "1998-06-01",
               "method": "design", "doc_ref": doc} for tag, quantity, value, unit, doc in DESIGN]
    statement = []
    for year, rows in STATEMENT.items():
        for tag, quantity, value, unit in rows:
            node = tag.startswith(("OKJ", "ESC", "EGP"))
            statement.append({
                "reading_id": f"CS{year}-{tag}", "tag": "" if node else tag, "node": tag if node else "",
                "quantity": quantity, "value": str(value), "unit": unit, "effective": ISSUED[year],
                "method": "statement", "owner": rng.choice(["Production Engineer", "Facilities Engineer"]),
            })
    moc = [{"reading_id": number, "tag": tag, "node": "", "quantity": quantity, "value": str(value), "unit": unit,
            "effective": effective, "method": "moc" if status == "closed" else "moc_open", "status": status,
            "description": text} for number, effective, status, tag, quantity, value, unit, text in MOC]
    return {"design_basis": design, "capacity_statement": statement, "moc_register": moc}
