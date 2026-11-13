"""Shared helpers for the Okan fake plant."""

import csv
from dataclasses import dataclass
from pathlib import Path

DAMAGE_TEXT = {"D01": "High vibration on {d}", "D02": "Seal / flange leak on {d}", "D03": "{d} failed to start",
               "D04": "High temperature on {d}", "D05": "Low output from {d}", "D06": "Abnormal noise from {d}",
               "D07": "Level indication faulty on {d}", "D08": "Internal leak suspected on {d}",
               "D09": "{d} tripped unexpectedly", "D10": "{d} broken down"}
CAUSES = {"D01": ["C02", "C06", "C01"], "D02": ["C01", "C03"], "D03": ["C04", "C05"], "D04": ["C05", "C01"],
          "D05": ["C01", "C05"], "D06": ["C01", "C06"], "D07": ["C04"], "D08": ["C03", "C01"],
          "D09": ["C04", "C02"], "D10": ["C01"]}
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


def _purchase_orders(materials: list[dict]) -> list[dict]:
    """SAP purchase orders (P5.5a): every other material out of stock is on order, due in a few days to a few weeks
    (dated from the day it is generated), and a few delivered orders show what came in."""
    from datetime import date, timedelta

    today = date.today()
    out = []
    for n, m in enumerate(materials):
        if m["stock"] == "0" and n % 2 == 0:
            out.append({"ebeln": f"45000{n:05d}", "matnr": m["matnr"], "quantity": "2",
                        "delivery_date": (today + timedelta(days=3 + n % 20)).isoformat(), "status": "open", "vendor": m["manufacturer"]})
        elif n % 9 == 0:
            out.append({"ebeln": f"45000{n:05d}", "matnr": m["matnr"], "quantity": "1",
                        "delivery_date": (today - timedelta(days=5 + n % 30)).isoformat(), "status": "delivered", "vendor": m["manufacturer"]})
    return out


def _lead_times(materials: list[dict]) -> None:
    """Each material's planned delivery time in days (SAP MARC-PLIFZ, #362): a week for common seals and gaskets, up to
    a few months for bearing sets from some makers. Deterministic, added to the material rows."""
    for n, m in enumerate(materials):
        m["plifz"] = str(7 + (n * 11) % 35 + (45 if "Bearing set" in m["description"] and n % 3 == 0 else 0))


def _reservations(orders: list[dict], bom: list[dict], materials: list[dict], pos: list[dict]) -> tuple[list, dict]:
    """SAP reservations (RESB, #362): the parts the BOM lists for an open work order, reserved against it for the day
    the job starts (dated from the day it is generated). Two are planted for the late-arrival check: a part whose
    open purchase order is due after the job needs it, and a part with none in stock, nothing on order and a planned
    delivery time longer than the time left. The answer key names them."""
    from datetime import date, timedelta

    today = date.today()
    by_mat = {m["matnr"]: m for m in materials}
    po_due = {p["matnr"]: p for p in pos if p["status"] == "open"}
    parts: dict[str, list[str]] = {}
    for b in bom:
        parts.setdefault(b["equnr"], []).append(b["matnr"])
    open_orders = [o for o in orders if o["status"] in ("CRTD", "REL") and parts.get(o["equnr"])]
    out, key, late, short = [], {}, None, None
    for n, o in enumerate(open_orders[:40]):
        for m in parts[o["equnr"]]:
            mat, need = by_mat[m], today + timedelta(days=14 + (n * 3) % 28)
            if late is None and m in po_due:  # the order lands five days after the job needs it
                due = date.fromisoformat(po_due[m]["delivery_date"])
                if due - timedelta(days=5) > today:
                    need, late = due - timedelta(days=5), (o["aufnr"], m)
            elif (short is None and mat["stock"] == "0" and m not in po_due
                  and int(mat["plifz"]) > 21):  # none in stock, nothing on order, and too far off to arrive in time
                need, short = today + timedelta(days=10), (o["aufnr"], m)
            out.append({"rsnum": f"{len(out) + 1:010d}", "rspos": "0001", "aufnr": o["aufnr"], "matnr": m,
                        "quantity": "1", "need_date": need.isoformat(), "status": "open"})
    if late:
        key["late_order"] = {"work_order": late[0], "material": late[1]}
    if short:
        key["not_on_order"] = {"work_order": short[0], "material": short[1]}
    return out, key


WORK_CENTERS = [  # SAP work centers (P5.5b): the crews work orders are planned against
    {"arbpl": "MECH", "description": "Mechanical maintenance crew", "crew_size": "6", "shift": "days"},
    {"arbpl": "INST", "description": "Instrument technicians", "crew_size": "2", "shift": "days"},
    {"arbpl": "ELEC", "description": "Electrical technicians", "crew_size": "2", "shift": "days"},
]


def _permits(equipment: list[dict]) -> tuple[list[dict], list[dict]]:
    """Permit-to-work permits and their isolations (P5.5c) on every seventh item now installed: most issued and in
    date, some expired, and every third one still waiting on an isolation. Dated from the day it is generated."""
    from datetime import date, timedelta

    today = date.today()
    items = sorted({e["floc"]: e for e in equipment if e["floc"].count("-") == 2}.values(), key=lambda e: e["floc"])
    permits, isolations = [], []
    for n, e in enumerate(items[::7]):
        expired = n % 4 == 3
        start, end = (today - timedelta(days=9), today - timedelta(days=2)) if expired else \
            (today - timedelta(days=1), today + timedelta(days=2))
        no = f"PTW-{30000 + n}"
        permits.append({"permit_no": no, "floc": e["floc"], "permit_type": "hot work" if n % 5 == 0 else "cold work",
                        "state": "expired" if expired else "issued", "valid_from": f"{start.isoformat()} 06:00",
                        "valid_to": f"{end.isoformat()} 18:00", "issued_to": "Mechanical maintenance crew",
                        "work": f"Maintenance on {e['description'].lower()}"})
        for k, (point, method) in enumerate([("inlet valve", "closed and locked"), ("outlet valve", "closed and locked"),
                                             ("casing", "drained and vented")][: 2 + n % 2]):
            last = k == 1 + n % 2
            isolations.append({"isolation_id": f"ISO-{30000 + n}-{k + 1}", "permit_no": no,
                               "point": f"{e['floc'].split('-', 1)[1]} {point}", "method": method,
                               "state": "planned" if last and n % 3 == 0 else "applied"})
    # Closed permits migrated from the plant's old permit system (P6.5): it named the location by the SAP equipment
    # number with its own prefix, which no resolver reads; stewards answer a few, and the register learns the rest.
    for n, e in enumerate(items[3::5]):
        start = today - timedelta(days=400 + 7 * n)
        permits.append({"permit_no": f"LPS-{7000 + n}", "floc": f"EQ-{e['equnr']}", "permit_type": "cold work",
                        "state": "closed", "valid_from": f"{start.isoformat()} 06:00",
                        "valid_to": f"{(start + timedelta(days=2)).isoformat()} 18:00",
                        "issued_to": "Mechanical maintenance crew", "work": f"Maintenance on {e['description'].lower()}"})
    return permits, isolations


def _rounds(equipment: list[dict]) -> list[dict]:
    """Operator rounds checklist points (P5.4a): one round per area, reading and checking up to four items installed
    there now. Deterministic."""
    items = sorted({e["floc"]: e for e in equipment if e["floc"].count("-") == 2}.values(), key=lambda e: e["floc"])
    by_area: dict[str, list[dict]] = {}
    for e in items:
        by_area.setdefault(e["floc"].split("-")[1], []).append(e)
    out = []
    for area, es in sorted(by_area.items()):
        for n, e in enumerate(es[:4]):
            tag = e["floc"].split("-", 1)[1]
            reading = n % 2 == 0
            out.append({"point_id": f"R-{area}-{n + 1:02d}", "round": f"Area {area} rounds", "area": area,
                        "sort": str(n + 1), "floc": e["floc"],
                        "what": f"{tag} bearing temperature" if reading else f"{tag} leak-free, no unusual noise",
                        "kind": "reading" if reading else "check", "unit": "degC" if reading else "",
                        "low": "0" if reading else "", "high": "85" if reading else ""})
    return out


def _work_center(text: str) -> str:
    """The crew a work order's text calls for: instrument work to INST, electrical to ELEC, the rest to MECH."""
    t = text.lower()
    if any(w in t for w in ("transmitter", "calibrat", "instrument", "analyser", "analyzer")):
        return "INST"
    if any(w in t for w in ("motor", "electrical", "switchgear", "battery", "batteries", "cable")):
        return "ELEC"
    return "MECH"


def _add_hours(when: str, n: int) -> str:
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(when) + timedelta(hours=n)).isoformat(timespec="minutes")


def _add_days(day: str, n: int) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()
