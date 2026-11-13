"""WITSML trajectories (#61): the directional survey a drilling contractor or a well data store hands over as
WITSML 1.4.1 XML (`trajectorys` holding `trajectory`, each with its `trajectoryStation`s). Each station becomes the
same record a survey CSV gives (sources/../mappings/well_survey.yaml): well, measured depth in metres, inclination and
azimuth in degrees, survey date and tool. Standard library only; WITSML 2.x (Energistics ML) is not read yet.
"""

import math
import xml.etree.ElementTree as ET
from pathlib import Path

FEET = 0.3048
_LENGTH = {"m": 1.0, "ft": FEET, "usft": 1200 / 3937}
_ANGLE = {"dega": 1.0, "deg": 1.0, "rad": 180 / math.pi}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name: str):
    return next((c for c in el if _local(c.tag) == name), None)


def _measure(el, name: str, units: dict[str, float]) -> float | None:
    c = _child(el, name)
    if c is None or not (c.text or "").strip():
        return None
    uom = (c.get("uom") or next(iter(units))).strip()
    if uom not in units:
        raise ValueError(f"WITSML {name} in {uom!r}: not a unit read here ({', '.join(units)})")
    return round(float(c.text) * units[uom], 2 if units is _LENGTH else 3)  # centimetres; a thousandth of a degree


def read(path: Path):
    """(well, station key, payload) for each trajectory station, in the order the file gives them."""
    root = ET.parse(path).getroot()
    for traj in (e for e in root.iter() if _local(e.tag) == "trajectory"):
        well = (traj.get("nameWell") or (_child(traj, "nameWell").text if _child(traj, "nameWell") is not None
                                        else "") or traj.get("uidWell") or "").strip()
        uid = traj.get("uid") or traj.get("uidWellbore") or well
        for st in (e for e in traj if _local(e.tag) == "trajectoryStation"):
            md = _measure(st, "md", _LENGTH)
            if md is None:
                continue
            when = _child(st, "dTimStn")
            kind = _child(st, "typeTrajStation")
            tool = _child(st, "typeSurveyTool")
            yield well, f"{uid}-{st.get('uid') or md}", {
                "well": well, "md_m": md, "inclination_deg": _measure(st, "incl", _ANGLE),
                "azimuth_deg": _measure(st, "azi", _ANGLE), "survey_date": (when.text or "")[:10] if when is not None else None,
                "tool": (tool.text if tool is not None else None) or (kind.text if kind is not None else None),
                "format": "witsml 1.4.1", "trajectory": uid}
