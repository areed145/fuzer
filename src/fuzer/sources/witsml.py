"""WITSML trajectories (#61, #901): the directional survey a drilling contractor or a well data store hands over as
WITSML XML. Both generations are read:

- 1.4.1: `trajectorys` holding `trajectory`, each with its `trajectoryStation`s and the well in `nameWell`;
- 2.x (Energistics ML, namespace .../energyml/data/witsmlv2): `Trajectory` objects, each with its
  `TrajectoryStation`s and the well in the `Wellbore` reference's `Title`. Element names are the 1.4.1 ones with a
  capital first letter (`Md`, `Incl`, `Azi`, `DTimStn`, ...), so both are matched without regard to case.

Each station becomes the same record a survey CSV gives (sources/../mappings/well_survey.yaml): well, measured depth
in metres, inclination and azimuth in degrees, survey date and tool. Standard library only.
"""

import math
import xml.etree.ElementTree as ET
from pathlib import Path

FEET = 0.3048
_LENGTH = {"m": 1.0, "ft": FEET, "usft": 1200 / 3937}
_ANGLE = {"dega": 1.0, "deg": 1.0, "rad": 180 / math.pi}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()  # 1.4.1 names are 2.x names with a lower-case first letter


def _child(el, name: str):
    return next((c for c in el if _local(c.tag) == name.lower()), None)


def _text(el, name: str) -> str:
    c = _child(el, name) if el is not None else None
    return (c.text or "").strip() if c is not None else ""


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
        v2 = "witsmlv2" in traj.tag or traj.tag.rsplit("}", 1)[-1][:1].isupper()
        if v2:  # the well is the Wellbore reference's title; the trajectory is named by its uuid
            well = _text(_child(traj, "Wellbore"), "Title")
            uid = traj.get("uuid") or _text(_child(traj, "Citation"), "Title") or well
            fmt = f"witsml {traj.get('schemaVersion') or '2.x'}"
        else:
            well = (traj.get("nameWell") or _text(traj, "nameWell") or traj.get("uidWell") or "").strip()
            uid = traj.get("uid") or traj.get("uidWellbore") or well
            fmt = "witsml 1.4.1"
        for st in (e for e in traj.iter() if _local(e.tag) == "trajectorystation"):
            md = _measure(st, "md", _LENGTH)
            if md is None:
                continue
            yield well, f"{uid}-{st.get('uid') or md}", {
                "well": well, "md_m": md, "inclination_deg": _measure(st, "incl", _ANGLE),
                "azimuth_deg": _measure(st, "azi", _ANGLE), "survey_date": _text(st, "dTimStn")[:10] or None,
                "tool": _text(st, "typeSurveyTool") or _text(st, "typeTrajStation") or None,
                "format": fmt, "trajectory": uid}
