"""Tag and well name grammars.

Each source writes the same tag differently (20-PT-1001A, 20PT1001A,
OSB.20PT1001A.PV, "PT" over "1001A" on a P&ID). A grammar finds tags in any of
these styles and turns them into one canonical key.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TagMatch:
    area: str | None
    func: str
    loop: str
    suffix: str
    start: int
    end: int

    @property
    def short_key(self) -> str:
        return f"{self.func}-{self.loop}{self.suffix}"

    def key(self, default_area: str | None = None) -> str | None:
        area = self.area or default_area
        return f"{area}-{self.short_key}" if area else None


class TagGrammar:
    """ISA-style tags: optional area, function letters, loop number, one-letter suffix."""

    def __init__(self, function_codes, area_digits=2, loop_digits=(3, 5)):
        funcs = "|".join(sorted(map(re.escape, function_codes), key=len, reverse=True))
        lo, hi = loop_digits
        self._re = re.compile(
            r"(?<![A-Z0-9])"
            rf"(?:(?P<area>\d{{{area_digits}}})[-_.\s]?)?"
            rf"(?P<func>{funcs})[-_.\s]?"
            rf"(?P<loop>\d{{{lo},{hi}}})"
            r"(?:[- ]?(?P<suffix>[A-Z]))?"
            r"(?![A-Z0-9])"
        )

    def find_all(self, text: str) -> list[TagMatch]:
        return [
            TagMatch(m["area"], m["func"], m["loop"], m["suffix"] or "", m.start(), m.end())
            for m in self._re.finditer(text.upper())
        ]

    def parse(self, text: str, default_area: str | None = None) -> str | None:
        found = self.find_all(text)
        return found[0].key(default_area) if found else None


@dataclass(frozen=True)
class WellMatch:
    platform: str
    number: int
    sidetrack: int
    start: int
    end: int

    @property
    def key(self) -> str:
        base = f"{self.platform}-{self.number:02d}"
        return f"{base} ST{self.sidetrack}" if self.sidetrack else base


class WellGrammar:
    """Platform letter + slot number, with optional sidetrack (A-07 ST1 or A07A)."""

    def __init__(self, platforms):
        plats = "|".join(map(re.escape, platforms))
        self._re = re.compile(
            r"(?<![A-Z0-9])"
            rf"(?P<plat>{plats})[-_\s]?(?P<num>\d{{1,2}})"
            r"(?:[-_\s]?ST[-_\s]?(?P<st>\d)|(?P<stl>[A-D]))?"
            r"(?![A-Z0-9])"
        )

    def find_all(self, text: str) -> list[WellMatch]:
        out = []
        for m in self._re.finditer(text.upper()):
            sidetrack = int(m["st"]) if m["st"] else (ord(m["stl"]) - ord("A") + 1 if m["stl"] else 0)
            out.append(WellMatch(m["plat"], int(m["num"]), sidetrack, m.start(), m.end()))
        return out

    def parse(self, text: str) -> str | None:
        found = self.find_all(text)
        return found[0].key if found else None


@dataclass(frozen=True)
class PatternWellMatch:
    key: str
    start: int
    end: int


class PatternWellGrammar:
    """Wells named by an asset-specific pattern (onshore lease / pad / well numbers, API numbers, ...).
    `pattern` is a regular expression with named groups; `key` formats them into the canonical name,
    e.g. pattern 'RB[-\\s]?(?P<pad>\\d{2})[-\\s]?(?P<num>\\d{2})(?P<h>H?)' and key 'RB {pad}-{num}{h}'."""

    def __init__(self, pattern: str, key: str):
        self._re = re.compile(rf"(?<![A-Z0-9])(?:{pattern})(?![A-Z0-9])")
        self._key = key

    def find_all(self, text: str) -> list[PatternWellMatch]:
        return [PatternWellMatch(self._key.format(**{k: v or "" for k, v in m.groupdict().items()}), m.start(), m.end())
                for m in self._re.finditer(text.upper())]

    def parse(self, text: str) -> str | None:
        found = self.find_all(text)
        return found[0].key if found else None
