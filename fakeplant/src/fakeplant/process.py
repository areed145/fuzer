"""The fake plant's process model (#91): which PI point measures what on which unit, the units' design
values, and the stories as timed events, written to process_model.json for fuzer.process_model to
evaluate. Stories are taken from the same downtime and log records the generator wrote, so the process
values agree with what the operators logged."""

import math
import re
from datetime import datetime, timedelta

from . import curves

VARS = {"Discharge Pressure": "discharge_pressure", "Bearing Temperature": "bearing_temperature",
        "Vibration": "vibration", "Discharge Temperature": "discharge_temperature", "Speed": "speed",
        "Exhaust Temperature": "exhaust_temperature", "Vessel Pressure": "vessel_pressure",
        "Liquid Level": "liquid_level", "Outlet Temperature": "outlet_temperature",
        "Suction Pressure": "suction_pressure", "Flow": "flow", "Motor Power": "power"}
WELL_VARS = {"WHP": "whp", "WHT": "wht", "CHOKE": "choke", "GLIR": "glir", "CASP": "casp", "QGAS": "qgas"}
TAG = re.compile(r"(\d\d)-?([A-Z]+)-?(\d{4})([A-Z]?)\b")

# Compression: stage -> (suction, discharge) barg; the 3rd stage feeds the lift gas header.
STAGES = {1: (6.0, 18.0), 2: (17.0, 38.0), 3: (37.0, 92.0)}
HEADER = {"10": "header:20", "30": "header:30", "40": "header:40", "50": "header:20"}
VESSELS = {"10-V-2001": (12.0, 50), "10-V-2002": (3.0, 50), "10-V-2003": (12.0, 45), "10-V-2004": (2.5, 55),
           "20-V-2001": (6.5, 45), "20-V-2002": (88.0, 25), "20-V-2400": (89.0, 40)}


def _hours(t: str, h: float) -> str:
    return (datetime.fromisoformat(t) + timedelta(hours=h)).isoformat(timespec="minutes")


def _days(t: str, d: float) -> str:
    return _hours(t, 24 * d)


SG = 0.9  # produced liquid through the choke
P_RES, C_GAS = 120.0, 40 / 600  # gas producers: reservoir pressure (barg) and drawdown per kSm3/d


def choke_opening(c: dict, cv: float) -> float:
    """The opening at which a choke passes `cv`."""
    if c["trim"] == "linear":
        o = 100 * cv / c["cv_max"]
    else:
        o = 100 * (1 + math.log(max(cv, 1e-6) / c["cv_max"]) / math.log(30))
    return round(min(85.0, max(12.0, o)), 1)


def okan_model(seed: int, points: list[dict], equipment: list[dict], wells: list[dict], wparams: dict,
               hub: dict, downtime: list[dict], logs: list[dict], oem: dict | None = None,
               chokes: list[dict] | None = None, tests: list[dict] | None = None) -> dict:
    units: dict[str, dict] = {}
    pts: dict[str, list[str]] = {}
    events: list[dict] = []

    # ---- equipment and trains ----
    tags = {}
    for e in equipment:
        _, area, rest = e["floc"].split("-")  # OKAN-20-K2200B
        func = rest.rstrip("0123456789ABC")
        loop, suffix = rest[len(func):len(func) + 4], rest[len(func) + 4:]
        tags[(area, loop, suffix)] = (f"{area}-{func}-{loop}{suffix}", func)
    for t in "ABC":
        units[f"train:{t}"] = {"type": "train"}
    for (area, loop, s), (tag, func) in tags.items():
        if area == "20" and s in "ABC" and s:
            train = f"train:{s}"
            stage = int(loop[1]) if loop[0] == "2" and loop[1] in "123" else None
            if func == "K":
                ps, pd = STAGES[stage]
                units[tag] = {"type": "compressor", "train": train, "stage": stage, "p_suction": ps, "p_discharge": pd,
                              "vibration": 2.2, "cooler_before": f"20-E-2{stage - 1}50{s}" if stage > 1 else None}
            elif func == "GT":
                units[tag] = {"type": "gas_turbine", "train": train}
            elif func == "E":
                units[tag] = {"type": "cooler", "train": train}
            elif func == "V":
                units[tag] = {"type": "vessel", "train": train, "pressure": STAGES[stage][0], "level": 35}
        elif func == "V":
            p, lvl = VESSELS.get(tag, (13.0, 50))
            units[tag] = {"type": "vessel", "pressure": p, "level": lvl}
        elif func == "P":
            c = (oem or {}).get(next(e["model"] for e in equipment if e["floc"].endswith(f"-{area}-P{loop}{s}")), {})
            units[tag] = {"type": "pump", "group": "oil_export", "meter": "meter:oil_export", "duty": s != "C",
                          "q_bep": c.get("q_bep", 190.0), "h_bep": c.get("h_bep", 430.0), "h0": c.get("h0", 520.0),
                          "eff_bep": c.get("eff_bep", 0.78), "p_suction": 3.0, "vibration": 1.8}

    # ---- field: headers, meters, valves ----
    gas_share = {"OK-NWP-G1": 0.08, "OK-NWP-G2": 0.23, "OK-NWP-G3": 0.08}
    units["header:20"] = {"type": "header", "p0": 88.0, "flow0": 1800.0, "trains": ["train:A", "train:B", "train:C"],
                          "gas_wells": list(gas_share), "gas_share": gas_share, "bypass": "valve:20-XV-3100"}
    units["header:30"] = {"type": "header", "parent": "header:20", "dp": 3.0, "p0": 85.0, "flow0": 720.0}
    units["header:40"] = {"type": "header", "parent": "header:20", "dp": 4.0, "p0": 84.0, "flow0": 440.0}
    units["meter:oil_export"] = {"type": "meter", "base": 8600.0, "header": "header:20", "lift_share": 0.3}
    units["meter:st1"] = {"type": "meter", "base": 2600.0, "header": "header:30", "lift_share": 0.35}
    units["meter:st2"] = {"type": "meter", "base": 1700.0, "header": "header:40", "lift_share": 0.35}
    units["meter:egp_export"] = {"type": "meter", "base": 1700.0}
    units["meter:nwp_export"] = {"type": "meter", "base": 900.0}
    units["valve:20-XV-3100"] = {"type": "valve", "normal": 0}
    units["valve:50-XV-3100"] = {"type": "valve", "normal": 100}
    loose = {"Oil Export Fiscal Meter": ("meter:oil_export", "flow"), "EGP Gas Export Meter": ("meter:egp_export", "flow"),
             "GGCP Lift Gas Header Flow": ("header:20", "flow"), "GGCP Lift Gas Header Pressure": ("header:20", "pressure"),
             "ST1 Lift Gas Header Flow": ("header:30", "flow"), "ST1 Lift Gas Header Pressure": ("header:30", "pressure"),
             "ST2 Lift Gas Header Flow": ("header:40", "flow"), "ST2 Lift Gas Header Pressure": ("header:40", "pressure"),
             "Compression Bypass Valve": ("valve:20-XV-3100", "position"), "ST1 Trunkline Meter": ("meter:st1", "flow"),
             "ST2 Trunkline Meter": ("meter:st2", "flow"), "NWP Gas Export Meter": ("meter:nwp_export", "flow"),
             "NWP EGP Export ESDV": ("valve:50-XV-3100", "position")}

    # ---- wells and jackets ----
    choke_of = {c["well"]: c for c in (chokes or []) if not c.get("valid_to")}
    liquid: dict[str, list] = {}
    for t in tests or []:  # each well's liquid rate between tests follows its tests
        cut = min(float(t["water_cut_pct"]), 98.0)
        liquid.setdefault(t["well"], []).append([f"{t['test_date']}T12:00", round(float(t["oil_sm3d"]) / (1 - cut / 100), 2)])
    for w in wells:
        jacket = f"jacket:{w['area']}" if w["site"].startswith("J") else None
        p = wparams.get(w["name"], {})
        q = p.get("rate", 60.0) * 1.6  # liquid through the choke
        ch = (oem or {}).get(choke_of[w["name"]]["model"]) if w["name"] in choke_of else None
        ch = ch or {"cv_max": 12.0, "trim": "equal_percentage"}
        if w["type"] == "gas":
            choke0 = 45.0
            cv0 = curves.cv_at(ch, choke0) if "series" in ch else ch["cv_max"] * 0.45
            k = 600.0 / (cv0 * (P_RES - C_GAS * 600 + 1.013))
        else:
            dp = 8.0  # bar across the choke at the normal rate
            choke0 = choke_opening(ch, q / 24 * 4.403 * math.sqrt(SG / (dp * 14.5038)))
            k = None
        units[w["name"]] = {"type": "well", "kind": w["type"], "lift": w["lift"], "jacket": jacket,
                            "header": HEADER[hub.get(w["area"], "50")] if w["site"].startswith("J") else "header:20",
                            "q_ref": q, "knee": p.get("knee", 8.0), "glir0": 12.5, "choke0": choke0,
                            "choke": {"cv_max": ch["cv_max"], "trim": ch["trim"]}, "sg": SG, "qgas0": 600.0,
                            "p_res": P_RES, "c_gas": C_GAS, **({"k_gas": round(k, 5)} if k else {}),
                            **({"liquid_path": sorted(liquid[w["name"]])} if w["name"] in liquid else {})}
        if jacket:
            j = units.setdefault(jacket, {"type": "jacket", "wells": [], "gl_wells": [], "separator": "10-V-2001"})
            j["wells"].append(w["name"])
            if w["lift"] == "GL":
                j["gl_wells"].append(w["name"])

    # ---- points -> (unit, variable) ----
    for p in points:
        name, desc = p["tag"], p["descriptor"]
        if desc.startswith("OK-") and "." in name:  # a well's point: OK_J07_03.GLIR
            well = desc.split(" ", 1)[0]
            var = WELL_VARS.get(name.rsplit(".", 1)[1])
            if var and well in units:
                pts[name] = [well, var]
            continue
        if desc in loose:
            pts[name] = list(loose[desc])
            continue
        jacket_desc = desc.split(" ", 1)[1] if desc.startswith("OKJ-") else None
        if jacket_desc:
            area = TAG.search(name).group(1)
            var = {"Gas Lift Supply Meter": "gas_lift_supply", "Production Header Pressure": "production_header_pressure",
                   "Jacket ESDV": "esdv"}.get(jacket_desc)
            if var and f"jacket:{area}" in units:
                pts[name] = [f"jacket:{area}", var]
            continue
        m = TAG.search(name)
        item = tags.get((m.group(1), m.group(3), m.group(4))) if m else None
        if m and not item and desc.endswith("Suction Pressure"):  # a pump's suction transmitter: next loop up
            item = tags.get((m.group(1), str(int(m.group(3)) - 10), m.group(4)))
        var = next((v for k, v in VARS.items() if desc.endswith(k)), None)
        if item and var and item[0] in units:
            pts[name] = [item[0], var]

    # ---- stories, from what the generator logged ----
    for d in downtime:
        start, end, why, obj, text = d["start"], d["end"], d["reason_code"], d["object_code"], d["comment"]
        if why == "PLAN-MAINT":
            events.append({"unit": "field", "kind": "shutdown", "start": start, "end": end})
        elif why in ("PROC-TRIP", "LIFT-GAS"):
            train = "B" if "20-K-2200B" in text else "A" if "20-K-2300A" in text else "C" if "Train C" in text else None
            if train:
                events.append({"unit": f"train:{train}", "kind": "trip", "start": start, "end": end})
            if train == "B":  # bypass opened to keep lift gas to the jackets; two wells died
                events.append({"unit": "valve:20-XV-3100", "kind": "open", "start": _hours(start, 0.5), "end": end,
                               "position": 60})
                for w in ("OK-J07-03", "OK-J12-02"):
                    events.append({"unit": w, "kind": "shut_in", "start": _hours(start, 1), "end": _hours(end, 2)})
        elif why == "HYDRATE":
            w = obj.replace("OKAN ", "OK-")
            events.append({"unit": w, "kind": "restriction", "points": [[_hours(start, -6), 0], [start, 0.7],
                                                                         [_hours(start, 0.5), 0]]})
            events.append({"unit": w, "kind": "shut_in", "start": _hours(start, 0.5), "end": end})
            events.append({"unit": w, "kind": "choke", "at": start, "to": 30})
            events.append({"unit": w, "kind": "choke", "at": end, "to": 45})
        elif why == "EXT-EGP":
            events.append({"unit": "meter:egp_export", "kind": "curtail", "start": start, "end": end, "factor": 0.33})
            events.append({"unit": "meter:nwp_export", "kind": "curtail", "start": start, "end": end, "factor": 1.0})
            events.append({"unit": "valve:50-XV-3100", "kind": "open", "start": start, "end": end, "position": 0})
        elif why in ("WELL-INT", "GL-VALVE") and obj in units:
            events.append({"unit": obj, "kind": "shut_in", "start": start, "end": end})
        if why == "GL-VALVE" and obj in units:
            events.append({"unit": obj, "kind": "valve_failed_open", "start": start, "end": _days(start, 75)})

    # Train B 2nd stage bearing wear: vibration creeps up from late January, peaks before each trip,
    # gone after the bearings are replaced (2024-04-03, back 04-07).
    trips_b = sorted(e["start"] for e in events if e["unit"] == "train:B" and e["kind"] == "trip")
    wear = [["2024-01-20T00:00", 0], ["2024-02-26T09:00", 4.6], ["2024-03-08T14:00", 6.8]]
    for t in trips_b:
        wear += [[_hours(t, -6), 6.9], [_hours(t, -0.1), 8.8], [_hours(t, 1), 6.6]]
    wear += [["2024-04-02T23:00", 7.4], ["2024-04-03T00:00", 0]]
    events.append({"unit": "20-K-2200B", "kind": "vibration", "points": sorted(wear)})
    events.append({"unit": "train:B", "kind": "down", "start": "2024-04-03T00:00", "end": "2024-04-07T16:00"})

    # Train A: the 3rd stage after-cooler fouls each spring and summer until cleaned; recycle through it
    # heats the 3rd stage suction, so its discharge temperature climbs to the trip on hot afternoons.
    events.append({"unit": "20-E-2350A", "kind": "fouling", "points": [
        ["2024-02-01T00:00", 0], ["2024-08-19T00:00", 34], ["2024-08-19T06:00", 0],
        ["2025-02-01T00:00", 3], ["2025-07-28T00:00", 36], ["2025-07-28T06:00", 0]]})
    units["20-K-2300A"].update({"recycle_cooler": "20-E-2350A", "recycle": 0.45})
    for e in [e for e in events if e["unit"] == "train:A" and e["kind"] == "trip"]:
        events.append({"unit": "20-K-2300A", "kind": "discharge_temperature",
                       "points": [[_hours(e["start"], -3), 0], [_hours(e["start"], -0.1), 26], [e["start"], 0]]})

    # Inlet separator: the level transmitter hunts from 4 to 12 November 2024 while the real level twice
    # runs high, and liquid carries over into train C's 1st stage scrubber.
    events.append({"unit": "20-V-2001", "kind": "erratic", "start": "2024-11-04T02:00", "end": "2024-11-12T12:00"})
    for e in [e for e in events if e["unit"] == "train:C" and e["kind"] == "trip"]:
        events.append({"unit": "20-V-2001", "kind": "level", "points": [
            [_hours(e["start"], -3), 0], [_hours(e["start"], -1), 45], [e["start"], 52], [_hours(e["start"], 0.5), 0]]})
    if "20-V-2101C" in units:
        units["20-V-2101C"].update({"carryover_from": "20-V-2001", "carryover_gain": 2.2})

    # Choke moves and pump swaps the operators logged.
    for lg in logs:
        if m := re.match(r"(J\d\d-\d\d|NWP-\d\d) choke opened \d+ to (\d+)%", lg["text"]):
            if f"OK-{m.group(1)}" in units:
                events.append({"unit": f"OK-{m.group(1)}", "kind": "choke", "at": lg["timestamp"][:16], "to": int(m.group(2))})
        elif lg["text"].startswith("Swapped oil export pumps 10-P-1001A to 10-P-1001C"):  # 8 h of maintenance
            t = lg["timestamp"][:16]
            events += [{"unit": "10-P-1001A", "kind": "stopped", "start": t, "end": _hours(t, 8)},
                       {"unit": "10-P-1001C", "kind": "covering", "start": t, "end": _hours(t, 8)}]
    events += [{"unit": "10-P-1001B", "kind": "stopped", "start": "2025-05-20T11:00", "end": "2025-05-23T12:00"},
               {"unit": "10-P-1001C", "kind": "covering", "start": "2025-05-20T11:05", "end": "2025-05-23T12:00"}]

    # Oil export pump A: wear rings open up through 2025, head falls at the same flow; new rings on 18 Sep.
    events.append({"unit": "10-P-1001A", "kind": "head_loss", "points": [
        ["2025-01-01T00:00", 0], ["2025-09-10T00:00", 0.08], ["2025-09-19T00:00", 0.085], ["2025-09-20T12:00", 0]]})
    # OK-J12-02's choke erodes (the operators trim it back to hold the rate) until replaced on 15 Oct;
    # OK-NWP-G3's choke plugs with scale over August until the bean change on 12 Sep.
    events.append({"unit": "OK-J12-02", "kind": "choke_wear", "points": [
        ["2025-01-15T00:00", 0], ["2025-09-28T00:00", 0.6], ["2025-10-15T06:00", 0.62], ["2025-10-15T08:00", 0]]})
    j = units.get("OK-J12-02")
    if j:
        for month in range(2, 11):
            wear = 0.6 * (month - 1) / 8.5
            events.append({"unit": "OK-J12-02", "kind": "choke", "at": f"2025-{month:02d}-01T08:00",
                           "to": round(max(12.0, j["choke0"] - 100 * math.log(1 + min(wear, 0.6)) / math.log(30)), 1)})
        events.append({"unit": "OK-J12-02", "kind": "choke", "at": "2025-10-15T08:00", "to": j["choke0"]})
    events.append({"unit": "OK-NWP-G3", "kind": "choke_wear", "points": [
        ["2025-08-01T00:00", 0], ["2025-09-03T00:00", -0.35], ["2025-09-12T10:00", -0.36], ["2025-09-12T12:00", 0]]})
    # Back-pressure stories (#107): a flowline waxing up over seven weeks until it is pigged; the HP separator's
    # setpoint lowered 2 bar; a well rerouted to another jacket's header.
    plain = sorted(n for n, u in units.items() if u.get("type") == "well" and u.get("kind") != "gas" and u.get("jacket")
                   and n not in STORY_WELLS)
    if plain:
        events.append({"unit": plain[len(plain) // 3], "kind": "flowline_restriction", "points": [
            ["2025-10-01T00:00", 0], ["2025-11-18T00:00", 1.0], ["2025-11-18T06:00", 0]]})
        if "10-V-2001" in units:
            events.append({"unit": "10-V-2001", "kind": "setpoint", "at": "2025-11-25T08:00",
                           "to": units["10-V-2001"].get("pressure", 12.0) - 2.0})
        moved = plain[2 * len(plain) // 3]
        other = next((j for j in sorted(u for u in units if u.startswith("jacket:")) if j != units[moved]["jacket"]), None)
        if other:
            events.append({"unit": moved, "kind": "reroute", "at": "2025-12-05T10:00", "to": other})
    return {"version": 1, "seed": seed, "points": pts, "units": units, "events": events}


STORY_WELLS = {"OK-J07-03", "OK-J12-02", "OK-J03-02", "OK-NWP-G3"}


def scale_tests(model: dict, tests: list[dict], since: str = "2025-10-01") -> list[dict]:
    """Well tests after a back-pressure event as a test separator would measure them: the model's rate
    factor at the test applied to oil (the model itself runs on the tests as generated)."""
    from fuzer.process_model import ProcessModel

    m = ProcessModel(model)
    out = []
    for t in tests:
        if t["test_date"] >= since and t["well"] in model["units"] and model["units"][t["well"]].get("jacket"):
            f = m.at(t["well"], "bp_factor", datetime.fromisoformat(f"{t['test_date']}T12:00"))
            if abs(f - 1) > 1e-3:
                t = {**t, "oil_sm3d": round(float(t["oil_sm3d"]) * f, 1)}
        out.append(t)
    return out
