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

from statistics import mean

from ..core.metrics import metric


def _rows(items: list[tuple[str, str, float | None, list]]) -> list[dict]:
    return [{"key": k, "label": label, "value": v, "refs": refs} for k, label, v, refs in items]


# --- production ------------------------------------------------------------------------------------------------------

@metric("production.efficiency", "Production efficiency", group="Production", unit="%", grains=("facility",),
        period="the 14 days to the latest daily report", dated=True,
        definition="Oil allocated to the facility against the most it could have delivered on the same days, the "
                   "binding system capacity each day.",
        formula="sum(allocated oil) / sum(dynamic maximum) x 100, over days with both",
        standard="ISO 20815 production efficiency",
        records="production days (daily reports) and the facility's system capacity measurements")
def production_efficiency(conn, pack, grain=None, at=None) -> dict:
    from ..analysis import production_efficiency as pe

    rows, alloc, top, ends = [], 0.0, 0.0, []
    for f in conn.execute(
        "select distinct f.fuzer_id, coalesce(l.label, f.fuzer_id) as label from edge r join entity f on f.fuzer_id = r.dst "
        "and f.kind = 'facility' and f.merged_into is null left join entity_label l on l.fuzer_id = f.fuzer_id "
        "where r.rel = 'reports_on' order by 2").fetchall():
        try:
            out = pe.production_efficiency(conn, f["fuzer_id"], at)
        except ValueError:
            continue
        s = out["summary"]
        rows.append((f["fuzer_id"], f["label"], s["efficiency_pct"], [f["fuzer_id"]]))
        if s["allocated_sm3"] is not None and s["dynamic_max_sm3"]:
            alloc, top = alloc + s["allocated_sm3"], top + s["dynamic_max_sm3"]
        ends.append(out["end"])
    if not rows:
        return {"note": "no facility has daily production reports"}
    return {"value": round(100 * alloc / top, 1) if top else None, "rows": _rows(rows) if grain else [],
            "refs": [r[0] for r in rows], "as_of": max(ends)}


# --- maintenance -----------------------------------------------------------------------------------------------------

def _backlog(conn, pack) -> dict | None:
    from ..analysis import backlog

    try:
        return backlog.health(conn, pack, limit=0)
    except (ValueError, KeyError):
        return None


@metric("maintenance.backlog_ageing", "Corrective backlog older than 90 days", group="Maintenance", unit="%",
        better="lower",
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
        definition="Priority 1 and 2 work orders past their basic finish date.",
        formula="count(open orders with priority 1 or 2 and basic finish before today)",
        records="SAP work orders (open, priority, basic finish)")
def urgent_overdue(conn, pack, grain=None) -> dict:
    h = _backlog(conn, pack)
    if not h:
        return {"note": "no work orders"}
    return {"value": h["urgent_overdue"], "as_of": h["as_of"]}


# --- how the work is running (P7.2's measures, read as they are) -----------------------------------------------------

def _work(measure: str, label: str, definition: str, formula: str, better: str):
    @metric(f"work.{measure}", label, group="Work", unit="cases", grains=("queue", "type"), period="this week",
            better=better, definition=definition, formula=formula, records="cases and their history (work items)")
    def compute(conn, pack, grain=None) -> dict:
        from . import performance

        m = performance.measures(conn)
        dim, name = ("types", "type") if grain == "type" else ("queues", "queue")
        rows = [(r[name], r[name].replace("_", " "), r["weeks"][-1][measure]["n"], [f"case:{i}" for i in
                 r["weeks"][-1][measure]["ids"]]) for r in m[dim]]
        rows = [r for r in rows if r[2]]
        return {"value": sum(r[2] for r in rows), "rows": _rows(rows) if grain else [],
                "refs": [x for r in rows for x in r[3]], "as_of": m["weeks"][-1]["start"][:10]}
    return compute


_work("arrivals", "Cases opened", "Cases opened this week (Monday to now).", "count(cases created this week)", "none")
_work("closed", "Cases closed", "Cases closed this week (Monday to now).", "count(cases closed this week)", "higher")
_work("backlog", "Open cases", "Cases still open now.", "count(cases open now)", "lower")
_work("sla_misses", "Steps past due", "Person steps due this week that finished late or are still open past due.",
      "count(person steps due this week, done after their due time or still open past it)", "lower")


# --- context and sources ---------------------------------------------------------------------------------------------

@metric("context.completeness", "Asset context completeness", group="Context", unit="%", grains=("area", "class"),
        definition="Of what an asset's class usually has (a check at least half its class passes), the share it "
                   "has: class, model, maintenance, historian points, roles, limits, drawing, documents, criticality.",
        formula="mean over assets of (expected checks passed / expected checks) x 100",
        records="assets (top-level tags and wells) and what the register holds about each")
def context_completeness(conn, pack, grain=None) -> dict:
    from ..analysis import completeness

    assets = [a for a in completeness.assets(conn) if a["score"] is not None]
    if not assets:
        return {"note": "no assets with an expectation yet"}
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
        grains=("system",),
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
            period="the latest rebuild", definition=definition, formula=formula,
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
