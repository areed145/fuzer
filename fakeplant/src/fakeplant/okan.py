"""Okan field demo: the troubleshooting scenario for the South Offshore area of the NMA business unit.

One model of the field drives every source, so they agree with each other:

- Okan Production Platform (OKA_PP, area 10): separation, oil export to the Escravos terminal.
- Gas gathering and compression platform (OK_GGCP, area 20), bridge-linked to PP: inlet separation,
  three 3-stage trains A/B/C driven by gas turbines, the lift gas header (gas reaches it compressed
  or through the compression bypass 20-XV-3100) and gas export to the Escravos Gas Plant (EGP).
- Satellites OKA_ST1 (30) and OKA_ST2 (40) gathering jacket production to PP.
- New Wells Platform OKA_NWP (50): wellheads on the facility, oil wells and the naturally flowing gas
  producers OK-NWP-G1..G3 that are the lift gas source; NWP can also export gas to EGP.
- Jackets OKJ-01..24 (areas 61-84), 5-10 wellheads each, mostly gas lifted (GL), some naturally
  flowing (NF), each with a gas lift supply meter and ESDV.

Sources: SAP (FLOCs, equipment, notifications, work orders, materials, BOM), PI points and the AF
tree, the EC allocation network, downtime and DAY001 reports, operator logs, P&ID drawings, manuals
and troubleshooting guides, and GIS (facility locations, pipelines and flowlines as GeoJSON).

Planted troubleshooting stories run through all of them (see STORIES): train B second-stage
bearing wear and the lift gas shortfalls its trips cause, train A after-cooler fouling, inlet
separator carryover, hydrates on an NWP gas producer, EGP export curtailment, gas lift valve
failures and an export pump seal leak.
"""

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

from . import docs, pid
from .offshore import ACTIONS, CAUSES, DAMAGE_TEXT, Tag, _add_days, _add_hours, _spares, _write_csv

FIELD = "OKAN"
AS_OF = "2025-12-31"  # the demo's "today": open work, overdue plans and backlog ages count from here
CENTRE = (4.985, 5.420)  # lon, lat
EGP_AT, TERMINAL_AT = (5.180, 5.585), (5.150, 5.555)

FACILITIES = {  # area -> (code, name, type, (east km, north km) from PP)
    "10": ("OKA_PP", "Okan Production Platform", "production platform", (0.0, 0.0)),
    "20": ("OK_GGCP", "Okan Gas Gathering and Compression Platform", "compression platform", (0.08, 0.0)),
    "30": ("OKA_ST1", "Okan Satellite 1", "satellite platform", (-4.2, 0.6)),
    "40": ("OKA_ST2", "Okan Satellite 2", "satellite platform", (0.9, -4.4)),
    "50": ("OKA_NWP", "Okan New Wells Platform", "wellhead platform", (1.4, 1.1)),
}
JACKETS = 24
HUBS = ("10", "30", "40")  # where jackets send oil and get lift gas: PP / GGCP, ST1, ST2
# Jackets were built around a host platform (their position); the hub they are piped to is the nearest.
HOST = {**{j: "10" for j in range(1, 9)}, **{j: "30" for j in range(9, 17)}, **{j: "40" for j in range(17, 25)}}

# area -> (function, loop, suffixes, description, AF template)
EQUIPMENT = {
    "10": [("V", 2001, [""], "HP Production Separator", "Pressure Vessel"),
           ("V", 2002, [""], "LP Production Separator", "Pressure Vessel"),
           ("V", 2003, [""], "Test Separator", "Pressure Vessel"),
           ("E", 3001, [""], "Crude Cooler", "Heat Exchanger"),
           ("V", 2004, [""], "Export Surge Drum", "Pressure Vessel"),
           ("P", 1001, ["A", "B", "C"], "Oil Export Pump", "Centrifugal Pump")],
    "20": [("V", 2001, [""], "GGCP Inlet Separator", "Pressure Vessel"),
           ("V", 2002, [""], "Lift Gas Scrubber", "Pressure Vessel"),
           ("V", 2400, [""], "Glycol Contactor", "Pressure Vessel")],
    "30": [("V", 2001, [""], "ST1 Test Separator", "Pressure Vessel"),
           ("V", 2002, [""], "ST1 Production Separator", "Pressure Vessel")],
    "40": [("V", 2001, [""], "ST2 Test Separator", "Pressure Vessel"),
           ("V", 2002, [""], "ST2 Production Separator", "Pressure Vessel")],
    "50": [("V", 2001, [""], "NWP Production Separator", "Pressure Vessel"),
           ("V", 2002, [""], "NWP Test Separator", "Pressure Vessel")],
}
TRAIN = [  # per train A/B/C on GGCP: 3 stages of scrubber, compressor and after-cooler, one gas turbine
    ("GT", 2000, "Gas Turbine Driver", "Gas Turbine"),
    ("V", 2101, "1st Stage Suction Scrubber", "Pressure Vessel"), ("K", 2100, "1st Stage Compressor", "Compressor"),
    ("E", 2150, "1st Stage After-cooler", "Heat Exchanger"),
    ("V", 2201, "2nd Stage Suction Scrubber", "Pressure Vessel"), ("K", 2200, "2nd Stage Compressor", "Compressor"),
    ("E", 2250, "2nd Stage After-cooler", "Heat Exchanger"),
    ("V", 2301, "3rd Stage Suction Scrubber", "Pressure Vessel"), ("K", 2300, "3rd Stage Compressor", "Compressor"),
    ("E", 2350, "3rd Stage After-cooler", "Heat Exchanger"),
]
INSTRUMENTS = {
    "P": [("PT", "Discharge Pressure", "barg"), ("TT", "Bearing Temperature", "degC"), ("VT", "Vibration", "mm/s")],
    "K": [("PT", "Discharge Pressure", "barg"), ("TT", "Discharge Temperature", "degC"), ("VT", "Vibration", "mm/s")],
    "GT": [("ST", "Speed", "rpm"), ("TT", "Exhaust Temperature", "degC")],
    "V": [("PT", "Vessel Pressure", "barg"), ("LT", "Liquid Level", "%")],
    "E": [("TT", "Outlet Temperature", "degC")],
}
# Single instruments and valves that aren't on an item: area -> (function, loop, description, uom)
LOOSE = {
    "10": [("FT", 3001, "Oil Export Fiscal Meter", "Sm3/d")],
    "20": [("FT", 3001, "EGP Gas Export Meter", "kSm3/d"), ("FT", 3002, "GGCP Lift Gas Header Flow", "kSm3/d"),
           ("PT", 3003, "GGCP Lift Gas Header Pressure", "barg"), ("XV", 3100, "Compression Bypass Valve", "%")],
    "30": [("FT", 3001, "ST1 Trunkline Meter", "Sm3/d"), ("FT", 3002, "ST1 Lift Gas Header Flow", "kSm3/d"),
           ("PT", 3003, "ST1 Lift Gas Header Pressure", "barg")],
    "40": [("FT", 3001, "ST2 Trunkline Meter", "Sm3/d"), ("FT", 3002, "ST2 Lift Gas Header Flow", "kSm3/d"),
           ("PT", 3003, "ST2 Lift Gas Header Pressure", "barg")],
    "50": [("FT", 3001, "NWP Gas Export Meter", "kSm3/d"), ("XV", 3100, "NWP EGP Export ESDV", "%")],
}
JACKET_ITEMS = [("FT", 3001, "Gas Lift Supply Meter", "kSm3/d"), ("PT", 3002, "Production Header Pressure", "barg"),
                ("XV", 3100, "Jacket ESDV", "%")]
MAKERS = {"P": ["Flowserve", "Sulzer"], "K": ["Solar Turbines", "Baker Hughes", "Siemens Energy"],
          "GT": ["Solar Turbines"], "V": ["Kanfa", "Sembcorp"], "E": ["Alfa Laval", "Kelvion"]}
MODEL = {"Solar Turbines": {"K": "C40", "GT": "Taurus 60"}, "Baker Hughes": {"K": "BCL-406"},
         "Siemens Energy": {"K": "STC-SV"}}
PI_STYLE = {"10": "OKA_PP.{a}{f}{l}{s}.PV", "20": "OK_GGCP:{a}-{f}-{l}{s}.PV", "30": "{a}{f}{l}{s}.PV",
            "40": "{a}{f}{l}{s}.PV", "50": "OKA_NWP.{a}{f}{l}{s}.PV", "jacket": "{a}{f}{l}{s}.PV"}

DAMAGE = {"P": ["D01", "D02", "D03", "D05"], "K": ["D01", "D04", "D09", "D06"], "GT": ["D03", "D04", "D09"],
          "E": ["D04", "D08"], "V": ["D07", "D08", "D02"], "XV": ["D03", "D08"], "FT": ["D07"], "PT": ["D07"]}
FAILURE_RATE = {"P": 0.8, "K": 1.2, "GT": 0.9, "E": 0.3, "V": 0.2}
AUTHORS = ["GGCP CRO", "PP CRO", "OIM", "Production Engineer", "Night Supervisor", "Field Operator"]


# Questions for the operations assistant, one per domain plus one across domains, with what a good answer
# must name (case-insensitive; a list means any of these) given the planted stories. `fuzer eval --agents` scores them (costs money).
ASSISTANT_QUESTIONS = [
    {"domain": "troubleshooting", "question": "Why did 20-K-2200B keep tripping in March 2024, and what should we "
     "check if it happens again?", "must_mention": ["20-K-2200B", "bearing", "vibration"],
     "specialists": ["troubleshooter"]},
    {"domain": "production efficiency", "question": "What was our production efficiency in 2025, and what were "
     "the biggest losses?", "must_mention": ["planned", "%"], "specialists": ["production"]},
    {"domain": "production surveillance", "question": "How did oil production trend through 2025, and what "
     "explains the changes?", "must_mention": ["Sm3", "planned"], "specialists": ["production"]},
    {"domain": "constraint management", "question": "What has been limiting production over the last two years, "
     "and how does it cascade through the field?", "must_mention": ["lift gas", "20-K-2200B"],
     "specialists": ["production"]},
    {"domain": "production engineering", "question": "Which wells lost the most oil rate in 2025, and why?",
     "must_mention": ["OK-J03-02", "water"], "specialists": ["production"]},
    {"domain": "reliability", "question": "Who are our bad actors over the last two years, and what are their "
     "repeat failure modes?", "must_mention": ["20-K-2200B", "MTBF"], "specialists": ["maintenance"]},
    {"domain": "work orders", "question": "What in the work order backlog should we tackle first?",
     "must_mention": ["overdue", ["priority", "P1", "P2"]], "specialists": ["maintenance"]},
    {"domain": "turnarounds", "question": "What is planned for the next turnaround, and what is missing from "
     "its scope?", "must_mention": ["OK-TAR-2026", "inspection"], "specialists": ["maintenance"]},
    {"domain": "gas lift", "question": "How is lift gas distributed across the headers, and where would moving lift "
     "gas between wells gain the most oil?", "must_mention": [["ST1", "30-FT-3002"], ["ST2", "40-FT-3002"], "kSm3"],
     "specialists": ["production"]},
    {"domain": "across domains", "question": "Why does lift gas keep running short, and what should go in the next "
     "turnaround to fix it?", "must_mention": ["20-K-2200B", "OK-TAR-2026"],
     "specialists": ["production", "maintenance"]},
]


def well_name(site: str, num: str) -> str:
    return f"OK-{site}-{num}"


def _xy(east_km: float, north_km: float, origin=CENTRE) -> list[float]:
    lon, lat = origin
    return [round(lon + east_km / (111.32 * math.cos(math.radians(lat))), 6), round(lat + north_km / 110.57, 6)]


def _km(a: list[float], b: list[float]) -> float:
    dx = (b[0] - a[0]) * 111.32 * math.cos(math.radians(a[1]))
    dy = (b[1] - a[1]) * 110.57
    return math.hypot(dx, dy)


def generate(out, seed: int = 11) -> dict:
    rng = random.Random(seed)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    # ---- hierarchy, equipment, instruments, PI and AF -------------------------------------------------
    floc = [{"floc": FIELD, "parent": "", "description": "Okan field (NMA South Offshore)"}]
    # AF: field \ installation (area number first) \ item. The BU and area sit on the field element.
    af = [{"path": FIELD, "template": "Field", "description": "Okan field, NMA business unit, South Offshore area",
           "attributes": []}]
    equipment, points, titles = [], [], {}
    sheet_tags: dict[str, list[Tag]] = {}
    serial = 70000

    def add_points(area: str, inst: Tag, desc: str, uom: str, style: str) -> dict:
        point = inst.fmt(style)
        points.append({"tag": point, "descriptor": desc, "engunits": uom, "pointsource": "OPC"})
        return {"name": desc, "pi_point": point, "uom": uom}

    def add_item(area: str, func: str, loop: int, s: str, desc: str, template: str, sheet: str, parent_path: str):
        nonlocal serial
        eq = Tag(area, func, loop, s)
        eq_floc = eq.fmt(f"{FIELD}-{{a}}-{{f}}{{l}}{{s}}")
        title = f"{desc} {s}".strip() if func not in ("GT", "K", "E") or not s else f"Train {s} {desc}"
        floc.append({"floc": eq_floc, "parent": f"{FIELD}-{area}", "description": title})
        titles[eq.canonical] = title
        sheet_tags.setdefault(sheet, []).append(eq)
        attrs = []
        for ifunc, idesc, uom in INSTRUMENTS[func]:
            inst = Tag(area, ifunc, loop, s)
            sheet_tags[sheet].append(inst)
            floc.append({"floc": inst.fmt(f"{FIELD}-{{a}}-{{f}}{{l}}{{s}}"), "parent": eq_floc,
                         "description": f"{title} {idesc}"})
            attrs.append(add_points(area, inst, f"{title} {idesc}", uom, PI_STYLE[area]))
        af.append({"path": f"{parent_path}\\{eq.canonical}", "template": template, "attributes": attrs})
        maker = rng.choice(MAKERS[func])
        model = MODEL.get(maker, {}).get(func) or f"{maker[:3].upper()}-{rng.choice([200, 400, 600])}"
        # Train C came later (2022); everything else in 2009.
        since = "2022-03-01" if s == "C" and area == "20" else "2009-06-01"
        serial += rng.randint(1, 900)
        equipment.append({"equnr": str(30000000 + len(equipment)), "description": desc, "manufacturer": maker,
                          "model": model, "serial": f"SN{serial}", "floc": eq_floc, "valid_from": since, "valid_to": ""})

    for area, (code, name, ftype, _) in FACILITIES.items():
        floc.append({"floc": f"{FIELD}-{area}", "parent": FIELD, "description": f"{code} {name}"})
        path = f"{FIELD}\\{area} {code}"
        af.append({"path": path, "template": "Facility", "attributes": []})
        sheet = f"{area}-1"
        for func, loop, suffixes, desc, template in EQUIPMENT[area]:
            for s in suffixes:
                if area == "10" and func == "P":
                    sheet = "10-2"
                add_item(area, func, loop, s, desc, template, sheet, path)
        if area == "20":
            for t in "ABC":
                for func, loop, desc, template in TRAIN:
                    add_item(area, func, loop, t, desc, template, f"20-{t}", path)
        for func, loop, desc, uom in LOOSE[area]:
            inst = Tag(area, func, loop, "")
            sheet_tags.setdefault(f"{area}-1", []).append(inst)
            floc.append({"floc": inst.fmt(f"{FIELD}-{{a}}-{{f}}{{l}}{{s}}"), "parent": f"{FIELD}-{area}",
                         "description": desc})
            add_points(area, inst, desc, uom, PI_STYLE[area])

    # ---- wells and jackets ----------------------------------------------------------------------------
    wells: list[dict] = []  # {name, site, area, lift, type}
    jacket_at = {}
    for j in range(1, JACKETS + 1):
        area = str(60 + j)
        host = HOST[j]
        hx, hy = FACILITIES[host][3]
        ang = rng.uniform(0, 2 * math.pi)
        dist = rng.uniform(1.2, 4.5)
        jacket_at[area] = (hx + dist * math.cos(ang), hy + dist * math.sin(ang))
        code = f"OKJ-{j:02d}"
        floc.append({"floc": f"{FIELD}-{area}", "parent": FIELD, "description": f"{code} Okan Jacket {j:02d}"})
        path = f"{FIELD}\\{area} {code}"
        af.append({"path": path, "template": "Jacket", "attributes": []})
        for func, loop, desc, uom in JACKET_ITEMS:
            inst = Tag(area, func, loop, "")
            floc.append({"floc": inst.fmt(f"{FIELD}-{{a}}-{{f}}{{l}}{{s}}"), "parent": f"{FIELD}-{area}",
                         "description": f"{code} {desc}"})
            add_points(area, inst, f"{code} {desc}", uom, PI_STYLE["jacket"])
        for n in range(1, rng.randint(5, 10) + 1):
            lift = "GL" if rng.random() < 0.82 else "NF"
            if (j, n) in ((7, 3), (7, 5), (12, 2)):  # the stories' gas lifted wells
                lift = "GL"
            wells.append({"name": well_name(f"J{j:02d}", f"{n:02d}"), "site": f"J{j:02d}", "area": area,
                          "lift": lift, "type": "oil"})
    # Lift gas and production networks: each jacket belongs to the nearest hub (PP / GGCP, ST1 or ST2), and
    # is piped through a neighbouring jacket on the way when that saves a lot of pipe (shared corridors),
    # so some jackets' oil and lift gas pass through another jacket's manifolds. At most two jackets deep.
    hub = {a: min(HUBS, key=lambda h: math.dist(xy, FACILITIES[h][3])) for a, xy in jacket_at.items()}
    route, depth = {}, {}
    for a in sorted(jacket_at, key=lambda a: math.dist(jacket_at[a], FACILITIES[hub[a]][3])):
        home = FACILITIES[hub[a]][3]
        direct = math.dist(jacket_at[a], home)
        via = min((b for b in route if hub[b] == hub[a] and depth[b] < 2 and math.dist(jacket_at[b], home) < direct),
                  key=lambda b: math.dist(jacket_at[a], jacket_at[b]), default=None)
        if via and math.dist(jacket_at[a], jacket_at[via]) < 0.55 * direct:
            route[a], depth[a] = via, depth[via] + 1
        else:
            route[a], depth[a] = hub[a], 0

    for n in range(1, 9):
        wells.append({"name": well_name("NWP", f"{n:02d}"), "site": "NWP", "area": "50",
                      "lift": "GL" if n % 3 else "NF", "type": "oil"})
    for n in range(1, 4):
        wells.append({"name": well_name("NWP", f"G{n}"), "site": "NWP", "area": "50", "lift": "NF", "type": "gas"})

    for w in wells:
        site, num = w["site"], w["name"].rsplit("-", 1)[1]
        kind = "gas producer" if w["type"] == "gas" else f"{w['lift']} oil producer"
        floc.append({"floc": f"{FIELD}-WELL-OK{site}-{num}", "parent": f"{FIELD}-{w['area']}",
                     "description": f"Well {w['name']} ({kind})"})
        stem = f"OK_{site}_{num}"
        codes = [("WHP", "Wellhead Pressure", "barg"), ("WHT", "Wellhead Temperature", "degC"),
                 ("CHOKE", "Choke Position", "%")]
        if w["lift"] == "GL":
            codes += [("GLIR", "Gas Lift Injection Rate", "kSm3/d"), ("CASP", "Casing Pressure", "barg")]
        if w["type"] == "gas":
            codes += [("QGAS", "Gas Rate", "kSm3/d")]
        for c, d, uom in codes:
            points.append({"tag": f"{stem}.{c}", "descriptor": f"{w['name']} {d}", "engunits": uom,
                           "pointsource": "OPC"})

    gl_wells = [w for w in wells if w["lift"] == "GL" and w["site"].startswith("J")]
    gas_wells = [w for w in wells if w["type"] == "gas"]

    # ---- maintenance, stories, downtime, logs, daily reports ------------------------------------------
    by_tag = {e["floc"].split("-", 1)[1]: e for e in equipment}  # "20-K2200B" -> equipment

    def eq_of(tag: str) -> dict:
        area, rest = tag.split("-", 1)
        return by_tag[f"{area}-{rest.replace('-', '')}"]

    notifications, orders, downtime, logs = [], [], [], []
    mrng, drng, lrng = random.Random(seed + 3), random.Random(seed + 4), random.Random(seed + 5)

    def notify(tag: str, created: str, damage: str, text: str, cause: str | None = None, prio: str = "2") -> dict:
        e = eq_of(tag)
        n = {"qmnum": str(20000000 + len(notifications)), "notif_type": "M1", "created": created, "equnr": e["equnr"],
             "floc": e["floc"], "priority": prio, "short_text": text, "damage_code": damage,
             "cause_code": cause or mrng.choice(CAUSES[damage]), "status": "NOCO"}
        notifications.append(n)
        return n

    def work(n: dict | None, tag: str, start: str, activity: str, kind: str, text: str, days: int | None = None,
             status: str = "TECO", priority: str | None = None, due: str | None = None, revision: str = ""):
        e = eq_of(tag)
        finish = _add_days(start, mrng.randint(0, 6) if days is None else days)
        orders.append({"aufnr": str(50000000 + len(orders)), "order_type": kind, "qmnum": n["qmnum"] if n else "",
                       "equnr": e["equnr"], "floc": e["floc"], "start": start,
                       "finish": finish if status == "TECO" else "", "basic_finish": due or finish,
                       "status": status, "priority": priority or (n["priority"] if n else "3"), "revision": revision,
                       "activity_code": activity, "cost": f"{mrng.uniform(800, 60000):.0f}" if status == "TECO" else "",
                       "text": text})

    def lost(obj: str, start: str, hours: float, reason: str, comment: str, oil_rate: float, gas_rate: float = 0):
        downtime.append({"event_id": f"OKDT-{len(downtime) + 1:05d}", "object_code": obj, "start": start,
                         "end": _add_hours(start, int(hours)), "reason_code": reason,
                         "lost_oil_sm3": f"{oil_rate * hours / 24:.0f}", "lost_gas_ksm3": f"{gas_rate * hours / 24:.0f}",
                         "comment": comment})

    def log(when: str, text: str, author: str | None = None):
        logs.append({"entry_id": f"OKLOG-{when[:10]}-{len(logs):05d}", "timestamp": when, "facility": FIELD,
                     "author": author or lrng.choice(AUTHORS), "text": text})

    # Background corrective and preventive maintenance on every item (own random sequence).
    for e in equipment:
        _, area, rest = e["floc"].split("-")  # OKAN-20-K2200B
        func = rest.rstrip("0123456789ABC")
        tag = f"{area}-{func}-{rest[len(func):]}"
        for year in range(max(2020, int(e["valid_from"][:4])), 2026):
            for _ in range(sum(mrng.random() < FAILURE_RATE.get(func, 0.2) / 4 for _ in range(4))):
                damage = mrng.choice(DAMAGE[func])
                n = notify(tag, f"{year}-{mrng.randint(1, 12):02d}-{mrng.randint(1, 28):02d}", damage,
                           DAMAGE_TEXT[damage].format(d=e["description"].lower()), prio=str(mrng.randint(2, 4)))
                if mrng.random() < 0.85:
                    acts = ACTIONS[damage]
                    work(n, tag, _add_days(n["created"], mrng.randint(0, 4)), mrng.choice(["A01", "A02", "A03"]),
                         "PM01", acts[len(orders) % len(acts)])
            if mrng.random() < 0.6:
                work(None, tag, f"{year}-{mrng.randint(1, 12):02d}-{mrng.randint(1, 28):02d}",
                     mrng.choice(["A04", "A05"]), "PM02", f"Preventive maintenance {e['description'].lower()}")

    # Story 1: train B 2nd stage bearing wear -> vibration -> trips -> lift gas shortfall -> lost oil.
    hit = ["J07", "J12", "J15"]
    log("2024-02-26T09:00:00", "20-VT-2200B on GGCP train B 2nd stage creeping up, 6.8 mm/s. Monitoring.", "GGCP CRO")
    log("2024-03-08T14:00:00", "Train B 2nd stage compressor vibration 9 mm/s, bearing temp 20-TT-2200B 96 degC.", "GGCP CRO")
    n1 = notify("20-K-2200B", "2024-03-09", "D01", "High vibration on train B 2nd stage compressor", "C06", "2")
    for when, hours, p in [("2024-03-14T03:20", 7, 61), ("2024-03-29T22:10", 11, 57)]:
        log(f"{when}:00", f"20-K-2200B tripped on high vibration. Lift gas header pressure 20-PT-3003 dropped from "
            f"88 to {p} barg. Opened compression bypass 20-XV-3100 to keep lift gas to jackets.", "GGCP CRO")
        log(_add_hours(when, 1) + ":00", f"Lift gas shortfall: GL wells on {', '.join(hit)} lost lift, "
            f"OK-J07-03 and OK-J12-02 died. Prioritised lift gas to J07.", "PP CRO")
        lost(FIELD, when, hours, "LIFT-GAS", f"20-K-2200B tripped on high vibration; lift gas shortfall on "
             f"{', '.join(hit)} GL wells, restarted after {hours}h", 1450, 300)
        notify("20-K-2200B", when[:10], "D09", "Train B 2nd stage compressor tripped on high vibration", "C02", "1")
    work(n1, "20-K-2200B", "2024-04-03", "A03", "PM01", "Replace thrust and journal bearings, train B 2nd stage", 4)
    log("2024-04-07T16:00:00", "Train B back in service after bearing replacement on 20-K-2200B, vibration 2.1 mm/s.",
        "GGCP CRO")

    # Story 2: train A 3rd stage after-cooler fouling -> high discharge temperature trips in hot months.
    for when in ["2024-06-11T15:00", "2024-07-02T14:30", "2024-07-24T13:45"]:
        log(f"{when}:00", "20-K-2300A tripped on high discharge temperature, 20-TT-2300A 168 degC. After-cooler "
            "20-E-2350A approach temperature high.", "GGCP CRO")
        lost(FIELD, when, 3, "PROC-TRIP", "20-K-2300A tripped on high discharge temp; train A down, "
             "lift gas reduced, restarted after 3h", 600, 150)
    n2 = notify("20-E-2350A", "2024-07-25", "D04", "High temperature on train A 3rd stage after-cooler", "C05")
    work(n2, "20-E-2350A", "2024-08-19", "A02", "PM01", "Clean cooler tubes, train A 3rd stage after-cooler", 3)
    # ... and comes back the next summer: cleaning only buys a year, the bundle needs retubing at the TAR.
    for when in ["2025-06-24T14:00", "2025-07-16T15:30"]:
        log(f"{when}:00", "20-K-2300A tripped on high discharge temperature again, 20-TT-2300A 171 degC. "
            "After-cooler 20-E-2350A fouled again, eddy current found thinned tubes.", "GGCP CRO")
        lost(FIELD, when, 4, "PROC-TRIP", "20-K-2300A tripped on high discharge temp; after-cooler fouled, "
             "restarted after 4h", 600, 150)
    n2b = notify("20-E-2350A", "2025-07-17", "D04", "Repeat fouling, thinned tubes on train A 3rd stage after-cooler", "C05")
    work(n2b, "20-E-2350A", "2025-07-28", "A02", "PM01", "Clean cooler tubes, train A 3rd stage after-cooler", 2)

    # Story 3: inlet separator level transmitter erratic -> liquid carryover -> train C scrubber trips.
    log("2024-11-04T02:00:00", "20-LT-2001 on GGCP inlet separator hunting, disagrees with gauge glass.", "Night Supervisor")
    for when in ["2024-11-05T05:30", "2024-11-09T18:15"]:
        log(f"{when}:00", "Train C tripped on high level in 1st stage suction scrubber 20-V-2101C. Liquid carryover "
            "from inlet separator suspected.", "GGCP CRO")
        lost(FIELD, when, 5, "PROC-TRIP", "Train C trip on high level in 20-V-2101C, carryover from 20-V-2001; "
             "restarted after 5h", 700, 160)
    n3 = notify("20-V-2001", "2024-11-06", "D07", "Level indication faulty on GGCP inlet separator", "C04")
    work(n3, "20-V-2001", "2024-11-12", "A02", "PM01", "Recalibrate level transmitter 20-LT-2001 and clear impulse lines", 1)

    # Story 4: hydrates on gas producer OK-NWP-G2 cut the lift gas supply.
    for when, a, b in [("2025-01-14T05:00", 45, 30), ("2025-01-21T04:30", 40, 25)]:
        log(f"{when}:00", f"OK-NWP-G2 WHT falling, rate dropping. Hydrate suspected in flowline, methanol injection "
            f"started, choke {a} to {b}%.", "Field Operator")
        lost("OKAN NWP-G2", when, 18, "HYDRATE", "OK-NWP-G2 hydrate in flowline, methanol injected", 0, 900)
        log(_add_hours(when, 3) + ":00", "Lift gas supply short with NWP-G2 down; lift gas header 20-PT-3003 at 74 barg, "
            "reduced GL rates on ST2 jackets.", "PP CRO")

    # Story 5: EGP curtails gas export.
    for when, hours in [("2025-02-10T08:00", 30), ("2025-03-02T12:00", 14)]:
        log(f"{when}:00", "EGP requested export cut to 40 MMscfd. Routed excess gas to lift gas header and flare, "
            "NWP export ESDV 50-XV-3100 closed.", "OIM")
        lost(FIELD, when, hours, "EXT-EGP", "Escravos Gas Plant curtailed export; gas flared and wells choked back",
             300, 2200)

    # Story 6: gas lift valve failures on J07 wells.
    for when, w in [("2024-09-03T10:00", "OK-J07-03"), ("2025-04-15T09:00", "OK-J07-05")]:
        log(f"{when}:00", f"{w} casing heading, GL valve suspected failed open. Unloading not stable.", "Production Engineer")
        lost(w, when, 96, "GL-VALVE", f"{w} gas lift valve failure, well shut in for slickline valve change", 45)

    # Story 7: export pump seal leak.
    log("2025-05-20T11:00:00", "Seal leak on 10-P-1001B oil export pump, started 10-P-1001C, isolated B.", "PP CRO")
    n7 = notify("10-P-1001B", "2025-05-20", "D02", "Seal / flange leak on oil export pump B", "C01")
    work(n7, "10-P-1001B", "2025-05-22", "A01", "PM01", "Replace mechanical seal on oil export pump B", 2)

    # Background downtime: planned shutdowns, well interventions, random trips.
    for year in (2024, 2025):
        lost(FIELD, f"{year}-0{drng.randint(4, 6)}-0{drng.randint(1, 9)}T06:00", 96, "PLAN-MAINT",
             "Planned GGCP and PP shutdown", 8200, 1400)
        for w in drng.sample([w for w in wells if w["type"] == "oil"], k=8):
            lost(w["name"], f"{year}-{drng.randint(1, 12):02d}-{drng.randint(1, 28):02d}T08:00",
                 drng.choice([24, 48, 72]), "WELL-INT", f"{w['name']} shut in for {drng.choice(['slickline', 'scale squeeze', 'wireline survey', 'choke change'])}",
                 drng.uniform(25, 80))

    # Background operator log, two years.
    t0 = date(2024, 1, 1)
    templates = [
        lambda w, x: f"Lined up {w['name']} to {x} test separator for monthly well test.",
        lambda w, x: f"{w['name']} GL rate adjusted from {lrng.randint(8, 14)} to {lrng.randint(10, 18)} kSm3/d.",
        lambda w, x: f"{w['name'].replace('OK-', '')} choke opened {lrng.randint(20, 40)} to {lrng.randint(40, 64)}%, WHP steady.",
        lambda w, x: f"Lift gas header 20-PT-3003 steady at {lrng.randint(84, 92)} barg, all three trains running.",
        lambda w, x: f"Train {lrng.choice('ABC')} on recycle during {x} well test, loading back up.",
        lambda w, x: f"Gas export to EGP {lrng.randint(55, 85)} MMscfd, 20-FT-3001 reading normal.",
        lambda w, x: "Swapped oil export pumps 10-P-1001A to 10-P-1001C for planned maintenance.",
        lambda w, x: f"{w['name']} slugging, increased gas lift.",
    ]
    for i in range(2400):
        day = t0 + timedelta(days=lrng.randint(0, 729))
        w = lrng.choice([w for w in wells if w["type"] == "oil"])
        sat = {"10": "PP", "30": "ST1", "40": "ST2", "50": "NWP"}.get(HOST.get(int(w["site"][1:]), "50")
                                                                   if w["site"].startswith("J") else "50")
        log(f"{day.isoformat()}T{lrng.choice(['06', '12', '18'])}:00:00", lrng.choice(templates)(w, sat))

    # DAY001: field figures and the day's events.
    by_day: dict[str, list[dict]] = {}
    for d in downtime:
        by_day.setdefault(d["start"][:10], []).append(d)
    daily, oil = [], 8600.0
    drng2 = random.Random(seed + 6)
    for i in range(730):
        day = (t0 + timedelta(days=i)).isoformat()
        oil = max(7200.0, min(9400.0, oil + drng2.uniform(-120, 120)))
        events = by_day.get(day, [])
        lost_oil = sum(float(d["lost_oil_sm3"]) for d in events)
        lost_gas = sum(float(d["lost_gas_ksm3"]) for d in events)
        notes = [d["comment"] for d in events] or [drng2.choice(["Steady production.", "All trains running.",
                                                                 "Routine operations, no upsets."])]
        daily.append({"report_date": day, "facility": FIELD, "oil_sm3": f"{oil - lost_oil:.0f}",
                      "oil_potential_sm3": f"{oil:.0f}",
                      "gas_ksm3": f"{3100 - lost_gas * 0.5:.0f}", "water_inj_sm3": "0",
                      "gas_export_ksm3": f"{max(0, 1700 - lost_gas):.0f}", "lift_gas_ksm3": f"{1800 - lost_gas * 0.3:.0f}",
                      "flare_ksm3": f"{40 + (lost_gas * 0.4 if any(d['reason_code'] == 'EXT-EGP' for d in events) else 0):.0f}",
                      "comments": " ".join(notes)})

    # ---- open work, maintenance plans, well tests (as of AS_OF) --------------------------------------
    brng = random.Random(seed + 7)
    has_order = {o["qmnum"] for o in orders if o["qmnum"]}
    for n in notifications:  # recent notifications nobody has planned yet
        if n["created"] >= "2025-09-01" and n["qmnum"] not in has_order:
            n["status"] = "OSNO"
    for e in brng.sample(equipment, k=min(32, len(equipment))):  # the running backlog
        _, area, rest = e["floc"].split("-")
        func = rest.rstrip("0123456789ABC")
        tag = f"{area}-{func}-{rest[len(func):]}"
        start = f"2025-{brng.randint(7, 12):02d}-{brng.randint(1, 28):02d}"
        corrective = brng.random() < 0.5
        n = notify(tag, start, brng.choice(DAMAGE[func]), "", prio=str(brng.randint(2, 4))) if corrective else None
        if n:
            n["short_text"] = DAMAGE_TEXT[n["damage_code"]].format(d=e["description"].lower())
            n["status"] = "OSNO"
        work(n, tag, start, brng.choice(["A01", "A02"]) if n else brng.choice(["A04", "A05"]),
             "PM01" if n else "PM02", f"Investigate and repair: {n['short_text'].lower()}" if n
             else f"Preventive maintenance {e['description'].lower()}",
             status=brng.choice(["CRTD", "REL", "REL"]), priority=n["priority"] if n else str(brng.randint(3, 4)),
             due=_add_days(start, brng.choice([14, 30, 60, 90])))
    # Work that needs the plant down, flagged for the 2026 turnaround (SAP revision OK-TAR-2026).
    tar = "OK-TAR-2026"
    for tag, activity, text, prio in [
        ("20-K-2200B", "A05", "Overhaul train B 2nd stage compressor: bearings, dry gas seals, impeller inspection", "2"),
        ("20-E-2350A", "A01", "Retube train A 3rd stage after-cooler bundle (repeat fouling, thinned tubes)", "2"),
        ("20-V-2001", "A06", "Internal inspection of GGCP inlet separator, replace level bridle and LT-2001", "2"),
        ("10-V-2001", "A06", "Statutory internal inspection, PP HP production separator", "3"),
        ("10-P-1001B", "A01", "Replace oil export pump B rotating element and seal plan 53B", "3"),
    ]:
        work(None, tag, "2026-04-06", activity, "PM03", text, status="CRTD", priority=prio, due="2026-04-30",
             revision=tar)

    plans = []
    last_pm = {}
    for o in orders:
        if o["order_type"] == "PM02" and o["status"] == "TECO":
            last_pm[o["equnr"]] = max(last_pm.get(o["equnr"], ""), o["finish"])
    for e in equipment:
        func = e["floc"].split("-")[2].rstrip("0123456789ABC")
        interval = {"K": 180, "GT": 180, "P": 180}.get(func, 365)
        done = last_pm.get(e["equnr"]) or "2024-06-01"
        if done < _add_days(AS_OF, -2 * interval):  # plans get rescheduled; a few stay overdue below
            done = _add_days(AS_OF, -brng.randint(10, int(interval * 1.25)))
        plans.append({"plan": f"PM-{e['equnr']}", "kind": "PM", "equnr": e["equnr"], "floc": e["floc"],
                      "task": f"{interval}-day preventive maintenance, {e['description'].lower()}",
                      "interval_days": str(interval), "last_done": done, "next_due": _add_days(done, interval),
                      "needs_shutdown": ""})
        if func in ("V", "E"):  # pressure equipment: statutory internal inspection, needs the unit down
            done = f"{brng.randint(2021, 2023)}-{brng.randint(1, 12):02d}-15"
            if e["floc"].endswith("20-V2001"):
                done = "2021-09-30"  # overdue: the carryover story's separator
            plans.append({"plan": f"INSP-{e['equnr']}", "kind": "INSP", "equnr": e["equnr"], "floc": e["floc"],
                          "task": f"Statutory internal inspection, {e['description'].lower()}", "interval_days": "1461",
                          "last_done": done, "next_due": _add_days(done, 1461), "needs_shutdown": "X"})
        if func == "K":
            done = _add_days(AS_OF, -brng.randint(5, 40))
            plans.append({"plan": f"VIB-{e['equnr']}", "kind": "INSP", "equnr": e["equnr"], "floc": e["floc"],
                          "task": f"Vibration monitoring route, {e['description'].lower()}", "interval_days": "30",
                          "last_done": done, "next_due": _add_days(done, 30), "needs_shutdown": ""})

    tests = []
    trng = random.Random(seed + 8)
    for w in wells:
        if w["type"] != "oil":
            continue
        rate, wc = trng.uniform(35, 95), trng.uniform(8, 45)
        decline, wc_rise = trng.uniform(0.004, 0.015), trng.uniform(0.2, 0.9)
        # Gas lift performance curve: rate climbs with injection and flattens. A small `knee` means the
        # well saturates early (extra lift gas buys little oil); a large one means it still responds.
        knee = trng.uniform(2.5, 14)
        for m in range(24):
            day = f"{2024 + m // 12}-{m % 12 + 1:02d}-{trng.randint(3, 26):02d}"
            q = rate * (1 - decline) ** m
            cut = min(95.0, wc + wc_rise * m)
            gas = trng.uniform(6, 20) if w["lift"] == "GL" else 0.0
            if w["lift"] == "GL":
                q *= (0.55 + 0.45 * (1 - math.exp(-gas / knee))) / (0.55 + 0.45 * (1 - math.exp(-12 / knee)))
            if w["name"] in ("OK-J07-03", "OK-J07-05"):  # gas lift valve failed open, fixed by slickline:
                failed = "2024-09-03" if w["name"] == "OK-J07-03" else "2025-04-15"
                if failed <= day <= _add_days(failed, 75):  # more gas goes in, less oil comes out
                    q *= 0.55
                    gas *= 1.45
            if w["name"] == "OK-J03-02" and day >= "2025-06-01":  # water breakthrough
                cut = min(95.0, 72 + (m - 17) * 2.5)
                q *= 0.6
            tests.append({"test_id": f"WT-{w['name']}-{day}", "well": w["name"], "test_date": day,
                          "oil_sm3d": f"{q:.1f}", "water_cut_pct": f"{cut:.1f}",
                          "gor_sm3_sm3": f"{trng.uniform(90, 160):.0f}", "lift_gas_ksm3d": f"{gas:.1f}",
                          "whp_barg": f"{trng.uniform(14, 26):.1f}"})
    log("2025-06-12T10:00:00", "OK-J03-02 water cut up to 72% on test, oil rate down. Water breakthrough suspected.",
        "Production Engineer")

    # ---- EC allocation network ------------------------------------------------------------------------
    nodes = [{"code": f"OKAN_{w['site']}_{w['name'].rsplit('-', 1)[1]}", "type": "well", "name": w["name"]}
             for w in wells]
    sep = lambda area, tag, name, code: {"code": code, "type": "separator", "tag": tag, "name": name}  # noqa: E731
    nodes += [
        sep("10", "10V2001", "PP HP Production Separator", "PP-HPSEP"),
        sep("10", "10V2002", "PP LP Production Separator", "PP-LPSEP"),
        {"code": "PP-OILMTR", "type": "meter", "tag": "10FT3001", "name": "Oil Export Fiscal Meter"},
        {"code": "ESCRAVOS-TERM", "type": "sales_point", "name": "Escravos crude terminal"},
        sep("30", "30V2002", "ST1 Production Separator", "ST1-PRODSEP"),
        sep("40", "40V2002", "ST2 Production Separator", "ST2-PRODSEP"),
        sep("50", "50V2001", "NWP Production Separator", "NWP-PRODSEP"),
        sep("20", "20V2001", "GGCP Inlet Separator", "GGCP-INLET"),
        {"code": "NWP-GASMTR", "type": "meter", "tag": "50FT3001", "name": "NWP Gas Export Meter"},
        {"code": "GGCP-EGPMTR", "type": "meter", "tag": "20FT3001", "name": "EGP Gas Export Meter"},
        {"code": "LG-HDR-GGCP", "type": "header", "tag": "20FT3002", "name": "GGCP Lift Gas Header"},
        {"code": "LG-HDR-ST1", "type": "header", "tag": "30FT3002", "name": "ST1 Lift Gas Header"},
        {"code": "LG-HDR-ST2", "type": "header", "tag": "40FT3002", "name": "ST2 Lift Gas Header"},
        {"code": "NWP-GLM", "type": "manifold", "name": "OKA_NWP Gas Lift Manifold", "site": f"{FIELD}-50"},
        {"code": "EGP", "type": "sales_point", "name": "Escravos Gas Plant"},
    ] + [{"code": f"GGCP-TRAIN-{t}", "type": "compressor", "tag": f"20K2300{t}", "name": f"Compression Train {t}"}
         for t in "ABC"] + [{"code": f"OKJ{j:02d}-PM", "type": "manifold", "name": f"OKJ-{j:02d} Production Manifold",
                             "site": f"{FIELD}-{60 + j}"} for j in range(1, JACKETS + 1)] + [
        {"code": f"OKJ{j:02d}-GLM", "type": "manifold", "name": f"OKJ-{j:02d} Gas Lift Manifold", "site": f"{FIELD}-{60 + j}"}
        for j in range(1, JACKETS + 1)]
    edge = lambda a, b, frm="", to="": {"from": a, "to": b, "valid_from": frm, "valid_to": to}  # noqa: E731
    lift = lambda a, b, frm="": {**edge(a, b, frm), "service": "lift_gas"}  # noqa: E731
    host_node = {"10": "PP-HPSEP", "30": "ST1-PRODSEP", "40": "ST2-PRODSEP"}
    header = {"10": "LG-HDR-GGCP", "30": "LG-HDR-ST1", "40": "LG-HDR-ST2"}
    jacket_code = lambda area: f"OKJ{int(area) - 60:02d}"  # noqa: E731
    edges = []
    for w in wells:
        code = f"OKAN_{w['site']}_{w['name'].rsplit('-', 1)[1]}"
        if w["type"] == "gas":
            edges.append(edge(code, "GGCP-INLET"))
        elif w["site"] == "NWP":
            edges.append(edge(code, "NWP-PRODSEP"))
        elif w["name"] == "OK-J09-04":  # re-routed from ST1 to PP in 2024 when its flowline was replaced
            edges += [edge(code, "OKJ09-PM", "2009-06-01", "2024-08-01"), edge(code, "PP-HPSEP", "2024-08-01")]
        else:
            edges.append(edge(code, f"OKJ{int(w['site'][1:]):02d}-PM"))
        if w["lift"] == "GL":  # lift gas into the well's annulus from its manifold
            edges.append(lift("NWP-GLM" if w["site"] == "NWP" else f"OKJ{int(w['site'][1:]):02d}-GLM", code))
    for a, via in route.items():
        j = jacket_code(a)
        if via in HUBS:
            edges += [edge(f"{j}-PM", host_node[via]), lift(header[via], f"{j}-GLM")]
        else:  # through the neighbouring jacket's manifolds
            edges += [edge(f"{j}-PM", f"{jacket_code(via)}-PM"), lift(f"{jacket_code(via)}-GLM", f"{j}-GLM")]
    edges += [lift("LG-HDR-GGCP", "LG-HDR-ST1"), lift("LG-HDR-GGCP", "LG-HDR-ST2"), lift("LG-HDR-GGCP", "NWP-GLM"),
              lift("GGCP-INLET", "LG-HDR-GGCP")]  # compression bypass: lift gas without compression
    edges += [edge("ST1-PRODSEP", "PP-HPSEP"), edge("ST2-PRODSEP", "PP-HPSEP"), edge("NWP-PRODSEP", "PP-HPSEP"),
              edge("PP-HPSEP", "PP-LPSEP"), edge("PP-LPSEP", "PP-OILMTR"), edge("PP-OILMTR", "ESCRAVOS-TERM"),
              edge("PP-HPSEP", "GGCP-INLET"), edge("NWP-PRODSEP", "NWP-GASMTR"), edge("NWP-GASMTR", "EGP")]
    for t in "ABC":
        frm = "2022-03-01" if t == "C" else ""
        edges += [edge("GGCP-INLET", f"GGCP-TRAIN-{t}", frm), lift(f"GGCP-TRAIN-{t}", "LG-HDR-GGCP", frm),
                  edge(f"GGCP-TRAIN-{t}", "GGCP-EGPMTR", frm)]
    edges.append(edge("GGCP-EGPMTR", "EGP"))

    # ---- GIS: facilities and pipelines ----------------------------------------------------------------
    place = {area: _xy(*f[3]) for area, f in FACILITIES.items()} | {a: _xy(*xy) for a, xy in jacket_at.items()}
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": place[a]},
              "properties": {"facility_id": f[0], "floc": f"{FIELD}-{a}", "name": f[1], "type": f[2],
                             "water_depth_m": round(rng.uniform(12, 22), 1), "install_year": 2022 if a == "50" else 1998,
                             "longitude": place[a][0], "latitude": place[a][1]}} for a, f in FACILITIES.items()]
    feats += [{"type": "Feature", "geometry": {"type": "Point", "coordinates": place[a]},
               "properties": {"facility_id": f"OKJ-{int(a) - 60:02d}", "floc": f"{FIELD}-{a}",
                              "name": f"Okan Jacket {int(a) - 60:02d}", "type": "wellhead jacket",
                              "water_depth_m": round(rng.uniform(10, 24), 1), "install_year": rng.choice([1998, 2004, 2011]),
                              "longitude": place[a][0], "latitude": place[a][1]}} for a in jacket_at]
    for fid, name, at in [("EGP", "Escravos Gas Plant", EGP_AT), ("ESCRAVOS", "Escravos crude terminal", TERMINAL_AT)]:
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": list(at)},
                      "properties": {"facility_id": fid, "floc": fid, "name": name, "type": "onshore plant",
                                     "water_depth_m": 0, "install_year": 1997, "longitude": at[0], "latitude": at[1]}})
    lines = []

    spatial_truth = []

    def pipe(service: str, a: str, b: str, frm: list, to: list, dia: int, mop: int, year: int, status="in service"):
        # Surveyed ends sit within ~15 m of the platform position.
        jit = lambda p: [round(p[0] + rng.uniform(-1.2e-4, 1.2e-4), 6), round(p[1] + rng.uniform(-1.2e-4, 1.2e-4), 6)]  # noqa: E731
        frm, to = jit(frm), jit(to)
        mid = [round((frm[0] + to[0]) / 2 + rng.uniform(-0.004, 0.004), 6),
               round((frm[1] + to[1]) / 2 + rng.uniform(-0.004, 0.004), 6)]
        coords = [frm, mid, to]
        floc_of = lambda x: x if x in ("EGP", "ESCRAVOS") else f"{FIELD}-{x}"  # noqa: E731
        line_id = f"OK-PL-{len(lines) + 1:03d}"
        # Legacy (1998) jacket flowlines were digitised without asset IDs: only their route says where they go.
        legacy = service == "production flowline" and year == 1998 and status == "in service"
        if legacy:
            spatial_truth.extend([{"src": line_id, "rel": "starts_at", "dst": floc_of(a)},
                                  {"src": line_id, "rel": "ends_at", "dst": floc_of(b)}])
        lines.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                      "properties": {"line_id": line_id,
                                     "name": f'{dia}" {service} {a} to {b}', "service": service,
                                     "from_floc": "" if legacy else floc_of(a), "to_floc": "" if legacy else floc_of(b),
                                     "diameter_in": dia, "length_km": round(_km(frm, mid) + _km(mid, to), 2),
                                     "mop_barg": mop, "install_year": year, "status": status}})

    for a in jacket_at:  # each jacket's flowline and gas lift line run to its hub, or to the jacket it routes through
        via = route[a]
        lg_from = "20" if via == "10" else via  # the PP hub's lift gas comes from the GGCP header
        pipe("production flowline", a, via, place[a], place[via], rng.choice([6, 8, 10]), 70, rng.choice([1998, 2004, 2011]))
        pipe("gas lift", lg_from, a, place[lg_from], place[a], rng.choice([4, 6]), 120, rng.choice([1998, 2004, 2011]))
    pipe("gas lift", "20", "30", place["20"], place["30"], 10, 120, 1998)  # GGCP to the satellite lift gas headers
    pipe("gas lift", "20", "40", place["20"], place["40"], 10, 120, 1999)
    pipe("gas lift", "20", "50", place["20"], place["50"], 6, 120, 2022)
    pipe("production trunkline", "30", "10", place["30"], place["10"], 16, 70, 1998)
    pipe("production trunkline", "40", "10", place["40"], place["10"], 14, 70, 1999)
    pipe("production trunkline", "50", "10", place["50"], place["10"], 10, 70, 2022)
    pipe("gas", "50", "20", place["50"], place["20"], 10, 110, 2022)
    pipe("gas export", "20", "EGP", place["20"], list(EGP_AT), 16, 100, 1998)
    pipe("gas export", "50", "EGP", place["50"], list(EGP_AT), 12, 100, 2022)
    pipe("oil export", "10", "ESCRAVOS", place["10"], list(TERMINAL_AT), 20, 60, 1998)
    # Replaced flowline behind the OK-J09-04 re-route.
    pipe("production flowline", "69", "30", place["69"], place["30"], 8, 70, 1998, status="abandoned")

    # Well surface locations: slots a few metres apart around their jacket (or on NWP).
    well_feats = []
    for i, w in enumerate(wells):
        x, y = place[w["area"]]
        slot = int(w["name"].rsplit("-", 1)[1].lstrip("G") or 0)
        ang = slot * 0.7 + (1.5 if w["type"] == "gas" else 0)
        lon, lat = round(x + 3.0e-5 * math.cos(ang) * (1 + slot % 3), 6), round(y + 3.0e-5 * math.sin(ang) * (1 + slot % 3), 6)
        well_feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                           "properties": {"well_name": w["name"], "well_type": w["type"], "lift_type": w["lift"],
                                          "status": "producing", "latitude": lat, "longitude": lon}})
        spatial_truth.append({"src": w["name"], "rel": "located_at", "dst": f"{FIELD}-{w['area']}"})

    # ---- P&ID drawings --------------------------------------------------------------------------------
    sheets, pid_truth = [], {}
    sheet_info = {"10-1": ("smart", "OKA_PP SEPARATION"), "10-2": ("pdf_text", "OKA_PP OIL EXPORT"),
                  "20-1": ("pdf_text", "OK_GGCP INLET SEPARATION AND LIFT GAS"), "20-A": ("image", "OK_GGCP TRAIN A"),
                  "20-B": ("image", "OK_GGCP TRAIN B"), "20-C": ("pdf_text", "OK_GGCP TRAIN C"),
                  "30-1": ("smart", "OKA_ST1"), "40-1": ("image", "OKA_ST2"), "50-1": ("pdf_text", "OKA_NWP")}
    drng3 = random.Random(seed + 2)
    for i, (key, tags) in enumerate(sheet_tags.items()):
        fmt, label = sheet_info[key]
        area = key.split("-")[0]
        sheet = {"sheet": f"{FIELD}-{area}-PID-{i + 1:03d}", "title": f"AREA {area} - {label}",
                 "revision": rng.choice("BCD"), "format": fmt, "image": f"pid/{FIELD}-{area}-PID-{i + 1:03d}.png"}
        eq = [t.canonical for t in tags if t.func in ("P", "K", "E", "V")]  # a turbine drives by shaft, not pipe
        conns = [{"from": a, "to": b, "line": f'{rng.choice([6, 8, 10])}"-P-{area}{100 + i * 10 + k}-A1'}
                 for k, (a, b) in enumerate(zip(eq, eq[1:]))]
        if fmt == "smart":
            sheet["tags"] = [t.canonical for t in tags]
            sheet["connections"] = conns
        elif fmt == "pdf_text":
            sheet["text"] = sheet["title"] + "\n" + "\n".join(t.fmt("{f}\n{l}{s}") for t in tags)
        truth = pid.draw(out / sheet["image"], sheet, tags, titles, drng3, conns, scan=fmt == "image")
        if fmt == "smart":
            sheet["positions"] = {x["key"]: [x["x"], x["y"]] for x in truth["tags"]}
        pid_truth[sheet["sheet"]] = truth
        sheets.append(sheet)

    # ---- documents ------------------------------------------------------------------------------------
    areas = {a: (a, f"{f[0]} {f[1]}") for a, f in FACILITIES.items()}
    documents = docs.generate(out, equipment, areas, FIELD, guides=GUIDES)
    materials, bom = _spares(equipment)

    # ---- write ----------------------------------------------------------------------------------------
    (out / "answer_keys").mkdir(exist_ok=True)
    _write_csv(out / "sap_floc.csv", floc)
    _write_csv(out / "sap_equipment.csv", equipment)
    _write_csv(out / "sap_notifications.csv", notifications)
    _write_csv(out / "sap_work_orders.csv", orders)
    _write_csv(out / "sap_materials.csv", materials)
    _write_csv(out / "sap_bom.csv", bom)
    _write_csv(out / "pi_points.csv", points)
    _write_csv(out / "oplogs.csv", sorted(logs, key=lambda r: r["timestamp"]))
    _write_csv(out / "ec_downtime.csv", downtime)
    _write_csv(out / "ec_day001.csv", daily)
    _write_csv(out / "sap_maintenance_plans.csv", plans)
    _write_csv(out / "ec_well_tests.csv", tests)
    (out / "pi_af.json").write_text(json.dumps(af, indent=1))
    (out / "ec_network.json").write_text(json.dumps({"nodes": nodes, "edges": edges}, indent=1))
    (out / "pid_sheets.json").write_text(json.dumps(sheets, indent=1))
    (out / "pid_truth.json").write_text(json.dumps(pid_truth, indent=1))
    (out / "gis_facilities.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, indent=1))
    (out / "gis_pipelines.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": lines}, indent=1))
    (out / "gis_wells.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": well_feats}, indent=1))
    (out / "answer_keys" / "spatial.json").write_text(json.dumps(spatial_truth, indent=1))
    (out / "answer_keys" / "assistant.json").write_text(json.dumps(ASSISTANT_QUESTIONS, indent=1))
    (out / "wells.json").write_text(json.dumps(wells, indent=1))  # scenario reference, not a source
    return {"asset": "okan", "facilities": len(FACILITIES), "jackets": JACKETS, "wells": len(wells),
            "gl_wells": len(gl_wells) + sum(1 for w in wells if w["site"] == "NWP" and w["lift"] == "GL"),
            "gas_producers": len(gas_wells), "equipment": len(equipment), "flocs": len(floc), "pi_points": len(points),
            "notifications": len(notifications), "work_orders": len(orders), "downtime": len(downtime),
            "logs": len(logs), "daily_reports": len(daily), "pid_sheets": len(sheets), "documents": len(documents),
            "pipelines": len(lines), "gis_facilities": len(feats), "gis_wells": len(well_feats),
            "unlabelled_pipeline_ends": sum(1 for s in spatial_truth if s["rel"] != "located_at")}


GUIDES = {
    "20": ("GGCP compressor trip response",
           ["20-K-2100A", "20-K-2200B", "20-K-2300A", "20-PT-3003", "20-XV-3100", "20-V-2001", "20-LT-2001"],
           ["After a trip of any compression train, watch lift gas header pressure 20-PT-3003. Below 75 barg, open "
            "the compression bypass 20-XV-3100 so lift gas keeps flowing uncompressed, and tell the PP CRO which "
            "jackets to prioritise.",
            "High vibration trip on a 2nd stage compressor such as 20-K-2200B: check bearing temperature and the "
            "vibration trend before restart. Two trips in 30 days means a bearing inspection, not another restart.",
            "High discharge temperature on a 3rd stage compressor such as 20-K-2300A: check the 3rd stage after-cooler "
            "for fouling and cooling water flow; clean it if the approach temperature is above 15 degC.",
            "Train trips on high scrubber level: check inlet separator 20-V-2001 level against the gauge glass; "
            "20-LT-2001 drifting low causes liquid carryover to the trains."]),
    "20L": ("Lift gas shortfall",
            ["20-PT-3003", "20-FT-3002", "20-XV-3100"],
            ["Signs: lift gas header 20-PT-3003 falling, GL wells on jackets losing casing pressure, oil rate down "
             "across several jackets at once rather than on one well.",
             "Causes, most likely first: a compression train trip on OK_GGCP, a gas producer on OKA_NWP down "
             "(hydrates on OK-NWP-G1, OK-NWP-G2 or OK-NWP-G3), EGP export demand taking gas.",
             "Actions: open compression bypass 20-XV-3100, cut gas export to EGP, then share lift gas to the jackets "
             "with the best oil per unit of gas (usually J07 and J12 first)."]),
    "50": ("NWP gas producer problems",
           ["50-V-2001", "50-FT-3001", "50-XV-3100"],
           ["Hydrates in a gas producer flowline (OK-NWP-G1, OK-NWP-G2, OK-NWP-G3): wellhead temperature falling, "
            "pressure rising and rate dropping. Start methanol injection and reduce the choke before shutting in.",
            "If EGP curtails export, close NWP EGP export ESDV 50-XV-3100 and send NWP gas to OK_GGCP for lift gas "
            "rather than flaring."]),
    "10": ("Production platform separation and export",
           ["10-V-2001", "10-LT-2001", "10-P-1001A", "10-P-1001B", "10-P-1001C", "10-FT-3001"],
           ["High level in HP separator 10-V-2001 after a satellite restart: slow the ST1 and ST2 ramp-up and check "
            "10-LT-2001 against the gauge glass.",
            "Seal leak on an oil export pump (10-P-1001A/B/C): start the standby pump, isolate and drain the leaking "
            "one, raise a notification."]),
}
