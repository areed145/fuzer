"""Turn one raw record into candidates and edges.

What each column means lives in mapping files (mappings.py, src/fuzer/mappings/*.yaml). This module
holds only the named pieces of Python those files refer to: value transforms, resolvers that read
both kind and key from a value, and hooks for logic that truly is source-specific.
"""

import re
from datetime import datetime

from ..core import nel
from ..core.model import Candidate, Edge
from ..core.pack import Pack


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


def area_in(text: str | None, pack: Pack) -> str | None:
    """The area code in `text`, in the pack's area format."""
    return pack.tags.area_in(text)


# ---- value transforms: (value, pack, default area) -> canonical value

TRANSFORMS = {
    "tag": lambda v, pack, area: pack.tags.parse(v if v is not None else "", default_area=area),
    "well": lambda v, pack, area: pack.wells.parse(v),
    "serial": lambda v, pack, area: norm_serial(v),
    "upper": lambda v, pack, area: v.upper() if isinstance(v, str) else v,
    "number": lambda v, pack, area: _num(v),
    "model_key": lambda v, pack, area: model_key(v, pack),
    "date": lambda v, pack, area: iso_date(v),
}

_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?)?")


def iso_date(v) -> str | None:
    """A date or date-time as ISO text (2026-04-06, 2026-04-06T07:30). A value in another format is kept as written:
    the system's date format (formats.py) reads local dates before mapping, so what is left isn't guessed at."""
    if v in (None, ""):
        return None
    m = _ISO.match(str(v).strip())
    if not m:
        return str(v).strip()
    return f"{m.group(1)}T{m.group(2)}" if m.group(2) and m.group(2) != "00:00" else m.group(1)


def model_key(v, pack) -> str | None:
    """"Manufacturer|model" as the equipment_model key the taxonomy derives (canonical maker, normalised model)."""
    from ..core.taxonomy import Taxonomy, normalise_model

    mfr, _, model = str(v or "").partition("|")
    mfr, model = Taxonomy(pack).manufacturer(mfr, []), normalise_model(model)
    return f"{mfr.upper()}|{model}" if mfr and model else None


# ---- resolvers: (value, pack) -> (kind, key), or None when the value names nothing

def _floc(floc: str, pack: Pack):
    if "-" not in floc:
        return "facility", floc
    head, _, rest = floc.partition("-")
    if re.fullmatch(r"[A-Z]+", head) and pack.tags.is_area(rest):
        return "area", floc
    if "-WELL-" in floc:
        return "well", pack.wells.parse(floc)
    return "tag", pack.tags.parse(floc)


def _af(parts: list[str], pack: Pack):
    if len(parts) == 1:
        return "facility", parts[0]
    if len(parts) == 2:
        return "area", f"{parts[0]}-{area_in(parts[1], pack)}"
    return "tag", pack.tags.parse(parts[-1], default_area=area_in(parts[1], pack))


def _ec_node(node: dict, pack: Pack):
    if node["type"] == "well":
        return "well", pack.wells.parse(node["code"])
    if node.get("tag"):  # separators, meters, compressors: EC names the equipment tag
        return "tag", pack.tags.parse(node["tag"])
    return "allocation_node", node["code"]


def _tag_or_well(v: str, pack: Pack):
    if tag := pack.tags.parse(v):
        return "tag", tag
    if well := pack.wells.parse(v):
        return "well", well
    return None


def _place(v: str, pack: Pack):
    """A plant location written in any of the styles planners and contractors use: a name the pack knows
    ("ggcp"), the facility, a FLOC (OKAN-20, OKAN-20-K2200B), an area code (20), a tag in any spacing
    (20-K-2200B, 20K2200B, 20 K 2200B) or a well (OK-J12-07, OK J12 07). None when it names nothing known."""
    v = str(v or "").strip()
    if not v:
        return None
    if alias := pack.aliases.get(v.lower()):
        return tuple(alias)
    u, fac = v.upper(), pack.facility.upper()
    if u == fac:
        return "facility", pack.facility
    if u.startswith(fac + "-"):
        found = _floc(u, pack)
        if found and found[1]:
            return found
    if pack.tags.is_area(u):
        return "area", f"{pack.facility}-{u}"
    if tag := pack.tags.parse(v) or pack.tags.parse(re.sub(r"\s+", "-", v)):
        return "tag", tag
    if well := pack.wells.parse(v) or _bare_well(u, pack):
        return "well", well
    return None


def _bare_well(u: str, pack: Pack) -> str | None:
    """A well written without the field's prefix (J12-04 for OK-J12-04), as planners do in a location column. Only
    here: in free text a bare J12 04 is too often something else."""
    prefix = getattr(pack.wells, "prefix", "")
    return pack.wells.parse(prefix + u) if prefix and not u.startswith(prefix) else None


def _document_part(row, pack: Pack):
    """A row of a documents folder (connectors.DocumentFolder): section 0 is the document itself (by its file name),
    the others its sections ("<file>#003")."""
    if not isinstance(row, dict) or not row.get("file"):
        return None
    n = int(row.get("section") or 0)
    return ("document", row["file"]) if n == 0 else ("document_section", f"{row['file']}#{n:03d}")


RESOLVERS = {
    "floc": _floc,
    "document_part": _document_part,
    "place": _place,
    "af_path": lambda v, pack: _af(v.split("\\"), pack),
    "af_parent": lambda v, pack: _af(v.split("\\")[:-1], pack) if "\\" in v else None,
    "ec_node": _ec_node,
    "tag_or_well": _tag_or_well,
    "facility_or_well": lambda v, pack: ("facility", v) if v == pack.facility else ("well", pack.wells.parse(v or "")),
}


# ---- hooks: (candidate, row, pack, **options) -> None, changing the candidate in place

def _iso(pack: Pack, kind: str, raw: str | None) -> tuple[str | None, str | None]:
    """Site catalog code -> (ISO 14224 code, name) through loaded references, then pack defaults."""
    from ..core.taxonomy import load_standard

    entry = _reference_entry(pack, kind, raw)
    code = (entry or {}).get("standard_code")
    if not code and raw:
        code = pack.maintenance_codes.get(kind, {}).get(raw)
    if not code:
        return None, None
    table = {"damage": "failure_modes", "cause": "failure_mechanisms", "activity": "maintenance_activities"}[kind]
    name = load_standard(pack.classification.get("taxonomy", "iso14224")).get(table, {}).get(code)
    return code, name or (entry or {}).get("name")


def _reference_entry(pack: Pack, list_: str, code: str | None) -> dict | None:
    return pack.reference_data.get(list_, {}).get(str(code)) if code not in (None, "") else None


def _unmapped(c, list_: str, code) -> None:
    if code not in (None, ""):
        c.fields.setdefault("unmapped_codes", []).append({"list": list_, "code": str(code)})


def iso_code(c, p, pack, *, column: str, catalog: str, field: str):
    """A site maintenance code as its ISO 14224 code (`field`) and name (`field`_name)."""
    raw = p.get(column)
    code, name = _iso(pack, catalog, raw)
    if code:
        c.fields |= {field: code, f"{field}_name": name}
    elif raw not in (None, "") and not _reference_entry(pack, catalog, raw) \
            and not pack.maintenance_codes.get(catalog, {}).get(raw):
        _unmapped(c, catalog, raw)


def sidetrack(c, p, pack):
    if c.kind == "well" and c.key and " ST" in c.key:
        c.edges.append(Edge(("ref", c.ref), "sidetrack_of", ("well", c.key.split(" ST")[0]), c.source))


def downtime(c, p, pack):
    """EC downtime: loaded reason names (or pack defaults), duration and a check against the comment."""
    reason_code = p.get("reason_code")
    reason = {**pack.downtime_reasons.get(reason_code, {}),
              **{k: v for k, v in (_reference_entry(pack, "downtime_reasons", reason_code) or {}).items()
                 if k in ("name", "category") and v not in (None, "")}}
    if reason_code not in (None, "") and not reason:
        _unmapped(c, "downtime_reasons", reason_code)
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
    from ..core.taxonomy import Taxonomy, normalise_model

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
    from ..core.taxonomy import failure_modes_in

    if modes := failure_modes_in(f"{p.get('heading', '')}\n{p.get('text', '')}", pack):
        c.fields["failure_modes"] = modes


def pid_sheet(c, p, pack):
    """Piping and tags from a P&ID sheet, by format: smart (tag list and connections), text PDF, or scan."""
    area = area_in(p.get("title"), pack)
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



def extract(record: dict, pack: Pack, maps: dict | None = None) -> tuple[list[Candidate], list[Edge]]:
    """Candidates and edges for one record, through its mapping (`maps` from mappings.load(conn), so
    approved mappings count; built-in files only when not given)."""
    m = (maps if maps is not None else mappings.load()).get((record["source"], record["record_type"]))
    if m is None:
        raise KeyError(f"no mapping for {record['source']} {record['record_type']}")
    return m.extract(record["source_key"], record["payload"], pack)


from . import (
    mappings,
)
