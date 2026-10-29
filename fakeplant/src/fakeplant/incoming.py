"""Fake tables Fuzer has no mapping for, with messy headers, for suggested mappings (suggest.py).

Written to <out>/incoming/ from the same fake plant, plus answer_keys/schema.json: the key column
and each column's meaning for these tables and for the built-in ones (as if they were unmapped).
"""

import json
import random
import re
from pathlib import Path

from .offshore import _write_csv

ROLES = ["Day CRO", "Night CRO", "OIM", "Production Tech", "Maintenance Lead"]
UNITS = ["barg", "degC", "mm/s", "%", "m3/h"]

BUILT_IN = {
    "sap_floc": ("floc", {"floc": "location", "parent": "parent", "description": "description"}),
    "sap_equipment": ("equnr", {"equnr": "other", "description": "description", "manufacturer": "manufacturer",
                                "model": "model", "serial": "serial", "floc": "location",
                                "valid_from": "timestamp", "valid_to": "timestamp"}),
    "jde_assets": ("asset_number", {"asset_number": "other", "description": "description",
                                    "manufacturer": "manufacturer", "model": "model", "serial": "serial"}),
    "maximo_assets": ("assetnum", {"assetnum": "other", "description": "description",
                                   "manufacturer": "manufacturer", "serial": "serial", "location": "tag"}),
    "pi_points": ("tag", {"tag": "tag", "descriptor": "description", "engunits": "unit", "pointsource": "code"}),
    "sap_notifications": ("qmnum", {"qmnum": "other", "notif_type": "code", "created": "timestamp", "equnr": "ref",
                                    "floc": "location", "priority": "code", "short_text": ["description", "text"],
                                    "damage_code": "code", "cause_code": "code", "status": "code"}),
    "sap_work_orders": ("aufnr", {"aufnr": "other", "order_type": "code", "qmnum": "ref", "equnr": "ref",
                                  "floc": "location", "start": "timestamp", "finish": "timestamp",
                                  "activity_code": "code", "cost": "number", "text": ["description", "text"]}),
    "ec_downtime": ("event_id", {"event_id": "other", "object_code": ["well", "location"], "start": "timestamp",
                                 "end": "timestamp", "reason_code": "code", "lost_oil_sm3": "number",
                                 "lost_gas_ksm3": "number", "comment": ["text", "description"]}),
    "oplogs": ("entry_id", {"entry_id": "other", "timestamp": "timestamp", "facility": "location",
                            "author": "person", "text": ["text", "description"]}),
    "ec_day001": ("report_date", {"report_date": "timestamp", "facility": "location", "oil_sm3": "number",
                                  "gas_ksm3": "number", "water_inj_sm3": "number",
                                  "comments": ["text", "description"]}),
    "sap_materials": ("matnr", {"matnr": "other", "description": "description", "manufacturer": "manufacturer",
                                "mpn": ["other", "model"], "stock": "number", "bin": ["other", "code"]}),
    "sap_bom": (None, {"equnr": "ref", "matnr": "ref", "quantity": ["number", "code"]}),
}


def _tag(floc: str) -> str:
    """OSB-20-P1001B -> 20-P-1001B, the way a contractor writes it."""
    _, area, rest = floc.split("-", 2)
    m = re.match(r"([A-Z]+)(\d+[A-Z]?)$", rest)
    return f"{area}-{m.group(1)}-{m.group(2)}" if m else f"{area}-{rest}"


def generate(out: Path, equipment: list[dict], floc: list[dict], wells: list[str], seed: int = 23) -> dict:
    rng = random.Random(seed)
    inc = out / "incoming"
    inc.mkdir(exist_ok=True)
    tables = {}

    # One row per tag: the item installed now (SAP keeps replaced ones too).
    eq = list({e["floc"]: e for e in equipment if e["floc"].count("-") == 2}.values())
    tables["contractor_equipment_list"] = [
        {"Tag No.": _tag(e["floc"]), "Equipment Description": e["description"], "Mfr": e["manufacturer"],
         "Model No": e["model"], "S/N": e["serial"], "Install Date": f"{rng.randint(2012, 2020)}-0{rng.randint(1, 9)}-15",
         "Dry Weight (kg)": str(rng.randint(400, 25000))} for e in eq]

    tables["line_list"] = [
        {"Line Number": f"LN-{20000 + i:06d}", "From": _tag(a["floc"]), "To": _tag(b["floc"]),
         "Size (in)": str(rng.choice([2, 3, 4, 6, 8, 10, 12])),
         "Service": f"{a['description']} to {b['description'].lower()}", "Pipe Spec": rng.choice(["A1", "B2", "C3"])}
        for i, (a, b) in enumerate(zip(eq, eq[1:]))]

    tables["well_tests"] = [
        {"TEST_ID": f"WT{i:05d}", "WELL": f"{w[0]}-{w[1:]}", "TEST_DT": f"2024-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
         "OIL_RATE_SM3D": f"{rng.uniform(80, 900):.1f}", "WATER_CUT_PCT": f"{rng.uniform(0, 60):.1f}",
         "GOR": f"{rng.uniform(80, 250):.0f}", "TESTED_BY": rng.choice(ROLES)}
        for i, w in enumerate(w for w in wells for _ in range(3))]

    tables["location_hierarchy"] = [{"LOC_CODE": f["floc"], "SUPERIOR_LOC": f["parent"], "LOC_TEXT": f["description"]}
                                    for f in floc if f["parent"]]

    tables["inspection_points"] = [
        {"Point ID": f"CML-{i:04d}", "Asset": _tag(e["floc"]), "Lat": f"{58.4 + rng.uniform(0, 0.01):.6f}",
         "Long": f"{1.9 + rng.uniform(0, 0.01):.6f}", "Last Reading (mm)": f"{rng.uniform(6, 14):.2f}",
         "Reading Date": f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"}
        for i, e in enumerate(e for e in eq for _ in range(2))]

    tables["export_0425"] = [
        {"C1": _tag(e["floc"]), "C2": e["description"], "C3": rng.choice(ROLES), "C4": rng.choice("ABC"),
         "C5": rng.choice(UNITS)} for e in eq]

    for name, rows in tables.items():
        _write_csv(inc / f"{name}.csv", rows)
    # One lake-style table among them.
    from .lake import write_parquet

    write_parquet(inc / "well_tests.csv", inc / "well_tests")
    (inc / "well_tests.csv").unlink()

    truth = [{"dir": ".", "dataset": name, "key": key, "columns": cols} for name, (key, cols) in BUILT_IN.items()]
    truth += [
        {"dir": "incoming", "dataset": "contractor_equipment_list", "key": "Tag No.", "columns": {
            "Tag No.": "tag", "Equipment Description": "description", "Mfr": "manufacturer", "Model No": "model",
            "S/N": "serial", "Install Date": "timestamp", "Dry Weight (kg)": "number"}},
        {"dir": "incoming", "dataset": "line_list", "key": "Line Number", "columns": {
            "Line Number": "other", "From": "from", "To": "to", "Size (in)": ["number", "code"],
            "Service": "description", "Pipe Spec": "code"}},
        {"dir": "incoming", "dataset": "well_tests", "key": "TEST_ID", "columns": {
            "TEST_ID": "other", "WELL": "well", "TEST_DT": "timestamp", "OIL_RATE_SM3D": "number",
            "WATER_CUT_PCT": "number", "GOR": "number", "TESTED_BY": "person"}},
        {"dir": "incoming", "dataset": "location_hierarchy", "key": "LOC_CODE", "columns": {
            "LOC_CODE": "location", "SUPERIOR_LOC": "parent", "LOC_TEXT": "description"}},
        {"dir": "incoming", "dataset": "inspection_points", "key": "Point ID", "columns": {
            "Point ID": "other", "Asset": "tag", "Lat": "latitude", "Long": "longitude",
            "Last Reading (mm)": "number", "Reading Date": "timestamp"}},
        {"dir": "incoming", "dataset": "export_0425", "key": "C1", "columns": {
            "C1": "tag", "C2": "description", "C3": "person", "C4": "code", "C5": "unit"}},
    ]
    (out / "answer_keys").mkdir(exist_ok=True)
    (out / "answer_keys" / "schema.json").write_text(json.dumps(truth, indent=1))
    return {name: len(rows) for name, rows in tables.items()}
