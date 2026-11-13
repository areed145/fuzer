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
    return plain({k: r[k] for k in ("id", "template", "period", "version", "status", "sections", "marked",
                                    "coverage", "drafted_by", "item_id", "created_at")}
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
