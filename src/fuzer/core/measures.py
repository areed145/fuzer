"""Measures over the register's records, declared in YAML (the reports design, Nov 2026): the platform's in
`measures.yaml`, and the ones a pack adds or rebinds under `measures:` in its pack.yaml, in the same shape, like trend
templates. Each one is a registered metric (core/metrics.py), scoped to any entity and read for any named window, so a
new customer's sources map onto the model once and every report works for them unchanged. Checked when read: a measure
that names an unknown kind, link, grain, cut-off or window is refused with the reason, at init for a pack.

    production.oil:
      label: Oil produced
      group: Production
      unit: bbl
      better: higher                    # higher | lower | none
      target: null                      # optional
      definition: ...                   # what it means, one or two sentences
      formula: sum(oil_bbl) over the production days in the window
      records: production days (daily reports)
      cutoff: production_day            # what `at` is when nobody says: day | production_day | rebuild
      window: 30d                       # its own period when a report doesn't name one
      from:
        kind: production_day            # the records it reads: entities of this kind
        at: report_date                 # the field that dates one
        end: null                       # the field that ends one, for a stock (open until it is set or passes)
        links: [reports_on]             # the links from a record to what it is about, which place it in a scope;
                                        # `a.b` follows two (a bypass `bypasses` a safeguard that `protects` a unit)
        value: oil_bbl                  # the field it adds up (sum, mean, ratio)
        over: null                      # ratio only: the field it is divided by
        where: {}                       # {field: [values]}: only records with one of these values
        unless: {}                      # {field: [values]}: records with one of these are left out
      aggregate: sum                    # count | sum | mean | ratio (100 x sum(value) / sum(over)) | age_days
      stock: false                      # true: records open at the window's end, not those dated inside it
      grains: [facility, cause]         # kind grains (the record's subject's ancestor of that kind) and cuts
      cuts: {cause: reason_category}    # a grain read from a field of the record

A record is in a scope when it, or anything it links to, is the scope entity or beneath it. A sum over a window with
no records in it is 0 where the scope has such records at all, and None (with a note) where it has none.

    builtin()         the platform's measures, checked
    checked(raw)      a pack's `measures:`, checked (pack.py calls it)

The records are read and added up by `ops/measures.py`, which registers them in the metric registry.
"""

from functools import cache
from pathlib import Path

import yaml

from . import windows

BUILTIN = Path(__file__).with_name("measures.yaml")
AGGREGATES = ("count", "sum", "mean", "ratio", "age_days")
FIELDS = {"label", "group", "unit", "better", "target", "definition", "formula", "records", "standard", "cutoff",
          "window", "from", "aggregate", "stock", "grains", "cuts", "period"}
FROM = {"kind", "at", "end", "links", "value", "over", "where", "unless"}
REFS = 200


class MeasureError(ValueError):
    """A YAML measure that names something the model doesn't have."""


@cache
def _ontology() -> tuple[frozenset, frozenset]:
    from . import ontology

    o = ontology.builtin()
    return frozenset(o["kinds"]), frozenset(o["relations"])


def _check(key: str, m: dict) -> dict:
    from .metrics import GRAINS

    def bad(why: str):
        raise MeasureError(f"measure {key}: {why}")

    if not isinstance(m, dict):
        bad("is not a mapping")
    if extra := set(m) - FIELDS:
        bad(f"unknown field {sorted(extra)}")
    for f in ("label", "unit", "definition", "formula", "records", "from"):
        if not m.get(f):
            bad(f"needs {f}")
    src = m["from"]
    if not isinstance(src, dict) or (extra := set(src) - FROM):
        bad(f"from: unknown field {sorted(extra) if isinstance(src, dict) else src}")
    kinds, rels = _ontology()
    if src.get("kind") not in kinds:
        bad(f"from.kind {src.get('kind')!r} isn't a kind in the ontology")
    if not src.get("at"):
        bad("from.at names the field that dates a record")
    if bad_rels := {p for x in src.get("links") or [] for p in str(x).split(".")} - rels:
        bad(f"from.links {sorted(bad_rels)} aren't links in the ontology")
    agg = m.get("aggregate", "count")
    if agg not in AGGREGATES:
        bad(f"aggregate {agg!r} isn't one of {', '.join(AGGREGATES)}")
    if agg in ("sum", "mean", "ratio") and not src.get("value"):
        bad(f"a {agg} needs from.value")
    if agg == "ratio" and not src.get("over"):
        bad("a ratio needs from.over")
    if agg == "age_days" and not m.get("stock"):
        bad("age_days is the age of what is open at the window's end: it needs stock: true")
    cuts = m.get("cuts") or {}
    for g in [*(m.get("grains") or []), *cuts]:
        if g not in GRAINS:
            bad(f"grain {g!r} isn't one of {', '.join(GRAINS)}")
        if g not in cuts and g not in kinds:
            bad(f"grain {g!r} is neither a kind nor a cut (cuts: {{{g}: <field>}})")
    if m.get("better", "higher") not in ("higher", "lower", "none"):
        bad("better is higher, lower or none")
    if m.get("cutoff", "day") not in windows.CUTOFFS:
        bad(f"cutoff isn't one of {', '.join(windows.CUTOFFS)}")
    if not windows.valid(m.get("window", "30d")):
        bad(f"window {m.get('window')!r} isn't a named window")
    for f in ("where", "unless"):
        if not isinstance(src.get(f) or {}, dict):
            bad(f"from.{f} is {{field: [values]}}")
    return m


def checked(raw: dict | None) -> dict:
    """A pack's measures, checked: {key: measure}."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise MeasureError("measures holds a mapping of measure key to measure")
    return {k: _check(k, v) for k, v in raw.items()}


@cache
def builtin() -> dict:
    raw = yaml.safe_load(BUILTIN.read_text())
    return {k: _check(k, v) for k, v in raw["measures"].items()}
