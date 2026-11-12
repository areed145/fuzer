"""The finding contract (P1.1): one record shape for anything detected, whoever detected it.

A finding is what a raiser hands to `raise_exception`: who saw it and how (`detector`, `origin`, `method`,
`standard`), when (`start`, `end`), how sure it is (`confidence`), what a person reads (`items`: labelled values with
their units), the checks before the work (`checks`), and the variables its process routes on (`data`, never shown).
The assets it is about and the value at stake travel with it on the exception (`refs`, `value_usd`), so `shown()`
returns them all as one record.

    from fuzer.core.findings import DETERMINISTIC, Finding, asset, at, fact

    raise_exception(conn, TYPE, key, title, by, refs=[...], value_usd=..., finding=Finding(
        "limits.iow", DETERMINISTIC, method="Time outside an integrity operating window", standard="API RP 584",
        start=first, end=last,
        items=[fact("Limit", 85.0, "barg"), fact("Peak", 91.2, "barg"), at("First outside", first),
               asset("Measured on", point_id)],
        data={"iow": iow, "level": level}))

Origins: `deterministic` (a rule or a calculation), `agentic` (an agent looked where rules cannot) and `external`
(another system's own detection or record). A raiser never writes evidence keys of its own: what people read goes
in `items` under the words they read it by, so one component renders every finding on the case, the task and the
phone. `external()` is the one place an outside system's own keys (a webhook body, an API call) become a finding.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from . import methods

DETERMINISTIC, AGENTIC, EXTERNAL = "deterministic", "agentic", "external"
ORIGINS = (DETERMINISTIC, AGENTIC, EXTERNAL)
KINDS = ("text", "number", "flag", "time", "asset", "page", "link")
VERSION = 1
KEY = "finding"  # where an exception's stored evidence holds the record
MAX_ITEMS = 12  # what fits a phone before it stops being read

_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_ID = re.compile(r"fz-[a-z_]+-[0-9a-f]{6,}")
_INTERNAL = ("id", "key", "type", "via")  # an outside sender's own bookkeeping, not something to read


class FindingError(ValueError):
    """A finding the contract refused: an unknown origin, an item with no label, a confidence outside 0 to 1."""


@dataclass(frozen=True)
class Item:
    """One thing a person reads: a label in words, a value and its unit."""
    label: str
    value: str | float | int | bool
    unit: str | None = None
    kind: str = "text"
    text: str | None = None  # a page link's words

    def record(self) -> dict:
        return {"label": self.label, "value": self.value, "kind": self.kind, **({"unit": self.unit} if self.unit else {}),
                **({"text": self.text} if self.text else {})}


def _iso(v) -> str | None:
    if v is None or v == "":
        return None
    return v.isoformat(timespec="minutes") if isinstance(v, (datetime, date)) else str(v)


def fact(label: str, value, unit: str | None = None) -> Item | None:
    """A labelled value with its unit: a number, yes or no, or a short text. None when there is nothing to say."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return Item(label, value, None, "flag")
    if isinstance(value, (int, float)):
        return Item(label, value, unit, "number")
    return Item(label, str(value)[:240], unit, "text")


def at(label: str, when) -> Item | None:
    """A moment, shown in the reader's local time."""
    return Item(label, iso, None, "time") if (iso := _iso(when)) else None


def asset(label: str, fuzer_id: str | None) -> Item | None:
    """An asset, shown by its tag and description, never its id."""
    return Item(label, fuzer_id, None, "asset") if fuzer_id else None


def page(label: str, route: str | None, text: str) -> Item | None:
    """A page in Fuzer where the finding is worked (`#/masterdata?tab=questions&item=12`), shown as a link."""
    if not route:
        return None
    if not route.startswith("#/"):
        raise FindingError(f"a page link is a route in Fuzer (#/...), not {route!r}")
    return Item(label, route, None, "page", text)


def link(label: str, url: str | None, text: str | None = None) -> Item | None:
    """Evidence another system keeps (a provider's own alert page), shown as a link out: http(s) only."""
    if not url:
        return None
    if not re.match(r"https?://[^\s]+$", url):
        raise FindingError(f"an evidence link is an http(s) address, not {url!r}")
    return Item(label, url, None, "link", text)


@dataclass(frozen=True)
class Finding:
    detector: str                        # what saw it: a detector, a reader or the outside system ("limits.iow", "sap")
    origin: str                          # deterministic, agentic or external
    items: tuple = ()                    # what a person reads; None entries (nothing to say) are dropped
    method: str | None = None            # how it was worked out: a registered method's id (core.methods), else words
    standard: str | None = None          # the standard and clause behind the method
    start: datetime | str | None = None  # the time window it covers; no end while it is still going
    end: datetime | str | None = None
    confidence: float | None = None      # 0 to 1, where the detector can say
    checks: tuple = ()                   # the checks before the work, in order
    data: dict = field(default_factory=dict)  # the variables its process routes on; never shown

    def __post_init__(self):
        if self.origin not in ORIGINS:
            raise FindingError(f"a finding's origin is one of {', '.join(ORIGINS)}, not {self.origin!r}")
        if not self.detector:
            raise FindingError("a finding names its detector")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise FindingError("a finding's confidence is between 0 and 1")
        items = tuple(i for i in self.items if i is not None)
        for i in items:
            if not isinstance(i, Item) or not i.label.strip() or i.kind not in KINDS:
                raise FindingError(f"a finding's items are fact(), at(), asset() or page() with a label, not {i!r}")
        if KEY in self.data:
            raise FindingError(f"`{KEY}` is the record's own place in the evidence")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "checks", tuple(str(c) for c in self.checks if c))

    def record(self) -> dict:
        """The record as it is stored: plain JSON, versioned."""
        out = {"v": VERSION, "detector": self.detector, "origin": self.origin, "method": self.method,
               "standard": self.standard, "start": _iso(self.start), "end": _iso(self.end),
               "confidence": self.confidence, "items": [i.record() for i in self.items], "checks": list(self.checks)}
        return {k: v for k, v in out.items() if v not in (None, [])}

    def evidence(self) -> dict:
        """What an exception stores: the process variables, and the record under `finding`."""
        return {**self.data, KEY: self.record()}


def _label(key: str) -> str:
    words = str(key).replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def external(detector: str, said: dict | None = None, origin: str = EXTERNAL, **kw) -> Finding:
    """A finding from what an outside sender said in its own keys (a webhook body, an API call): each short value
    becomes an item labelled by its key in words, in the order sent; nested detail, ids and the sender's own
    bookkeeping stay in `data` for a process to use, unshown."""
    said = dict(said or {})
    said.pop(KEY, None)
    items = []
    for k, v in said.items():
        name = str(k).lower()
        if isinstance(v, (dict, list)) or name in _INTERNAL or name.endswith(("_id", "_key")):
            continue
        if isinstance(v, str) and _ID.fullmatch(v):
            items.append(asset(_label(k), v))
        elif isinstance(v, str) and _TIME.match(v):
            items.append(at(_label(k), v))
        else:
            items.append(fact(_label(k), v))
    return Finding(detector, origin, items=tuple(items)[:MAX_ITEMS], data=said, **kw)


ORIGIN_WORDS = {DETERMINISTIC: "Rule", AGENTIC: "Agent", EXTERNAL: "Reported by"}


def text(item: dict) -> str:
    """One stored item's value as plain text (for a list, a message or an agent): the unit stated, times to the
    minute, yes or no."""
    v, kind = item.get("value"), item.get("kind")
    if kind == "flag":
        return "yes" if v else "no"
    if kind == "number" and isinstance(v, (int, float)):
        return f"{v:g}" + (f" {item['unit']}" if item.get("unit") else "")
    if kind == "time":
        return str(v).replace("T", " ")[:16]
    return str(item.get("text") or v) + (f" {item['unit']}" if item.get("unit") else "")


def shown(evidence: dict | None, source: str | None = None, subjects=(), value_usd: float | None = None,
          names: dict[str, str] | None = None) -> dict:
    """The finding as every reader gets it, from an exception's stored evidence: the record, the assets it is about
    and the value at stake, with each asset named (`names`: Fuzer ID to tag and description). Evidence stored
    before the contract is read as an outside sender's would be."""
    evidence = evidence or {}
    rec = evidence.get(KEY) or external(source or "unknown", {
        k: v for k, v in evidence.items() if k not in ("field", "resolved", "checklist")},
        checks=tuple(c for c in evidence.get("checklist") or [] if isinstance(c, str))).record()
    names = names or {}
    how, standard = rec.get("method"), rec.get("standard")
    if methods.known(how):  # a registered method reads as its title, with the standard the registry names
        m = methods.get(how)
        how, standard = m.title, standard or (m.cites if m.standard else None)
    items = [{**i, "text": names.get(i["value"])} if i.get("kind") == "asset" else i for i in rec.get("items", [])]
    # An asset nobody can name is not shown as its id
    items = [i for i in items if i.get("kind") != "asset" or i.get("text")]
    return {"detector": rec["detector"], "origin": rec["origin"], "origin_label": ORIGIN_WORDS[rec["origin"]],
            "method": how, "standard": standard, "start": rec.get("start"),
            "end": rec.get("end"), "confidence": rec.get("confidence"), "items": items,
            "checks": rec.get("checks", []), "subjects": list(subjects), "value_usd": value_usd}


def asset_ids(evidence: dict | None) -> list[str]:
    """The assets a stored finding's items name, for the reader to look their names up."""
    rec = (evidence or {}).get(KEY) or {}
    return [i["value"] for i in rec.get("items", []) if i.get("kind") == "asset"]
