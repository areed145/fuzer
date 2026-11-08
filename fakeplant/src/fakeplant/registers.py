"""Legacy equipment registers beside SAP: the same physical items, recorded again by other systems.

- JDE:    the register from before the SAP migration (items installed before 2015). The tag sits only in
          the description ("2ND STAGE COMPRESSOR - 20-K2200B"), serials are bare numbers and often
          missing, and makers are sometimes misspelt or left blank.
- Maximo: the maintenance contractor's register of what is installed now. Locations like
          "OKAN/20/K-2200B" (the area read from the text), serials as "sn-70123", makers in capitals.

Most records carry a serial and merge with their SAP item on it. Some planted cases make every other
matching path run (see PLANTED): a record with no serial that scores high enough to merge on tag, maker
and description; one with no serial and no maker that waits in the review queue; one at a tag with two
SAP items (a replaced choke) that is ambiguous; one with the right serial but another maker (a value
conflict); and a scrapped item at a live tag with its own serial, which must never merge.

answer_keys/matches.json names which SAP item each record really is. The registers draw from their own
random stream, so every other generated file is unchanged.
"""

import json
import random
import re
from pathlib import Path

from .common import _write_csv

MIGRATION = "2015-01-01"  # JDE holds what was installed before SAP replaced it
TYPOS = {"Flowserve": "Flowserv", "Solar Turbines": "Solar Turbine", "Baker Hughes": "Baker-Hughes",
         "Siemens Energy": "Siemens", "Alfa Laval": "AlfaLaval", "Kelvion": "Kelvion GmbH", "Kanfa": "Kanfa AS",
         "Sembcorp": "Sembcorp Marine", "Master Flo": "MasterFlo", "Cameron": "Cameron Intl", "Sulzer": "Sulzer Pumps",
         "Mokveld": "Mokveld Valves"}
FLOC = re.compile(r"^[A-Z]+-(\d{2})-([A-Z]+)(\d+)([A-Z]?)$")

# SAP tag (area-func+loop+suffix as in the FLOC) -> how its JDE record is planted.
PLANTED = {
    "20-K2200A": "score",      # no serial, misspelt maker: merges on tag, maker and description
    "20-K2100A": "review",     # no serial, no maker: scores in the middle, so a person decides
    "10-P1001B": "conflict",   # the right serial, another maker: merges, and the maker goes to review
    "72-HCV1002": "ambiguous",  # no serial at OK-J12-02's choke, replaced in 2025: two SAP items fit
}
SCRAPPED = {"tag": "10-P1001C", "serial": "69001", "manufacturer": "Sulzer", "model": "SUL-400"}


def _jde_serial(serial: str) -> str:
    return serial.removeprefix("SN") if serial.startswith("SN") else f"{serial[:2]}-{serial[2:]}"


def generate(out: Path, equipment: list[dict], field: str, seed: int = 31) -> dict:
    """Write jde_assets.csv, maximo_assets.csv and answer_keys/matches.json from the SAP register."""
    rng = random.Random(seed)
    out = Path(out)
    jde, maximo, truth = [], [], []
    tags = {}  # SAP tag -> its items, oldest first

    for e in equipment:
        m = FLOC.match(e["floc"])
        if not m:
            continue
        area, func, loop, suffix = m.groups()
        tag = f"{area}-{func}{loop}{suffix}"
        tags.setdefault(tag, []).append(e)
        plant = PLANTED.get(tag)
        # The ambiguous case is the old choke only (JDE predates its replacement).
        if plant and (plant != "ambiguous" or e["valid_to"]):
            how = plant
        elif e["valid_from"] < MIGRATION and rng.random() < 0.7:
            how = "serial" if rng.random() < 0.75 else "score" if rng.random() < 0.8 else "review"
        else:
            how = None

        if how:
            serial = _jde_serial(e["serial"]) if how in ("serial", "conflict") else ""
            if how == "conflict":
                mfr = "Sulzer" if e["manufacturer"] != "Sulzer" else "Flowserve"
            elif how == "review":
                mfr = ""
            elif how == "score" or rng.random() < 0.3:
                mfr = TYPOS.get(e["manufacturer"], e["manufacturer"])
            else:
                mfr = e["manufacturer"]
            tag_text = f"{area}-{func}{loop}" + (f"-{suffix}" if suffix and rng.random() < 0.4 else suffix)
            jde.append({"asset_number": str(500000 + len(jde)), "description": f"{e['description'].upper()} - {tag_text}",
                        "manufacturer": mfr, "model": e["model"], "serial": serial})
            truth.append({"source": "jde", "key": jde[-1]["asset_number"], "case": how,
                          "same_as": {"source": "sap", "key": e["equnr"]}})

        if not e["valid_to"] and (plant or rng.random() < 0.5):
            maximo.append({"assetnum": f"MX-{len(maximo) + 1:05d}", "description": e["description"],
                           "manufacturer": e["manufacturer"].upper(), "serial": e["serial"].lower().replace("sn", "sn-"),
                           "location": f"{field}/{area}/{func}-{loop}{suffix}"})
            truth.append({"source": "maximo", "key": maximo[-1]["assetnum"], "case": "serial",
                          "same_as": {"source": "sap", "key": e["equnr"]}})

    # A pump scrapped before the migration: JDE still lists it at the tag its replacement holds now.
    old = tags[SCRAPPED["tag"]][0]
    jde.append({"asset_number": str(500000 + len(jde)), "description": f"{old['description'].upper()} - {SCRAPPED['tag']}",
                "manufacturer": SCRAPPED["manufacturer"], "model": SCRAPPED["model"], "serial": SCRAPPED["serial"]})
    scrapped = jde[-1]["asset_number"]
    assert not any(e["serial"].removeprefix("SN") == SCRAPPED["serial"] for e in equipment)

    _write_csv(out / "jde_assets.csv", jde)
    _write_csv(out / "maximo_assets.csv", maximo)
    (out / "answer_keys").mkdir(exist_ok=True)
    (out / "answer_keys" / "matches.json").write_text(json.dumps(truth, indent=1))
    return {"jde": len(jde), "maximo": len(maximo), "match_pairs": len(truth), "scrapped": scrapped}
