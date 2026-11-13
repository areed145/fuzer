"""LAS depth logs (#901): a well log as a logging contractor hands it over, in the Canadian Well Logging Society's
LAS 2.0 text format. Sections start with `~`: version (~V), well (~W), curves (~C), parameters (~P), other (~O) and
the ASCII log data (~A), one row per depth with a value per curve. Each header line is `MNEM.UNIT  DATA : DESCRIPTION`.

    read(path)   {version, well: {mnemonic: value}, curves: [{mnemonic, unit, description}], depth_unit,
                  rows: [{"md_m": depth in metres, <curve>: value or None}]}

Depth reads in metres or feet; a null value (~W NULL, -999.25 by default) is left out as None. Wrapped data
(VERS 1.2 or WRAP YES) and LAS 3.0 are refused with a message rather than guessed. Standard library only.
"""

from pathlib import Path

FEET = 0.3048
_DEPTH_UNITS = {"m": 1.0, "metre": 1.0, "meter": 1.0, "metres": 1.0, "meters": 1.0,
                "f": FEET, "ft": FEET, "feet": FEET, "usft": 1200 / 3937}
DEPTH = ("DEPT", "DEPTH", "MD")


class LasError(ValueError):
    """A LAS file this reader won't read, with why."""


def _line(text: str) -> dict:
    """One header line: `MNEM.UNIT  DATA : DESCRIPTION` (the unit runs from the dot to the first space)."""
    if "." not in text:
        raise LasError(f"not a LAS header line (no '.' after the mnemonic): {text.strip()!r}")
    mnem, rest = text.split(".", 1)
    unit, _, rest = rest.partition(" ") if not rest.startswith(" ") else ("", "", rest)
    data, _, desc = rest.rpartition(":") if ":" in rest else (rest, "", "")
    return {"mnemonic": mnem.strip(), "unit": unit.strip(), "data": data.strip(), "description": desc.strip()}


def read(path: Path) -> dict:
    sections: dict[str, list[str]] = {}
    current = None
    for raw in Path(path).read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("~"):
            current = line[1:2].upper()
            sections.setdefault(current, [])
            continue
        if current is None:
            raise LasError(f"{path}: text before the first ~ section")
        sections[current].append(raw)

    version = {(h := _line(x))["mnemonic"].upper(): h["data"] for x in sections.get("V", [])}
    vers = version.get("VERS", "")
    if not vers.startswith("2"):
        raise LasError(f"{path}: LAS version {vers or 'unknown'}; only LAS 2.0 is read")
    if version.get("WRAP", "NO").upper().startswith("Y"):
        raise LasError(f"{path}: wrapped data (WRAP YES) is not read; ask for the file unwrapped")

    well = {h["mnemonic"].upper(): h["data"] for h in map(_line, sections.get("W", []))}
    curves = [{k: h[k] for k in ("mnemonic", "unit", "description")} for h in map(_line, sections.get("C", []))]
    if not curves:
        raise LasError(f"{path}: no ~C curve section")
    if curves[0]["mnemonic"].upper() not in DEPTH:
        raise LasError(f"{path}: the first curve is {curves[0]['mnemonic']}, not a depth ({', '.join(DEPTH)})")
    unit = curves[0]["unit"] or next((h["unit"] for h in map(_line, sections.get("W", []))
                                      if h["mnemonic"].upper() == "STRT"), "")
    if unit.lower() not in _DEPTH_UNITS:
        raise LasError(f"{path}: depth in {unit!r}: not a unit read here (m or ft)")
    scale = _DEPTH_UNITS[unit.lower()]
    try:
        null = float(well.get("NULL") or -999.25)
    except ValueError:
        raise LasError(f"{path}: NULL {well['NULL']!r} isn't a number") from None

    names = [c["mnemonic"] for c in curves]
    values = [v for row in sections.get("A", []) for v in row.split()]
    if len(values) % len(names):
        raise LasError(f"{path}: {len(values)} values in ~A don't divide into rows of {len(names)} curves")
    rows = []
    for i in range(0, len(values), len(names)):
        try:
            got = [float(v) for v in values[i:i + len(names)]]
        except ValueError as e:
            raise LasError(f"{path}: a value in ~A isn't a number ({e})") from None
        if got[0] == null:
            continue
        rows.append({"md_m": round(got[0] * scale, 3),
                     **{n: (None if v == null else v) for n, v in zip(names[1:], got[1:])}})
    return {"version": vers, "well": well, "curves": curves, "depth_unit": unit, "rows": rows}
