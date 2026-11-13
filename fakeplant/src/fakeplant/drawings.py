"""Drawings for a hand-written bundle's smart P&IDs (Kestrel): one clean sheet per entry in pid_sheets.json.

A smart P&ID already carries its tags and piping, so Fuzer never sends it to Claude; the drawing is what the
entity page shows, with every tag marked where it sits. `render` draws each smart sheet with `pid.draw` (no scan
damage, so it's deterministic), saves it beside the exports as pid/<sheet>.png, and writes each sheet's `image`
and tag `positions` back into pid_sheets.json (or, for a sheet kept as DEXPI XML, into its file). The drawings and positions are committed with the bundle, so a
deploy ingests them like any other export; run it again after editing a sheet:

    uv run fakeplant drawings fakeplant/bundles/kestrel
"""

import csv
import json
import random
from pathlib import Path

from fuzer.core import bundles
from fuzer.sources import dexpi as fuzer_dexpi

from . import dexpi, pid
from .common import Tag


def _tag(pack, text: str) -> Tag:
    """A canonical tag (area-FUNC-loop[suffix]) as the drawing's tag."""
    key = pack.tags.parse(text)
    if key is None:
        raise ValueError(f"{text!r} isn't a tag in this bundle's grammar")
    area, func, rest = key.split("-", 2)
    loop = rest.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return Tag(area, func, int(loop), rest[len(loop):])


def render(bundle: Path) -> dict:
    """Draw every smart sheet in the bundle's pid_sheets.json and record its image and tag positions."""
    b = bundles.read(bundle)
    pack, exports = b["pack"], Path(b["config"]["data"])
    path = exports / "pid_sheets.json"
    sheets = json.loads(path.read_text())
    titles = {}
    if (exports / "sap_equipment.csv").is_file():
        for r in csv.DictReader((exports / "sap_equipment.csv").open()):
            if key := pack.tags.parse(r["floc"]):
                titles[key] = r["description"]
    drawn = []
    for s in sheets:
        if s.get("format") != "smart":
            continue
        s["image"] = f"pid/{s['sheet']}.png"
        # a sheet kept as DEXPI XML (#858) has its tags and piping in the file, and gets its positions written back
        sheet = {**s, **fuzer_dexpi.read(exports / s["dexpi"], s.get("area"))} if s.get("dexpi") else s
        truth = pid.draw(exports / s["image"], sheet, [_tag(pack, t) for t in sheet["tags"]], titles, random.Random(0),
                         sheet.get("connections", []), scan=False, equipment_funcs=pid.PLANT_EQUIPMENT_FUNCS)
        positions = {x["key"]: [x["x"], x["y"]] for x in truth["tags"]}
        if s.get("dexpi"):
            (exports / s["dexpi"]).write_text(dexpi.sheet_xml({**sheet, "positions": positions}))
        else:
            s["positions"] = positions
        drawn.append(s["sheet"])
    path.write_text(json.dumps(sheets, indent=1) + "\n")
    return {"bundle": b["name"], "drawn": drawn}
