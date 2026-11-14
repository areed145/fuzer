"""Okan's flare system: one flare fed by the relief, blowdown and pressure control valves of both PP and GGCP.

The relief headers of OKA_PP (area 10, across the PP-GGCP bridge) and OK_GGCP (area 20) run to the HP flare knock-out
drum 25-V-4001 on GGCP's flare deck. The drum's gas passes the flare gas meter 25-FT-4003 (the meter permit condition
PC-OK-08 asked for, commissioned on INSTALLED) and goes to one of two flares:

- the GGCP flare boom 25-FL-4010 (area 25), angled out over the sea off GGCP's north-east corner: the normal flare;
- the alternate flare stack 26-FL-4030 on its own small jacket OKA_FLJ (area 26), about 145 m (some 470 ft) from GGCP
  and reached by a bridge carrying the flare line, behind a liquid seal drum: it takes the flare whenever the boom
  is isolated (the boom's tip replacement in the 2026 turnaround, MOC 233).

Everything here draws from its own random sequence and is added to the exports after the rest of the field is made,
so no other story's draws move. The daily report's flare volume is what this system flared: until the meter came in
service it was estimated, after it the meter's reading. Energy Components allocates each day's flare back to the
installation it came from (`ec_flare_allocation`): routine flare (purge, pilots, seal vents, pressure control) by the
standing split, a curtailment's excess to GGCP's lift gas header pressure control valve, and a day nobody explains
(UNEXPLAINED) pro rata, since the records name no source.
"""

import random
from datetime import date, timedelta
from math import cos, hypot, radians

from .common import Tag

FIELD = "OKAN"
INSTALLED = "2025-09-22"  # the flare gas meter in service, eight days inside the permit's 30 September deadline
METER = "25-FT-4003"
KO_DRUM, BOOM, SEAL_DRUM, STACK = "25-V-4001", "25-FL-4010", "26-V-4032", "26-FL-4030"
ROUTINE = 40.0  # kSm3/d: purge, pilots, compressor seal vents and pressure control spill, as the daily report books it
SPLIT = {"OKAN-10": 0.3, "OKAN-20": 0.7}  # routine flare by installation (EC's standing allocation)
SOURCES = {"OKAN-10": "OKA_PP", "OKAN-20": "OK_GGCP"}

# area -> (code, name, type, (east km, north km) from PP, parent FLOC)
AREAS = {
    "25": ("OK_GGCP_FLR", "GGCP Flare System", "flare boom on platform", (0.135, 0.045), f"{FIELD}-20"),
    "26": ("OKA_FLJ", "Okan Alternate Flare Jacket", "flare jacket", (0.20, 0.09), FIELD),
}
# items with an SAP equipment record: tag -> (description, AF template, manufacturer, model, in service from)
ITEMS = {
    KO_DRUM: ("HP Flare Knock-out Drum", "Pressure Vessel", "Kanfa", "KAN-600", "2009-06-01"),
    BOOM: ("GGCP Flare Boom", "Flare", "Zeeco", "Sonic HP-24", "2009-06-01"),
    SEAL_DRUM: ("Alternate Flare Stack Seal Drum", "Pressure Vessel", "Sembcorp", "SEM-200", "2009-06-01"),
    STACK: ("Alternate Flare Stack", "Flare", "Zeeco", "Utility U-18", "2009-06-01"),
    METER: ("GGCP Flare Gas Meter", "Flow Meter", "SICK", "FLOWSIC100 Flare", INSTALLED),
}
# instruments: tag -> (description, units, the process model's unit for it)
INSTRUMENTS = {
    "25-PT-4001": ("HP Flare Knock-out Drum Pressure", "barg", {"type": "controlled", "value": 0.35, "noise": 0.02}),
    "25-LT-4001": ("HP Flare Knock-out Drum Liquid Level", "%", {"type": "controlled", "value": 18.0, "noise": 1.5}),
    METER: ("GGCP Flare Gas Meter", "kSm3/d", {"type": "controlled", "value": ROUTINE, "noise": 1.2, "tau": 5,
                                                "since": f"{INSTALLED}T10:00"}),
    "25-TT-4010": ("GGCP Flare Boom Pilot Temperature", "degC", {"type": "controlled", "value": 640.0, "noise": 8.0}),
    "25-XV-4020": ("Flare Boom Isolation Valve", "%", {"type": "controlled", "value": 100.0, "noise": 0.0}),
    "25-XV-4021": ("Alternate Flare Stack Isolation Valve", "%", {"type": "controlled", "value": 0.0, "noise": 0.0}),
    "26-TT-4030": ("Alternate Flare Stack Pilot Temperature", "degC",
                   {"type": "controlled", "value": 610.0, "noise": 8.0}),
    "26-LT-4032": ("Alternate Flare Stack Seal Drum Level", "%", {"type": "controlled", "value": 55.0, "noise": 1.0}),
}
# the flare system's DCS alarms: tag -> {alarm: setpoint}. High level in the knock-out drum and a low seal drum level
# matter most; a pilot going out shows as its temperature falling.
ALARMS = {"25-PT-4001": {"PVHI": 2.0, "PVHH": 3.5}, "25-LT-4001": {"PVHI": 50.0, "PVHH": 70.0},
          "25-TT-4010": {"PVLO": 300.0}, "26-TT-4030": {"PVLO": 300.0}, "26-LT-4032": {"PVLO": 35.0, "PVLL": 25.0}}
# what relieves into the flare: device -> (description, the item it relieves, line size in inches)
RELIEF = {
    "10-PSV-2001": ("HP Production Separator Relief Valve", "10-V-2001", 6),
    "10-PSV-2002": ("LP Production Separator Relief Valve", "10-V-2002", 8),
    "10-PSV-2003": ("Test Separator Relief Valve", "10-V-2003", 4),
    "10-PSV-2004": ("Export Surge Drum Relief Valve", "10-V-2004", 4),
    "10-PV-2001": ("HP Production Separator Pressure Control Valve to Flare", "10-V-2001", 6),
    "10-BDV-2005": ("PP Process Blowdown Valve", "10-V-2001", 4),
    "20-PSV-2001": ("GGCP Inlet Separator Relief Valve", "20-V-2001", 8),
    "20-PSV-2002": ("Lift Gas Scrubber Relief Valve", "20-V-2002", 4),
    "20-PSV-2400": ("Glycol Contactor Relief Valve", "20-V-2400", 4),
    "20-PV-3003": ("Lift Gas Header Pressure Control Valve to Flare", "20-V-2002", 6),
    **{f"20-BDV-2000{t}": (f"Train {t} Blowdown Valve", f"20-K-2300{t}", 4) for t in "ABC"},
}
# each platform's relief header, drawn leaving the sheet its separators are on: (sheet key, item, header line)
HEADERS = [("10-1", "10-V-2001", '16"-FL-104190-B1'), ("20-1", "20-V-2001", '20"-FL-204191-B1')]
PI_STYLE = "OK_GGCP:{a}-{f}-{l}{s}.PV"  # the flare deck and the flare jacket come in on GGCP's DCS


def _tag(t: str) -> Tag:
    area, func, rest = t.split("-")
    num = rest.rstrip("ABC")
    return Tag(area, func, int(num), rest[len(num):])


def _floc(t: str) -> str:
    return _tag(t).fmt(f"{FIELD}-{{a}}-{{f}}{{l}}{{s}}")


def register(xy) -> dict:
    """The flare system's FLOCs, SAP equipment, PI points, AF elements and titles. `xy(east, north)` places a point."""
    floc, titles = [], {}
    for area, (code, name, _, _, parent) in AREAS.items():
        floc.append({"floc": f"{FIELD}-{area}", "parent": parent, "description": f"{code} {name}"})
    for t, (desc, *_rest) in ITEMS.items():
        if t == METER:
            continue
        floc.append({"floc": _floc(t), "parent": f"{FIELD}-{t[:2]}", "description": desc})
        titles[t] = desc
    for t, (desc, _, _) in INSTRUMENTS.items():
        owner = next((i for i in ITEMS if i != METER and _tag(i).loop == _tag(t).loop and i[:2] == t[:2]), None)
        floc.append({"floc": _floc(t), "parent": _floc(owner) if owner else f"{FIELD}-{t[:2]}", "description": desc})
        titles[t] = desc
    for t, (desc, item, _) in RELIEF.items():
        floc.append({"floc": _floc(t), "parent": _floc(item), "description": desc})
        titles[t] = desc
    rng = random.Random(9161)
    equipment = [{"equnr": str(30080000 + i), "description": desc, "manufacturer": maker, "model": model,
                  "serial": f"FL{rng.randint(10000, 99999)}", "floc": _floc(t), "valid_from": since, "valid_to": "",
                  "motor_rated_kw": "", "motor_service_factor": "", "driver": ""}
                 for i, (t, (desc, _, maker, model, since)) in enumerate(ITEMS.items())]
    points, point_tag, af = [], {}, []
    for area, (code, *_rest) in AREAS.items():
        af.append({"path": f"{FIELD}\\{area} {code}", "template": "Facility", "attributes": []})
    attrs: dict[str, list] = {}
    for t, (desc, uom, _) in INSTRUMENTS.items():
        point = _tag(t).fmt(PI_STYLE)
        points.append({"tag": point, "descriptor": desc, "engunits": uom, "pointsource": "OPC"})
        point_tag[point] = t
        owner = next((i for i in ITEMS if _tag(i).loop == _tag(t).loop and i[:2] == t[:2]), t)
        attrs.setdefault(owner, []).append({"name": desc, "pi_point": point, "uom": uom})
    for t, (desc, template, *_rest) in ITEMS.items():
        code = AREAS[t[:2]][0]
        af.append({"path": f"{FIELD}\\{t[:2]} {code}\\{t}", "template": template, "attributes": attrs.get(t, [])})
    for f in floc:
        parts = f["floc"].split("-")
        func = parts[2].rstrip("0123456789ABC") if len(parts) > 2 else ""
        f["abc_indicator"] = {"PSV": "A", "BDV": "A", "PV": "B", "V": "A", "FL": "A", "XV": "A"}.get(func, "C") \
            if len(parts) > 2 else ""
    return {"floc": floc, "equipment": equipment, "points": points, "point_tag": point_tag, "af": af, "titles": titles}


def alarms() -> list[dict]:
    """The flare system's rows for the DCS alarm export, configured when the deck was commissioned."""
    return [{"tag": t.replace("-", ""), "alarm": a, "setpoint": f"{v:g}", "eu": INSTRUMENTS[t][1],
             "priority": "1" if a in ("PVHH", "PVLL") else "2", "enabled": "Y", "last_changed": "2009-06-01"}
            for t, by in ALARMS.items() for a, v in by.items()]


def lines() -> list[dict]:
    """The flare system's lines for the line list: each relief device to the knock-out drum
    (PP's over the bridge in the PP relief header), the drum through the meter to the boom, and the changeover to the
    alternate stack. `sheet` is filled in by the caller."""
    out, n = [], 0

    def line(a: str, b: str, size: int, area: str) -> None:
        nonlocal n
        out.append({"line": f'{size}"-FL-{area}{4100 + n}-B1', "from_tag": a, "to_tag": b})
        n += 1

    for dev, (_, item, size) in RELIEF.items():  # the item a device relieves is its FLOC parent, not a drawn line
        line(dev, KO_DRUM, max(size, 10 if dev.startswith("10-") else 8), dev[:2])
    line(KO_DRUM, METER, 24, "25")
    line(METER, "25-XV-4020", 24, "25")
    line("25-XV-4020", BOOM, 24, "25")
    line(METER, "25-XV-4021", 20, "25")
    line("25-XV-4021", SEAL_DRUM, 20, "26")
    line(SEAL_DRUM, STACK, 20, "26")
    return out


def sheets(sheet_no: dict[str, str], next_no: int) -> tuple[list[tuple[str, dict, list, list, list]], dict[str, list]]:
    """The two flare P&IDs, as (key, sheet, tags, connections, off-page connectors), and the off-page ends the PP and
    GGCP sheets gain for them ({sheet key: [end]}). Each platform's relief and blowdown devices tie into its relief
    header, which leaves the separator sheet the devices sit on and enters GGCP's flare deck at the knock-out drum; the
    alternate stack's sheet takes the flare line from the deck."""
    no25, no26 = f"{FIELD}-25-PID-{next_no:03d}", f"{FIELD}-26-PID-{next_no + 1:03d}"
    by_line = {(x["from_tag"], x["to_tag"]): x["line"] for x in lines()}
    t25 = [_tag(t) for t in (KO_DRUM, "25-PT-4001", "25-LT-4001", METER, BOOM, "25-TT-4010", "25-XV-4020",
                             "25-XV-4021")]
    t26 = [_tag(t) for t in (SEAL_DRUM, "26-LT-4032", STACK, "26-TT-4030")]
    off25, back = [], {}
    for key, item, line in HEADERS:
        off25.append({"tag": KO_DRUM, "direction": "from", "sheet": sheet_no[key], "other": item, "line": line})
        back.setdefault(key, []).append({"tag": item, "direction": "to", "sheet": no25, "other": KO_DRUM, "line": line})
    # the changeover line to the alternate stack leaves the deck's sheet under the boom (through 25-XV-4021)
    stack_line = by_line[("25-XV-4021", SEAL_DRUM)]
    off25.append({"tag": KO_DRUM, "direction": "to", "sheet": no26, "other": SEAL_DRUM, "line": stack_line,
                  "low": True})
    off26 = [{"tag": SEAL_DRUM, "direction": "from", "sheet": no25, "other": KO_DRUM, "line": stack_line}]
    return [
        ("25-1", {"sheet": no25, "title": "AREA 25 - OK_GGCP FLARE SYSTEM", "revision": "B", "format": "pdf_text",
                  "image": f"pid/{no25}.png"}, t25,
         [{"from": KO_DRUM, "to": BOOM, "line": by_line[("25-XV-4020", BOOM)]}], off25),
        ("26-1", {"sheet": no26, "title": "AREA 26 - OKA_FLJ ALTERNATE FLARE STACK", "revision": "A",
                  "format": "pdf_text", "image": f"pid/{no26}.png"}, t26,
         [{"from": SEAL_DRUM, "to": STACK, "line": by_line[(SEAL_DRUM, STACK)]}], off26),
    ], back


PFD = ("OKAN-25-PFD-003", "AREA 25 - OK_GGCP FLARE SYSTEM", "clean", "FROM PP AND GGCP RELIEF HEADERS",
       "TO ALTERNATE STACK (STANDBY)", "VAP", [KO_DRUM, BOOM],
       ["ALTERNATE STACK 26-FL-4030 ON OKA_FLJ TAKES THE FLARE WHEN THE BOOM IS ISOLATED.",
        "FLARE GAS METER 25-FT-4003 ON THE DRUM OUTLET, IN SERVICE SEPTEMBER 2025."])


def gis(xy, first_line: int) -> tuple[list[dict], list[dict]]:
    """The flare boom's and the flare jacket's positions, and the flare lines: PP's relief header over the PP-GGCP
    bridge, and the flare line over the bridge from GGCP to the alternate stack's jacket."""
    place = {a: xy(*v[3]) for a, v in AREAS.items()}
    place["10"], place["20"] = xy(0.0, 0.0), xy(0.08, 0.0)
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": place[a]},
              "properties": {"facility_id": code, "floc": f"{FIELD}-{a}", "name": name, "type": kind,
                             "water_depth_m": 16.4 if a == "25" else 17.1, "install_year": 2009 if a == "25" else 1998,
                             "longitude": place[a][0], "latitude": place[a][1]}}
             for a, (code, name, kind, _, _) in AREAS.items()]
    pipes = []
    for n, (a, b, dia, year) in enumerate([("10", "20", 10, 1998), ("20", "26", 20, 1998)]):
        frm, to = place[a], place[b]
        km = hypot((to[0] - frm[0]) * 111.32 * cos(radians(frm[1])), (to[1] - frm[1]) * 110.57)
        pipes.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [frm, to]},
                      "properties": {"line_id": f"OK-PL-{first_line + n:03d}", "name": f'{dia}" flare {a} to {b}',
                                     "service": "flare", "from_floc": f"{FIELD}-{a}", "to_floc": f"{FIELD}-{b}",
                                     "diameter_in": dia, "length_km": round(km, 2), "mop_barg": 10,
                                     "install_year": year, "status": "in service"}})
    return feats, pipes


def network() -> tuple[list[dict], list[dict]]:
    """Energy Components' flare nodes and edges: both installations' relief into the knock-out drum, the drum to the
    meter, the meter to the boom and the alternate stack. `service: flare` says what the edges carry."""
    nodes = [
        {"code": "PP-RELIEF-HDR", "type": "header", "name": "PP Relief and Blowdown Header", "site": f"{FIELD}-10"},
        {"code": "GGCP-FLARE-KO", "type": "separator", "tag": "25V4001", "name": "HP Flare Knock-out Drum"},
        {"code": "GGCP-FLAREMTR", "type": "meter", "tag": "25FT4003", "name": "GGCP Flare Gas Meter"},
        {"code": "GGCP-FLARE-BOOM", "type": "flare", "tag": "25FL4010", "name": "GGCP Flare Boom"},
        {"code": "OKFLJ-FLARE-STACK", "type": "flare", "tag": "26FL4030", "name": "Alternate Flare Stack"},
    ]
    e = lambda a, b, frm="": {"from": a, "to": b, "valid_from": frm, "valid_to": "", "service": "flare"}
    edges = [e("PP-HPSEP", "PP-RELIEF-HDR"), e("PP-LPSEP", "PP-RELIEF-HDR"), e("PP-RELIEF-HDR", "GGCP-FLARE-KO"),
             e("GGCP-INLET", "GGCP-FLARE-KO"), e("LG-HDR-GGCP", "GGCP-FLARE-KO")]
    edges += [e(f"GGCP-TRAIN-{t}", "GGCP-FLARE-KO", "2022-03-01" if t == "C" else "") for t in "ABC"]
    edges += [e("GGCP-FLARE-KO", "GGCP-FLAREMTR"), e("GGCP-FLAREMTR", "GGCP-FLARE-BOOM"),
              e("GGCP-FLAREMTR", "OKFLJ-FLARE-STACK")]
    return nodes, edges


def allocation(daily: list[dict], downtime: list[dict], unexplained: str) -> list[dict]:
    """EC's flare allocation, a row per day, installation and kind of flaring: the day's flare (the daily report's)
    split back to PP and GGCP. Routine flare by the standing split; a curtailment's excess to GGCP, whose lift gas
    header pressure control valve spilled it; an excess no record explains pro rata, since nothing names its
    source. Estimated until the meter came in service, metered after."""
    curtailed = {d["start"][:10] for d in downtime if d["reason_code"] == "EXT-EGP"}
    rows = []
    for d in daily:
        day, total = d["report_date"], float(d["flare_ksm3"])
        basis = "metered" if day >= INSTALLED else "estimated"
        routine, extra = min(total, ROUTINE), max(0.0, total - ROUTINE)
        split = {k: routine * s for k, s in SPLIT.items()}
        kind = None
        if extra:
            kind = "non-routine: export curtailment" if day in curtailed else \
                "non-routine: source not recorded" if day == unexplained else "non-routine"
        for src, v in split.items():
            rows.append({"record_id": f"FLA-{day}-{src}-R", "report_date": day, "meter": METER, "source": src,
                         "source_name": SOURCES[src], "category": "routine", "basis": basis,
                         "allocation": "standing split", "flare_ksm3": f"{v:.1f}"})
        if extra:
            shares = {"OKAN-20": 1.0} if day in curtailed else SPLIT
            for src, s in shares.items():
                rows.append({"record_id": f"FLA-{day}-{src}-N", "report_date": day, "meter": METER, "source": src,
                             "source_name": SOURCES[src], "category": kind, "basis": basis,
                             "allocation": "to the curtailed export's pressure control" if day in curtailed
                             else "pro rata: no record names the source", "flare_ksm3": f"{extra * s:.1f}"})
    return rows


def model(unexplained: str) -> tuple[dict, dict, list[dict]]:
    """The process model's units, points and events for the flare system's instruments: the meter has no reading
    before it was commissioned, then reads the routine flare, and the day nobody explains as a flare of about 280 kSm3/d
    over the routine for some fourteen hours (the 170 kSm3 the daily report books over it), with the knock-out
    drum's pressure lifting with the flow."""
    units = {t: dict(u) for t, (_, _, u) in INSTRUMENTS.items()}
    points = {_tag(t).fmt(PI_STYLE): [t, "value"] for t in INSTRUMENTS}
    d = unexplained
    events = [{"unit": METER, "kind": "drift", "points": [[f"{d}T02:00", 0], [f"{d}T02:20", 281.0],
                                                          [f"{d}T16:30", 281.0], [f"{d}T16:50", 0]]},
              {"unit": "25-PT-4001", "kind": "drift", "points": [[f"{d}T02:00", 0], [f"{d}T02:20", 0.5],
                                                                 [f"{d}T16:30", 0.5], [f"{d}T16:50", 0]]}]
    return units, points, events


def maintenance(eq_of_floc: dict[str, dict], qmnum0: int, aufnr0: int) -> tuple[list[dict], list[dict], list[dict]]:
    """A few records on the flare system: the boom's pilot failing in July 2025 and its repair, and the maintenance
    plans for the drum, the boom, the stack and the meter."""
    boom, drum = eq_of_floc[_floc(BOOM)], eq_of_floc[_floc(KO_DRUM)]
    n = {"qmnum": str(qmnum0), "notif_type": "M1", "created": "2025-07-14", "equnr": boom["equnr"],
         "floc": boom["floc"], "priority": "2", "short_text": "Boom flare pilot 2 flame failure, relit from panel",
         "damage_code": "D03", "cause_code": "C04", "status": "NOCO"}
    o = {"aufnr": str(aufnr0), "order_type": "PM01", "qmnum": n["qmnum"], "equnr": boom["equnr"], "floc": boom["floc"],
         "start": "2025-07-16", "finish": "2025-07-17", "basic_finish": "2025-07-18", "status": "TECO", "priority": "2",
         "revision": "", "activity_code": "A01", "cost": "8400",
         "text": "Replace pilot 2 thermocouple and clean the igniter, GGCP flare boom", "arbpl": "INST",
         "confirmation": "Pilot 2 thermocouple replaced and igniter cleaned; all three pilots proven lit from the panel"}
    plans = []
    for e, kind, task, days, done, shut in [
            (drum, "PM", "365-day preventive maintenance, hp flare knock-out drum", 365, "2025-05-12", ""),
            (drum, "INSP", "Statutory internal inspection, hp flare knock-out drum", 1461, "2022-04-20", "X"),
            (boom, "INSP", "Flare tip and pilot inspection by drone, ggcp flare boom", 365, "2025-03-03", ""),
            (eq_of_floc[_floc(STACK)], "PM", "Pilot and ignition test, alternate flare stack", 90, "2025-11-04", ""),
            (eq_of_floc[_floc(METER)], "PM", "Flare gas meter verification, ggcp flare gas meter", 365, INSTALLED, "")]:
        due = (date.fromisoformat(done) + timedelta(days=days)).isoformat()
        plans.append({"plan": f"{kind}-{e['equnr']}", "kind": kind, "equnr": e["equnr"], "floc": e["floc"], "task": task,
                      "interval_days": str(days), "last_done": done, "next_due": due, "needs_shutdown": shut})
    return [n], [o], plans

