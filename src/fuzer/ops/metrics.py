"""The registered metrics (P7.1, core/metrics.py): each declaration reads the module that owns the computation, so a
page, the daily report and an agent asking for the same metric get the same number.

    production.efficiency          oil delivered against what the facility could deliver (analysis.production_efficiency)
    maintenance.backlog_ageing     corrective orders open longer than 90 days (analysis.backlog)
    maintenance.urgent_overdue     priority 1 and 2 orders past their finish date (analysis.backlog)
    work.arrivals / closed / backlog / sla_misses   this week's work, by queue or type (ops.performance, P7.2)
    context.completeness           the share of what an asset's class usually has that it has (analysis.completeness)
    sources.straight_through       a source's records the rebuild decided on its own (graph.contextualized)
    sources.completeness / validity / timeliness / agreement   the source quality scorecard (sources.scorecard)
"""

from datetime import date, datetime, timedelta
from statistics import mean, median

from ..core.metrics import HISTORY_WEEKS as HISTORY
from ..core.metrics import metric
from ..core.rowsets import row_set
from ..core.windows import parse_time

IDS = 200  # case ids kept behind a number


def _rows(items: list[tuple[str, str, float | None, list]]) -> list[dict]:
    return [{"key": k, "label": label, "value": v, "refs": refs} for k, label, v, refs in items]


# --- production ------------------------------------------------------------------------------------------------------

@metric("production.efficiency", "Production efficiency", group="Production", unit="%", grains=("facility",),
        period="the window (the 14 days to the last production day by default)", target=95, scoped=True,
        windowed=True, cutoff="production_day", default_window="14d",
        definition="Oil allocated to the facility against the most it could have delivered on the same days, the "
                   "binding system capacity each day.",
        formula="sum(allocated oil) / sum(dynamic maximum) x 100, over days with both",
        standard="ISO 20815 production efficiency",
        records="production days (daily reports) and the facility's system capacity measurements")
def production_efficiency(conn, pack, q) -> dict:
    rows, alloc, top = [], 0.0, 0.0
    for f in _reporting_facilities(conn, q):
        days = _pe_days(conn, f["fuzer_id"], q)
        paired = [days[d] for d in _dates(q.span) if days.get(d)]
        a, m = sum(x[0] for x in paired), sum(x[1] for x in paired)
        rows.append((f["fuzer_id"], f["label"], round(100 * a / m, 1) if m else None, [f["fuzer_id"]]))
        alloc, top = alloc + a, top + m
    if not rows:
        return {"note": "no facility with daily production reports " + ("in this scope" if q.scope else "yet")}
    return {"value": round(100 * alloc / top, 1) if top else None, "rows": _rows(rows) if q.grain else [],
            "refs": [r[0] for r in rows]}


PE_BLOCK = 14  # days read at a time: the analysis reads a long window far slower than its blocks


def _dates(span) -> list[date]:
    return [span.start.date() + timedelta(days=k) for k in range(span.days)]


def _pe_days(conn, fid: str, q) -> dict[date, tuple[float, float] | None]:
    """Each day's allocated oil and dynamic maximum for the facility (`analysis.production_efficiency`), where it has
    both, read in blocks of PE_BLOCK days on fixed boundaries and kept for the rest of the call. The period's
    efficiency is the ratio of the paired days' sums, as the analysis gives it."""
    from ..analysis import production_efficiency as pe

    days = q.cache.setdefault(("pe", fid), {})
    for d in _dates(q.span):
        if d in days:
            continue
        end = date.fromordinal(d.toordinal() + (-d.toordinal()) % PE_BLOCK)
        try:
            points = pe.production_efficiency(conn, fid, end, PE_BLOCK)["points"]
        except ValueError:
            points = []
        for k in range(PE_BLOCK):
            days.setdefault(end - timedelta(days=k), None)
        for p in points:
            if p["gap_bbld"] is not None:
                days[date.fromisoformat(p["date"])] = (p["allocated_bbld"], p["dynamic_max_bbld"])
    return days


def _reporting_facilities(conn, q) -> list[dict]:
    """The facilities the daily reports report on, in scope."""
    if "facilities" not in q.cache:
        q.cache["facilities"] = [dict(r) for r in conn.execute(
            "select distinct f.fuzer_id, coalesce(l.label, f.fuzer_id) as label from edge r join entity f on "
            "f.fuzer_id = r.dst and f.kind = 'facility' and f.merged_into is null left join entity_label l on "
            "l.fuzer_id = f.fuzer_id where r.rel = 'reports_on' order by 2")]
    return [f for f in q.cache["facilities"] if q.within([f["fuzer_id"]])]


DOWNTIME = {"kind": "downtime", "at": "start", "end": "end", "links": ["affects", "caused_by"], "value": "duration_h"}


@metric("production.uptime", "Uptime", group="Production", unit="%", grains=("facility",), target=95, scoped=True,
        windowed=True, cutoff="production_day", default_window="30d", period="the window (30 days by default)",
        definition="The share of the window's hours the facility was not stopped: no downtime against the facility "
                   "itself was open. A well or a unit stopping alone is deferment, not facility downtime. For the "
                   "whole register, the mean over the facilities the daily reports cover.",
        formula="100 - hours covered by downtime against the facility / hours in the window x 100",
        records="downtime records (start, end) against the facility, and the facilities the daily reports cover")
def uptime(conn, pack, q) -> dict:
    from . import measures

    facilities = _reporting_facilities(conn, q)
    if q.scope and q.scope.kind == "facility" and not any(f["fuzer_id"] == q.scope.id for f in facilities):
        facilities.append({"fuzer_id": q.scope.id, "label": q.scope.label})
    if not facilities:
        return {"note": "no facility in this scope" if q.scope else "no facility with daily production reports yet"}
    stops = measures.records(conn, DOWNTIME, q)
    hours = (q.span.end - q.span.start).total_seconds() / 3600
    rows, refs = [], []
    for f in facilities:
        spans = sorted((max(r["at"], q.span.start), min(r["end"] or q.span.end, q.span.end), r["id"]) for r in stops
                       if f["fuzer_id"] in r["subjects"][1:] and r["at"] and r["at"] < q.span.end
                       and (r["end"] is None or r["end"] > q.span.start))
        down, last = 0.0, q.span.start
        for a, b, rid in spans:
            a = max(a, last)
            if b > a:
                down += (b - a).total_seconds() / 3600
                last = b
            refs.append(rid)
        rows.append((f["fuzer_id"], f["label"], round(100 * (1 - down / hours), 1) if hours else None, [f["fuzer_id"]]))
    vals = [r[2] for r in rows if r[2] is not None]
    return {"value": round(mean(vals), 1) if vals else None, "rows": _rows(rows) if q.grain else [],
            "refs": refs[:IDS]}


REPEAT_DAYS = 30


@metric("downtime.repeat_stops", "Repeat stops", group="Downtime", unit="events", better="lower", scoped=True,
        windowed=True, cutoff="production_day", default_window="30d", period="the window (30 days by default)",
        definition=f"Stops that started in the window on a well or unit that had already stopped in the "
                   f"{REPEAT_DAYS} days before: the same thing failing again, not just a busy month. Facility-wide "
                   "stops are left out.",
        formula=f"count(downtime starting in the window whose well or unit had another stop starting in the "
                f"{REPEAT_DAYS} days before it)",
        records="downtime records and the wells and units each affects or was caused by")
def repeat_stops(conn, pack, q) -> dict:
    from . import measures

    stops = measures.records(conn, DOWNTIME, q)
    kinds = q.cache.setdefault("kinds", {})
    want = sorted({s for r in stops for s in r["subjects"][1:]} - set(kinds))
    if want:
        kinds.update({r["fuzer_id"]: r["kind"] for r in conn.execute(
            "select fuzer_id, kind from entity where fuzer_id = any(%s)", (want,))})
    units = {r["id"]: [s for s in r["subjects"][1:] if kinds.get(s) not in ("facility", "area")] for r in stops}
    hit = []
    for r in stops:
        if not (q.span.holds(r["at"]) and q.within(r["subjects"])):
            continue
        if any(o["id"] != r["id"] and o["at"] and r["at"] - timedelta(days=REPEAT_DAYS) <= o["at"] < r["at"]
               and set(units[o["id"]]) & set(units[r["id"]]) for o in stops):
            hit.append(r["id"])
    if not any(q.within(r["subjects"]) for r in stops):
        return {"note": "no downtime records " + ("within this scope" if q.scope else "in the register")}
    return {"value": len(hit), "refs": hit[:IDS]}


# --- maintenance -----------------------------------------------------------------------------------------------------

def _backlog(conn, pack) -> dict | None:
    from ..analysis import backlog

    try:
        return backlog.health(conn, pack, limit=0)
    except (ValueError, KeyError):
        return None


@metric("maintenance.backlog_ageing", "Corrective backlog older than 90 days", group="Maintenance", unit="%",
        better="lower", target=20,
        definition="Of the open corrective work orders, the share opened more than 90 days ago: a backlog that "
                   "isn't being worked down, not just a big one.",
        formula="open corrective (PM01) orders older than 90 days / open corrective orders x 100",
        records="SAP work orders (open, corrective)")
def backlog_ageing(conn, pack, grain=None) -> dict:
    h = _backlog(conn, pack)
    if not h or not h["ageing"]["corrective_open"]:
        return {"note": "no open corrective work orders"}
    return {"value": round(100 * h["ageing"]["share_older_than_90_days"], 1), "as_of": h["as_of"]}


@metric("maintenance.urgent_overdue", "Urgent work orders overdue", group="Maintenance", unit="orders", better="lower",
        target=0,
        definition="Priority 1 and 2 work orders past their basic finish date.",
        formula="count(open orders with priority 1 or 2 and basic finish before today)",
        records="SAP work orders (open, priority, basic finish)")
def urgent_overdue(conn, pack, grain=None) -> dict:
    h = _backlog(conn, pack)
    if not h:
        return {"note": "no work orders"}
    return {"value": h["urgent_overdue"], "as_of": h["as_of"]}


# --- how the work is running: cases, scoped by what they are about, over any window (P7.2's rules) -------------------
#
# A case is in a scope when one of its subjects is the scope entity or beneath it; its queue is its role, else the role
# of its first person step (ops.performance, which the Performance page reads with the same rules).

WORK_GRAINS = ("queue", "type")
WORK = {"group": "Work", "grains": WORK_GRAINS, "scoped": True, "windowed": True, "default_window": "7d",
        "records": "cases and their history (work items)"}


def _cases(conn, q) -> list[dict]:
    """Every case open in, or closed since, the reach of this read (the window and the seven before it), in scope."""
    from . import performance

    if "cases" not in q.cache:
        since = q.span.start - timedelta(days=q.span.days * HISTORY)
        rows = [dict(r) for r in performance._cases(conn, since)]
        for r in rows:
            r["queue"] = performance._queue(r["role"])
            r["created"], r["closed"] = parse_time(r["created_at"]), parse_time(r["closed_at"])
            r["dispatched"] = parse_time(r["dispatched_at"])
            r["first_touch"] = next((parse_time(t) for t in (r["touched"] or []) if t >= r["created_at"]), None)
        q.cache["cases"] = rows
        q.cache["events"] = [{**e, "at": parse_time(e["at"])} for e in performance._events(conn, since)]
    return [c for c in q.cache["cases"] if q.within(c["subjects"])]


def _by_grain(q, cases: list[dict], measure) -> list[dict]:
    """The measure over each queue's (or type's) cases."""
    if not q.grain:
        return []
    groups: dict[str, list[dict]] = {}
    for c in cases:
        groups.setdefault(c["queue" if q.grain == "queue" else "type"], []).append(c)
    rows = []
    for k, cs in sorted(groups.items()):
        v, refs = measure(cs)
        if v:
            rows.append((k, k.replace("_", " "), v, refs))
    return _rows(rows)


def _case_measure(key: str, label: str, definition: str, formula: str, better: str, unit: str = "cases",
                  target: float | None = None, stock: bool = False):
    """Declare a work measure; the decorated function takes (cases in scope, q) and gives (value, case ids)."""
    def wrap(fn):
        @metric(key, label, definition=definition, formula=formula, unit=unit, better=better, target=target,
                stock=stock, period="open at the window's end" if stock else "the window (7 days by default)", **WORK)
        def compute(conn, pack, q) -> dict:
            def measure(cs):
                v, ids = fn(cs, q)
                return v, [f"case:{i}" for i in ids][:IDS]
            cases = _cases(conn, q)
            v, refs = measure(cases)
            return {"value": v, "refs": refs, "rows": _by_grain(q, cases, measure),
                    "note": None if v is not None else NONE_TO_MEASURE}
        return fn
    return wrap


NONE_TO_MEASURE = "no case in the window to measure it on"


def _median_hours(pairs) -> float | None:
    hours = [(b - a).total_seconds() / 3600 for a, b in pairs if a and b and b >= a]
    return round(median(hours), 1) if hours else None


@_case_measure("work.arrivals", "Cases opened", "Cases opened in the window.", "count(cases created in the window)",
               "none")
def _arrivals(cases, q):
    ids = [c["id"] for c in cases if q.span.holds(c["created"])]
    return len(ids), ids


@_case_measure("work.closed", "Cases closed", "Cases closed in the window.", "count(cases closed in the window)",
               "higher")
def _closed(cases, q):
    ids = [c["id"] for c in cases if q.span.holds(c["closed"])]
    return len(ids), ids


@_case_measure("work.backlog", "Open cases", "Cases open at the window's end (now, for a window ending today).",
               "count(cases created before the window's end and not closed by it)", "lower", stock=True)
def _open_at_end(cases, q):
    t = q.span.end
    ids = [c["id"] for c in cases if c["created"] and c["created"] < t and (c["closed"] is None or c["closed"] >= t)]
    return len(ids), ids


def _events(cases, q, what: str) -> list[int]:
    mine = {c["id"] for c in cases}
    return [e["item_id"] for e in q.cache["events"] if e["what"] == what and e["item_id"] in mine and q.span.holds(e["at"])]


@_case_measure("work.sla_misses", "Steps late", "Person steps due in the window that finished late or are still open "
               "past due.", "count(person steps due in the window, done after their due time or still open past it)",
               "lower", target=0)
def _sla(cases, q):
    ids = _events(cases, q, "sla_misses")
    return len(ids), sorted(set(ids))


@_case_measure("work.time_to_triage", "Time to triage", "How long a case opened in the window waited for the first "
               "thing a person did on it, the median.", "median(hours from opening to the first person's action) over "
               "cases opened in the window", "lower", unit="h")
def _triage(cases, q):
    got = [c for c in cases if q.span.holds(c["created"]) and c["first_touch"]]
    return _median_hours((c["created"], c["first_touch"]) for c in got), [c["id"] for c in got]


@_case_measure("work.time_to_dispatch", "Time to dispatch", "How long a case opened in the window waited to be handed "
               "off to the people who do it, the median, over the cases that were handed off.",
               "median(hours from opening to the first hand-off) over cases opened in the window and handed off",
               "lower", unit="h")
def _dispatch(cases, q):
    got = [c for c in cases if q.span.holds(c["created"]) and c["dispatched"]]
    return _median_hours((c["created"], c["dispatched"]) for c in got), [c["id"] for c in got]


@_case_measure("work.time_to_close", "Time to close", "How long the cases closed in the window took from opening to "
               "closing, the median.", "median(hours from opening to closing) over cases closed in the window",
               "lower", unit="h")
def _to_close(cases, q):
    got = [c for c in cases if q.span.holds(c["closed"])]
    return _median_hours((c["created"], c["closed"]) for c in got), [c["id"] for c in got]


def _field(conn, q) -> dict[int, list[dict]]:
    """What the field reported on each case's events (on site, done, could not do), oldest first."""
    if "field" not in q.cache:
        out: dict[int, list[dict]] = {}
        for r in conn.execute("select item_id, evidence->'field' as field from exception_record "
                              "where item_id is not null and jsonb_typeof(evidence->'field') = 'array'"):
            for f in r["field"]:
                out.setdefault(r["item_id"], []).append({"status": f.get("status"), "at": parse_time(f.get("at"))})
        for v in out.values():
            v.sort(key=lambda f: (f["at"] is None, f["at"].isoformat() if f["at"] else ""))
        q.cache["field"] = out
    return q.cache["field"]


def _on_site(reports: list[dict]) -> tuple | None:
    start = next((f["at"] for f in reports if f["status"] == "on_site" and f["at"]), None)
    end = next((f["at"] for f in reports if f["status"] == "done" and f["at"] and start and f["at"] >= start), None)
    return (start, end) if start and end else None


@metric("work.time_on_site", "Time on site", unit="h", better="lower", period="the window (7 days by default)",
        definition="How long the field spent on a job, from reporting on site to reporting it done, the median over "
                   "the cases closed in the window that the field reported on.",
        formula="median(hours from the field's first 'on site' to its 'done') over cases closed in the window", **WORK)
def time_on_site(conn, pack, q) -> dict:
    field, cases = _field(conn, q), _cases(conn, q)

    def measure(cs):
        got = [(c["id"], _on_site(field.get(c["id"], []))) for c in cs if q.span.holds(c["closed"])]
        got = [(i, p) for i, p in got if p]
        return _median_hours(p for _, p in got), [f"case:{i}" for i, _ in got][:IDS]
    v, refs = measure(cases)
    return {"value": v, "refs": refs, "rows": _by_grain(q, cases, measure),
            "note": None if v is not None else NONE_TO_MEASURE}


@metric("work.first_time_fix", "First-time fix", unit="%", better="higher", target=90,
        period="the window (7 days by default)",
        definition="Of the cases closed in the window, the share done on the first visit: the field never reported "
                   "it could not do the job, and the problem has not come back as a repeat.",
        formula="cases closed in the window with no 'could not do' and no repeat / cases closed in the window x 100",
        **WORK)
def first_time_fix(conn, pack, q) -> dict:
    field, cases = _field(conn, q), _cases(conn, q)
    repeated = {e["item_id"] for e in q.cache["events"] if e["what"] == "repeats"}

    def measure(cs):
        closed = [c for c in cs if q.span.holds(c["closed"])]
        fixed = [c["id"] for c in closed if c["id"] not in repeated
                 and not any(f["status"] == "could_not_do" for f in field.get(c["id"], []))]
        return (round(100 * len(fixed) / len(closed), 1) if closed else None), [f"case:{i}" for i in fixed][:IDS]
    v, refs = measure(cases)
    return {"value": v, "refs": refs, "rows": _by_grain(q, cases, measure),
            "note": None if v is not None else NONE_TO_MEASURE}


@_case_measure("work.reopened", "Cases reopened", "Cases reopened in the window because the problem came back "
               "(a repeat of a case already closed).", "count(repeats recorded in the window)", "lower", target=0)
def _reopened(cases, q):
    ids = _events(cases, q, "repeats")
    return len(ids), sorted(set(ids))


@metric("work.planned_share", "Planned work", unit="%", better="higher", period="the window (7 days by default)",
        definition="Of the cases closed in the window, the share that was planned: booked on the plan or counted "
                   "against a business plan line before it closed. The rest was reactive.",
        formula="cases closed in the window with a plan bar or a plan line / cases closed in the window x 100", **WORK)
def planned_share(conn, pack, q) -> dict:
    if "planned" not in q.cache:
        q.cache["planned"] = {r["item_id"] for r in conn.execute(
            "select item_id from plan_item where item_id is not null union select item_id from work_plan_match")}
    planned, cases = q.cache["planned"], _cases(conn, q)

    def measure(cs):
        closed = [c for c in cs if q.span.holds(c["closed"])]
        hit = [c["id"] for c in closed if c["id"] in planned]
        return (round(100 * len(hit) / len(closed), 1) if closed else None), [f"case:{i}" for i in hit][:IDS]
    v, refs = measure(cases)
    return {"value": v, "refs": refs, "rows": _by_grain(q, cases, measure),
            "note": None if v is not None else NONE_TO_MEASURE}


def _concerns(conn, q, ids: list[str]) -> dict[str, list[str]]:
    """What each work order is about (its concerns links), for placing it in a scope."""
    known = q.cache.setdefault("concerns", {})
    want = [i for i in ids if i not in known]
    if want:
        for i in want:
            known[i] = [i]
        for r in conn.execute("select src, dst from edge where src = any(%s) and rel = 'concerns'", (want,)):
            known[r["src"]].append(r["dst"])
    return known


@metric("work.schedule_compliance", "Schedule compliance", group="Work", unit="%", better="higher", target=90,
        scoped=True, windowed=True, default_window="30d", period="the window (30 days by default)",
        definition="Of the preventive and inspection work orders due in the window, the share finished by their due "
                   "date. One still open past its due date is late.",
        formula="PM02 and PM03 orders due in the window and finished by their basic finish / orders due in the window "
                "x 100", records="SAP work orders (PM02, PM03: basic finish, finish) and what each concerns")
def schedule_compliance(conn, pack, q) -> dict:
    from ..graph import operations

    p = operations.preventive_on_time(conn, q.span.start.date().isoformat(), q.span.end.date().isoformat()
                                      if q.span.end.time() == datetime.min.time() else
                                      (q.span.end.date() + timedelta(days=1)).isoformat())
    near = _concerns(conn, q, [*p["on_time"], *p["late"]])
    on_time = [i for i in p["on_time"] if q.within(near[i])]
    late = [i for i in p["late"] if q.within(near[i])]
    due = len(on_time) + len(late)
    if not due:
        return {"note": "no preventive or inspection order due in the window"}
    return {"value": round(100 * len(on_time) / due, 1), "refs": (late + on_time)[:IDS]}


# --- context and sources ---------------------------------------------------------------------------------------------

@metric("context.completeness", "Asset context completeness", group="Context", unit="%", grains=("area", "class"), target=90,
        scoped=True,
        definition="Of what an asset's class usually has (a check at least half its class passes), the share it "
                   "has: class, model, maintenance, historian points, roles, limits, drawing, documents, criticality.",
        formula="mean over assets of (expected checks passed / expected checks) x 100",
        records="assets (top-level tags and wells) and what the register holds about each")
def context_completeness(conn, pack, q) -> dict:
    from ..analysis import completeness

    grain = q.grain
    assets = [a for a in completeness.assets(conn) if a["score"] is not None and q.within([a["fuzer_id"]])]
    if not assets:
        return {"note": "no assets with an expectation " + ("in this scope" if q.scope else "yet")}
    rows = []
    if grain:
        key = "area" if grain == "area" else "class_name"
        groups: dict[str, list[dict]] = {}
        for a in assets:
            groups.setdefault(a[key] or "No area", []).append(a)
        rows = [(k, k, round(100 * mean(a["score"] for a in g), 1), [a["fuzer_id"] for a in g][:200])
                for k, g in sorted(groups.items())]
    return {"value": round(100 * mean(a["score"] for a in assets), 1), "rows": _rows(rows),
            "refs": [a["fuzer_id"] for a in assets if a["score"] < 1][:200]}


@metric("sources.straight_through", "Records decided without a steward", group="Sources", unit="%",
        grains=("system",), target=95,
        definition="Of a source's records, the share the last rebuild matched, linked or kept as new on its own, "
                   "leaving nothing for a steward.",
        formula="(records - records needing a steward) / records x 100",
        records="each source's records (crosswalk) and the questions open about them")
def straight_through(conn, pack, grain=None) -> dict:
    from ..graph import contextualized

    rows, total, need = [], 0, 0
    for r in conn.execute("select distinct source from crosswalk order by 1"):
        rep = contextualized.report(conn, r["source"])
        if not rep["records"]:
            continue
        rows.append((r["source"], r["source"].upper(), round(100 * (1 - rep["share"]), 1), []))
        total, need = total + rep["records"], need + rep["needs_steward"]
    if not total:
        return {"note": "no source records yet"}
    return {"value": round(100 * (1 - need / total), 1), "rows": _rows(rows) if grain else []}


def _scorecard(measure: str, label: str, definition: str, formula: str):
    @metric(f"sources.{measure}", f"Source {label.lower()}", group="Sources", unit="%", grains=("dataset",),
            period="the latest rebuild", definition=definition, formula=formula, target=95,
            records="each dataset's scorecard row (source_score), kept with every rebuild")
    def compute(conn, pack, grain=None) -> dict:
        rows = [(r["name"], r["name"], float(r["v"]), [])
                for r in conn.execute(
                    f"select distinct on (s.dataset_id) d.name, s.{measure} as v, s.at from source_score s "
                    f"join source_dataset d on d.id = s.dataset_id order by s.dataset_id, s.id desc")
                if r["v"] is not None]
        if not rows:
            return {"note": "no dataset has a score for it yet"}
        return {"value": round(mean(r[2] for r in rows), 1), "rows": _rows(sorted(rows)) if grain else []}
    return compute


_scorecard("completeness", "Completeness", "A dataset's mean column fill, from its latest profile.",
           "mean over its columns of filled rows / rows x 100; mean over datasets")
_scorecard("validity", "Validity", "The share of a dataset's quality checks passing, its key counted as one.",
           "passing checks / checks x 100; mean over datasets")
_scorecard("timeliness", "Timeliness", "100 while a delivery is on time; past its expected interval, the interval "
           "over its age.", "min(100, expected hours / hours since the last delivery x 100); mean over datasets")
_scorecard("agreement", "Agreement", "Of a source's records that join an item another source also has, the share "
           "with no open disagreement about them.", "(shared - in conflict) / shared x 100; mean over datasets")


@metric("sources.held_loads", "Loads held", group="Sources", unit="datasets", better="lower", target=0,
        grains=("dataset",), period="the latest load of each dataset",
        definition="Datasets whose latest load was held at its checks (duplicate keys, a failing blocking check or "
                   "changed columns) and waits for a steward to release or fix it.",
        formula="count(datasets whose latest run is held)",
        records="each dataset's runs (source_run), the latest first")
def held_loads(conn, pack, grain=None) -> dict:
    runs = [dict(r) for r in conn.execute(
        "select distinct on (r.dataset_id) d.name, r.status from source_run r join source_dataset d on d.id = r.dataset_id "
        "order by r.dataset_id, r.id desc")]
    if not runs:
        return {"note": "no dataset has loaded yet"}
    held = sorted(r["name"] for r in runs if r["status"] == "held")
    return {"value": len(held), "rows": _rows([(n, n, 1, []) for n in held]) if grain else []}


# --- safety and integrity, from the modules that own them --------------------------------------------------------------

@metric("safety.days_since_incident", "Days since the last incident", group="Safety", unit="days", better="higher",
        scoped=True, windowed=True, stock=True, default_window="1d", period="at the window's end",
        definition="Whole days from the start of the last incident in scope to the window's end.",
        formula="days from the latest incident start to the window's end",
        records="incident records and the unit each is about")
def days_since_incident(conn, pack, q) -> dict:
    from . import measures

    seen = [r for r in measures.records(conn, {"kind": "incident", "at": "start", "links": ["about"]}, q)
            if r["at"] and r["at"] < q.span.end and q.within(r["subjects"])]
    if not seen:
        return {"note": "no incident on record " + ("within this scope" if q.scope else "in the register")}
    last = max(seen, key=lambda r: r["at"])
    return {"value": (q.span.end - last["at"]).days, "refs": [last["id"]]}


@metric("safety.inspections_overdue", "Inspections overdue", group="Safety", unit="items", better="lower", target=0,
        scoped=True, windowed=True, stock=True, default_window="1d", period="at the window's end",
        definition="Items with wall thickness readings whose next inspection has passed: the inspection their wall "
                   "supports, or the date their condition inspection plan set.",
        formula="count(items whose wall-based inspection or inspection plan is past due on the window's day)",
        records="wall thickness readings (CMLs) and the condition inspection plans (ops.integrity)")
def inspections_overdue(conn, pack, q) -> dict:
    from . import integrity

    key = ("inspections", q.span.at)
    if key not in q.cache:
        q.cache[key] = integrity.findings(conn, q.span.at)
    found = q.cache[key]
    if not found and not conn.execute("select 1 from measurement where quantity = 'wall_thickness' limit 1").fetchone():
        return {"note": "no wall thickness readings on record"}
    hit = [f["id"] for f in found if {"overdue", "plan_overdue"} & set(f["reasons"]) and q.within([f["id"]])]
    return {"value": len(hit), "refs": hit[:IDS]}


@metric("safety.ldar_leaks", "Leaks found", group="Safety", unit="leaks", better="lower", scoped=True, windowed=True,
        default_window="90d", period="the window (90 days by default)",
        definition="Components an LDAR survey found leaking in the window: a screening reading at or above the leak "
                   "threshold. Equipment no survey reached is unknown, not clean.",
        formula="count(LDAR screening readings at or above the leak threshold in the window)",
        standard="EPA Method 21 screening",
        records="LDAR survey readings (ppm) on the tags screened")
def ldar_leaks(conn, pack, q) -> dict:
    from ..analysis import ldar

    if "ldar" not in q.cache:
        q.cache["ldar"] = [(r["fuzer_id"], parse_time(r["at"]), float(r["value"])) for r in conn.execute(
            "select fuzer_id, at, value from measurement where quantity = %s", (ldar.QUANTITY,))]
    mine = [r for r in q.cache["ldar"] if q.within([r[0]])]
    if not mine:
        return {"note": "no LDAR survey readings " + ("within this scope" if q.scope else "on record")}
    hit = [fid for fid, at, v in mine if q.span.holds(at) and v >= ldar.LEAK_PPM]
    return {"value": len(hit), "refs": sorted(set(hit))[:IDS]}


# --- the business plan against actual (ops.business_plan) ----------------------------------------------------------------

PLAN = {"group": "Plan", "grains": ("program",), "scoped": True, "windowed": True, "default_window": "qtd",
        "records": "business plan lines (estimates, period, assets) and the cases matched to them, their jobs and orders"}


def _lines(conn, q) -> list[dict]:
    """The open plan lines whose period meets the window, in scope (a line on no asset counts for the whole register
    only), each with its actuals read once."""
    from . import business_plan

    if "lines" not in q.cache:
        rows = [dict(r) for r in conn.execute(
            "select l.*, c.title as program, (select coalesce(array_agg(m.item_id), '{}') from work_plan_match m "
            "where m.line_id = l.id) as cases from work_plan_line l join work_container c on c.id = l.container_id "
            "where l.status = 'open' order by l.id")]
        for r in rows:
            r["actual"] = business_plan._actuals(conn, list(r["cases"]))
        q.cache["lines"] = rows
    lo, hi = q.span.start.date(), q.span.at
    return [r for r in q.cache["lines"]
            if (r["period_start"] is None or r["period_start"] <= hi) and (r["period_end"] is None or r["period_end"] >= lo)
            and (q.within(r["subjects"]) if q.scope else True)]


def _plan_measure(key: str, label: str, unit: str, definition: str, formula: str, read, better: str = "none"):
    @metric(key, label, unit=unit, better=better, definition=definition, formula=formula,
            period="plan lines whose period meets the window (the quarter to date by default)", **PLAN)
    def compute(conn, pack, q) -> dict:
        from . import business_plan

        lines = _lines(conn, q)
        if not q.cache["lines"]:  # no business plan at all: unknown, not 0 hours
            return {"note": business_plan.MISSING, "unknown": True}
        if not lines:
            return {"note": "no business plan line " + ("on this scope " if q.scope else "") + "in the window"}

        def total(ls):
            vals = [v for v in (read(x) for x in ls) if v is not None]
            return round(sum(vals), 1) if vals else None
        groups: dict[str, list] = {}
        for x in lines:
            groups.setdefault(x["program"], []).append(x)
        value = total(lines)
        return {"value": value, "refs": [f"case:{c}" for x in lines for c in x["cases"]][:IDS],
                "note": None if value is not None else "none recorded yet for the lines in the window",
                "rows": _rows([(k, k, total(ls), [f"case:{c}" for x in ls for c in x["cases"]][:IDS])
                               for k, ls in sorted(groups.items())]) if q.grain else []}
    return compute


_plan_measure("plan.estimate_hours", "Planned hours", "h", "The hours the business plan's lines estimate for their work.",
              "sum(estimate_hours) over plan lines in the window", lambda x: x["estimate_hours"])
_plan_measure("plan.actual_hours", "Actual hours", "h", "The hours worked and still booked on the jobs of the cases "
              "matched to the plan's lines.", "sum(hours done + hours booked) over the matched cases' jobs",
              lambda x: x["actual"]["hours_done"] + x["actual"]["hours_booked"] if x["cases"] else None)
_plan_measure("plan.estimate_cost", "Planned cost", "USD", "The cost the business plan's lines estimate for their work.",
              "sum(estimate_cost) over plan lines in the window", lambda x: x["estimate_cost"])
_plan_measure("plan.actual_cost", "Actual cost", "USD", "The cost SAP records on the orders of the jobs of the cases "
              "matched to the plan's lines.", "sum(order cost) over the matched cases' jobs",
              lambda x: x["actual"]["cost"])


@metric("plan.unplanned_share", "Unplanned work", unit="%", better="lower", group="Plan", scoped=True, windowed=True,
        default_window="qtd", period="the window (the quarter to date by default)",
        definition="Of the cases closed in the window inside a plan or program, the share no plan line covers: work "
                   "the plan did not foresee.",
        formula="cases closed in the window in a plan container and matched to no line / cases closed in the window "
                "in a plan container x 100",
        records="cases in plan containers (their jobs or their container) and the plan lines they are matched to")
def unplanned_share(conn, pack, q) -> dict:
    from . import business_plan

    if not business_plan.loaded(conn):  # with no plan every case would be unplanned: unknown instead
        return {"note": business_plan.MISSING, "unknown": True}
    if "contained" not in q.cache:
        q.cache["contained"] = {r["item_id"] for r in conn.execute(
            "select item_id from plan_item where item_id is not null and container_id is not null "
            "union select item_id from work_container_case") if r["item_id"]}
        q.cache["governing"] = {r["item_id"] for r in conn.execute(
            "select item_id from work_container where item_id is not null")}
        q.cache["matched"] = {r["item_id"] for r in conn.execute("select item_id from work_plan_match")}
    closed = [c for c in _cases(conn, q) if q.span.holds(c["closed"]) and c["id"] in q.cache["contained"]
              and c["id"] not in q.cache["governing"]]
    if not closed:
        return {"note": "no case in a plan or program closed in the window"}
    off = [c["id"] for c in closed if c["id"] not in q.cache["matched"]]
    return {"value": round(100 * len(off) / len(closed), 1), "refs": [f"case:{i}" for i in off][:IDS]}


# --- row sets over cases and plan lines (core/rowsets.py) -------------------------------------------------------------



@row_set("cases.open", "Open cases", scoped=True, windowed=True, default_window="1d",
         definition="Cases open at the window's end (now, for a window ending today), the highest priority first.",
         columns=[{"key": "case", "label": "Case"}, {"key": "type", "label": "Type"},
                  {"key": "queue", "label": "Queue"}, {"key": "opened", "label": "Opened"},
                  {"key": "due", "label": "Due"}])
def open_cases(conn, pack, q) -> dict:
    from . import performance

    t = q.span.end
    rows = [dict(r) for r in conn.execute(
        """
        select i.id, i.title, i.type, i.priority, i.due, i.created_at, i.subjects,
               coalesce(i.role, (select k.role from work_token k where k.item_id = i.id and k.kind in ('user', 'fix')
                                 and k.role is not null order by k.id limit 1)) as role
        from work_item i where i.created_at < %(t)s and (i.closed_at is null or i.closed_at >= %(t)s)
        order by i.priority desc nulls last, i.created_at, i.id
        """, {"t": t})]
    rows = [r for r in rows if q.within(r["subjects"])]
    return {"rows": [{"key": f"case:{r['id']}", "href": f"/cases/{r['id']}", "cells": {
        "case": r["title"], "type": r["type"].replace("_", " "), "queue": performance._queue(r["role"]).replace("_", " "),
        "opened": parse_time(r["created_at"]).isoformat(timespec="minutes"),
        "due": parse_time(r["due"]).isoformat(timespec="minutes") if r["due"] else None}} for r in rows],
        "note": None if rows else "no case open " + ("on this scope" if q.scope else "at the window's end")}


@row_set("plan.lines", "Plan lines", scoped=True, windowed=True, default_window="qtd",
         definition="The business plan's open lines whose period meets the window, each estimate against its actuals.",
         columns=[{"key": "program", "label": "Program"}, {"key": "line", "label": "Line"},
                  {"key": "est_h", "label": "Planned hours", "unit": "h", "align": "end"},
                  {"key": "act_h", "label": "Actual hours", "unit": "h", "align": "end"},
                  {"key": "var_h", "label": "Hours variance", "unit": "h", "align": "end"},
                  {"key": "est_cost", "label": "Planned cost", "unit": "USD", "align": "end"},
                  {"key": "act_cost", "label": "Actual cost", "unit": "USD", "align": "end"},
                  {"key": "var_cost", "label": "Cost variance", "unit": "USD", "align": "end"},
                  {"key": "cases", "label": "Cases", "align": "end"}])
def plan_lines(conn, pack, q) -> dict:
    from . import business_plan

    lines = _lines(conn, q)
    if not q.cache["lines"]:
        return {"rows": [], "note": business_plan.MISSING, "unknown": True}
    rows = []
    for x in lines:
        v = business_plan.variance(x, x["actual"])
        rows.append({"key": f"line:{x['id']}", "href": f"/cases/{x['cases'][0]}" if len(x["cases"]) == 1 else None,
                     "cells": {"program": x["program"], "line": x["title"], "est_h": x["estimate_hours"],
                               "act_h": round(x["actual"]["hours_done"] + x["actual"]["hours_booked"], 1),
                               "var_h": v["hours_variance"], "est_cost": x["estimate_cost"],
                               "act_cost": x["actual"]["cost"], "var_cost": v["cost_variance"],
                               "cases": len(x["cases"])}})
    return {"rows": rows,
            "note": None if lines else "no business plan line " + ("on this scope " if q.scope else "") + "in the window"}
