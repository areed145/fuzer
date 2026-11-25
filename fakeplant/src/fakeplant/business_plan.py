"""The business plan export for a facility's data folder (`business_plan_lines.csv`, read by Fuzer's built-in
business plan source, source_packs/business_plan.yaml). Fictional, like the rest: written from the folder's own SAP
exports, so its lines plan the work the plant really has on. Each line is the work a program means to do on an area
over a quarter, with the hours and cost it estimates; the estimates sit a little over or under what the work then
takes, so plan against actual shows a variance both ways. A few lines plan work nothing has started on yet.

    export(folder, seed)   the lines, for any folder with sap_floc.csv, sap_work_orders.csv and
                           sap_maintenance_plans.csv: generated dates (Okan) or relative ones (`today-30d`, Kestrel
                           and Harrier), written back in the same form
    write(folder, seed)    the lines as business_plan_lines.csv in that folder

    uv run fakeplant business-plan fakeplant/bundles/kestrel/exports
"""

import csv
import random
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .common import _write_csv

FILE = "business_plan_lines.csv"
CLOSED = ("TECO", "CLSD")  # orders done: no job left on the plan for a line to take
DAY_HOURS = 24  # a job's hours on the plan where nothing else says: the day it is booked for
LABOUR_USD_H = 95  # what an hour of the crew costs, for planned work SAP holds no order cost for yet
QUARTER_DAYS = 91
HORIZON = (3, 4)  # quarters planned before and after the one the export is written in: the plan's year and a half
RELATIVE = re.compile(r"^today(?:([+-])(\d+)d)?")
PROGRAMS = {"maintenance": "Maintenance", "inspection": "Inspection"}


def _read(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _when(v: str | None):
    """A day as written: a date, or the days from today for a relative one (`today-30d` is -30); None when blank."""
    v = (v or "").strip()
    if not v:
        return None
    if m := RELATIVE.match(v):
        return (-1 if m.group(1) == "-" else 1) * int(m.group(2) or 0)
    return date.fromisoformat(v[:10])


def _quarter(day) -> tuple[int, str, str, str]:
    """(its number, label, first day, last day) of the quarter a day is in, written as the export writes days."""
    if isinstance(day, int):
        b = day // QUARTER_DAYS
        tok = lambda n: f"today{n:+d}d" if n else "today"
        return b, f"P{b:+d}", tok(b * QUARTER_DAYS), tok(b * QUARTER_DAYS + QUARTER_DAYS - 1)
    q = (day.month - 1) // 3
    first = date(day.year, 3 * q + 1, 1)
    after = date(day.year + (q == 3), 1 if q == 3 else 3 * q + 4, 1)
    return 4 * day.year + q, f"{day.year}Q{q + 1}", first.isoformat(), (after - timedelta(days=1)).isoformat()


def _days(start, end) -> int:
    if start is None or end is None:
        return 1
    span = end - start
    return max(span if isinstance(span, int) else span.days, 1)


def export(folder, seed: int = 29) -> list[dict]:
    folder = Path(folder)
    flocs = {r["floc"]: r for r in _read(folder / "sap_floc.csv")}
    roots = [f for f, r in flocs.items() if not r.get("parent")]
    if not roots:
        return []
    root = roots[0]

    def area(floc: str) -> str:
        seen = set()
        while floc in flocs and flocs[floc].get("parent") and flocs[floc]["parent"] != root and floc not in seen:
            seen.add(floc)
            floc = flocs[floc]["parent"]
        return floc if floc in flocs else root

    # the jobs Fuzer puts on the plan (ops/plan.sync_work_orders, sync_maintenance_plans): open orders, each a day or
    # its start to finish, and each maintenance plan's next due task, a day
    jobs = []  # (program, kind, floc of the line, day, hours, cost)
    last_done = None
    for o in _read(folder / "sap_work_orders.csv"):
        status = (o.get("status") or "").split()[0:1]
        day = _when(o.get("start")) or _when(o.get("basic_finish"))
        if status and status[0] in CLOSED:
            done = _when(o.get("finish")) or _when(o.get("basic_finish"))
            if done is not None and (last_done is None or done > last_done):
                last_done = done
            continue
        if day is None or not o.get("floc"):
            continue
        cost = float(o["cost"]) if (o.get("cost") or "").replace(".", "", 1).isdigit() else 0.0
        hours = DAY_HOURS * _days(_when(o.get("start")), _when(o.get("finish")))
        if rev := (o.get("revision") or "").strip():
            jobs.append((f"Turnaround {rev}", "turnaround", root, day, hours, cost))
        else:
            jobs.append((PROGRAMS["maintenance"], "maintenance", area(o["floc"]), day, hours, cost))
    for p in _read(folder / "sap_maintenance_plans.csv"):
        day = _when(p.get("next_due"))
        if day is None or not p.get("floc"):
            continue
        kind = "inspection" if (p.get("kind") or "").upper() == "INSP" else "maintenance"
        jobs.append((PROGRAMS[kind], kind, area(p["floc"]), day, DAY_HOURS, 0.0))

    if not jobs:
        return []
    relative = isinstance(jobs[0][3], int)
    now = _quarter(0 if relative else last_done or max(j[3] for j in jobs))  # the quarter the export was written in
    groups: dict[tuple, list] = defaultdict(list)
    for program, kind, where, day, hours, cost in jobs:
        q = _quarter(day)
        if -HORIZON[0] <= q[0] - now[0] <= HORIZON[1]:
            groups[(program, kind, where, q)].append((hours, cost))
    # work planned that nothing has started on: coating repairs on the areas with no maintenance this quarter
    busy = {w for (p, k, w, q) in groups if k == "maintenance" and q == now}
    areas = sorted(f for f, r in flocs.items() if r.get("parent") == root)
    idle = [a for a in areas if a not in busy][:2]

    rnd = random.Random(seed)
    out = []
    for (program, kind, where, (_, label, first, last)), work in sorted(groups.items(), key=lambda g: (g[0][3][0], g[0][:3])):
        hours = sum(h for h, _ in work)
        cost = sum(c for _, c in work)
        planned_h = max(8, round(hours * rnd.uniform(0.75, 1.3) / 4) * 4)
        planned_cost = round((cost or hours * LABOUR_USD_H) * rnd.uniform(0.8, 1.25), -2)
        title = (f"{program} scope" if kind == "turnaround"
                 else f"{program}, {flocs.get(where, {}).get('description') or where}")
        out.append(_line(root, program, kind, where, label, first, last, planned_h, planned_cost, title))
    for where in idle:
        _, label, first, last = now
        planned_h = rnd.choice((48, 72, 96))
        out.append(_line(root, PROGRAMS["maintenance"], "maintenance", where, label, first, last, planned_h,
                         round(planned_h * LABOUR_USD_H * 1.6, -2),
                         f"Coating repairs, {flocs[where].get('description') or where}", code="COAT"))
    return out


def _line(root, program, kind, where, label, first, last, hours, cost, title, code=None) -> dict:
    code = code or {"turnaround": "TAR", "inspection": "INSP"}.get(kind, "MNT")
    return {"line_id": f"BP-{where}-{code}-{label}", "plan": f"{root} business plan", "program": program,
            "title": title, "work_kind": kind, "floc": where, "period_start": first, "period_end": last,
            "estimate_hours": hours, "estimate_cost": int(cost), "currency": "USD"}


def write(folder, seed: int = 29) -> dict:
    rows = export(folder, seed)
    if rows:
        _write_csv(Path(folder) / FILE, rows)
    return {"file": str(Path(folder) / FILE), "lines": len(rows)}
