"""Asset packs: everything that differs between assets, loaded from YAML."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .tags import PatternWellGrammar, TagGrammar, WellGrammar

PACKS_DIR = Path(__file__).parent / "packs"


@dataclass
class Pack:
    name: str
    facility: str
    tags: TagGrammar
    wells: WellGrammar | PatternWellGrammar
    aliases: dict[str, tuple[str, str]]
    matching: dict
    survivorship: dict
    classification: dict
    maintenance_codes: dict
    downtime_reasons: dict

    def rule(self, field: str) -> dict:
        return {**self.survivorship["default"], **self.survivorship.get("fields", {}).get(field, {})}


def load_pack(name: str | None = None) -> Pack:
    """An asset pack by name; FUZER_PACK (default offshore_demo) when no name is given."""
    name = name or os.environ.get("FUZER_PACK", "offshore_demo")
    raw = yaml.safe_load((PACKS_DIR / f"{name}.yaml").read_text())
    tg = raw["tag_grammar"]
    return Pack(
        name=raw["name"],
        facility=raw["facility"],
        tags=TagGrammar(tg["function_codes"], tg["area_digits"], tuple(tg["loop_digits"])),
        wells=(PatternWellGrammar(wg["pattern"], wg["key"]) if "pattern" in (wg := raw["well_grammar"])
               else WellGrammar(wg["platforms"])),
        aliases={phrase: tuple(target) for phrase, target in raw.get("aliases", {}).items()},
        matching=raw["matching"],
        survivorship=raw["survivorship"],
        classification=raw.get("classification", {}),
        maintenance_codes=raw.get("maintenance_codes", {}),
        downtime_reasons=raw.get("downtime_reasons", {}),
    )
