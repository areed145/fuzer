"""Project schedules for Okan as a Delta Lake table (#227): a source Fuzer has no mapping for, to add through the
Sources page alone.

One row per project or activity, the way a Primavera P6 / MS Project export looks: activity_id (the key),
project_id, parent_id, wbs, name, location, start / finish, baseline_start / baseline_finish, actual_start /
actual_finish, percent_complete, owner, status, work_order, updated_at. Built from the Okan exports, so it links
to what Fuzer already holds: the 2026 turnaround's activities are the SAP work orders under revision OK-TAR-2026
(with their order numbers), the gas lift project works on J12's real wells.

Mess on purpose, for profiling and checks to find (listed in the answer key):
- locations in several styles (20-K-2200B, 20K2200B, OKAN-10-V2001, J12-04, OK J12 07) and one that matches nothing;
- dates in the export's local format, dd/mm/yyyy (set the system's date_format to %d/%m/%Y);
- one activity id twice (a duplicate key: the load is held until it's fixed);
- a percent_complete of 112.

    uv run fakeplant schedules --out fakeplant/data/okan_lake             # Delta version 0, as of 2025-12-30
    uv run fakeplant schedules --out fakeplant/data/okan_lake --update    # version 1, as of 2026-01-15: progress
                                                                          # moves, a finish slips, one row goes
"""

import csv
import json
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path

TABLE = "project_schedules"
AS_OF = (date(2025, 12, 30), date(2026, 1, 15))
OWNERS = {"tar": "M. Okafor (Turnaround)", "maint": "J. Ortiz (Maintenance)", "pe": "A. Bello (Production Engineering)",
          "rel": "S. Haddad (Reliability)", "proc": "Process Engineering"}


def _d(x: date | None) -> str | None:
    return x.strftime("%d/%m/%Y") if x else None  # the export's local format


def _tag_styles(floc: str, i: int) -> str:
    """A FLOC (OKAN-20-K2200B) written the way different planners write it."""
    m = re.match(r"OKAN-(\d+)-([A-Z]+)(\d+[A-Z]?)$", floc)
    if not m:
        return floc
    area, fn, num = m.groups()
    return [f"{area}-{fn}-{num}", f"{area}{fn}{num}", floc, f"{area}-{fn}{num}", f"{area} {fn} {num}"][i % 5]


def _well_style(name: str, i: int) -> str:
    site, n = re.match(r"OK-(J\d+)-(\d+)", name).groups()
    return [name, f"{site}-{n}", f"OK {site} {n}", f"OK-{site}-{int(n)}"][i % 4]


def rows(okan: Path, version: int = 0, seed: int = 11) -> tuple[list[dict], dict]:
    """The table's rows at a version, and the answer key."""
    rnd = random.Random(seed)
    as_of = AS_OF[min(version, 1)]
    out, truth = [], {"as_of": as_of.isoformat(), "links": {}, "issues": []}

    def add(aid, project, parent, wbs, name, location, start, finish, owner, *, pct=0, base=None, actual=(None, None),
            status=None, wo=None, expect=None):
        base = base or (start, finish)
        st = status or ("complete" if pct >= 100 else "in progress" if pct > 0 else "not started")
        out.append({"activity_id": aid, "project_id": project, "parent_id": parent, "wbs": wbs, "name": name,
                    "location": location, "start": _d(start), "finish": _d(finish), "baseline_start": _d(base[0]),
                    "baseline_finish": _d(base[1]), "actual_start": _d(actual[0]), "actual_finish": _d(actual[1]),
                    "percent_complete": pct, "owner": owner, "status": st, "work_order": wo,
                    "updated_at": datetime.combine(as_of, datetime.min.time()) + timedelta(hours=7, minutes=len(out))})
        truth["links"][aid] = {**(expect or {}), **({"work_order": wo} if wo else {})}

    # 1. the 2026 turnaround: one activity per SAP work order under its revision, plus the work around them
    tar_s, tar_f = date(2026, 4, 6), date(2026, 4, 30)
    add("OK-TAR-2026", "OK-TAR-2026", None, "1", "Okan 2026 turnaround (GGCP and production platform)", "OKAN",
        date(2025, 10, 1), tar_f, OWNERS["tar"], pct=6 if version == 0 else 9, actual=(date(2025, 10, 1), None),
        expect={"kind": "facility", "key": "OKAN"})
    orders = [r for r in csv.DictReader((okan / "sap_work_orders.csv").open()) if r["revision"] == "OK-TAR-2026"]
    prep = [("A0010", "Turnaround scope freeze and work pack approval", date(2025, 10, 1), date(2026, 1, 31), 70, 85),
            ("A0020", "Scaffolding and isolation plan, area 20", date(2026, 2, 1), date(2026, 3, 27), 0, 10),
            ("A0030", "Mobilise turnaround crew and rental compressors", date(2026, 3, 16), date(2026, 4, 3), 0, 0)]
    for code, name, s, f, p0, p1 in prep:
        add(f"OK-TAR-2026-{code}", "OK-TAR-2026", "OK-TAR-2026", f"1.{code[2:4].lstrip('0') or '0'}", name,
            "20" if "area 20" in name else "OKAN", s, f, OWNERS["tar"], pct=p0 if version == 0 else p1,
            actual=(s, None) if (p0 if version == 0 else p1) else (None, None),
            expect={"kind": "area", "floc": "OKAN-20"} if "area 20" in name else {"kind": "facility", "key": "OKAN"})
    for i, wo in enumerate(orders):
        aid = f"OK-TAR-2026-A{100 + 10 * i:04d}"
        add(aid, "OK-TAR-2026", "OK-TAR-2026", f"1.{4 + i}", wo["text"], _tag_styles(wo["floc"], i), tar_s + timedelta(days=i),
            tar_f - timedelta(days=2 * (len(orders) - i)), OWNERS["maint"], wo=wo["aufnr"],
            expect={"kind": "tag", "floc": wo["floc"]})
    add("OK-TAR-2026-A0900", "OK-TAR-2026", "OK-TAR-2026", "1.9", "Restart, leak test and handback to operations", "OKAN",
        tar_f - timedelta(days=2), tar_f, OWNERS["proc"], expect={"kind": "facility", "key": "OKAN"})

    # 2. gas lift optimisation on J12: a valve change per well, some done, one slipping
    gl = [w["name"] for w in json.loads((okan / "wells.json").read_text()) if w["site"] == "J12"]
    gl_s = date(2025, 9, 1)
    add("OK-CAP-0142", "OK-CAP-0142", None, "2", "Gas lift optimisation, jacket J12", "J12", gl_s, date(2026, 3, 31),
        OWNERS["pe"], pct=55 if version == 0 else 68, actual=(gl_s, None), expect={"kind": "area", "floc": "OKAN-72", "note": "jacket J12"})
    for i, well in enumerate(gl):
        s = gl_s + timedelta(days=14 * i)
        f = s + timedelta(days=10)
        done0, done1 = s + timedelta(days=12) <= AS_OF[0], s + timedelta(days=12) <= AS_OF[1]
        pct = 100 if (done0 if version == 0 else done1) else (40 if version == 1 and i == len(gl) - 3 else 0)
        slip = i == len(gl) - 2  # waiting on a slickline unit: its finish moves out in version 1
        add(f"OK-CAP-0142-{i + 1:03d}", "OK-CAP-0142", "OK-CAP-0142", f"2.{i + 1}",
            f"Change out gas lift valves and re-optimise injection, {well}", _well_style(well, i), s,
            f + timedelta(days=21 if slip and version == 1 else 0), OWNERS["pe"], pct=pct, base=(s, f),
            actual=(s, s + timedelta(days=12)) if pct == 100 else ((s, None) if pct else (None, None)),
            expect={"kind": "well", "key": well})

    # 3. train C reliability after the November 2024 trips
    k = "OKAN-20-K2100C"
    add("OK-CAP-0157", "OK-CAP-0157", None, "3", "Train C 1st stage compressor 20-K-2100C reliability improvements",
        _tag_styles(k, 0), date(2025, 2, 1), date(2026, 6, 30), OWNERS["rel"], pct=40 if version == 0 else 45,
        actual=(date(2025, 2, 1), None), expect={"kind": "tag", "floc": k})
    for j, (name, s, f, p) in enumerate([
            ("Root cause analysis of the November 2024 trips", date(2025, 2, 1), date(2025, 4, 30), 100),
            ("Vibration monitoring upgrade on 20-K-2100C", date(2025, 6, 1), date(2025, 11, 30), 80),
            ("Anti-surge valve and controller replacement", date(2026, 4, 6), date(2026, 4, 30), 0)]):
        add(f"OK-CAP-0157-{j + 1:03d}", "OK-CAP-0157", "OK-CAP-0157", f"3.{j + 1}", name, _tag_styles(k, j + 1), s, f,
            OWNERS["rel"], pct=p, actual=(s, f if p == 100 else None) if p else (None, None),
            expect={"kind": "tag", "floc": k})

    # 4. done: oil export pump B upgrade
    add("OK-CAP-0121", "OK-CAP-0121", None, "4", "Oil export pump 10-P-1001B seal plan upgrade", "10-P-1001B",
        date(2025, 3, 3), date(2025, 5, 30), OWNERS["maint"], pct=100, actual=(date(2025, 3, 3), date(2025, 6, 6)),
        base=(date(2025, 3, 3), date(2025, 5, 30)), expect={"kind": "tag", "floc": "OKAN-10-P1001B"})

    # 5. a location that matches nothing in Fuzer
    add("OK-MOC-0233", "OK-MOC-0233", None, "5", "Replace HP flare tip (MOC 233)", "FLARE-TIP-9", date(2026, 4, 6),
        date(2026, 4, 20), OWNERS["proc"], expect={"kind": None, "note": "matches nothing: a coverage gap"})
    truth["issues"].append({"activity_id": "OK-MOC-0233", "issue": "location matches nothing"})

    # mess: a duplicate key (a planner copied a row) and a percentage over 100
    dup = next(r for r in out if r["activity_id"] == "OK-CAP-0157-002")
    out.append({**dup, "percent_complete": 85, "updated_at": dup["updated_at"] + timedelta(minutes=1)})
    truth["issues"].append({"activity_id": "OK-CAP-0157-002", "issue": "duplicate activity_id"})
    over = next(r for r in out if r["activity_id"] == "OK-TAR-2026-A0010")
    over["percent_complete"] = 112 if version == 0 else 85
    if version == 0:
        truth["issues"].append({"activity_id": "OK-TAR-2026-A0010", "issue": "percent_complete over 100"})
    truth["issues"].append({"column": "start, finish, baseline_*, actual_*", "issue": "dates as dd/mm/yyyy"})

    if version == 1:  # the flare tip job was cancelled and dropped from the export
        out = [r for r in out if r["activity_id"] != "OK-MOC-0233"]
        truth["links"].pop("OK-MOC-0233")
        truth["issues"] = [x for x in truth["issues"] if x.get("activity_id") != "OK-MOC-0233"]
        truth["changes"] = {"removed": ["OK-MOC-0233"], "slipped": [f"OK-CAP-0142-{len(gl) - 1:03d}"],
                            "progressed": ["OK-TAR-2026", "OK-CAP-0142", "OK-CAP-0157", "OK-TAR-2026-A0010"]}
    rnd.shuffle(out)  # an export isn't sorted
    return out, truth


def write(okan: Path, out_dir: Path, update: bool = False, fixed: bool = False) -> dict:
    """Write the Delta table (version 0, or version 1 over it with --update) and its answer key. `fixed` drops the
    copied row (the planner's fix after the first load is held on the duplicate key), as a new table version."""
    import pyarrow as pa
    from deltalake import write_deltalake

    data, truth = rows(okan, 1 if update else 0)
    if fixed:
        data = list({r["activity_id"]: r for r in reversed(data)}.values())[::-1]  # the first of each id
    schema = pa.schema([*((c, pa.string()) for c in ("activity_id", "project_id", "parent_id", "wbs", "name", "location",
                                                       "start", "finish", "baseline_start", "baseline_finish",
                                                       "actual_start", "actual_finish")),
                        ("percent_complete", pa.int64()), ("owner", pa.string()), ("status", pa.string()),
                        ("work_order", pa.string()), ("updated_at", pa.timestamp("us"))])
    table = pa.Table.from_pylist(data, schema=schema)
    path = Path(out_dir) / TABLE
    path.parent.mkdir(parents=True, exist_ok=True)
    write_deltalake(str(path), table, mode="overwrite")
    key = Path(out_dir) / "answer_keys" / f"{TABLE}.json"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps(truth, indent=1, default=str))
    from deltalake import DeltaTable

    return {"table": str(path), "version": DeltaTable(str(path)).version(), "rows": table.num_rows, "answer_key": str(key)}
