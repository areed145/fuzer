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
    """ISA-style tags: optional area, function letters, loop number, one-letter suffix.

    The pack defines the shape (#376): `area` is a regular expression for an area code (default
    `area_digits` digits), `loop_digits` the loop number's length, and `pattern`, when given, replaces the
    whole tag expression. A pattern names the groups `func` and `loop` (and optionally `area` and
    `suffix`) and may use the placeholders {area}, {funcs} and {loop} for the parts above."""

    DEFAULT_PATTERN = (r"(?:(?P<area>{area})[-_.\s]?)?(?P<func>{funcs})[-_.\s]?(?P<loop>{loop})"
                       r"(?:[- ]?(?P<suffix>[A-Z]))?")

    def __init__(self, function_codes, area_digits=2, loop_digits=(3, 5), *, area: str | None = None,
                 pattern: str | None = None):
        funcs = "|".join(sorted(map(re.escape, function_codes), key=len, reverse=True))
        lo, hi = loop_digits
        self.area = area or rf"\d{{{area_digits}}}"
        body = (pattern or self.DEFAULT_PATTERN).replace("{area}", f"(?:{self.area})").replace(
            "{funcs}", f"(?:{funcs})").replace("{loop}", rf"\d{{{lo},{hi}}}")
        self._re = re.compile(rf"(?<![A-Z0-9])(?:{body})(?![A-Z0-9])")
        missing = {"func", "loop"} - set(self._re.groupindex)
        if missing:
            raise ValueError(f"tag pattern needs the groups {sorted(missing)}")
        self._area_re = re.compile(rf"\b({self.area})\b")
        self._area_full = re.compile(self.area)

    def find_all(self, text: str) -> list[TagMatch]:
        out = []
        for m in self._re.finditer(text.upper()):
            g = m.groupdict()
            out.append(TagMatch(g.get("area"), g["func"], g["loop"], g.get("suffix") or "", m.start(), m.end()))
        return out

    def parse(self, text: str, default_area: str | None = None) -> str | None:
        found = self.find_all(text)
        return found[0].key(default_area) if found else None

    def area_in(self, text: str | None) -> str | None:
        """The first area code standing on its own in `text` ("Train 20 inlet" -> "20")."""
        m = self._area_re.search((text or "").upper())
        return m.group(1) if m else None

    def is_area(self, code: str) -> bool:
        return self._area_full.fullmatch(code or "") is not None


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

    prefix = ""  # names start with the platform letter: nothing to leave off

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
    """Wells named by an asset-specific pattern.
    `pattern` is a regular expression with named groups; `key` formats them into the canonical name,
    e.g. pattern 'RB[-\\s]?(?P<pad>\\d{2})[-\\s]?(?P<num>\\d{2})(?P<h>H?)' and key 'RB {pad}-{num}{h}'."""

    def __init__(self, pattern: str, key: str):
        self._re = re.compile(rf"(?<![A-Z0-9])(?:{pattern})(?![A-Z0-9])")
        self._key = key
        self.prefix = key.split("{")[0]  # the canonical name's fixed start (OK- in OK-{site}-{num})

    def find_all(self, text: str) -> list[PatternWellMatch]:
        return [PatternWellMatch(self._key.format(**{k: v or "" for k, v in m.groupdict().items()}), m.start(), m.end())
                for m in self._re.finditer(text.upper())]

    def parse(self, text: str) -> str | None:
        found = self.find_all(text)
        return found[0].key if found else None


class NoWellGrammar:
    """An asset with no wells (a pipeline, a terminal): its pack declares no well grammar, and nothing parses as a
    well."""

    prefix = ""

    def find_all(self, text: str) -> list[PatternWellMatch]:
        return []

    def parse(self, text: str) -> str | None:
        return None
