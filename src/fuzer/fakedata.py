"""Make a small, messy, fake offshore asset so Fuzer can be built and tested
without company data. Each source names things its own way on purpose:

- SAP FLOCs:  OSB-20-PT1001A          (SAP is the cleanest source)
- JDE:        tag buried in free text, serials often missing, maker typos
- Maximo:     OSB/20/P-1001A locations, serials as "sn-40123"
- PI:         a different naming style per area, including legacy names
- EC:         its own node codes; one well re-routed, one well sidetracked
- P&IDs:      smart (area 20, 23), text PDF (27), scanned image (44)
- Op logs:    short tags, nicknames ("test sep"), lowercase
"""

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path

FACILITY = "OSB"
AREAS = {"20": "Separation", "23": "Gas Compression", "27": "Oil Export", "44": "Water Injection"}

# area -> (function, loop, suffixes, description)
EQUIPMENT = {
    "20": [("V", 2001, [""], "Production Separator"), ("V", 2002, [""], "Test Separator"),
           ("P", 1001, ["A", "B"], "Separator Oil Pump"), ("E", 3001, [""], "Crude Heater")],
    "23": [("K", 1001, ["A", "B"], "HP Gas Compressor"), ("V", 2001, [""], "Compressor Suction Scrubber"),
           ("E", 3001, ["A", "B"], "Gas Cooler")],
    "27": [("P", 1001, ["A", "B", "C"], "Oil Export Pump"), ("V", 2001, [""], "Export Surge Drum")],
    "44": [("P", 1001, ["A", "B"], "Water Injection Pump"), ("V", 2001, [""], "Deaerator")],
}
INSTRUMENTS = {
    "P": [("PT", "Discharge Pressure", "barg"), ("TT", "Bearing Temperature", "degC"), ("VT", "Vibration", "mm/s")],
    "K": [("PT", "Discharge Pressure", "barg"), ("TT", "Discharge Temperature", "degC"), ("ST", "Speed", "rpm")],
    "V": [("PT", "Vessel Pressure", "barg"), ("LT", "Liquid Level", "%"), ("TT", "Vessel Temperature", "degC")],
    "E": [("TT", "Outlet Temperature", "degC"), ("PDT", "Differential Pressure", "bar")],
}
TEMPLATES = {"P": "Centrifugal Pump", "K": "Compressor", "V": "Pressure Vessel", "E": "Heat Exchanger"}
MAKERS = {"P": ["Flowserve", "Sulzer", "KSB"], "K": ["Siemens Energy", "Baker Hughes"],
          "V": ["Kanfa", "Sembcorp"], "E": ["Alfa Laval", "Kelvion"]}
TYPOS = {"Flowserve": "Flowsrve", "Sulzer": "SULZER PUMPS", "Siemens Energy": "Siemens",
         "Alfa Laval": "Alfa-Laval", "Baker Hughes": "BHGE", "KSB": "K.S.B."}
PI_STYLE = {"20": "OSB.{a}{f}{l}{s}.PV", "23": "OSB:{a}-{f}-{l}{s}.DACA.PV",
            "27": "{a}{f}{l}{s}_PV", "44": "OSB.{a}{f}{l}{s}.PV"}
PID_FORMAT = {"20": "smart", "23": "smart", "27": "pdf_text", "44": "image"}
METER = ("27", "FT", 3001, "Export Oil Fiscal Meter", "Sm3/d")

LOGS = [
    "20-P-1001B tripped on high vibration. Started 20-P-1001A. VT-1001B reading 11 mm/s before trip.",
    "Lined up A-03 to test sep for monthly well test.",
    "A07 ST1 choke opened from 32 to 40%. WHP steady.",
    "PT-2001 on production separator reading erratic, WO raised.",
    "Swapped 27-P-1001A to 27-P-1001C for planned maintenance.",
    "lt-2002 on test separator hunting, put controller in manual.",
    "HP compressor 23-K-1001A tripped on high discharge temp, restarted after 2h.",
    "Seal leak on water injection pump 44P1001B, isolated and drained.",
    "E-3001 outlet temp high, checked heating medium.",
    "Well A-11 shut in for scale squeeze.",
]
AUTHORS = ["Night CRO", "Day CRO", "OIM", "Production Tech"]


@dataclass
class Tag:
    area: str
    func: str
    loop: int
    suffix: str

    def fmt(self, style: str) -> str:
        return style.format(a=self.area, f=self.func, l=self.loop, s=self.suffix)

    @property
    def canonical(self) -> str:
        return self.fmt("{a}-{f}-{l}{s}")


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def generate(out, seed: int = 7, scale: int = 1, n_wells: int = 12) -> dict:
    rng = random.Random(seed)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    floc = [{"floc": FACILITY, "parent": "", "description": "Osprey Bravo Platform"}]
    af = [{"path": FACILITY, "template": "Facility", "attributes": []}]
    equipment, jde, maximo, points, sheets = [], [], [], [], []
    area_tags: dict[str, list[Tag]] = {a: [] for a in AREAS}
    serial = 40000

    for area, items in EQUIPMENT.items():
        area_floc = f"{FACILITY}-{area}"
        floc.append({"floc": area_floc, "parent": FACILITY, "description": f"Area {area} {AREAS[area]}"})
        af.append({"path": f"{FACILITY}\\Area {area}", "template": "Area", "attributes": []})

        for func, base, suffixes, desc in items:
            for i in range(scale):
                for s in suffixes:
                    eq = Tag(area, func, base + 10 * i, s)
                    eq_floc = eq.fmt(f"{FACILITY}-{{a}}-{{f}}{{l}}{{s}}")
                    title = f"{desc} {s}".strip()
                    floc.append({"floc": eq_floc, "parent": area_floc, "description": title})
                    area_tags[area].append(eq)

                    attrs = []
                    for ifunc, idesc, uom in INSTRUMENTS[func]:
                        inst = Tag(area, ifunc, eq.loop, s)
                        area_tags[area].append(inst)
                        floc.append({"floc": inst.fmt(f"{FACILITY}-{{a}}-{{f}}{{l}}{{s}}"), "parent": eq_floc,
                                     "description": f"{title} {idesc}"})
                        point = inst.fmt(PI_STYLE[area])
                        points.append({"tag": point, "descriptor": f"{title} {idesc}", "engunits": uom,
                                       "pointsource": "OPC"})
                        attrs.append({"name": idesc, "pi_point": point, "uom": uom})
                    af.append({"path": f"{FACILITY}\\Area {area}\\{eq.canonical}", "template": TEMPLATES[func],
                               "attributes": attrs})

                    # 20-P-1001B was swapped in 2023: two physical pumps, one tag.
                    swapped = (area, func, s, i) == ("20", "P", "B", 0)
                    installs = [("2018-01-01", "2023-06-01"), ("2023-06-01", "")] if swapped else [("2018-01-01", "")]
                    maker = rng.choice(MAKERS[func])
                    model = f"{maker[:3].upper()}-{rng.randint(100, 999)}"
                    for n, (valid_from, valid_to) in enumerate(installs):
                        serial += rng.randint(1, 900)
                        equipment.append({
                            "equnr": str(10000000 + len(equipment)), "description": desc, "manufacturer": maker,
                            "model": model, "serial": f"SN{serial}", "floc": eq_floc,
                            "valid_from": valid_from, "valid_to": valid_to,
                        })
                        retired = bool(valid_to)
                        _add_jde(rng, jde, eq, desc, maker, model, serial, area, func, s, i, retired)
                        if not retired and rng.random() < 0.6:
                            maximo.append({
                                "assetnum": f"MX-{len(maximo) + 1:05d}", "description": desc,
                                "manufacturer": maker.upper(), "serial": f"sn-{serial}",
                                "location": eq.fmt(f"{FACILITY}/{{a}}/{{f}}-{{l}}{{s}}"),
                            })

    m_area, m_func, m_loop, m_desc, m_uom = METER
    meter = Tag(m_area, m_func, m_loop, "")
    area_tags[m_area].append(meter)
    floc.append({"floc": meter.fmt(f"{FACILITY}-{{a}}-{{f}}{{l}}{{s}}"), "parent": f"{FACILITY}-{m_area}",
                 "description": m_desc})
    points.append({"tag": meter.fmt(PI_STYLE[m_area]), "descriptor": m_desc, "engunits": m_uom,
                   "pointsource": "OPC"})

    wells = [f"A{n:02d}" for n in range(1, n_wells + 1)]
    for w in wells + ["A07A"]:
        label = f"Well {w[0]}-{w[1:3]}" + (" Sidetrack 1" if w.endswith("A") and len(w) == 4 else "")
        floc.append({"floc": f"{FACILITY}-WELL-{w}", "parent": FACILITY, "description": label})
    for w in wells + ["A07ST1"]:
        for code, desc, uom in [("WHP", "Wellhead Pressure", "barg"), ("WHT", "Wellhead Temperature", "degC"),
                                ("CHOKE", "Choke Position", "%")]:
            points.append({"tag": f"{FACILITY}.{w}.{code}", "descriptor": f"{w} {desc}", "engunits": uom,
                           "pointsource": "OPC"})

    ec = _ec_network(wells)

    for area, tags in area_tags.items():
        fmt = PID_FORMAT[area]
        sheet = {"sheet": f"{FACILITY}-{area}-PID-001", "title": f"AREA {area} - {AREAS[area].upper()}",
                 "revision": rng.choice("BCD"), "format": fmt}
        if fmt == "smart":
            sheet["tags"] = [t.canonical for t in tags]
        elif fmt == "pdf_text":
            bubbles = "\n".join(t.fmt("{f}\n{l}{s}") for t in tags)
            sheet["text"] = f"{sheet['title']}\n{bubbles}\nNOTE 3: SEE DWG {FACILITY}-27-PID-002"
        sheets.append(sheet)

    logs = []
    for n in range(30):
        day = f"2024-{1 + n // 5:02d}-{1 + (n * 5) % 28:02d}"
        logs.append({"entry_id": f"LOG-{day}-{n:03d}", "timestamp": f"{day}T{rng.choice(['06', '18'])}:00:00",
                     "facility": FACILITY, "author": rng.choice(AUTHORS), "text": LOGS[n % len(LOGS)]})

    _write_csv(out / "sap_floc.csv", floc)
    _write_csv(out / "sap_equipment.csv", equipment)
    _write_csv(out / "jde_assets.csv", jde)
    _write_csv(out / "maximo_assets.csv", maximo)
    _write_csv(out / "pi_points.csv", points)
    _write_csv(out / "oplogs.csv", logs)
    (out / "pi_af.json").write_text(json.dumps(af, indent=1))
    (out / "ec_network.json").write_text(json.dumps(ec, indent=1))
    (out / "pid_sheets.json").write_text(json.dumps(sheets, indent=1))
    return {"flocs": len(floc), "equipment": len(equipment), "jde": len(jde), "maximo": len(maximo),
            "pi_points": len(points), "af_elements": len(af), "pid_sheets": len(sheets), "logs": len(logs)}


def _add_jde(rng, jde, eq, desc, maker, model, serial, area, func, s, i, retired):
    # Planted cases so every matching path gets exercised.
    conflict = (area, func, s, i) == ("27", "P", "A", 0)      # different maker, same serial
    no_serial = (area, func, s, i) == ("23", "K", "B", 0)     # no serial, no maker -> review
    if not (retired or conflict or no_serial or rng.random() < 0.8):
        return
    has_serial = not no_serial and (retired or conflict or rng.random() < 0.7)
    if conflict:
        mfr = next(m for m in MAKERS[func] if m != maker)
    elif no_serial or (not has_serial and rng.random() < 0.4):
        mfr = ""
    elif rng.random() < 0.3:
        mfr = TYPOS.get(maker, maker)
    else:
        mfr = maker
    tag_text = f"{eq.area}-{eq.func}{eq.loop}" + (f"-{eq.suffix}" if eq.suffix else "")
    jde.append({"asset_number": str(500000 + len(jde)), "description": f"{desc.upper()} - {tag_text}",
                "manufacturer": mfr, "model": model, "serial": str(serial) if has_serial else ""})


def _ec_network(wells: list[str]) -> dict:
    nodes = [{"code": f"{FACILITY}_{w[0]}-{w[1:]}", "type": "well", "name": f"{w[0]}-{w[1:]}"} for w in wells]
    nodes += [
        {"code": f"{FACILITY}_A-07 ST1", "type": "well", "name": "A-07 ST1"},
        {"code": f"{FACILITY}-PM", "type": "manifold", "name": "Production Manifold"},
        {"code": f"{FACILITY}-TM", "type": "manifold", "name": "Test Manifold"},
        {"code": "SEP-PROD", "type": "separator", "tag": "20V2001", "name": "Production Separator"},
        {"code": "SEP-TEST", "type": "separator", "tag": "20V2002", "name": "Test Separator"},
        {"code": "GAS-SCRUB", "type": "separator", "tag": "23V2001", "name": "Compressor Suction Scrubber"},
        {"code": "SURGE", "type": "separator", "tag": "27V2001", "name": "Export Surge Drum"},
        {"code": "MTR-OIL", "type": "meter", "tag": "27FT3001", "name": "Export Oil Fiscal Meter"},
        {"code": f"{FACILITY}-EXPORT", "type": "sales_point", "name": "Oil Export Sales Point"},
    ]
    pm, tm = f"{FACILITY}-PM", f"{FACILITY}-TM"
    edges = []
    for w in wells:
        code = f"{FACILITY}_{w[0]}-{w[1:]}"
        if w == "A03":  # on test until moved to production
            edges += [{"from": code, "to": tm, "valid_from": "2018-01-01", "valid_to": "2024-03-15"},
                      {"from": code, "to": pm, "valid_from": "2024-03-15", "valid_to": ""}]
        elif w == "A07":  # original hole replaced by sidetrack
            edges += [{"from": code, "to": pm, "valid_from": "2018-01-01", "valid_to": "2022-09-01"},
                      {"from": f"{FACILITY}_A-07 ST1", "to": pm, "valid_from": "2022-09-01", "valid_to": ""}]
        else:
            edges.append({"from": code, "to": pm, "valid_from": "2018-01-01", "valid_to": ""})
    for a, b in [(pm, "SEP-PROD"), (tm, "SEP-TEST"), ("SEP-PROD", "GAS-SCRUB"), ("SEP-PROD", "SURGE"),
                 ("SEP-TEST", "SURGE"), ("SURGE", "MTR-OIL"), ("MTR-OIL", f"{FACILITY}-EXPORT")]:
        edges.append({"from": a, "to": b, "valid_from": "", "valid_to": ""})
    return {"nodes": nodes, "edges": edges}
