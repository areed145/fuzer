"""Regulatory and emissions reports as workflow outputs (P7.6, #954): a report drafted from its records, each figure
drilling to the readings and cases behind it, and reviewed as work.

A report is a template (`TEMPLATES`) filled for one period. The first is `flare_emissions_monthly`, the month's
flare and emissions report:

    Metered gas      each meter's volume over the month, one row a gas day, from the measurement system's
                     daily volumes; a day the system estimated (status E) is estimated, a day with no volume
                     is missing
    Flaring          each flare flow point's volume over the month, one row a day, the day's mean flow read from
                     the historian; a day with under `FULL_DAY` of its readings is estimated, one with none missing
    GHG inputs       the flared volume and its energy, at the heating value of the gas metered that month: an
                     input to the inventory, not the inventory, so it is always estimated
    Flare events     the flare episodes in the month (analysis/flare.py), each with its cause or "cause unknown"
                     and the `flare_event` case that holds its records

Every figure carries its basis: `measured`, `estimated` or `missing`, the worst of the rows it is made of. A figure
resting on estimated or missing data is marked in the draft (`marked`), so the reviewer sees what to check before it
goes out. Every row carries its ref: `record:<measurement record>`, `point:<id>@<day>` or `case:<work item>`.

`approve` is the `approve_report` fixed gate: a named person approves the latest version, with a note when figures
are marked, and its review case closes. `export` writes a version as CSV (a draft says DRAFT); `submit` sends the
approved version through the write-back queue to a system, or records the exported file the person files, and keeps
the version as submitted with the exact file and its hash.

`assemble` drafts a period's report. A new version is kept only when its figures changed (a late or corrected
record), and the review case for the template and period (`regulatory_report`, for the HSE specialist) is raised or
refreshed with it. A month that is not over yet is drafted through the data's last full day and says so.
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from psycopg.types.json import Jsonb

from ..core.exceptions import TYPES, exception_type
from ..core.findings import DETERMINISTIC, Finding, fact
from ..core.jsonsafe import plain

BASES = ("measured", "estimated", "missing")  # best to worst
ESTIMATED_STATUS = ("e", "est", "estimated")  # a source's status for a volume it estimated
FULL_DAY = 0.9  # the share of a day's historian readings that makes its flare volume measured
STEP_MINUTES = 10


@dataclass(frozen=True)
class Template:
    name: str
    title: str
    build: Callable  # (conn, pack, since: date, through: date, src) -> [section]
    period: str = "month"
    role: str = "role:hse_specialist"
    description: str = ""

    def describe(self) -> dict:
        return {"name": self.name, "title": self.title, "period": self.period, "role": self.role,
                "description": self.description}


TEMPLATES: dict[str, Template] = {}


def template(name: str, title: str, *, description: str, period: str = "month", role: str = "role:hse_specialist"):
    """Declare a report template; the decorated function builds its sections for [since, through)."""
    def wrap(fn: Callable) -> Callable:
        if name in TEMPLATES:
            raise ValueError(f"report template {name} is declared twice")
        TEMPLATES[name] = Template(name, title, fn, period, role, description)
        return fn
    return wrap


def _type():
    return TYPES.get("regulatory_report") or exception_type(
        "regulatory_report", "Regulatory report to review", role="role:hse_specialist", audience=("ioc",),
        joins=False,
        description="A regulatory or emissions report drafted from its records for one period. Check each figure, "
                    "above all the ones marked as resting on estimated or missing data, against the records it "
                    "drills to before the report is approved.")


REVIEW = _type().name


class ReportError(ValueError):
    """A template nobody declared, or a period that isn't one."""


# ---- periods ----------------------------------------------------------------------------------------------------

def period_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def bounds(conn, period: str) -> dict:
    """The month `period` (YYYY-MM): its first day, the day after its last, and the day after the last day it is
    drafted through (the data's last full day, when the month isn't over)."""
    from ..graph import operations

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period or ""):
        raise ReportError(f"a period is a month, YYYY-MM, not {period!r}")
    since = date.fromisoformat(f"{period}-01")
    end = (since + timedelta(days=32)).replace(day=1)
    today = date.fromisoformat(operations.as_of(conn))
    through = min(end, today)
    if through <= since:
        raise ReportError(f"{period} hasn't started yet; the data runs to {today.isoformat()}")
    return {"since": since, "end": end, "through": through, "partial": through < end,
            "label": since.strftime("%B %Y")}


def _days(since: date, through: date) -> list[date]:
    return [since + timedelta(days=i) for i in range((through - since).days)]


# ---- figures ----------------------------------------------------------------------------------------------------

def worst(bases) -> str:
    seen = [b for b in bases if b in BASES]
    return max(seen, key=BASES.index) if seen else "missing"


def figure(key: str, label: str, value, unit: str | None, rows: list[dict], note: str | None = None,
           basis: str | None = None) -> dict:
    basis = basis or worst(r["basis"] for r in rows)
    by = {b: [r["label"] for r in rows if r["basis"] == b] for b in BASES[1:]}
    flags = [f"{len(v)} {b}: {', '.join(v[:6])}{'…' if len(v) > 6 else ''}" for b, v in by.items() if v]
    return {"key": key, "label": label, "value": value, "unit": unit, "basis": basis, "note": note,
            "flags": flags, "rows": rows}


def _num(v: float | None, n: int = 1) -> float | None:
    return None if v is None else round(v, n)


def _meters(conn, since: date, through: date) -> tuple[list[dict], dict]:
    """One figure a meter: its daily volumes over the period, estimated and missing days marked."""
    rows = conn.execute(
        """
        select m.fuzer_id, coalesce(l.label, m.fuzer_id) as label, m.at::date as day, m.value, m.unit, m.record,
               lower(coalesce(sr.payload ->> 'status', '')) as status,
               (select e.value from measurement e where e.fuzer_id = m.fuzer_id and e.quantity = 'energy'
                  and e.at = m.at limit 1) as energy
        from measurement m
        left join source_record sr on m.record = sr.source || ':' || sr.record_type || ':' || sr.source_key
        left join lateral (select label from entity_label where fuzer_id = m.fuzer_id order by label limit 1) l
          on true
        where m.quantity = 'volume' and m.at >= %s and m.at < %s
        order by 2, 3
        """, (since, through)).fetchall()
    known = conn.execute(
        """
        select distinct m.fuzer_id, coalesce(l.label, m.fuzer_id) as label, m.unit from measurement m
        left join lateral (select label from entity_label where fuzer_id = m.fuzer_id order by label limit 1) l
          on true
        where m.quantity = 'volume' and m.at < %s and m.at >= %s
        """, (through, since - timedelta(days=62))).fetchall()
    meters = {r["fuzer_id"]: {"label": r["label"], "unit": r["unit"]} for r in known}
    by: dict[str, dict[date, dict]] = {m: {} for m in meters}
    for r in rows:
        by.setdefault(r["fuzer_id"], {})[r["day"]] = r
        meters.setdefault(r["fuzer_id"], {"label": r["label"], "unit": r["unit"]})
    out, energy, volume = [], 0.0, 0.0
    for fid, m in sorted(meters.items(), key=lambda x: x[1]["label"]):
        lines = []
        for d in _days(since, through):
            r = by[fid].get(d)
            if r is None:
                lines.append({"label": d.isoformat(), "value": None, "basis": "missing", "ref": None})
                continue
            lines.append({"label": d.isoformat(), "value": r["value"],
                          "basis": "estimated" if r["status"] in ESTIMATED_STATUS else "measured",
                          "ref": f"record:{r['record']}"})
            if r["energy"] is not None:
                energy, volume = energy + r["energy"], volume + r["value"]
        total = sum(x["value"] for x in lines if x["value"] is not None)
        out.append({**figure(f"meter:{fid}", f"Metered gas, {m['label']}", _num(total), m["unit"], lines),
                    "subject": fid})
    return out, {"energy_gj": energy, "volume": volume}


def _per_day(uom: str | None) -> tuple[str | None, float | None]:
    """A flow unit's volume unit and the factor from its mean over a day to that day's volume."""
    u = (uom or "").strip()
    if m := re.fullmatch(r"(.+?)/d(ay)?", u, re.IGNORECASE):
        return m.group(1), 1.0
    if m := re.fullmatch(r"(.+?)/h(r|our)?", u, re.IGNORECASE):
        return m.group(1), 24.0
    return None, None


def _unit(u: str | None) -> str | None:
    return {"ksm3": "kSm3", "sm3": "Sm3", "msm3": "MSm3"}.get((u or "").lower(), u)


def _flared(conn, since: date, through: date, src) -> list[dict]:
    """One figure a flare flow point: the day's mean flow read from the historian, as that day's volume."""
    from ..analysis import calcs, flare
    from ..sources import timeseries

    out = []
    for r in flare._points(conn):
        pt = timeseries._point(conn, r["point_id"])
        if not pt:
            continue
        unit, k = _per_day(pt.get("uom"))
        if k is None:
            out.append(figure(f"flare:{r['point_id']}", f"Flared gas, {r['label']}", None, None, [],
                              note=f"{pt['point']} is in {pt.get('uom') or 'no unit'}, not a flow a day or an hour",
                              basis="missing"))
            continue
        vals = calcs.read({r["point_id"]: pt}, datetime.combine(since, datetime.min.time()),
                          datetime.combine(through, datetime.min.time()), STEP_MINUTES, src)[r["point_id"]]
        days: dict[str, list[float]] = {}
        for t, v in vals.items():
            if v is not None:
                days.setdefault(t[:10], []).append(v)
        full = 1440 // STEP_MINUTES
        lines = []
        for d in _days(since, through):
            got = days.get(d.isoformat(), [])
            ref = f"point:{r['point_id']}@{d.isoformat()}"
            if not got:
                lines.append({"label": d.isoformat(), "value": None, "basis": "missing", "ref": ref})
                continue
            lines.append({"label": d.isoformat(), "value": _num(sum(got) / len(got) * k, 2), "ref": ref,
                          "basis": "measured" if len(got) >= FULL_DAY * full else "estimated",
                          "readings": len(got)})
        total = sum(x["value"] for x in lines if x["value"] is not None)
        out.append({**figure(f"flare:{r['point_id']}", f"Flared gas, {r['label']}", _num(total), _unit(unit), lines,
                             note=f"The day's mean of {pt['point']}, read every {STEP_MINUTES} minutes"),
                    "subject": r["asset"]})
    return out


def _events(conn, since: date, through: date, src) -> list[dict]:
    from ..analysis import flare

    found = flare.events(conn, datetime.combine(through, datetime.min.time()).isoformat(timespec="minutes"),
                         (through - since).days, src)
    keys = [flare.key_of(e) for e in found]
    cases = {r["key"]: r["item_id"] for r in conn.execute(
        "select distinct on (key) key, item_id from exception_record where type = %s and key = any(%s) "
        "order by key, id desc", (flare.EVENT_TYPE, keys))}
    lines, unknown = [], []
    for e, key in sorted(zip(found, keys), key=lambda x: x[0]["start"]):
        unit = f" {_unit(e['volume_unit'])}" if e["volume_unit"] else ""
        cause = "; ".join(c["text"] for c in e["causes"][:3]) if e["attributed"] else "cause unknown"
        item = cases.get(key)
        line = {"label": f"{e['start'].replace('T', ' ')} to {e['end'][11:]} at {e['label']}",
                "value": e["volume"], "unit": _unit(e["volume_unit"]), "basis": "measured",
                "text": f"About {e['volume']:g}{unit} over its baseline, an estimate; {cause}",
                "ref": f"case:{item}" if item else None, "subject": e["subject"]}
        lines.append(line)
        if not e["attributed"]:
            unknown.append(line)
    return [figure("flare_events", "Flare episodes", len(lines), None, lines,
                   note="Each episode's volume over its baseline is an estimate, and is not added to the flared gas"),
            figure("flare_events_unknown", "Flare episodes with no known cause", len(unknown), None, unknown)]


@template("flare_emissions_monthly", "Monthly flare and emissions report",
          description="The month's metered gas, flared gas from the flare flow meters, the flared volume and energy "
                      "as inputs to the greenhouse gas inventory, and each flare episode with its cause.")
def flare_emissions(conn, pack, since: date, through: date, src=None) -> list[dict]:
    meters, hhv = _meters(conn, since, through)
    flared = _flared(conn, since, through, src)
    total = [f for f in flared if f["value"] is not None]
    ghg = []
    if total:
        vol = sum(f["value"] for f in total)
        basis = worst(f["basis"] for f in total)
        ghg.append(figure("ghg:flared_volume", "Flared gas volume", _num(vol), total[0]["unit"],
                          [{"label": f["label"], "value": f["value"], "basis": f["basis"], "ref": None,
                            "figure": f["key"]} for f in total], basis=basis))
        if hhv["volume"] and total[0]["unit"] == "kSm3":
            mj = hhv["energy_gj"] / hhv["volume"]  # GJ per kSm3 is MJ per Sm3
            ghg.append(figure("ghg:flared_energy", "Flared gas energy", _num(vol * mj, 0), "GJ",
                              [{"label": "Heating value of the gas metered this month", "value": _num(mj, 2),
                                "unit": "MJ/Sm3", "basis": "estimated", "ref": None}],
                              note="At the heating value of the gas metered this month, not of the flare gas",
                              basis="estimated"))
    return [{"title": "Metered gas", "figures": meters},
            {"title": "Flaring", "figures": flared},
            {"title": "GHG inputs", "figures": ghg,
             "note": "Inputs to the greenhouse gas inventory, not the inventory itself."},
            {"title": "Flare events", "figures": _events(conn, since, through, src)}]


# ---- versions and the review ------------------------------------------------------------------------------------

def _template(name: str) -> Template:
    if name not in TEMPLATES:
        raise ReportError(f"no report template {name}; there is {', '.join(sorted(TEMPLATES)) or 'none'}")
    return TEMPLATES[name]


def marked(sections: list[dict]) -> list[dict]:
    """The figures resting on estimated or missing data."""
    return [{"key": f["key"], "label": f["label"], "basis": f["basis"], "flags": f["flags"]}
            for s in sections for f in s["figures"] if f["basis"] != "measured"]


def fingerprint(sections: list[dict]) -> str:
    return hashlib.sha256(json.dumps(plain(sections), sort_keys=True, default=str).encode()).hexdigest()


def latest(conn, name: str, period: str) -> dict | None:
    return conn.execute("select * from regulatory_report where template = %s and period = %s "
                        "order by version desc limit 1", (name, period)).fetchone()


def _raise(conn, t: Template, period: str, b: dict, version: int, report_id: int, marks: list[dict], by: str):
    from ..core.exceptions import raise_exception

    covers = f"{b['since'].isoformat()} to {(b['through'] - timedelta(days=1)).isoformat()}"
    title = f"{t.title}, {b['label']}: version {version} to review" + (
        f", {len(marks)} figure{'s' if len(marks) != 1 else ''} on estimated or missing data" if marks else "")
    found = Finding(
        "reports.draft", DETERMINISTIC, method="Filled from registered records, each figure with its rows",
        start=b["since"].isoformat(), end=b["through"].isoformat(),
        data={"template": t.name, "period": period, "version": version, "report_id": report_id},
        items=(fact("Report", t.title), fact("Period", b["label"]), fact("Version", version), fact("Covers", covers),
               fact("Month not over", "drafted through the data's last full day" if b["partial"] else None),
               fact("Figures on estimated or missing data",
                    "; ".join(f"{m['label']} ({m['basis']})" for m in marks) or "none")))
    return raise_exception(conn, REVIEW, f"{t.name}:{period}", title, by, source="reports", refs=[],
                           priority=0.5, finding=found, role=t.role)


def assemble(conn, pack, name: str, period: str, by: str = "agent:reporter", src=None) -> dict:
    """Draft the report `name` for `period` from its records; keep a new version when its figures changed, and raise
    or refresh its review case."""
    t = _template(name)
    b = bounds(conn, period)
    sections = plain(t.build(conn, pack, b["since"], b["through"], src))
    fp = fingerprint(sections)
    last = latest(conn, name, period)
    if last and last["fingerprint"] == fp:
        return {"template": name, "period": period, "version": last["version"], "changed": False,
                "report_id": last["id"], "item_id": last["item_id"]}
    version = (last["version"] + 1) if last else 1
    marks = marked(sections)
    coverage = {"since": b["since"].isoformat(), "through": (b["through"] - timedelta(days=1)).isoformat(),
                "partial": b["partial"], "label": b["label"]}
    rid = conn.execute(
        "insert into regulatory_report (template, period, version, sections, marked, coverage, fingerprint, "
        "drafted_by) values (%s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (name, period, version, Jsonb(sections), Jsonb(marks), Jsonb(coverage), fp, by)).fetchone()["id"]
    x = _raise(conn, t, period, b, version, rid, marks, by)
    conn.execute("update regulatory_report set item_id = %s where id = %s", (x["item_id"], rid))
    conn.commit()
    return {"template": name, "period": period, "version": version, "changed": True, "report_id": rid,
            "item_id": x["item_id"], "marked": [m["key"] for m in marks]}


def _review(conn, name: str, period: str) -> dict | None:
    r = conn.execute(
        "select e.id, e.state, e.item_id, w.state as case_state, w.assignee from exception_record e "
        "left join work_item w on w.id = e.item_id where e.type = %s and e.key = %s order by e.id desc limit 1",
        (REVIEW, f"{name}:{period}")).fetchone()
    return dict(r) if r else None


def get(conn, name: str, period: str, version: int | None = None) -> dict | None:
    """One version of a report (the latest by default), with its sections, marked figures and its review."""
    t = _template(name)
    r = conn.execute("select * from regulatory_report where template = %s and period = %s and "
                     "(%s::int is null or version = %s) order by version desc limit 1",
                     (name, period, version, version)).fetchone()
    if not r:
        return None
    versions = [v["version"] for v in conn.execute(
        "select version from regulatory_report where template = %s and period = %s order by version", (name, period))]
    sent = {k: v for k, v in (r["submitted"] or {}).items() if k != "content"} or None
    return plain({k: r[k] for k in ("id", "template", "period", "version", "status", "sections", "marked",
                                    "coverage", "drafted_by", "item_id", "created_at", "approved_by", "approved_at",
                                    "approval_note")} | {"submitted": sent}
                 | {"title": t.title, "versions": versions, "review": _review(conn, name, period)})


def listing(conn, limit: int = 50) -> dict:
    """The templates, and each report drafted, newest period first, with its latest version."""
    rows = conn.execute(
        "select distinct on (template, period) template, period, version, status, marked, coverage, item_id, "
        "created_at from regulatory_report order by template, period desc, version desc").fetchall()
    rows = sorted(rows, key=lambda r: (r["period"], r["template"]), reverse=True)[:limit]
    return plain({"templates": [t.describe() for t in TEMPLATES.values()],
                  "reports": [{**{k: r[k] for k in ("template", "period", "version", "status", "coverage",
                                                    "item_id", "created_at")},
                               "title": TEMPLATES[r["template"]].title if r["template"] in TEMPLATES else r["template"],
                               "marked": len(r["marked"] or [])} for r in rows]})


# ---- approval, export and submission (P7.6b) --------------------------------------------------------------------

def _key(name: str, period: str) -> str:
    return f"{name}:{period}"


def approve(conn, name: str, period: str, by: str, version: int | None = None, note: str | None = None) -> dict:
    """A named person approves the latest version (the `approve_report` fixed gate: an agent is refused). A version
    with figures on estimated or missing data needs a note saying why it can go as it is. The review case closes;
    the approved version stays as it was, and a later change to its records drafts a new version for review."""
    from ..core import gates
    from ..core.exceptions import clear_exception

    _template(name)
    gates.require_person(conn, "approve_report", by, ref=f"report:{_key(name, period)}",
                         what=f"the {name} report for {period}")
    r = latest(conn, name, period)
    if r is None:
        raise ReportError(f"no {name} report for {period}; draft it first")
    if version is not None and version != r["version"]:
        raise ReportError(f"version {version} is not the latest; version {r['version']} is the one to approve")
    if r["status"] != "draft":
        raise ReportError(f"version {r['version']} is already {r['status']}")
    note = (note or "").strip() or None
    if r["marked"] and (not note or len(note) < 3):
        raise ReportError(f"{len(r['marked'])} figure{'s' if len(r['marked']) != 1 else ''} rest on estimated or "
                          "missing data; say why the report can go as it is")
    row = conn.execute("update regulatory_report set status = 'approved', approved_by = %s, approved_at = now(), "
                       "approval_note = %s where id = %s returning *", (by, note, r["id"])).fetchone()
    clear_exception(conn, REVIEW, _key(name, period), by,
                    reason=f"Approved version {r['version']}" + (f": {note}" if note else ""))
    conn.commit()
    return plain({k: row[k] for k in ("id", "template", "period", "version", "status", "approved_by", "approved_at",
                                      "approval_note", "item_id")})


def export(conn, name: str, period: str, version: int | None = None) -> dict:
    """One version as a CSV file: every figure and every row with its basis and record. A draft says DRAFT in its
    name and first line, so it can't pass for the approved report."""
    import csv
    import io

    r = get(conn, name, period, version)
    if r is None:
        raise ReportError(f"no {name} report for {period}" + (f" version {version}" if version else ""))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([f"{'DRAFT ' if r['status'] == 'draft' else ''}{r['title']}", r["coverage"]["label"],
                f"version {r['version']}", r["status"]])
    w.writerow(["covers", r["coverage"]["since"], r["coverage"]["through"],
                "month not over" if r["coverage"]["partial"] else "whole month"])
    if r.get("approved_by"):
        w.writerow(["approved", r["approved_by"], r["approved_at"], r.get("approval_note") or ""])
    w.writerow(["section", "figure", "row", "value", "unit", "basis", "record"])
    for s in r["sections"]:
        for f in s["figures"]:
            w.writerow([s["title"], f["label"], "", f["value"], f["unit"] or "", f["basis"], ""])
            for x in f["rows"]:
                w.writerow([s["title"], f["label"], x["label"], x["value"], x.get("unit") or f["unit"] or "",
                            x["basis"], x["ref"] or ""])
    content = buf.getvalue()
    draft = "-DRAFT" if r["status"] == "draft" else ""
    return {"filename": f"{name}-{period}-v{r['version']}{draft}.csv", "content": content,
            "sha256": hashlib.sha256(content.encode()).hexdigest(), "version": r["version"], "status": r["status"]}


def submit(conn, name: str, period: str, by: str, system: str | None = None) -> dict:
    """Send the approved version: through the write-back queue to `system` (as a `regulatory_report` write-back,
    approved and sent by that queue's rules), else as the exported file the person files themselves. Either way the
    version is kept as submitted, with the exact file and its hash."""
    from ..core import gates

    _template(name)
    gates.require_person(conn, "approve_report", by, ref=f"report:{_key(name, period)}",
                         what=f"submitting the {name} report for {period}")
    r = latest(conn, name, period)
    if r is None or r["status"] == "draft":
        raise ReportError(f"the {name} report for {period} has no approved version to submit; approve it first")
    if r["status"] == "submitted":
        raise ReportError(f"version {r['version']} was already submitted")
    out = export(conn, name, period, r["version"])
    sent = {"by": by, "file": out["filename"], "sha256": out["sha256"], "content": out["content"]}
    if system:
        from . import writeback

        wb = writeback.propose(conn, system, "regulatory_report",
                               {"template": name, "period": period, "version": r["version"], "file": out["filename"],
                                "sha256": out["sha256"], "content": out["content"]}, by,
                               record=_key(name, period), key=f"report:{_key(name, period)}:v{r['version']}",
                               title=f"{TEMPLATES[name].title}, {r['coverage']['label']}, to send to {system}")
        sent |= {"via": "writeback", "system": system, "writeback_id": wb["id"]}
    else:
        sent |= {"via": "export"}
    row = conn.execute("update regulatory_report set status = 'submitted', submitted = %s || jsonb_build_object("
                       "'at', now()) where id = %s returning *", (Jsonb(sent), r["id"])).fetchone()
    conn.commit()
    return plain({k: row[k] for k in ("id", "template", "period", "version", "status")}
                 | {"submitted": {k: v for k, v in row["submitted"].items() if k != "content"}})


# ---- the obligations calendar (P7.6c) ---------------------------------------------------------------------------

def _due_type():
    return TYPES.get("report_due") or exception_type(
        "report_due", "Regulatory report coming due", role="role:hse_specialist", audience=("ioc",), joins=False,
        description="A regulatory report whose due date is coming with no draft started, or that is past its due date "
                    "and not submitted. Draft it from its records, review and approve it, and submit it; the work "
                    "clears once a draft is in progress before the due date, or once it is submitted.")


DUE = _due_type().name


def set_obligation(conn, name: str, template: str, by: str, *, regulator: str | None = None, due_days: int = 15,
                   lead_days: int = 10, draft_ahead: bool = True, active: bool = True, note: str | None = None,
                   starts: date | None = None) -> dict:
    """Declare (or change) an obligation: the report `template` for each period, due `due_days` days after the
    period ends (15: the 15th of the next month) and started `lead_days` before that. With `draft_ahead` the
    calendar drafts it from its records when its window opens. It counts periods due on or after `starts` (by
    default the data's today when it is first declared), so declaring one doesn't make every past period overdue."""
    from ..graph import operations

    _template(template)
    starts = starts or date.fromisoformat(operations.as_of(conn))
    if not re.fullmatch(r"[a-z0-9_:-]{2,64}", name or ""):
        raise ReportError("an obligation's name is 2 to 64 lowercase letters, digits, _, : or -")
    if not 1 <= due_days <= 120 or not 0 <= lead_days <= 90:
        raise ReportError("due_days is 1 to 120 and lead_days 0 to 90")
    row = conn.execute(
        """
        insert into report_obligation (name, template, regulator, due_days, lead_days, draft_ahead, active, note,
                                       updated_by, starts)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (name) do update set template = excluded.template, regulator = excluded.regulator,
          due_days = excluded.due_days, lead_days = excluded.lead_days, draft_ahead = excluded.draft_ahead,
          active = excluded.active, note = excluded.note, updated_by = excluded.updated_by, updated_at = now()
        returning *
        """, (name, template, regulator, due_days, lead_days, draft_ahead, active, note, by, starts)).fetchone()
    conn.commit()
    return plain(dict(row))


def _shift(period: str, months: int) -> str:
    y, m = map(int, period.split("-"))
    m += months
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def due_date(period: str, due_days: int) -> date:
    """The day a period's report is due: `due_days` days after the period's last day."""
    end = date.fromisoformat(f"{_shift(period, 1)}-01")
    return end + timedelta(days=due_days - 1)


def calendar(conn, today: date | None = None) -> list[dict]:
    """Each active obligation's periods whose window has opened and which aren't submitted: the due date, when it
    opened, and where its report stands (none, draft, approved), the earliest due first."""
    from ..graph import operations

    today = today or date.fromisoformat(operations.as_of(conn))
    out = []
    for o in conn.execute("select * from report_obligation where active order by name"):
        for back in (3, 2, 1, 0):
            period = _shift(period_of(today), -back)
            due = due_date(period, o["due_days"])
            opens = due - timedelta(days=o["lead_days"])
            if today < opens or due < o["starts"]:
                continue
            r = latest(conn, o["template"], period)
            if r and r["status"] == "submitted":
                continue
            out.append({"obligation": o["name"], "template": o["template"], "regulator": o["regulator"],
                        "period": period, "due": due.isoformat(), "opens": opens.isoformat(),
                        "overdue": today > due, "days_left": (due - today).days,
                        "report": {"version": r["version"], "status": r["status"]} if r else None,
                        "draft_ahead": o["draft_ahead"]})
    return sorted(out, key=lambda x: (x["due"], x["obligation"]))


def run_calendar(conn, pack, today: date | None = None, by: str = "agent:reporter", src=None) -> dict:
    """The obligations calendar, as its daily job runs it: each obligation whose window opened is drafted from its
    records when it may be and has no draft; one with no draft in progress, or past its due date and not submitted,
    raises `report_due` for the template's role. Work for an obligation now in progress before its due date, or
    submitted, clears."""
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    drafted, raised, cleared, errors = [], [], [], []
    due_now = calendar(conn, today)
    keep = set()
    for c in due_now:
        key = f"{c['obligation']}:{c['period']}"
        why = None
        if c["report"] is None and c["draft_ahead"]:
            try:
                out = assemble(conn, pack, c["template"], c["period"], by=by, src=src)
                c["report"] = {"version": out["version"], "status": "draft"}
                drafted.append(key)
            except ReportError as e:
                conn.rollback()
                why = f"it could not be drafted: {e}"
        t = TEMPLATES[c["template"]]
        if c["report"] is None or c["overdue"]:
            stand = ("not started" if c["report"] is None else f"version {c['report']['version']} {c['report']['status']}"
                     ", not submitted")
            when = f"was due {c['due']}" if c["overdue"] else f"is due {c['due']}, in {c['days_left']} days"
            found = Finding(
                "reports.calendar", DETERMINISTIC, method="The obligations calendar",
                data={k: c[k] for k in ("obligation", "template", "period", "due", "overdue")},
                items=(fact("Obligation", c["obligation"]), fact("Regulator", c["regulator"]),
                       fact("Report", t.title), fact("Period", bounds_label(c["period"])), fact("Due", c["due"]),
                       fact("Where it stands", stand), fact("Why it wasn't drafted", why)))
            try:
                x = raise_exception(conn, DUE, key, f"{t.title}, {bounds_label(c['period'])}, {when}: {stand}", by,
                                    source="reports", refs=[], priority=0.8 if c["overdue"] else 0.6,
                                    finding=found, role=t.role)
                raised.append({"key": key, "item_id": x["item_id"], "new": x["raised"]})
                keep.add(key)
            except ExceptionError as e:
                errors.append(f"{key}: {e}")
    for r in conn.execute("select key from exception_record where type = %s and state = 'open'", (DUE,)).fetchall():
        if r["key"] not in keep:
            reason = "a draft is in progress before its due date, or it was submitted"
            if clear_exception(conn, DUE, r["key"], by, reason=reason):
                cleared.append(r["key"])
    conn.commit()
    return {"due": due_now, "drafted": drafted, "raised": raised, "cleared": cleared, "errors": errors}


def bounds_label(period: str) -> str:
    return date.fromisoformat(f"{period}-01").strftime("%B %Y")


def obligations(conn, today: date | None = None) -> dict:
    """Every obligation, and the calendar of what is open now."""
    rows = [plain(dict(r)) for r in conn.execute("select * from report_obligation order by name")]
    return {"obligations": rows, "calendar": calendar(conn, today)}
