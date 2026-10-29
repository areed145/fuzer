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
import re
from dataclasses import dataclass
from pathlib import Path

from . import fakedocs, fakepid

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


# Size presets. "small" is the hand-checked asset the tests use; bigger sizes clone the four
# area types into more areas and add trains of equipment inside each area.
SIZES = {
    "small": {"areas": 4, "trains": 1, "wells": 12, "logs": 30, "extra_points": False, "images": True},
    "medium": {"areas": 60, "trains": 8, "wells": 40, "logs": 3000, "extra_points": True, "images": True},
    # Close to a real offshore asset: several instruments of each type per item (loop +10, +20, ...).
    "large": {"areas": 90, "trains": 10, "wells": 60, "logs": 20000, "extra_points": True, "images": True,
              "instrument_repeats": 5},
}
AREA_TYPES = list(AREAS)  # "20", "23", "27", "44": the template for every other area

# Extra log styles used at bigger sizes, including the hard cases rules can't link.
LOG_TEMPLATES = [
    "{pump} tripped on high vibration, started {pump_alt}.",
    "{pump_short} seal leak, isolated and drained.",
    "Lined up {well} to test sep for well test.",
    "{well} choke changed from {a} to {b}%.",
    "{inst} reading erratic, WO raised.",
    "{well} shut in for {reason}.",
    "Swapped {pump} to {pump_alt} for planned maintenance.",
    "{suffix} pump on {area_name} tripped again, operator restarted.",
    "High level alarm on {vessel_desc}, drained back to normal.",
    "{inst_short} spiking, suspect transmitter. Instrument tech called.",
]
REASONS = ["scale squeeze", "workover", "high water cut", "SSV leak test", "slickline"]


def _area_plan(n_areas: int) -> dict[str, tuple[str, str]]:
    """area code -> (template area code, display name)."""
    if n_areas > 90:
        raise ValueError("at most 90 two-digit areas")
    spare = [f"{c:02d}" for c in range(10, 100) if f"{c:02d}" not in AREA_TYPES]
    plan = {}
    for i in range(n_areas):
        tmpl = AREA_TYPES[i % len(AREA_TYPES)]
        copy = i // len(AREA_TYPES) + 1
        plan[AREA_TYPES[i] if i < len(AREA_TYPES) else spare[i - len(AREA_TYPES)]] = (
            tmpl, AREAS[tmpl] + (f" {copy}" if copy > 1 else ""))
    return plan


def generate(out, seed: int = 7, size: str = "small", **overrides) -> dict:
    cfg = {**SIZES[size], **{k: v for k, v in overrides.items() if v is not None}}
    if not 1 <= cfg["trains"] <= 10:
        raise ValueError("trains must be 1-10 (loop numbers step by 100)")
    rng = random.Random(seed)
    big = random.Random(seed + 1)  # only used beyond "small", so small stays identical
    is_big = cfg["areas"] > len(AREA_TYPES) or cfg["trains"] > 1
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    plan = _area_plan(cfg["areas"])
    floc = [{"floc": FACILITY, "parent": "", "description": "Osprey Bravo Platform"}]
    af = [{"path": FACILITY, "template": "Facility", "attributes": []}]
    equipment, jde, maximo, points, sheets = [], [], [], [], []
    sheet_tags: dict[tuple[str, int], list[Tag]] = {}
    titles: dict[str, str] = {}  # canonical tag -> description, for drawing labels
    pumps, vessels, instruments = [], [], []  # for log text at bigger sizes
    serial = 40000

    for area, (tmpl, area_name) in plan.items():
        area_floc = f"{FACILITY}-{area}"
        floc.append({"floc": area_floc, "parent": FACILITY, "description": f"Area {area} {area_name}"})
        af.append({"path": f"{FACILITY}\\Area {area}", "template": "Area", "attributes": []})

        for t in range(cfg["trains"]):
            tags = sheet_tags.setdefault((area, t), [])
            for func, base, suffixes, desc in EQUIPMENT[tmpl]:
                for s in suffixes:
                    eq = Tag(area, func, base + 100 * t, s)
                    eq_floc = eq.fmt(f"{FACILITY}-{{a}}-{{f}}{{l}}{{s}}")
                    title = f"{desc} {s}".strip()
                    floc.append({"floc": eq_floc, "parent": area_floc, "description": title})
                    titles[eq.canonical] = title
                    tags.append(eq)
                    if func == "P":
                        pumps.append((eq, area_name))
                    elif func == "V":
                        vessels.append((eq, title))

                    attrs = []
                    repeats = cfg.get("instrument_repeats", 1)
                    for ifunc, idesc, uom in [(f, d, u) for f, d, u in INSTRUMENTS[func] for _ in range(repeats)]:
                        k = sum(1 for t in tags if t.func == ifunc and t.suffix == s and eq.loop <= t.loop < eq.loop + 100)
                        inst = Tag(area, ifunc, eq.loop + 10 * k, s)
                        idesc = idesc if k == 0 else f"{idesc} {k + 1}"
                        tags.append(inst)
                        instruments.append(inst)
                        floc.append({"floc": inst.fmt(f"{FACILITY}-{{a}}-{{f}}{{l}}{{s}}"), "parent": eq_floc,
                                     "description": f"{title} {idesc}"})
                        point = inst.fmt(PI_STYLE[tmpl])
                        points.append({"tag": point, "descriptor": f"{title} {idesc}", "engunits": uom,
                                       "pointsource": "OPC"})
                        attrs.append({"name": idesc, "pi_point": point, "uom": uom})
                        if cfg["extra_points"]:  # controller setpoint and output alongside the PV
                            for kind in ("SP", "OP"):
                                points.append({"tag": point[:-2] + kind, "descriptor": f"{title} {idesc} {kind}",
                                               "engunits": uom if kind == "SP" else "%", "pointsource": "OPC"})
                    af.append({"path": f"{FACILITY}\\Area {area}\\{eq.canonical}", "template": TEMPLATES[func],
                               "attributes": attrs})

                    # 20-P-1001B was swapped in 2023: two physical pumps, one tag. Bigger sizes add more swaps.
                    if (area, func, s, t) == ("20", "P", "B", 0):
                        swap = "2023-06-01"
                    elif is_big and func in "PK" and big.random() < 0.05:
                        swap = f"{big.randint(2019, 2025)}-{big.randint(1, 12):02d}-01"
                    else:
                        swap = None
                    installs = [("2018-01-01", swap), (swap, "")] if swap else [("2018-01-01", "")]
                    maker = rng.choice(MAKERS[func])
                    # A fleet has a few models per maker, not one per item (same random draw as before).
                    model = f"{maker[:3].upper()}-{(rng.randint(100, 999) % 3 + 1) * 200}"
                    for valid_from, valid_to in installs:
                        serial += rng.randint(1, 900)
                        equipment.append({
                            "equnr": str(10000000 + len(equipment)), "description": desc, "manufacturer": maker,
                            "model": model, "serial": f"SN{serial}", "floc": eq_floc,
                            "valid_from": valid_from, "valid_to": valid_to,
                        })
                        retired = bool(valid_to)
                        _add_jde(rng, jde, eq, desc, maker, model, serial, area, func, s, t, retired)
                        if not retired and rng.random() < 0.6:
                            maximo.append({
                                "assetnum": f"MX-{len(maximo) + 1:05d}", "description": desc,
                                "manufacturer": maker.upper(), "serial": f"sn-{serial}",
                                "location": eq.fmt(f"{FACILITY}/{{a}}/{{f}}-{{l}}{{s}}"),
                            })

    m_area, m_func, m_loop, m_desc, m_uom = METER
    meter = Tag(m_area, m_func, m_loop, "")
    sheet_tags[(m_area, 0)].append(meter)
    floc.append({"floc": meter.fmt(f"{FACILITY}-{{a}}-{{f}}{{l}}{{s}}"), "parent": f"{FACILITY}-{m_area}",
                 "description": m_desc})
    points.append({"tag": meter.fmt(PI_STYLE[m_area]), "descriptor": m_desc, "engunits": m_uom,
                   "pointsource": "OPC"})

    wells = [f"A{n:02d}" for n in range(1, cfg["wells"] + 1)]
    for w in wells + ["A07A"]:
        label = f"Well {w[0]}-{w[1:3]}" + (" Sidetrack 1" if w.endswith("A") and len(w) == 4 else "")
        floc.append({"floc": f"{FACILITY}-WELL-{w}", "parent": FACILITY, "description": label})
    for w in wells + ["A07ST1"]:
        for code, desc, uom in [("WHP", "Wellhead Pressure", "barg"), ("WHT", "Wellhead Temperature", "degC"),
                                ("CHOKE", "Choke Position", "%")]:
            points.append({"tag": f"{FACILITY}.{w}.{code}", "descriptor": f"{w} {desc}", "engunits": uom,
                           "pointsource": "OPC"})

    ec = _ec_network(wells, big if is_big else None)

    pid_truth: dict[str, list[str]] = {}  # answer key for scoring image reading; never ingested
    drawing_rng = random.Random(seed + 2)  # own sequence, so drawings never change the other data
    for (area, t), tags in sheet_tags.items():
        tmpl, area_name = plan[area]
        fmt = PID_FORMAT[tmpl]
        sheet = {"sheet": f"{FACILITY}-{area}-PID-{t + 1:03d}", "title": f"AREA {area} - {area_name.upper()}",
                 "revision": rng.choice("BCD"), "format": fmt}
        if fmt == "smart":
            sheet["tags"] = [tag.canonical for tag in tags]
            sheet["connections"] = _pipe_connections(area, tags, t, len(sheets))
        elif fmt == "pdf_text":
            bubbles = "\n".join(tag.fmt("{f}\n{l}{s}") for tag in tags)
            sheet["text"] = f"{sheet['title']}\n{bubbles}\nNOTE 3: SEE DWG {FACILITY}-{area}-PID-{t + 2:03d}"
        elif fmt == "image" and cfg["images"]:
            sheet["image"] = f"pid/{sheet['sheet']}.png"
            pid_truth[sheet["sheet"]] = fakepid.draw(out / sheet["image"], sheet, tags, titles, drawing_rng)
        sheets.append(sheet)

    logs, log_truth = [], []
    for n in range(min(cfg["logs"], len(LOGS) * 3)):
        day = f"2024-{1 + n // 5:02d}-{1 + (n * 5) % 28:02d}"
        logs.append({"entry_id": f"LOG-{day}-{n:03d}", "timestamp": f"{day}T{rng.choice(['06', '18'])}:00:00",
                     "facility": FACILITY, "author": rng.choice(AUTHORS), "text": LOGS[n % len(LOGS)]})
        keys, events = LOG_TRUTH[n % len(LOGS)]
        log_truth.append({"entry_id": logs[-1]["entry_id"], "mentions": keys, "event_types": events})
    for n in range(len(logs), cfg["logs"]):
        day = f"{big.choice([2024, 2025])}-{big.randint(1, 12):02d}-{big.randint(1, 28):02d}"
        entry = {"entry_id": f"LOG-{day}-{n:05d}", "timestamp": f"{day}T{big.choice(['06', '18'])}:00:00",
                 "facility": FACILITY, "author": big.choice(AUTHORS)}
        entry["text"], truth = _log_text(big, pumps, vessels, instruments, wells)
        logs.append(entry)
        log_truth.append({"entry_id": entry["entry_id"], **truth})

    notifications, work_orders = _maintenance(random.Random(seed + 3), equipment)
    downtime = _downtime(random.Random(seed + 4), notifications, wells)
    daily = _daily_reports(random.Random(seed + 5), downtime, days=60 if not is_big else 730)
    documents = fakedocs.generate(out, equipment, plan, FACILITY)
    materials, bom = _spares(equipment)
    (out / "answer_keys").mkdir(exist_ok=True)
    (out / "answer_keys" / "logs.json").write_text(json.dumps(log_truth, indent=1))

    _write_csv(out / "sap_floc.csv", floc)
    _write_csv(out / "sap_notifications.csv", notifications)
    _write_csv(out / "sap_work_orders.csv", work_orders)
    _write_csv(out / "ec_downtime.csv", downtime)
    _write_csv(out / "sap_materials.csv", materials)
    _write_csv(out / "sap_bom.csv", bom)
    _write_csv(out / "ec_day001.csv", daily)
    _write_csv(out / "sap_equipment.csv", equipment)
    _write_csv(out / "jde_assets.csv", jde)
    _write_csv(out / "maximo_assets.csv", maximo)
    _write_csv(out / "pi_points.csv", points)
    _write_csv(out / "oplogs.csv", logs)
    (out / "pi_af.json").write_text(json.dumps(af, indent=1))
    (out / "ec_network.json").write_text(json.dumps(ec, indent=1))
    (out / "pid_sheets.json").write_text(json.dumps(sheets, indent=1))
    if pid_truth:
        (out / "pid_truth.json").write_text(json.dumps(pid_truth, indent=1))
    return {"size": size, "areas": len(plan), "flocs": len(floc), "equipment": len(equipment), "jde": len(jde),
            "maximo": len(maximo), "pi_points": len(points), "af_elements": len(af), "wells": len(wells),
            "pid_sheets": len(sheets), "pid_images": len(pid_truth), "logs": len(logs),
            "notifications": len(notifications), "work_orders": len(work_orders), "downtime": len(downtime),
            "daily_reports": len(daily), "documents": len(documents), "materials": len(materials)}


# SAP PM catalog codes per equipment type (mapped to ISO 14224 in the asset pack).
DAMAGE = {"P": ["D01", "D02", "D03", "D05", "D06"], "K": ["D01", "D04", "D09", "D06"],
          "E": ["D04", "D08", "D05"], "V": ["D07", "D08", "D02"]}
DAMAGE_TEXT = {"D01": "High vibration on {d}", "D02": "Seal / flange leak on {d}", "D03": "{d} failed to start",
               "D04": "High temperature on {d}", "D05": "Low output from {d}", "D06": "Abnormal noise from {d}",
               "D07": "Level indication faulty on {d}", "D08": "Internal leak suspected on {d}",
               "D09": "{d} tripped unexpectedly", "D10": "{d} broken down"}
CAUSES = {"D01": ["C02", "C06", "C01"], "D02": ["C01", "C03"], "D03": ["C04", "C05"], "D04": ["C05", "C01"],
          "D05": ["C01", "C05"], "D06": ["C01", "C06"], "D07": ["C04"], "D08": ["C03", "C01"], "D09": ["C04", "C02"],
          "D10": ["C01"]}
FAILURE_RATE = {"P": 0.8, "K": 1.0, "E": 0.3, "V": 0.2}  # notifications per item per year
# What corrective work orders actually did, per damage code (picked in turn, no extra random draws).
ACTIONS = {"D01": ["Realign driver and coupling", "Replace bearings", "Balance impeller / rotor"],
           "D02": ["Replace mechanical seal", "Clear seal flush line and replace seal faces"],
           "D03": ["Replace motor starter contactor", "Reset interlock and recalibrate permissive switch"],
           "D04": ["Clean cooler tubes", "Restore cooling medium flow"],
           "D05": ["Replace wear rings", "Clean suction strainer"],
           "D06": ["Replace bearings", "Tighten baseplate bolts"],
           "D07": ["Recalibrate level transmitter", "Clear impulse lines"],
           "D08": ["Replace gasket and pressure test", "Plug leaking tubes"],
           "D09": ["Replace faulty trip transmitter", "Adjust trip setpoint after review"],
           "D10": ["Overhaul unit"]}


def _maintenance(rng: random.Random, equipment: list[dict]) -> tuple[list[dict], list[dict]]:
    """A few years of SAP PM notifications and work orders (own random sequence)."""
    notifications, orders = [], []

    def order(n: dict | None, e: dict, start: str, activity: str, kind: str, text: str):
        finish = _add_days(start, rng.randint(0, 10))
        orders.append({"aufnr": str(40000000 + len(orders)), "order_type": kind, "qmnum": n["qmnum"] if n else "",
                       "equnr": e["equnr"], "floc": e["floc"], "start": start, "finish": finish,
                       "activity_code": activity, "cost": f"{rng.uniform(500, 40000):.0f}", "text": text})

    for e in equipment:
        func = re.match(r"[A-Z]+-\d{2}-([A-Z]+)", e["floc"]).group(1)
        first = max(2019, int(e["valid_from"][:4]))
        last = int(e["valid_to"][:4]) - 1 if e["valid_to"] else 2025
        for year in range(first, last + 1):
            for _ in range(sum(rng.random() < FAILURE_RATE[func] / 4 for _ in range(4))):
                damage = rng.choice(DAMAGE[func])
                created = f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
                n = {"qmnum": str(10000000 + len(notifications)), "notif_type": "M1", "created": created,
                     "equnr": e["equnr"], "floc": e["floc"], "priority": str(rng.randint(1, 4)),
                     "short_text": DAMAGE_TEXT[damage].format(d=e["description"].lower()),
                     "damage_code": damage, "cause_code": rng.choice(CAUSES[damage]), "status": "NOCO"}
                notifications.append(n)
                if rng.random() < 0.85:
                    actions = ACTIONS[damage]
                    order(n, e, _add_days(created, rng.randint(0, 5)), rng.choice(["A01", "A02", "A03"]), "PM01",
                          actions[len(orders) % len(actions)])
            if rng.random() < 0.5:  # preventive inspection
                order(None, e, f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}", rng.choice(["A04", "A05"]),
                      "PM02", f"Preventive maintenance {e['description'].lower()}")

    # Planted: the 2024-01-01 trip of 20-P-1001B in the operator log got a notification the next day.
    current = next(e for e in equipment if e["floc"].endswith("-20-P1001B") and not e["valid_to"])
    n = {"qmnum": str(10000000 + len(notifications)), "notif_type": "M1", "created": "2024-01-02",
         "equnr": current["equnr"], "floc": current["floc"], "priority": "2",
         "short_text": "Separator oil pump B tripped on high vibration", "damage_code": "D01", "cause_code": "C06",
         "status": "NOCO"}
    notifications.append(n)
    order(n, current, "2024-01-03", "A03", "PM01", "Realign pump and motor, check bearings")
    return notifications, orders


def _downtime(rng: random.Random, notifications: list[dict], wells: list[str]) -> list[dict]:
    """EC downtime / deferment records: failures of big rotating equipment take the facility down;
    wells have interventions; plus planned shutdowns and weather (own random sequence)."""
    rows = []

    def add(obj, start, hours, reason, comment, rate):
        end = _add_hours(start, hours)
        rows.append({"event_id": f"DT-{len(rows) + 1:05d}", "object_code": obj, "start": start, "end": end,
                     "reason_code": reason, "lost_oil_sm3": f"{rate * hours / 24:.0f}",
                     "lost_gas_ksm3": f"{rate * hours / 24 * 0.12:.0f}", "comment": comment})

    tag_of = lambda floc: re.sub(r"^[A-Z]+-(\d{2})-([A-Z]+)(\d+)([A-Z]?)$", r"\1-\2-\3\4", floc)
    for n in notifications:
        if n["damage_code"] in ("D01", "D03", "D09", "D10") and ("-K" in n["floc"] or "-27-P" in n["floc"]) \
                and rng.random() < 0.3:
            tag = tag_of(n["floc"])
            hours = rng.choice([2, 4, 6, 12, 24])
            add(FACILITY, f"{n['created']}T{rng.randint(0, 23):02d}:00", hours, rng.choice(["EQ-FAIL", "PROC-TRIP"]),
                f"{tag} {n['short_text'].lower()}; wells choked back, restarted after {hours}h", 9000)
    for year in range(2019, 2026):
        add(FACILITY, f"{year}-0{rng.randint(5, 8)}-0{rng.randint(1, 9)}T06:00", rng.choice([72, 96, 120]),
            "PLAN-MAINT", "Planned annual shutdown", 9000)
        for _ in range(rng.randint(1, 3)):
            add(FACILITY, f"{year}-{rng.choice(['01', '02', '11', '12'])}-{rng.randint(10, 28)}T00:00",
                rng.choice([6, 12, 24]), "WEATHER", "Export tanker disconnected, high seas", 9000)
        for w in rng.sample(wells, k=min(len(wells), 3)):
            add(f"{FACILITY}_{w[0]}-{w[1:]}", f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T08:00",
                rng.choice([24, 48, 96]), "WELL-INT", f"{w} shut in for {rng.choice(REASONS)}", 400)
    # Planted mismatches: the comment names failed equipment but the reason code says weather.
    add(FACILITY, "2024-02-03T14:00", 2, "WEATHER", "23-K-1001A tripped on high discharge temp, restarted after 2h", 9000)
    add(f"{FACILITY}_A-11", "2024-02-18T08:00", 72, "WELL-INT", "A-11 shut in for scale squeeze", 400)
    return rows


def _daily_reports(rng: random.Random, downtime: list[dict], days: int) -> list[dict]:
    """EC DAY001: one report a day with production figures and a comment that mentions that day's downtime."""
    from datetime import date, timedelta
    by_day: dict[str, list[dict]] = {}
    for d in downtime:
        by_day.setdefault(d["start"][:10], []).append(d)
    rows, oil = [], 40000.0
    for i in range(days):
        day = (date(2024, 1, 1) + timedelta(days=i)).isoformat()
        oil = max(25000.0, min(52000.0, oil + rng.uniform(-900, 900)))
        events = by_day.get(day, [])
        lost = sum(float(d["lost_oil_sm3"]) for d in events)
        notes = [d["comment"] for d in events] or [rng.choice(["Steady production.", "All wells online.",
                                                               "Routine operations, no upsets."])]
        rows.append({"report_date": day, "facility": FACILITY, "oil_sm3": f"{oil - lost:.0f}",
                     "gas_ksm3": f"{(oil - lost) * 0.12:.0f}", "water_inj_sm3": f"{rng.uniform(30000, 45000):.0f}",
                     "comments": " ".join(notes)})
    return rows


def _spares(equipment: list[dict]) -> tuple[list[dict], list[dict]]:
    """SAP materials for each model's spare parts (the part numbers its manual lists) and an equipment
    BOM line per part. Stock levels are deterministic; some are zero."""
    parts = [("SK1", "Seal kit"), ("BS2", "Bearing set"), ("GS3", "Gasket set")]
    materials, by_mpn, bom = [], {}, []
    for e in equipment:
        for code, name in parts:
            mpn = f"{e['model']}-{code}"
            if mpn not in by_mpn:
                n = len(materials)
                by_mpn[mpn] = f"MAT-{100001 + n}"
                materials.append({"matnr": by_mpn[mpn], "description": f"{name}, {e['manufacturer']} {e['model']}",
                                  "manufacturer": e["manufacturer"], "mpn": mpn, "stock": str((n * 7) % 5),
                                  "bin": f"W{1 + n % 3}-{10 + n % 40}"})
            if not e["valid_to"]:
                bom.append({"equnr": e["equnr"], "matnr": by_mpn[mpn], "quantity": "1"})
    return materials, bom


def _add_hours(when: str, n: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.fromisoformat(when) + timedelta(hours=n)).isoformat(timespec="minutes")


def _add_days(day: str, n: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


# What a correct reading of each log line finds: mentioned keys and event types (answer key).
LOG_TRUTH = [
    (["20-P-1001B", "20-P-1001A", "20-VT-1001B"], ["trip", "start"]),
    (["A-03", "20-V-2002"], ["well_test"]),
    (["A-07 ST1"], ["choke_change"]),
    (["20-PT-2001", "20-V-2001"], ["instrument_fault"]),
    (["27-P-1001A", "27-P-1001C"], ["changeover"]),
    (["20-LT-2002", "20-V-2002"], ["instrument_fault"]),
    (["23-K-1001A"], ["trip"]),
    (["44-P-1001B"], ["leak"]),
    (["20-E-3001"], ["alarm"]),
    (["A-11"], ["shut_in"]),
]
TEMPLATE_TRUTH = [  # aligned with LOG_TEMPLATES
    (lambda v: [v["pump"], v["alt"]], ["trip", "start"]),
    (lambda v: [v["pump"]], ["leak"]),
    (lambda v: [v["well"], "20-V-2002"], ["well_test"]),
    (lambda v: [v["well"]], ["choke_change"]),
    (lambda v: [v["inst"]], ["instrument_fault"]),
    (lambda v: [v["well"]], ["shut_in"]),
    (lambda v: [v["pump"], v["alt"]], ["changeover"]),
    (lambda v: [v["pump"]], ["trip"]),
    (lambda v: [v["vessel"]], ["alarm"]),
    (lambda v: [v["inst"]], ["instrument_fault"]),
]


# Process lines that leave a sheet for another area (as on real P&IDs: off-page connectors). Planted gap:
# EC routes the test separator 20-V-2002 to the surge drum 27-V-2001, but no P&ID shows that line.
OFF_PAGE = {"20": [("20-V-2001", "23-V-2001"), ("20-V-2001", "27-V-2001")]}


def _pipe_connections(area: str, tags: list, train: int, sheet_no: int) -> list[dict]:
    """Piping on a smart P&ID (DEXPI-style): consecutive equipment on the sheet, plus off-page lines."""
    equipment = [t.canonical for t in tags if t.func in ("P", "K", "E", "V")]
    pairs = list(zip(equipment, equipment[1:]))
    if train == 0:  # the first train's sheet carries the area's off-page lines
        pairs += OFF_PAGE.get(area, [])
    return [{"from": a, "to": b, "line": f'6"-P-{a[:2]}{100 + sheet_no * 10 + i}-A1'} for i, (a, b) in enumerate(pairs)]


def _log_text(rng, pumps, vessels, instruments, wells) -> tuple[str, dict]:
    # Random draws stay in the order the original single expression made them, so data doesn't change.
    pump, area_name = rng.choice(pumps)
    alt = Tag(pump.area, pump.func, pump.loop, "A" if pump.suffix != "A" else "B")
    inst = rng.choice(instruments)
    w = rng.choice(wells)
    template = rng.choice(LOG_TEMPLATES)
    well_text = rng.choice([f"A-{w[1:]}", w, f"well {w}"])
    a, b = rng.randint(10, 50), rng.randint(20, 80)
    reason = rng.choice(REASONS)
    vessel, vessel_title = rng.choice(vessels)
    text = template.format(
        pump=pump.canonical, pump_alt=alt.canonical, pump_short=pump.fmt("{f}-{l}{s}"), well=well_text, a=a, b=b,
        inst=inst.canonical, inst_short=inst.fmt("{f}-{l}{s}"), reason=reason, suffix=pump.suffix or "A",
        area_name=area_name.lower(), vessel_desc=vessel_title.lower(),
    )
    keys, events = TEMPLATE_TRUTH[LOG_TEMPLATES.index(template)]
    names = {"pump": pump.canonical, "alt": alt.canonical, "inst": inst.canonical, "well": f"A-{w[1:]}",
             "vessel": vessel.canonical}
    return text, {"mentions": keys(names), "event_types": events}


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


def _ec_network(wells: list[str], rng: random.Random | None = None) -> dict:
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
        elif rng and int(w[1:]) > 12 and rng.random() < 0.15:  # spent time on test, then production
            moved = f"{rng.randint(2019, 2025)}-{rng.randint(1, 12):02d}-01"
            edges += [{"from": code, "to": tm, "valid_from": "2018-01-01", "valid_to": moved},
                      {"from": code, "to": pm, "valid_from": moved, "valid_to": ""}]
        else:
            edges.append({"from": code, "to": pm, "valid_from": "2018-01-01", "valid_to": ""})
    for a, b in [(pm, "SEP-PROD"), (tm, "SEP-TEST"), ("SEP-PROD", "GAS-SCRUB"), ("SEP-PROD", "SURGE"),
                 ("SEP-TEST", "SURGE"), ("SURGE", "MTR-OIL"), ("MTR-OIL", f"{FACILITY}-EXPORT")]:
        edges.append({"from": a, "to": b, "valid_from": "", "valid_to": ""})
    return {"nodes": nodes, "edges": edges}
