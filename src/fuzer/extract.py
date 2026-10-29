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


def _iso(pack: Pack, kind: str, raw: str | None) -> tuple[str | None, str | None]:
    """Site catalog code -> (ISO 14224 code, name) through the pack's maintenance_codes."""
    from .taxonomy import load_standard

    code = pack.maintenance_codes.get(kind, {}).get(raw) if raw else None
    if not code:
        return None, None
    table = {"damage": "failure_modes", "cause": "failure_mechanisms", "activity": "maintenance_activities"}[kind]
    return code, load_standard(pack.classification.get("taxonomy", "iso14224")).get(table, {}).get(code)


def _maintenance_targets(c: Candidate, p: dict, pack: Pack) -> None:
    if p.get("equnr"):
        c.edges.append(Edge(("ref", c.ref), "concerns", ("ref", f"sap:equipment:{p['equnr']}"), "sap"))
    if p.get("floc"):
        c.edges.append(Edge(("ref", c.ref), "concerns", _floc_endpoint(p["floc"], pack), "sap"))


def sap_notification(key, p, pack):
    fields = _pick(p, "notif_type", "created", "priority", "status", "short_text", "damage_code", "cause_code")
    fields["description"] = p.get("short_text", "")
    mode, mode_name = _iso(pack, "damage", p.get("damage_code"))
    mech, mech_name = _iso(pack, "cause", p.get("cause_code"))
    if mode:
        fields |= {"failure_mode": mode, "failure_mode_name": mode_name}
    if mech:
        fields |= {"failure_mechanism": mech, "failure_mechanism_name": mech_name}
    c = Candidate("sap", "notification", key, "notification", key, fields)
    _maintenance_targets(c, p, pack)
    return [c], []


def sap_work_order(key, p, pack):
    fields = _pick(p, "order_type", "start", "finish", "activity_code", "cost", "text")
    fields["description"] = p.get("text", "")
    act, act_name = _iso(pack, "activity", p.get("activity_code"))
    if act:
        fields |= {"activity": act, "activity_name": act_name}
    c = Candidate("sap", "work_order", key, "work_order", key, fields)
    _maintenance_targets(c, p, pack)
    if p.get("qmnum"):
        c.edges.append(Edge(("ref", c.ref), "for_notification", ("notification", p["qmnum"]), "sap"))
    return [c], []


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ec_downtime(key, p, pack):
    from datetime import datetime

    reason = pack.downtime_reasons.get(p.get("reason_code"), {})
    hours = (datetime.fromisoformat(p["end"]) - datetime.fromisoformat(p["start"])).total_seconds() / 3600
    comment = p.get("comment", "")
    fields = {**_pick(p, "start", "end", "reason_code", "comment"), "timestamp": p["start"],
              "duration_h": round(hours, 1), "description": comment or reason.get("name", "Downtime"),
              "lost_oil_sm3": _num(p.get("lost_oil_sm3")), "lost_gas_ksm3": _num(p.get("lost_gas_ksm3")),
              **({"reason": reason["name"], "reason_category": reason["category"]} if reason else {})}
    c = Candidate("ec", "downtime", key, "downtime", key, fields)
    obj = p.get("object_code", "")
    target = ("facility", obj) if obj == pack.facility else ("well", pack.wells.parse(obj))
    c.edges.append(Edge(("ref", c.ref), "affects", target, "ec"))
    # Equipment named in the comment is the likely cause; Claude adds what the rules can't place.
    named = [m for m in nel.find_mentions(comment, pack) if m.kind in ("tag", "tag_noarea")]
    for m in named:
        c.edges.append(Edge(("ref", c.ref), "caused_by", (m.kind, m.key), "ec", confidence=m.confidence,
                            props={"text": m.text, "method": "rules"}))
    if named and reason and reason["category"] != "equipment":
        fields["reason_check"] = f"comment names {named[0].text} but the reason is {reason['name'].lower()}"
    return [c], []


def ec_daily_report(key, p, pack):
    fields = {**_pick(p, "report_date", "comments"), "timestamp": key, "description": f"Daily report {key}",
              **{k: _num(p.get(k)) for k in ("oil_sm3", "gas_ksm3", "water_inj_sm3")}}
    c = Candidate("ec", "daily_report", key, "daily_report", key, fields)
    if p.get("facility"):
        c.edges.append(Edge(("ref", c.ref), "reports_on", ("facility", p["facility"]), "ec"))
    for m in nel.find_mentions(p.get("comments", ""), pack):
        c.edges.append(Edge(("ref", c.ref), "mentions", (m.kind, m.key), "ec", confidence=m.confidence,
                            props={"text": m.text, "method": m.method}))
    return [c], []


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
    ("sap", "notification"): sap_notification,
    ("sap", "work_order"): sap_work_order,
    ("jde", "asset"): jde_asset,
    ("maximo", "asset"): maximo_asset,
    ("pi_af", "element"): af_element,
    ("pi", "point"): pi_point,
    ("ec", "node"): ec_node,
    ("ec", "edge"): ec_edge,
    ("ec", "downtime"): ec_downtime,
    ("ec", "daily_report"): ec_daily_report,
    ("pid", "sheet"): pid_sheet,
    ("oplog", "entry"): oplog_entry,
}


def extract(record: dict, pack: Pack) -> tuple[list[Candidate], list[Edge]]:
    fn = EXTRACTORS[(record["source"], record["record_type"])]
    return fn(record["source_key"], record["payload"], pack)
