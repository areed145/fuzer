"""Smart P&IDs as DEXPI XML (#858): what AVEVA, Hexagon and other P&ID tools export, read into the same shape as a
smart sheet in `pid_sheets.json` (tags, connections, positions, line numbers), so Fuzer links it the same way.

DEXPI 1.x is the Proteus schema (`<PlantModel>`): equipment, piping components and instrumentation functions are
elements with an `ID`, a tag (a `TagName` attribute, or the `TagNameAssignmentClass` / `…NumberAssignmentClass`
generic attributes DEXPI 1.3 uses), a `<Position><Location X= Y=/>` and nozzles. Piping is
`<PipingNetworkSystem>` (its line number) of `<PipingNetworkSegment>`s, each with its inline components in flow
order and `<Connection FromID= ToID=/>` naming what it runs from and to (a nozzle, a component, an off-page
connector, or another segment). A connection here is one hop between tagged items along a segment: from-end, each tagged inline
component (valves, meters), to-end. A nozzle stands for its equipment, an off-page connector for the item it names
on the other sheet (its reference generic attribute, or a TagName), and an end on another segment is followed to
that segment's own end. An instrument's tag is written as the bubble shows it (PT-1001, no area): the sheet's area,
when its manifest entry gives one, is put in front (B1-PT-1001).

DEXPI 2.0 (`<Model>` of `<Object>`s with `<Data property=…>`) is read for its tags only for now; its connections
are left to a later reader, and the result says so.

Standard library only (xml.etree): no dependency on an AGPL toolkit.
"""

import itertools
import xml.etree.ElementTree as ET
from pathlib import Path

TAGGED = ("Equipment", "PipingComponent", "ProcessInstrumentationFunction", "InstrumentComponent",
          "ProcessSignalGeneratingFunction", "ActuatingFunction", "PipingOffPageConnector")
OFF_PAGE = "PipingOffPageConnector"  # stands for the item it names on another sheet: not a tag on this one
TAG_ATTRIBUTES = ("TagNameAssignmentClass", "PipingOffPageConnectorReferenceAssignmentClass", "ProcessInstrumentationFunctionNumberAssignmentClass",
                  "PipingComponentNumberAssignmentClass", "TagName")
LINE_ATTRIBUTES = ("LineNumberAssignmentClass", "PipingNetworkSystemNumberAssignmentClass", "LineNumber")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _generic(el) -> dict[str, str]:
    """The element's own generic attributes (DEXPI 1.3 keeps tags and line numbers there)."""
    out = {}
    for ga in el.findall("./GenericAttributes/GenericAttribute"):
        if ga.get("Name") and ga.get("Value") not in (None, ""):
            out[ga.get("Name")] = ga.get("Value")
    return out


def _tag(el, area: str | None = None) -> str | None:
    if el.get("TagName"):
        return el.get("TagName").strip()
    g = _generic(el)
    if _local(el.tag) == "ProcessInstrumentationFunction":
        cat = g.get("ProcessInstrumentationFunctionCategoryAssignmentClass", "")
        mod = g.get("ProcessInstrumentationFunctionModifierAssignmentClass", "")
        num = g.get("ProcessInstrumentationFunctionNumberAssignmentClass", "")
        if num:
            t = f"{cat}{mod}-{num}" if cat or mod else num
            return f"{area}-{t}" if area and not t.startswith(f"{area}-") else t
    for k in TAG_ATTRIBUTES:
        if g.get(k):
            return g[k].strip()
    prefix, seq, suffix = (g.get(f"TagName{p}AssignmentClass", "") for p in ("Prefix", "SequenceNumber", "Suffix"))
    return f"{prefix}{seq}{suffix}" or None


def _position(el) -> list[float] | None:
    loc = el.find("./Position/Location")
    if loc is None or loc.get("X") is None:
        return None
    return [round(float(loc.get("X")), 1), round(float(loc.get("Y", 0)), 1)]


def read(path, area: str | None = None) -> dict:
    """The sheet a DEXPI file describes: `{dexpi_version, title?, revision?, tags, connections, positions, lines,
    unresolved}`; `connections` are `{from, to, line}` between tags, as a smart sheet gives them. `area` (the
    sheet's plant area, from its manifest entry) is put in front of instrument tags, which DEXPI writes without
    it."""
    root = ET.parse(Path(path)).getroot()
    for el in root.iter():  # namespaces vary by exporter; match on local names
        el.tag = _local(el.tag)
    if root.tag == "Model":
        return _read_v2(root)
    if root.tag != "PlantModel":
        raise ValueError(f"not a DEXPI file: root element <{root.tag}>")
    info = root.find("PlantInformation")
    drawing = root.find("Drawing")
    owner: dict[str, str] = {}  # element ID -> the tag it stands for (a nozzle: its equipment's)
    tags, positions = [], {}
    for el in root.iter():
        if el.tag not in TAGGED or not el.get("ID"):
            continue
        t = _tag(el, area)
        if not t:
            continue
        owner[el.get("ID")] = t
        if el.tag == OFF_PAGE:
            continue
        if t not in tags:
            tags.append(t)
        if (p := _position(el)) is not None:
            positions.setdefault(t, p)
        for nz in el.iter("Nozzle"):
            if nz.get("ID"):
                owner[nz.get("ID")] = t
    segments: dict[str, dict] = {}
    for system in root.iter("PipingNetworkSystem"):
        g = _generic(system)
        line = next((g[k] for k in LINE_ATTRIBUTES if g.get(k)), None) or system.get("TagName")
        for seg in system.iter("PipingNetworkSegment"):
            inline = [owner[c.get("ID")] for c in seg if c.tag in TAGGED and c.get("ID") in owner]
            conn = seg.find("Connection")
            segments[seg.get("ID")] = {"line": line, "inline": inline,
                                       "from": conn.get("FromID") if conn is not None else None,
                                       "to": conn.get("ToID") if conn is not None else None}

    def end(ref, side, seen=()):
        """The tag a segment end stands for, following ends that sit on other segments."""
        if ref in owner:
            return owner[ref]
        if ref in segments and ref not in seen:
            s = segments[ref]
            near = s["inline"][-1 if side == "from" else 0] if s["inline"] else None
            return near or end(s[side], side, (*seen, ref))
        return None

    connections, unresolved = [], 0
    for sid, s in segments.items():
        chain = [end(s["from"], "from", (sid,)), *s["inline"], end(s["to"], "to", (sid,))]
        unresolved += chain[0] is None or chain[-1] is None
        chain = [c for c in chain if c]
        for a, b in itertools.pairwise(chain):
            if a != b and {"from": a, "to": b, "line": s["line"]} not in connections:
                connections.append({"from": a, "to": b, "line": s["line"]})
    out = {"dexpi_version": (info.get("SchemaVersion") if info is not None else None) or "1.x", "tags": tags,
           "connections": connections, "positions": positions,
           "lines": sorted({s["line"] for s in segments.values() if s["line"]}), "unresolved": unresolved}
    if drawing is not None:
        title = drawing.get("Title") or _generic(drawing).get("DrawingNameAssignmentClass") or drawing.get("Name")
        rev = drawing.get("Revision") or _generic(drawing).get("RevisionNumberAssignmentClass")
        out |= {k: v for k, v in (("title", title), ("revision", rev)) if v}
    return out


def _read_v2(root) -> dict:
    tags = []
    for obj in root.iter("Object"):
        for d in obj.findall("Data"):
            if d.get("property") in ("TagName", "TagNameAssignmentClass"):
                s = d.find("String")
                if s is not None and (s.text or "").strip() and s.text.strip() not in tags:
                    tags.append(s.text.strip())
    return {"dexpi_version": "2.x", "tags": tags, "connections": [], "positions": {}, "lines": [],
            "unresolved": 0, "note": "DEXPI 2 connections are not read yet: tags only"}


def compare(exact: dict, reading: dict, pack, area: str | None = None) -> dict:
    """A DEXPI sheet against its scanned twin (#63): the same drawing read exactly from the P&ID tool's export and by
    the drawing reader from its image. `exact` is what read() returns (or a smart sheet's payload), `reading` the
    drawing reader's interpreted result (llm_pids.interpret). Tags compare as canonical keys (the sheet's area put in
    front where the drawing leaves it off); the scan's tags are the ones it linked and the ones it read but found no
    register entry for. Piping compares as directed hops between keys; a hop the scan shows the wrong way round is
    `reversed`, not missing. Returns {tags, connections: {both, only_dexpi, only_scan[, reversed]}, same}."""
    def key(t):
        return pack.tags.parse(t or "", default_area=area) or t

    def hops(cs):
        return {(key(c["from"]), key(c["to"])) for c in cs if c.get("from") and c.get("to")}

    a = {key(t) for t in exact.get("tags", [])}
    b = {m["key"] for m in reading.get("mentions", []) if m.get("key")} | {
        f["key"] for f in reading.get("findings", []) if f.get("key") and f.get("kind") != "connection"}
    ca, cb = hops(exact.get("connections", [])), hops(reading.get("connections", []))
    reversed_ = sorted(h for h in ca - cb if (h[1], h[0]) in cb)
    flipped = {(y, x) for x, y in reversed_}
    tags = {"both": sorted(a & b), "only_dexpi": sorted(a - b), "only_scan": sorted(b - a)}
    conns = {"both": sorted(ca & cb), "only_dexpi": sorted(ca - cb - set(reversed_)),
             "only_scan": sorted(cb - ca - flipped), "reversed": reversed_}
    same = not (tags["only_dexpi"] or tags["only_scan"] or conns["only_dexpi"] or conns["only_scan"] or reversed_)
    return {"tags": tags, "connections": conns, "same": same}
