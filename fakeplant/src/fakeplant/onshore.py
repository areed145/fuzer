"""A fake onshore field ("Ridgeback", RBF) for the onshore_demo pack: pads with many wells, each well on
an electric submersible pump (ESP), and a separator, tank battery and custody meter (LACT) per pad.
Only SAP, PI / PI AF, Energy Components and operator logs: no JDE or Maximo, so optional sources are
exercised too. Written in the same export formats as the offshore generator.
"""

import json
import math
import random
from pathlib import Path

from .offshore import _add_days, _write_csv

FACILITY = "RBF"
MAKERS = {"ESP": ["Apex Lift", "Canyon Pumps"], "SEP": ["Plains Fab"], "TK": ["Plains Fab"], "LACT": ["Meterco"]}
INSTRUMENTS = {"ESP": [("PT", "Intake pressure", "psi"), ("TT", "Motor temperature", "degF"), ("ST", "Speed", "Hz")],
               "SEP": [("PT", "Separator pressure", "psi"), ("LT", "Separator level", "%")],
               "TK": [("LT", "Tank level", "ft")], "LACT": [("FT", "Custody flow", "bbl/d")]}
LOGS = ["ESP on {well} tripped on underload, restarted.", "{well} shut in for workover.",
        "{tank} high level, called trucking.", "{sep} pressure high, checked dump valve.",
        "ESP {esp} motor temperature alarm, reduced speed."]


def generate(out, seed: int = 11, pads: int = 40, wells_per_pad: int = 12, logs: int = 2000) -> dict:
    rng = random.Random(seed)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    floc = [{"floc": FACILITY, "parent": "", "description": "Ridgeback field"}]
    af = [{"path": FACILITY, "template": "Field", "attributes": []}]
    equipment, points, notes, orders, downtime, entries = [], [], [], [], [], []
    nodes = [{"code": f"{FACILITY}-SALES", "type": "sales_point", "name": "Ridgeback sales"}]
    edges, wells_all, esps = [], [], []
    serial = 70000

    def item(pad, func, loop, suffix, desc):
        nonlocal serial
        tag = f"{pad}-{func}-{loop}{suffix}"
        tag_floc = f"{FACILITY}-{pad}-{func}{loop}{suffix}"
        floc.append({"floc": tag_floc, "parent": f"{FACILITY}-{pad}", "description": desc})
        attrs = []
        for ifunc, idesc, uom in INSTRUMENTS[func]:
            inst_floc = f"{FACILITY}-{pad}-{ifunc}{loop}{suffix}"
            floc.append({"floc": inst_floc, "parent": tag_floc, "description": f"{desc} {idesc.lower()}"})
            point = f"{FACILITY}.{pad}{ifunc}{loop}{suffix}.PV"
            points.append({"tag": point, "descriptor": f"{desc} {idesc}", "engunits": uom, "pointsource": "SCADA"})
            attrs.append({"name": idesc, "pi_point": point, "uom": uom})
        af.append({"path": f"{FACILITY}\\Pad {pad}\\{tag}", "template": func, "attributes": attrs})
        serial += rng.randint(1, 500)
        maker = rng.choice(MAKERS[func])
        equipment.append({"equnr": str(20000000 + len(equipment)), "description": desc, "manufacturer": maker,
                          "model": f"{maker[:3].upper()}-{rng.choice([100, 300])}", "serial": f"SN{serial}",
                          "floc": tag_floc, "valid_from": "2019-01-01", "valid_to": ""})
        return tag, tag_floc

    for p in range(1, pads + 1):
        pad = f"{p:02d}"
        floc.append({"floc": f"{FACILITY}-{pad}", "parent": FACILITY, "description": f"Pad {pad}"})
        af.append({"path": f"{FACILITY}\\Pad {pad}", "template": "Pad", "attributes": []})
        sep, _ = item(pad, "SEP", 101, "", f"Pad {pad} separator")
        tanks = [item(pad, "TK", 201, s, f"Pad {pad} oil tank {s}")[0] for s in "AB"]
        lact, _ = item(pad, "LACT", 401, "", f"Pad {pad} LACT unit")
        sep_node, lact_node = f"{FACILITY}_P{pad}_SEP", f"{FACILITY}_P{pad}_LACT"
        nodes += [{"code": sep_node, "type": "separator", "tag": sep.replace("-", ""), "name": f"Pad {pad} separator"},
                  {"code": lact_node, "type": "meter", "tag": lact.replace("-", ""), "name": f"Pad {pad} LACT"}]
        edges += [{"from": sep_node, "to": lact_node, "valid_from": "", "valid_to": ""},
                  {"from": lact_node, "to": f"{FACILITY}-SALES", "valid_from": "", "valid_to": ""}]
        for w in range(1, wells_per_pad + 1):
            well = f"RB {pad}-{w:02d}H"
            wells_all.append((pad, well))
            floc.append({"floc": f"{FACILITY}-WELL-RB{pad}-{w:02d}H", "parent": f"{FACILITY}-{pad}", "description": well})
            for code, desc, uom in [("TBG", "Tubing pressure", "psi"), ("CSG", "Casing pressure", "psi"),
                                    ("OIL", "Oil rate", "bbl/d")]:
                points.append({"tag": f"{FACILITY}.RB{pad}{w:02d}H.{code}", "descriptor": f"{well} {desc}",
                               "engunits": uom, "pointsource": "SCADA"})
            esp, esp_floc = item(pad, "ESP", 300 + w, "", f"{well} ESP")
            esps.append((esp, esp_floc, well))
            code = f"{FACILITY}_RB{pad}-{w:02d}H"
            nodes.append({"code": code, "type": "well", "name": well})
            edges.append({"from": code, "to": sep_node, "valid_from": "2019-01-01", "valid_to": ""})

    # ESP failures: notifications, work orders and the downtime they cause.
    for esp, esp_floc, well in esps:
        e = next(x for x in equipment if x["floc"] == esp_floc)
        for _ in range(rng.choice([0, 0, 1, 1, 2])):
            day = f"{rng.randint(2020, 2025)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            damage = rng.choice(["D03", "D09", "D05"])
            n = {"qmnum": str(30000000 + len(notes)), "notif_type": "M1", "created": day, "equnr": e["equnr"],
                 "floc": esp_floc, "priority": "2", "short_text": f"ESP {esp} {'failed to start' if damage == 'D03' else 'tripped'}",
                 "damage_code": damage, "cause_code": rng.choice(["C01", "C04", "C05"]), "status": "NOCO"}
            notes.append(n)
            orders.append({"aufnr": str(50000000 + len(orders)), "order_type": "PM01", "qmnum": n["qmnum"],
                           "equnr": e["equnr"], "floc": esp_floc, "start": _add_days(day, 1),
                           "finish": _add_days(day, rng.randint(2, 6)), "activity_code": "A01",
                           "cost": f"{rng.uniform(20000, 90000):.0f}", "text": f"Replace ESP on {well}"})
            hours = rng.choice([24, 48, 96])
            downtime.append({"event_id": f"RDT-{len(downtime) + 1:05d}", "object_code": f"{FACILITY}_{well.replace(' ', '')}",
                             "start": f"{day}T06:00", "end": f"{_add_days(day, hours // 24)}T06:00",
                             "reason_code": "ESP-FAIL", "lost_oil_sm3": f"{hours / 24 * 25:.0f}",
                             "lost_gas_ksm3": "0", "comment": f"{esp} down on {well}, replaced pump"})

    for i in range(logs):
        pad, well = rng.choice(wells_all)
        day = f"{rng.choice([2024, 2025])}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        text = rng.choice(LOGS).format(well=well, tank=f"{pad}-TK-201{rng.choice('AB')}", sep=f"{pad}-SEP-101",
                                       esp=f"{pad}-ESP-3{rng.randint(1, 12):02d}")
        entries.append({"entry_id": f"RLOG-{day}-{i:05d}", "timestamp": f"{day}T07:00:00", "facility": FACILITY,
                        "author": rng.choice(["Lease operator", "Pumper", "Foreman"]), "text": text})

    # GIS: pad and wellhead locations. Pads sit on a ~2 km grid; wellheads 10-60 m from their pad.
    grng = random.Random(seed + 9)
    origin = (-101.95, 31.92)
    pad_at = {}
    feats, well_feats, truth = [], [], []
    for p in range(1, pads + 1):
        pad = f"{p:02d}"
        lon = round(origin[0] + ((p - 1) % 8) * 0.021 + grng.uniform(-0.002, 0.002), 6)
        lat = round(origin[1] + ((p - 1) // 8) * 0.018 + grng.uniform(-0.002, 0.002), 6)
        pad_at[pad] = (lon, lat)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": {"facility_id": f"PAD-{pad}", "floc": f"{FACILITY}-{pad}", "name": f"Pad {pad}",
                                     "type": "well pad", "latitude": lat, "longitude": lon}})
    for pad, well in wells_all:
        lon0, lat0 = pad_at[pad]
        d, ang = grng.uniform(10, 60), grng.uniform(0, 6.283)
        lon = round(lon0 + d * math.cos(ang) / 94_500, 6)
        lat = round(lat0 + d * math.sin(ang) / 111_000, 6)
        well_feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                           "properties": {"well_name": well, "well_type": "oil", "lift_type": "ESP",
                                          "latitude": lat, "longitude": lon}})
        truth.append({"src": well, "rel": "located_at", "dst": f"{FACILITY}-{pad}"})
    (out / "gis_facilities.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, indent=1))
    (out / "gis_wells.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": well_feats}, indent=1))
    (out / "answer_keys").mkdir(exist_ok=True)
    (out / "answer_keys" / "spatial.json").write_text(json.dumps(truth, indent=1))

    _write_csv(out / "sap_floc.csv", floc)
    _write_csv(out / "sap_equipment.csv", equipment)
    _write_csv(out / "pi_points.csv", points)
    _write_csv(out / "oplogs.csv", entries)
    _write_csv(out / "sap_notifications.csv", notes)
    _write_csv(out / "sap_work_orders.csv", orders)
    _write_csv(out / "ec_downtime.csv", downtime)
    (out / "pi_af.json").write_text(json.dumps(af, indent=1))
    (out / "ec_network.json").write_text(json.dumps({"nodes": nodes, "edges": edges}, indent=1))
    return {"asset": "onshore", "pads": pads, "wells": len(wells_all), "flocs": len(floc), "equipment": len(equipment),
            "pi_points": len(points), "notifications": len(notes), "downtime": len(downtime), "logs": len(entries)}
