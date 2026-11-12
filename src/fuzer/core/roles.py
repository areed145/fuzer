"""Measurement roles (P1.4): what a signal means for the asset it is on, from a controlled list.

Detectors and methods ask for "the suction pressure on this compressor", never for a tag name, so they run on any
facility whose points carry roles. A role is proposed for every historian point from what the systems say about it,
and a steward confirms the uncertain ones:

- its description (the historian's descriptor or the AF attribute name): "Compressor Suction Pressure"
- its instrument letters, read with the pack's tag grammar or ISA-style from the point name: PT, TT, PDT, VT
- its units: barg, degC, mm/s
- the class of the equipment it is on (P&ID position and the register's classification): a compressor, a vessel

`propose` weighs these into one role with a confidence and the evidence behind it. Nothing here knows a facility's
tag names: the words are instrument vocabulary and the classes are ISO 14224's.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    key: str
    label: str
    quantity: str  # pressure | temperature | flow | level | vibration | displacement | position | speed | power | ...


_ROLES = [
    Role("suction_pressure", "Suction pressure", "pressure"),
    Role("discharge_pressure", "Discharge pressure", "pressure"),
    Role("interstage_pressure", "Interstage pressure", "pressure"),
    Role("differential_pressure", "Differential pressure", "pressure"),
    Role("lube_oil_pressure", "Lube oil pressure", "pressure"),
    Role("wellhead_pressure", "Wellhead pressure", "pressure"),
    Role("casing_pressure", "Casing pressure", "pressure"),
    Role("pressure", "Process pressure", "pressure"),
    Role("suction_temperature", "Suction temperature", "temperature"),
    Role("discharge_temperature", "Discharge temperature", "temperature"),
    Role("cylinder_discharge_temperature", "Cylinder discharge temperature", "temperature"),
    Role("exhaust_temperature", "Exhaust temperature", "temperature"),
    Role("bearing_temperature", "Bearing temperature", "temperature"),
    Role("lube_oil_temperature", "Lube oil temperature", "temperature"),
    Role("outlet_temperature", "Outlet temperature", "temperature"),
    Role("wellhead_temperature", "Wellhead temperature", "temperature"),
    Role("ambient_temperature", "Ambient air temperature", "temperature"),
    Role("temperature", "Process temperature", "temperature"),
    Role("vibration", "Casing vibration", "vibration"),
    Role("shaft_vibration", "Shaft vibration", "displacement"),
    Role("axial_position", "Axial position", "displacement"),
    Role("suction_flow", "Suction flow", "flow"),
    Role("fuel_flow", "Fuel flow", "flow"),
    Role("seal_vent_flow", "Seal gas vent flow", "flow"),
    Role("gas_lift_rate", "Gas lift injection rate", "flow"),
    Role("flow", "Process flow", "flow"),
    Role("level", "Level", "level"),
    Role("speed", "Speed", "speed"),
    Role("power", "Power", "power"),
    Role("running_status", "Running status", "state"),
    Role("anti_surge_valve_position", "Anti-surge valve position", "position"),
    Role("choke_position", "Choke position", "position"),
    Role("valve_position", "Valve position", "position"),
    Role("composition", "Composition or quality", "composition"),
]
ROLES: dict[str, Role] = {r.key: r for r in _ROLES}
# The generic role per quantity: what a point gets when nothing says more.
GENERIC = {"pressure": "pressure", "temperature": "temperature", "flow": "flow", "level": "level",
           "vibration": "vibration", "displacement": "shaft_vibration", "position": "valve_position",
           "speed": "speed", "power": "power", "composition": "composition", "state": "running_status"}
# A role the running status of a machine can be read from, best first (the detectors' "running status").
RUNNING = ("running_status", "speed", "power")

# ISA first letters (with a differential modifier) and what they measure.
LETTERS = {"PD": "pressure", "P": "pressure", "T": "temperature", "F": "flow", "L": "level", "V": "vibration",
           "S": "speed", "J": "power", "A": "composition", "Z": "position", "X": "position", "H": "position"}

_UNITS = [
    (r"^(barg?|bara|psi[ag]?|kpa[ag]?|mpa[ag]?|kg/cm2g?|mbar|inh2o)$", "pressure"),
    (r"^(degc|degf|°c|°f|c|f|k)$", "temperature"),
    (r"^(mm/s|in/s|ips|g)$", "vibration"),
    (r"^(um|µm|mil|mils|mm)$", "displacement"),
    (r"(m3|sm3|nm3|am3|scf|bbl|kg|t|lb)[a-z0-9]*/(s|min|h|hr|d|day)$|^(mmscfd|bpd|kbd)$", "flow"),
    (r"^(rpm|hz)$", "speed"),
    (r"^(w|kw|mw|hp)$", "power"),
    (r"^(mol%|ppm|ppmv|ppb|mg/sm3|mg/nm3|mg/l|ph|%h2s|%co2|g/sm3)$", "composition"),
]

# Words in a description that name a role, most specific first; `quantity` limits the rule to points of that kind.
_WORDS: list[tuple[str, str, str]] = [  # (pattern, quantity, role)
    (r"differential|\bdp\b|delta p", "pressure", "differential_pressure"),
    (r"\blube|\boil (header|supply)", "pressure", "lube_oil_pressure"),
    (r"casing|annulus", "pressure", "casing_pressure"),
    (r"well ?head|tubing head|\bthp\b|\bwhp\b", "pressure", "wellhead_pressure"),
    (r"interstage", "pressure", "interstage_pressure"),
    (r"suction|inlet to (the )?compressor", "pressure", "suction_pressure"),
    (r"discharge", "pressure", "discharge_pressure"),
    (r"ambient|air temperature", "temperature", "ambient_temperature"),
    (r"well ?head|tubing head|\bwht\b", "temperature", "wellhead_temperature"),
    (r"bearing|journal|thrust", "temperature", "bearing_temperature"),
    (r"\blube", "temperature", "lube_oil_temperature"),
    (r"exhaust|thermocouple", "temperature", "exhaust_temperature"),
    (r"cylinder.*discharge|discharge.*cylinder", "temperature", "cylinder_discharge_temperature"),
    (r"suction", "temperature", "suction_temperature"),
    (r"discharge", "temperature", "discharge_temperature"),
    (r"outlet|supply|after-?cooler|condenser", "temperature", "outlet_temperature"),
    (r"fuel", "flow", "fuel_flow"),
    (r"seal.*vent|vent.*seal", "flow", "seal_vent_flow"),
    (r"gas lift injection|lift gas injection|injection rate", "flow", "gas_lift_rate"),
    (r"suction flow|compressor.*flow", "flow", "suction_flow"),
    (r"axial|thrust position", "displacement", "axial_position"),
    (r"shaft", "displacement", "shaft_vibration"),
    (r"anti-?surge|recycle valve", "position", "anti_surge_valve_position"),
    (r"choke", "position", "choke_position"),
    (r"running|run status|\bon/off\b", "state", "running_status"),
]
# Words that say what a point measures when its letters and units are silent.
_QUANTITY_WORDS = [
    (r"differential pressure|pressure", "pressure"), (r"temperature|thermocouple", "temperature"),
    (r"vibration", "vibration"), (r"\blevel\b", "level"), (r"\bflow|rate\b|meter\b|circulation", "flow"),
    (r"speed", "speed"), (r"power", "power"), (r"position|valve|choke|esdv", "position"),
    (r"content|\bco2\b|\bh2s\b|analy[sz]er|composition|dew ?point|moisture|water in", "composition"),
]
# What a generic reading on equipment of a class usually is (ISO 14224 class codes).
_ON_CLASS = {("temperature", "HE"): "outlet_temperature", ("pressure", "WELL"): "wellhead_pressure",
             ("temperature", "WELL"): "wellhead_temperature", ("flow", "CO"): "suction_flow"}

# Roles only a machine has: a suction or discharge reading hung on a vessel is the vessel's (a suction scrubber's
# pressure). ISO 14224 rotating equipment: compressors, pumps, gas and steam turbines, turboexpanders, engines,
# motors, generators, blowers and fans.
MACHINE_CLASSES = {"CO", "PU", "GT", "ST", "TE", "CE", "EM", "EG", "BL"}
_MACHINE_ROLES = {"suction_pressure", "discharge_pressure", "interstage_pressure", "suction_temperature",
                  "discharge_temperature", "cylinder_discharge_temperature", "suction_flow"}

CONFIDENT = 0.8  # below this a proposal goes to a steward


def quantity_of_units(uom: str | None) -> str | None:
    u = (uom or "").strip().lower().replace(" ", "")
    if not u:
        return None
    if u == "%":
        return "percent"  # a level, a valve position or a load: says only that it isn't an absolute
    return next((q for pat, q in _UNITS if re.search(pat, u)), None)


def quantity_of_letters(letters: str | None) -> str | None:
    s = (letters or "").upper()
    if s.startswith("PD"):
        return "pressure"
    return LETTERS.get(s[:1]) if s else None


_ISA = re.compile(r"(?<![A-Z])([A-Z]{2,4})[-_.]?\d{3,5}")


def letters_in(name: str | None, grammar=None) -> str | None:
    """The instrument letters in a point or tag name: the pack's tag grammar first, else ISA-style (PT-1001)."""
    if not name:
        return None
    if grammar is not None and (found := grammar.find_all(name)):
        func = found[0].func
        if quantity_of_letters(func) and len(func) >= 2:
            return func
    m = _ISA.search(name.upper())
    return m.group(1) if m and quantity_of_letters(m.group(1)) else None


def propose(description: str | None, letters: str | None, uom: str | None, classes=()) -> dict:
    """One point's role: {role, confidence (0-1), basis: [what said so]}. `classes` are the ISO 14224 class codes of
    the equipment the point is on ("CO" a compressor, "VE" a vessel, "WELL" a well)."""
    text = (description or "").lower()
    basis: list[str] = []
    by_letters, by_units = quantity_of_letters(letters), quantity_of_units(uom)
    by_words = next((q for pat, q in _QUANTITY_WORDS if re.search(pat, text)), None)
    if by_units == "percent":  # % is a level, a position or a load: the letters or words say which
        by_units = None
    if by_units == "displacement" and "vibration" in (by_letters, by_words) or by_units == "displacement" and \
            "position" in (by_letters, by_words):  # a VT in um or a ZT in mm: a proximity probe, not a valve
        by_letters = by_words = "displacement"
    votes = [q for q in (by_letters, by_units, by_words) if q]
    if by_letters == "position" and by_words == "flow":  # a meter on a valve tag reads as the valve
        by_words = None
    quantity = max(set(votes), key=lambda q: (votes.count(q), q == by_letters, q == by_units)) if votes else None
    if by_letters:
        basis.append(f"letters {letters}: {by_letters}")
    if by_units:
        basis.append(f"units {uom}: {by_units}")
    if by_words:
        basis.append(f"description: {by_words}")
    if quantity is None:
        return {"role": None, "confidence": 0.0, "basis": basis or ["nothing says what it measures"]}
    agree = votes.count(quantity)
    conflict = len(set(votes)) > 1
    machine = not classes or bool(MACHINE_CLASSES & set(classes))
    role = next((r for pat, q, r in _WORDS if q == quantity and re.search(pat, text)
                 and (machine or r not in _MACHINE_ROLES)), None)
    guessed = False
    if role:
        basis.append(f"description says {ROLES[role].label.lower()}")
    else:
        role = next((_ON_CLASS[(quantity, c)] for c in classes if (quantity, c) in _ON_CLASS), None)
        if role:
            guessed = True
            basis.append(f"on {'/'.join(classes)}: usually {ROLES[role].label.lower()}")
        else:
            role = GENERIC[quantity]
    confidence = 0.5 + 0.2 * min(agree, 2) + (0.1 if role != GENERIC[quantity] or not conflict else 0.0)
    if guessed:  # what a reading on that class usually is, not what this one says: a steward confirms it
        confidence = min(confidence, CONFIDENT - 0.1)
    if conflict:
        confidence -= 0.3
    if not text:
        confidence -= 0.2
    return {"role": role, "confidence": round(max(0.0, min(confidence, 1.0)), 2), "basis": basis}
