"""Turn one raw record into candidates and edges.

What each column means lives in mapping files (mappings.py, src/fuzer/mappings/*.yaml). This module
holds only the named pieces of Python those files refer to: value transforms, resolvers that read
both kind and key from a value, and hooks for logic that truly is source-specific.
"""

import re
from datetime import datetime

from . import nel
from .model import Candidate, Edge
from .pack import Pack


def norm_serial(serial: str | None) -> str | None:
    if not serial:
        return None
    s = re.sub(r"[^A-Z0-9]", "", str(serial).upper())
    return re.sub(r"^SN", "", s) or None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def area_in(text: str | None) -> str | None:
    m = re.search(r"\b(\d{2})\b", text or "")
    return m.group(1) if m else None


# ---- value transforms: (value, pack, default area) -> canonical value

TRANSFORMS = {
    "tag": lambda v, pack, area: pack.tags.parse(v if v is not None else "", default_area=area),
    "well": lambda v, pack, area: pack.wells.parse(v),
    "serial": lambda v, pack, area: norm_serial(v),
    "upper": lambda v, pack, area: v.upper() if isinstance(v, str) else v,
    "number": lambda v, pack, area: _num(v),
}


# ---- resolvers: (value, pack) -> (kind, key), or None when the value names nothing

def _floc(floc: str, pack: Pack):
    if "-" not in floc:
        return "facility", floc
    if re.fullmatch(r"[A-Z]+-\d{2}", floc):
        return "area", floc
    if "-WELL-" in floc:
        return "well", pack.wells.parse(floc)
    return "tag", pack.tags.parse(floc)


def _af(parts: list[str], pack: Pack):
    if len(parts) == 1:
        return "facility", parts[0]
    if len(parts) == 2:
        return "area", f"{parts[0]}-{area_in(parts[1])}"
    return "tag", pack.tags.parse(parts[-1], default_area=area_in(parts[1]))


def _ec_node(node: dict, pack: Pack):
    if node["type"] == "well":
        return "well", pack.wells.parse(node["code"])
    if node["type"] in ("separator", "meter"):
        return "tag", pack.tags.parse(node["tag"])
    return "allocation_node", node["code"]


def _tag_or_well(v: str, pack: Pack):
    if tag := pack.tags.parse(v):
        return "tag", tag
    if well := pack.wells.parse(v):
        return "well", well
    return None


RESOLVERS = {
    "floc": _floc,
    "af_path": lambda v, pack: _af(v.split("\\"), pack),
    "af_parent": lambda v, pack: _af(v.split("\\")[:-1], pack) if "\\" in v else None,
    "ec_node": _ec_node,
    "tag_or_well": _tag_or_well,
    "facility_or_well": lambda v, pack: ("facility", v) if v == pack.facility else ("well", pack.wells.parse(v or "")),
}


# ---- hooks: (candidate, row, pack, **options) -> None, changing the candidate in place

def _iso(pack: Pack, kind: str, raw: str | None) -> tuple[str | None, str | None]:
    """Site catalog code -> (ISO 14224 code, name) through the pack's maintenance_codes."""
    from .taxonomy import load_standard

    code = pack.maintenance_codes.get(kind, {}).get(raw) if raw else None
    if not code:
        return None, None
    table = {"damage": "failure_modes", "cause": "failure_mechanisms", "activity": "maintenance_activities"}[kind]
    return code, load_standard(pack.classification.get("taxonomy", "iso14224")).get(table, {}).get(code)


def iso_code(c, p, pack, *, column: str, catalog: str, field: str):
    """A site maintenance code as its ISO 14224 code (`field`) and name (`field`_name)."""
    code, name = _iso(pack, catalog, p.get(column))
    if code:
        c.fields |= {field: code, f"{field}_name": name}


def sidetrack(c, p, pack):
    if c.kind == "well" and c.key and " ST" in c.key:
        c.edges.append(Edge(("ref", c.ref), "sidetrack_of", ("well", c.key.split(" ST")[0]), c.source))


def downtime(c, p, pack):
    """EC downtime: duration, the pack's reason names, and a check that the reason fits the comment."""
    reason = pack.downtime_reasons.get(p.get("reason_code"), {})
    hours = (datetime.fromisoformat(p["end"]) - datetime.fromisoformat(p["start"])).total_seconds() / 3600
    c.fields |= {"duration_h": round(hours, 1),
                 "description": p.get("comment", "") or reason.get("name", "Downtime"),
                 **({"reason": reason["name"], "reason_category": reason["category"]} if reason else {})}
    # Equipment named in the comment is the likely cause; Claude adds what the rules can't place.
    named = [e for e in c.edges if e.rel == "caused_by"]
    if named and reason and reason["category"] != "equipment":
        c.fields["reason_check"] = f"comment names {named[0].props['text']} but the reason is {reason['name'].lower()}"


def document_applies_to(c, p, pack):
    """What the EDMS says a document applies to: manufacturer + model, class, tag references."""
    from .taxonomy import Taxonomy, normalise_model

    if p.get("needs_ocr"):
        c.fields["needs_ocr"] = True
    tax = Taxonomy(pack)
    mfr, model = tax.manufacturer(p.get("manufacturer"), []), normalise_model(p.get("model"))
    if mfr and model:
        c.edges.append(Edge(("ref", c.ref), "applies_to", ("equipment_model", f"{mfr.upper()}|{model}"), "edms",
                            props={"basis": "document metadata"}))
    if p.get("equipment_class") and tax.valid(p["equipment_class"]):
        c.edges.append(Edge(("ref", c.ref), "applies_to", ("equipment_class", tax.key(p["equipment_class"])), "edms",
                            props={"basis": "document metadata"}))
    for t in p.get("tags", []):
        c.edges.append(Edge(("ref", c.ref), "applies_to", ("tag", pack.tags.parse(t)), "edms",
                            props={"basis": "tag reference"}))


def section_failure_modes(c, p, pack):
    from .taxonomy import failure_modes_in

    if modes := failure_modes_in(f"{p.get('heading', '')}\n{p.get('text', '')}", pack):
        c.fields["failure_modes"] = modes


def pid_sheet(c, p, pack):
    """Piping and tags from a P&ID sheet, by format: smart (tag list and connections), text PDF, or scan."""
    area = area_in(p.get("title"))
    for conn_ in p.get("connections", []):  # piping from smart P&IDs (DEXPI-style)
        c.edges.append(Edge(("tag", pack.tags.parse(conn_["from"], area)), "pipes_to",
                            ("tag", pack.tags.parse(conn_["to"], area)), "pid",
                            props={"line": conn_.get("line"), "sheet": c.source_key}))
    if p["format"] == "smart":
        for t in p.get("tags", []):
            c.edges.append(Edge(("ref", c.ref), "mentions", ("tag", pack.tags.parse(t, area)), "pid",
                                props={"text": t, "method": "smart_pid"}))
    elif p["format"] == "pdf_text":
        for m in nel.find_mentions(p.get("text", ""), pack, default_area=area):
            c.edges.append(Edge(("ref", c.ref), "mentions", (m.kind, m.key), "pid", confidence=0.9,
                                props={"text": m.text, "method": m.method}))
    else:
        c.fields["needs_ocr"] = True


HOOKS = {
    "iso_code": iso_code,
    "sidetrack": sidetrack,
    "downtime": downtime,
    "document_applies_to": document_applies_to,
    "section_failure_modes": section_failure_modes,
    "pid_sheet": pid_sheet,
}


def link_part_numbers(cands: list[Candidate]) -> list[Edge]:
    """Document sections that name a material's manufacturer part number mention that material.
    One compiled pattern over all part numbers, one pass over section text."""
    mpns = {c.fields["mpn"].upper(): c.key for c in cands if c.kind == "material" and c.fields.get("mpn")}
    if not mpns:
        return []
    pattern = re.compile(r"(?<![A-Z0-9-])(" + "|".join(map(re.escape, sorted(mpns, key=len, reverse=True)))
                         + r")(?![A-Z0-9-])")
    edges = []
    for c in cands:
        if c.kind == "document_section":
            for mpn in {m.group(1) for m in pattern.finditer(c.fields.get("text", "").upper())}:
                edges.append(Edge(("ref", c.ref), "mentions", ("material", mpns[mpn]), "edms",
                                  props={"text": mpn, "method": "part number"}))
    return edges



def extract(record: dict, pack: Pack) -> tuple[list[Candidate], list[Edge]]:
    m = mappings.load().get((record["source"], record["record_type"]))
    if m is None:
        raise KeyError(f"no mapping for {record['source']} {record['record_type']}")
    return m.extract(record["source_key"], record["payload"], pack)


from . import mappings  # noqa: E402  (mappings uses the transforms, resolvers and hooks above)
