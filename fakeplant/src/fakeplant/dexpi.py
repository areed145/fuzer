"""A smart P&ID sheet written as DEXPI 1.3 XML (Proteus schema), the way a P&ID tool exports one (#858).

`sheet_xml(sheet)` turns a smart sheet from pid_sheets.json (tags, connections with line numbers, positions) into a
`<PlantModel>`: equipment with nozzles, instrumentation functions, inline piping components (valves and meters sit
in the line, not at its ends), off-page connectors naming the item on the other sheet, and one
`<PipingNetworkSystem>` per line number with a segment per hop. Tags are written the DEXPI 1.3 way, as generic
attributes, not a TagName attribute, so a reader has to know the standard to find them.
"""

import re
import xml.etree.ElementTree as ET

EQUIPMENT = {"V": "Vessel", "K": "ReciprocatingCompressor", "E": "AirCoolingSystem", "P": "CentrifugalPump",
             "T": "Tank", "F": "Filter", "H": "FiredHeater", "G": "Generator", "C": "Column"}
VALVES = {"XV": "OperatedValve", "PV": "GlobeValve", "LV": "GlobeValve", "FV": "GlobeValve", "TV": "GlobeValve",
          "PSV": "SpringLoadedGlobeSafetyValve", "SDV": "OperatedValve", "BDV": "OperatedValve"}
METERS = {"FT": "FlowMeasuringElement"}
FUNC = re.compile(r"^[A-Z]\d-([A-Z]+)-(\d+[A-Z]?)$")


def _attrs(parent, **values):
    g = ET.SubElement(parent, "GenericAttributes", Set="DexpiAttributes", Number=str(len(values)))
    for k, v in values.items():
        ET.SubElement(g, "GenericAttribute", Name=k, Value=str(v), Format="string")


def _place(el, xy):
    if xy:
        pos = ET.SubElement(el, "Position")
        ET.SubElement(pos, "Location", X=f"{xy[0]:.1f}", Y=f"{xy[1]:.1f}", Z="0")


def sheet_xml(sheet: dict) -> str:
    on_sheet = set(sheet["tags"])
    root = ET.Element("PlantModel")
    ET.SubElement(root, "PlantInformation", SchemaVersion="4.1.1", OriginatingSystem="fakeplant", Date="2026-01-01",
                  Time="00:00:00", Is3D="no", Units="mm", Discipline="PID")
    d = ET.SubElement(root, "Drawing", Name=sheet["sheet"], Type="PID")
    _attrs(d, DrawingNameAssignmentClass=sheet["title"], RevisionNumberAssignmentClass=sheet.get("revision", ""))
    pos = sheet.get("positions") or {}
    ids: dict[str, str] = {}  # tag -> the ID a connection names (a nozzle for equipment)
    nozzles: dict[str, int] = {}
    inline: dict[str, ET.Element] = {}
    for n, tag in enumerate(sheet["tags"], 1):
        m = FUNC.match(tag)
        func = m.group(1) if m else ""
        if func in EQUIPMENT:
            el = ET.SubElement(root, "Equipment", ID=f"EQ-{n}", ComponentClass=EQUIPMENT[func])
            _attrs(el, TagNameAssignmentClass=tag)
            _place(el, pos.get(tag))
            ids[tag] = f"EQ-{n}"
        elif func in VALVES or func in METERS:
            el = ET.Element("PipingComponent", ID=f"PC-{n}", ComponentClass=VALVES.get(func) or METERS[func])
            _attrs(el, PipingComponentNumberAssignmentClass=tag)
            _place(el, pos.get(tag))
            ids[tag], inline[tag] = f"PC-{n}", el
        else:
            el = ET.SubElement(root, "ProcessInstrumentationFunction", ID=f"PIF-{n}",
                               ComponentClass="ProcessInstrumentationFunction")
            num = m.group(2) if m else tag  # the loop number without the area, as a P&ID writes it in its bubble
            _attrs(el, ProcessInstrumentationFunctionCategoryAssignmentClass=func[:1],
                   ProcessInstrumentationFunctionModifierAssignmentClass=func[1:],
                   ProcessInstrumentationFunctionNumberAssignmentClass=num)
            _place(el, pos.get(tag))
            ids[tag] = f"PIF-{n}"

    def nozzle(tag):
        eq = root.find(f"./Equipment[@ID='{ids[tag]}']")
        nozzles[tag] = nozzles.get(tag, 0) + 1
        nid = f"{ids[tag]}-N{nozzles[tag]}"
        ET.SubElement(eq, "Nozzle", ID=nid, ComponentClass="Nozzle")
        return nid

    off = 0
    by_line: dict[str, ET.Element] = {}
    placed: set[str] = set()
    for k, c in enumerate(sheet["connections"], 1):
        system = by_line.get(c["line"])
        if system is None:
            system = by_line[c["line"]] = ET.SubElement(root, "PipingNetworkSystem", ID=f"PNS-{len(by_line) + 1}")
            _attrs(system, LineNumberAssignmentClass=c["line"])
        seg = ET.SubElement(system, "PipingNetworkSegment", ID=f"PNSG-{k}")
        ends = []
        for side in ("from", "to"):
            tag = c[side]
            if tag not in on_sheet:  # on another sheet: an off-page connector naming it
                off += 1
                el = ET.SubElement(seg, "PipingOffPageConnector", ID=f"OPC-{off}", ComponentClass="FlowArrow")
                _attrs(el, PipingOffPageConnectorReferenceAssignmentClass=tag)
                ends.append(f"OPC-{off}")
            elif tag in inline:
                if tag not in placed:  # the valve or meter sits in the first segment that reaches it
                    seg.append(inline[tag])
                    placed.add(tag)
                ends.append(ids[tag])
            else:
                ends.append(nozzle(tag) if ids[tag].startswith("EQ") else ids[tag])
        ET.SubElement(seg, "Connection", FromID=ends[0], FromNode="1", ToID=ends[1], ToNode="2")
    for tag, el in inline.items():  # inline items no line reaches still belong to the sheet
        if tag not in placed:
            root.append(el)
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
