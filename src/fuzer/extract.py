"""Per-source extractors: turn one raw record into candidates and edges.

This is the only place that knows each source's field names. Everything after
this step works on Candidate and Edge.
"""

import re

from . import nel
from .model import Candidate, Edge
from .pack import Pack


def norm_serial(serial: str | None) -> str | None:
    if not serial:
        return None
    s = re.sub(r"[^A-Z0-9]", "", str(serial).upper())
    return re.sub(r"^SN", "", s) or None


def _area_in(text: str | None) -> str | None:
    m = re.search(r"\b(\d{2})\b", text or "")
    return m.group(1) if m else None


def _pick(payload: dict, *names: str) -> dict:
    return {n: payload[n] for n in names if payload.get(n) not in (None, "")}


def _floc_endpoint(floc: str, pack: Pack):
    if "-" not in floc:
        return "facility", floc
    if re.fullmatch(r"[A-Z]+-\d{2}", floc):
        return "area", floc
    if "-WELL-" in floc:
        return "well", pack.wells.parse(floc)
    return "tag", pack.tags.parse(floc)


def sap_floc(key, p, pack):
    kind, k = _floc_endpoint(key, pack)
    c = Candidate("sap", "floc", key, kind, k, _pick(p, "description"))
    if p.get("parent"):
        c.edges.append(Edge(("ref", c.ref), "part_of", _floc_endpoint(p["parent"], pack), "sap"))
    if kind == "well" and k and " ST" in k:
        c.edges.append(Edge(("ref", c.ref), "sidetrack_of", ("well", k.split(" ST")[0]), "sap"))
    return [c], []


def sap_equipment(key, p, pack):
    tag = pack.tags.parse(p["floc"])
    c = Candidate("sap", "equipment", key, "equipment", norm_serial(p.get("serial")),
                  _pick(p, "description", "manufacturer", "model", "serial"), block=tag)
    c.edges.append(Edge(("ref", c.ref), "installed_at", ("tag", tag), "sap",
                        valid_from=p.get("valid_from") or None, valid_to=p.get("valid_to") or None))
    return [c], []


def jde_asset(key, p, pack):
    # JDE only gives the tag inside free text; use it for matching, not as a link.
    c = Candidate("jde", "asset", key, "equipment", norm_serial(p.get("serial")),
                  _pick(p, "description", "manufacturer", "model", "serial"),
                  block=pack.tags.parse(p.get("description", "")))
    return [c], []


def maximo_asset(key, p, pack):
    loc = p.get("location", "")
    c = Candidate("maximo", "asset", key, "equipment", norm_serial(p.get("serial")),
                  _pick(p, "description", "manufacturer", "serial"),
                  block=pack.tags.parse(loc, default_area=_area_in(loc)))
    return [c], []


def _af_endpoint(parts: list[str], pack: Pack):
    if len(parts) == 1:
        return "facility", parts[0]
    if len(parts) == 2:
        return "area", f"{parts[0]}-{_area_in(parts[1])}"
    return "tag", pack.tags.parse(parts[-1], default_area=_area_in(parts[1]))


def af_element(key, p, pack):
    parts = key.split("\\")
    kind, k = _af_endpoint(parts, pack)
    c = Candidate("pi_af", "element", key, kind, k, _pick(p, "template"))
    if len(parts) > 1:
        c.edges.append(Edge(("ref", c.ref), "part_of", _af_endpoint(parts[:-1], pack), "pi_af"))
    for attr in p.get("attributes", []):
        c.edges.append(Edge(("ref", c.ref), "has_timeseries", ("timeseries", attr["pi_point"].upper()),
                            "pi_af", props={"attribute": attr["name"]}))
    return [c], []


def pi_point(key, p, pack):
    c = Candidate("pi", "point", key, "timeseries", key.upper(),
                  {"description": p.get("descriptor", ""), "uom": p.get("engunits", "")})
    if tag := pack.tags.parse(key):
        owner = ("tag", tag)
    elif well := pack.wells.parse(key):
        owner = ("well", well)
    else:
        owner = None
    if owner:
        c.edges.append(Edge(owner, "has_timeseries", ("ref", c.ref), "pi", confidence=0.9))
    return [c], []


def _ec_endpoint(node: dict, pack: Pack):
    if node["type"] == "well":
        return "well", pack.wells.parse(node["code"])
    if node["type"] in ("separator", "meter"):
        return "tag", pack.tags.parse(node["tag"])
    return "allocation_node", node["code"]


def ec_node(key, p, pack):
    kind, k = _ec_endpoint(p, pack)
    fields = {"ec_code": p["code"], "ec_type": p["type"], **_pick(p, "name")}
    if "name" in fields:
        fields["description"] = fields.pop("name")
    return [Candidate("ec", "node", key, kind, k, fields)], []


def ec_edge(key, p, pack):
    return [], [Edge(_ec_endpoint(p["from"], pack), "flows_to", _ec_endpoint(p["to"], pack), "ec",
                     valid_from=p.get("valid_from") or None, valid_to=p.get("valid_to") or None)]


def pid_sheet(key, p, pack):
    c = Candidate("pid", "sheet", key, "document", key, _pick(p, "title", "revision", "format"))
    area = _area_in(p.get("title"))
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
    return [c], []


def oplog_entry(key, p, pack):
    c = Candidate("oplog", "entry", key, "log_entry", key, _pick(p, "timestamp", "author", "text"))
    for m in nel.find_mentions(p.get("text", ""), pack):
        c.edges.append(Edge(("ref", c.ref), "mentions", (m.kind, m.key), "oplog", confidence=m.confidence,
                            props={"text": m.text, "span": [m.start, m.end], "method": m.method}))
    return [c], []


EXTRACTORS = {
    ("sap", "floc"): sap_floc,
    ("sap", "equipment"): sap_equipment,
    ("jde", "asset"): jde_asset,
    ("maximo", "asset"): maximo_asset,
    ("pi_af", "element"): af_element,
    ("pi", "point"): pi_point,
    ("ec", "node"): ec_node,
    ("ec", "edge"): ec_edge,
    ("pid", "sheet"): pid_sheet,
    ("oplog", "entry"): oplog_entry,
}


def extract(record: dict, pack: Pack) -> tuple[list[Candidate], list[Edge]]:
    fn = EXTRACTORS[(record["source"], record["record_type"])]
    return fn(record["source_key"], record["payload"], pack)
