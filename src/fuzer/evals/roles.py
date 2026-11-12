"""The measurement roles eval (P1.4): the roles proposed for a fake plant's points against what its process model
says each point is.

A fake plant's `process_model.json` maps every point it simulates to a unit and a variable of that unit (a
compressor's `suction_pressure`, a well's `whp`); `VARIABLE_ROLE` turns those into roles, and a unit that only holds
one value (`controlled`) names its role in the model (`"role": "differential_pressure"`). The proposer never reads the
model, so this is a fair answer key.

Done when at least 90% of points get the right role, and the wrong ones are below the confidence a steward is asked
at, so they arrive as steward work instead of silently feeding a detector.
"""

import json
import re
from pathlib import Path

from ..core import roles as vocabulary

# A process model variable -> its role. `(unit type, variable)` where the unit's type changes the meaning.
VARIABLE_ROLE = {
    "suction_pressure": "suction_pressure", "discharge_pressure": "discharge_pressure",
    "interstage_pressure": "interstage_pressure", "lube_pressure": "lube_oil_pressure",
    "vessel_pressure": "pressure", "production_header_pressure": "pressure", "pressure": "pressure",
    "whp": "wellhead_pressure", "casp": "casing_pressure",
    "discharge_temperature": "discharge_temperature", "cylinder_temperature": "cylinder_discharge_temperature",
    "engine_cylinder": "exhaust_temperature", "exhaust_tc": "exhaust_temperature",
    "exhaust_temperature": "exhaust_temperature", "journal_temperature": "bearing_temperature",
    "thrust_temperature": "bearing_temperature", "bearing_temperature": "bearing_temperature",
    "lube_temperature": "lube_oil_temperature", "outlet_temperature": "outlet_temperature",
    "wht": "wellhead_temperature", "ambient": "ambient_temperature",
    "vibration": "vibration", "shaft_vibration": "shaft_vibration", "axial_position": "axial_position",
    "flow": "flow", ("compressor", "flow"): "suction_flow", "fuel_flow": "fuel_flow", "seal_vent": "seal_vent_flow",
    "glir": "gas_lift_rate", "gas_lift_supply": "flow", "qgas": "flow",
    "liquid_level": "level", "speed": "speed", "power": "power",
    "asv": "anti_surge_valve_position", "choke": "choke_position", "esdv": "valve_position",
    "position": "valve_position",
}


def answer_key(model: dict) -> dict[str, str]:
    """Point name -> its role, from a process model spec."""
    out = {}
    for point, (unit, var) in model["points"].items():
        u = model["units"].get(unit, {})
        base = re.sub(r"_\d+$", "", var)  # cylinder_temperature_3 -> cylinder_temperature
        role = u.get("role") if var == "value" else VARIABLE_ROLE.get((u.get("type"), var)) or VARIABLE_ROLE.get(base)
        if role:
            out[point] = role
    return out


def score(conn, data_dir) -> dict:
    """The roles on this database against the process model in `data_dir`: share right, the misses, and how many
    misses were confident (the ones a steward would not be asked about)."""
    path = Path(data_dir)
    key = answer_key(json.loads((path / "process_model.json" if path.is_dir() else path).read_text()))
    unknown = sorted(set(key.values()) - set(vocabulary.ROLES))
    if unknown:
        raise ValueError(f"the answer key names roles that aren't on the list: {unknown}")
    got = {r["point"]: r for r in conn.execute(
        """
        select l.label as point, r.role, r.confidence, r.basis from entity_label l
        left join measurement_role r on r.point_id = l.fuzer_id where l.label = any(%s)
        """, (list(key),))}
    misses = []
    for point, want in sorted(key.items()):
        r = got.get(point)
        if r is None:
            continue  # the model simulates a point the register doesn't hold
        if r["role"] != want:
            misses.append({"point": point, "want": want, "got": r["role"], "confidence": r["confidence"],
                           "basis": r["basis"]})
    n = sum(1 for p in key if p in got)
    confident = [m for m in misses if (m["confidence"] or 0) >= vocabulary.CONFIDENT]
    return {"points": n, "right": n - len(misses), "accuracy": round((n - len(misses)) / n, 3) if n else None,
            "misses": misses, "confident_misses": len(confident)}
