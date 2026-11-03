"""Named entity linking: find tags, wells and known phrases in free text."""

import re
from dataclasses import dataclass

from .pack import Pack


@dataclass
class Mention:
    kind: str  # tag, tag_noarea, well, or an alias target kind
    key: str
    start: int
    end: int
    text: str
    method: str
    confidence: float


def find_mentions(text: str, pack: Pack, default_area: str | None = None) -> list[Mention]:
    upper = text.upper()
    found = []
    for phrase, (kind, key) in pack.aliases.items():
        for m in re.finditer(rf"\b{re.escape(phrase.upper())}\b", upper):
            found.append(Mention(kind, key, m.start(), m.end(), text[m.start():m.end()], "alias", 0.8))
    for t in pack.tags.find_all(text):
        key = t.key(default_area)
        kind, key = ("tag", key) if key else ("tag_noarea", t.short_key)
        conf = 0.95 if t.area else 0.85
        found.append(Mention(kind, key, t.start, t.end, text[t.start:t.end], "tag_grammar", conf))
    for w in pack.wells.find_all(text):
        found.append(Mention("well", w.key, w.start, w.end, text[w.start:w.end], "well_grammar", 0.9))

    # Keep the longest mention where spans overlap ("test separator" beats "test sep").
    found.sort(key=lambda m: (m.start, -(m.end - m.start)))
    out, last_end = [], -1
    for m in found:
        if m.start >= last_end:
            out.append(m)
            last_end = m.end
    return out
