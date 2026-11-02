"""Value and risk score (#100): one explainable number shared by upsets, work orders, maintenance plans and
opportunities, so the SRE (#96), the work board (#99) and the opportunity scan (#102) rank on one scale.

`score(item, cfg)` is pure: an `Item` (what is at stake, gathered from Fuzer data) and the pack's `value`
config in, a breakdown out. Every component is in money over the pack's horizon and shown on its own:

- production: oil / gas at risk or to gain (Sm3/d) x how long x price.
- risk: worst credible consequence (from SAP ABC criticality) x likelihood (failures per year) on the
  risk matrix; money is consequence x the share of failures that reach it x failures in the horizon.
- compliance: days overdue x a rate per kind (PM, INSP, IOW) x a criticality factor.
- urgency: a factor on the sum, halving every `urgency.days` until the consequence lands.
- effort: estimated cost, so value per effort can be ranked too.

A component that can't be worked out is `unknown` (value None), never zero; one that doesn't apply (no
failure risk in a gas lift move) is `n/a`. `items(conn, pack)` builds the items for an asset and
`ranked` scores and sorts them. `agreement` compares a ranking with an engineer's ordering.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import operations
from .operations import _num, _rows, _tags_of

DEFAULTS = {
    "currency": "USD",
    "prices": {"oil_sm3": 470.0, "gas_ksm3": 150.0},
    "horizon_days": 90,
    "risk_matrix": {"consequence_usd": [1e4, 1e5, 1e6, 1e7, 1e8], "likelihood_per_year": [0.1, 0.3, 1, 3],
                    "bands": {"low": 4, "medium": 9, "high": 15, "extreme": 25}, "worst_case_share": 0.01},
    "criticality": {"A": {"people": 4, "environment": 3, "asset": 4, "reputation": 3},
                    "B": {"people": 2, "environment": 2, "asset": 3, "reputation": 2},
                    "C": {"people": 1, "environment": 1, "asset": 2, "reputation": 1}},
    "compliance": {"usd_per_day_overdue": {"PM": 100, "INSP": 400, "IOW": 2000}, "max_days": 365,
                   "criticality_factor": {"A": 3, "B": 2, "C": 1}},
    "urgency": {"days": 30, "priority_days": {"1": 0, "2": 7, "3": 30, "4": 90}},
    "effort_usd": {"incident": 25000, "work_order": 20000, "maintenance_plan": 15000, "opportunity": 3000},
    "weights": {"production": 1.0, "risk": 1.0, "compliance": 1.0},
    "incident_lookback_days": 365,
    "opportunities": {"gas_lift_move_share": 0.25},
}
KINDS = ("incident", "work_order", "maintenance_plan", "opportunity")
PACK_SOURCE = "pack value config"


def config(pack) -> dict:
    """The pack's value config over the defaults (one level deep, so a pack can change one price)."""
    raw = getattr(pack, "value", None) or {}
    out = {}
    for k, v in DEFAULTS.items():
        out[k] = {**v, **(raw.get(k) or {})} if isinstance(v, dict) else raw.get(k, v)
    out["source"] = f"pack {pack.name}" if raw else "built-in defaults"
    return out


@dataclass
class Item:
    """What one thing puts at stake. None means not known; each known input names its source."""
    kind: str                                   # incident, work_order, maintenance_plan, opportunity
    id: str
    label: str
    text: str | None = None
    tag: str | None = None
    tag_id: str | None = None
    oil_sm3d: float | None = None               # at risk (or to gain, for an opportunity)
    gas_ksm3d: float | None = None
    duration_days: float | None = None          # how long that rate counts; None: the horizon
    chance: float | None = 1.0                  # that it happens (a repeat of a closed upset); None: unknown
    criticality: str | None = None              # SAP ABC indicator
    failures_per_year: float | None = None
    risk_applies: bool = True
    overdue_days: int = 0
    compliance_kind: str | None = None          # PM, INSP, IOW
    time_to_consequence_days: float | None = None
    effort_usd: float | None = None
    effort_basis: str | None = None
    sources: dict = field(default_factory=dict)  # input -> where it came from
    notes: list = field(default_factory=list)


def _money(v: float | None) -> str:
    if v is None:
        return "unknown"
    return f"${v / 1e6:.1f}M" if abs(v) >= 1e6 else f"${v / 1e3:.0f}k" if abs(v) >= 1e3 else f"${v:.0f}"


def _likelihood(per_year: float, bounds: list) -> int:
    return next((i + 1 for i, b in enumerate(bounds) if per_year <= b), len(bounds) + 1)


def _band(cell: int, bands: dict) -> str:
    return next((name for name, top in sorted(bands.items(), key=lambda b: b[1]) if cell <= top), "extreme")


def production(item: Item, cfg: dict) -> dict:
    if item.oil_sm3d is None and item.gas_ksm3d is None:
        return {"status": "unknown", "value_usd": None, "note": "no rate at risk or to gain"}
    if item.chance is None:
        return {"status": "unknown", "value_usd": None, "note": "chance it happens unknown"}
    days = item.duration_days if item.duration_days is not None else cfg["horizon_days"]
    p = cfg["prices"]
    oil, gas = item.oil_sm3d or 0.0, item.gas_ksm3d or 0.0
    value = (oil * p["oil_sm3"] + gas * p["gas_ksm3"]) * days * item.chance
    return {"status": "ok", "value_usd": round(value), "oil_sm3d": round(oil, 1), "gas_ksm3d": round(gas, 1),
            "days": round(days, 1), "oil_sm3": round(oil * days), "chance": round(item.chance, 2), "prices": p,
            "sources": {k: item.sources[k] for k in ("oil_sm3d", "gas_ksm3d", "chance") if k in item.sources}
            | {"prices": cfg["source"]}}


def risk(item: Item, cfg: dict) -> dict:
    if not item.risk_applies:
        return {"status": "n/a", "value_usd": None}
    m = cfg["risk_matrix"]
    levels = cfg["criticality"].get(item.criticality) if item.criticality else None
    missing = [n for n, v in (("criticality", levels), ("failures_per_year", item.failures_per_year)) if v is None]
    if missing:
        return {"status": "unknown", "value_usd": None, "criticality": item.criticality or "unknown",
                "failures_per_year": item.failures_per_year,
                "note": " and ".join(missing).replace("_", " ") + " unknown"}
    c = max(levels.values())
    likelihood = _likelihood(item.failures_per_year, m["likelihood_per_year"])
    years = cfg["horizon_days"] / 365.25
    value = m["consequence_usd"][c - 1] * m["worst_case_share"] * item.failures_per_year * years
    return {"status": "ok", "value_usd": round(value), "criticality": item.criticality, "consequence": levels,
            "consequence_level": c, "likelihood_level": likelihood, "matrix_cell": c * likelihood,
            "band": _band(c * likelihood, m["bands"]), "failures_per_year": round(item.failures_per_year, 2),
            "sources": {k: item.sources[k] for k in ("criticality", "failures_per_year") if k in item.sources}
            | {"matrix": cfg["source"]}}


def compliance(item: Item, cfg: dict) -> dict:
    if not item.compliance_kind:
        return {"status": "n/a", "value_usd": None}
    c = cfg["compliance"]
    rate = c["usd_per_day_overdue"].get(item.compliance_kind)
    if rate is None:
        return {"status": "unknown", "value_usd": None, "note": f"no rate for {item.compliance_kind} in the pack"}
    factor = c["criticality_factor"].get(item.criticality) if item.criticality else None
    out = {"status": "ok", "kind": item.compliance_kind, "overdue_days": item.overdue_days,
           "usd_per_day": rate, "criticality": item.criticality or "unknown",
           "sources": {k: item.sources[k] for k in ("overdue_days", "criticality") if k in item.sources}
           | {"rates": cfg["source"]}}
    if factor is None:  # still counts the overdue days, at the lowest factor, and says so
        factor = min(c["criticality_factor"].values())
        out["note"] = f"criticality unknown: lowest factor ({factor}) used"
    out["criticality_factor"] = factor
    days = min(item.overdue_days, c["max_days"])
    if days < item.overdue_days:
        out["note"] = (out.get("note", "") + f"; counted to {days} d").lstrip("; ")
    out["value_usd"] = round(days * rate * factor)
    return out


def urgency(item: Item, cfg: dict) -> dict:
    half = cfg["urgency"]["days"]
    ttc = item.time_to_consequence_days
    if ttc is None:  # unknown timing: counted as one half-life away, and shown as unknown
        return {"status": "unknown", "factor": 0.5, "days_to_consequence": None,
                "note": f"time to consequence unknown: factor 0.5 ({half} d) used"}
    ttc = max(0.0, ttc)
    return {"status": "ok", "factor": round(half / (half + ttc), 3), "days_to_consequence": round(ttc, 1),
            "sources": {k: item.sources[k] for k in ("time_to_consequence_days",) if k in item.sources}}


def effort(item: Item, cfg: dict) -> dict:
    if item.effort_usd is not None:
        return {"status": "ok", "value_usd": round(item.effort_usd), "basis": item.effort_basis,
                "sources": {k: item.sources[k] for k in ("effort_usd",) if k in item.sources}}
    default = cfg["effort_usd"].get(item.kind)
    if default is None:
        return {"status": "unknown", "value_usd": None}
    return {"status": "assumed", "value_usd": default, "basis": f"pack default for a {item.kind.replace('_', ' ')}",
            "sources": {"effort_usd": cfg["source"]}}


def score(item: Item, cfg: dict) -> dict:
    """The score of one item: total value (USD over the horizon, after urgency), value per effort, each
    component with its inputs and sources, what is unknown, and a one-line reason."""
    parts = {"production": production(item, cfg), "risk": risk(item, cfg), "compliance": compliance(item, cfg)}
    u, e = urgency(item, cfg), effort(item, cfg)
    w = cfg["weights"]
    at_stake = sum(w.get(k, 1.0) * (p["value_usd"] or 0) for k, p in parts.items())
    total = round(at_stake * u["factor"])
    cost = e["value_usd"]
    unknown = [k for k, p in {**parts, "urgency": u, "effort": e}.items() if p["status"] == "unknown"]
    if item.criticality is None and "criticality" not in unknown and item.risk_applies:
        unknown.append("criticality")
    return {"kind": item.kind, "id": item.id, "label": item.label, "text": item.text, "tag": item.tag,
            "tag_id": item.tag_id, "value_usd": total, "at_stake_usd": round(at_stake),
            "value_per_effort": round(total / cost, 2) if cost else None,
            "criticality": item.criticality or "unknown", "components": parts, "urgency": u, "effort": e,
            "unknown": unknown, "notes": item.notes, "reason": reason(item, parts, u, e, total),
            "currency": cfg["currency"], "horizon_days": cfg["horizon_days"]}


def reason(item: Item, parts: dict, u: dict, e: dict, total: float) -> str:
    """One line: what the value is made of, biggest first."""
    p, r, c = parts["production"], parts["risk"], parts["compliance"]
    bits = []
    if p["value_usd"]:
        verb = "to gain" if item.kind == "opportunity" else "at risk"
        what = (f"{p['oil_sm3']:,} Sm3 oil {verb} over {p['days']:g} d" if p["chance"] == 1 else
                f"{p['oil_sm3']:,} Sm3 oil per repeat x {p['chance']:.0%} chance")
        bits.append((p["value_usd"], f"{what} ({_money(p['value_usd'])})"))
    if r["status"] == "ok" and r["value_usd"]:
        what = f"risk {r['band']} ({item.criticality}-critical, {r['failures_per_year']:g} failures/yr"
        bits.append((r["value_usd"], f"{what}; {_money(r['value_usd'])})"))
    if c["status"] == "ok" and c["value_usd"]:
        bits.append((c["value_usd"], f"{c['kind']} {c['overdue_days']} d overdue ({_money(c['value_usd'])})"))
    text = "; ".join(b for _, b in sorted(bits, key=lambda b: -b[0])) or "nothing at stake found"
    when = ("now" if u.get("days_to_consequence") == 0 else "unknown" if u["status"] == "unknown"
            else f"in ~{u['days_to_consequence']:g} d")
    tail = f"Worth {_money(total)}, due {when}"
    if e["value_usd"]:
        tail += f", effort {_money(e['value_usd'])}{' (assumed)' if e['status'] == 'assumed' else ''}"
    if item.risk_applies and not item.criticality:
        tail += ", criticality unknown"
    return f"{item.label}: {text}. {tail}."


# --- items from Fuzer data ---------------------------------------------------------------------------

def _context(conn, today: str) -> dict:
    """Per tag: criticality, failures per year (2 years of notifications), oil lost per day (last year of
    downtime) and ISO 14224 class; and completed order costs for effort estimates."""
    actors = operations.bad_actors(conn, limit=100000)
    failures = {a["id"]: a["failures"] for a in actors["bad_actors"]}
    years = (date.fromisoformat(actors["until"]) - date.fromisoformat(actors["since"])).days / 365.25
    since = (date.fromisoformat(today) - timedelta(days=365)).isoformat()
    lost = {r["fuzer_id"]: r["lost_oil_sm3"] for r in
            operations.deferment_pareto(conn, "tag", since, None, 100000)["items"]}
    crit = {r["fuzer_id"]: r["v"] for r in conn.execute(
        "select fuzer_id, value #>> '{}' as v from golden where field = 'criticality'")}
    classes = {r["src"]: r["dst"] for r in conn.execute("select src, dst from edge where rel = 'classified_as'")}
    done = _rows(conn, "work_order", "f->>'status' = 'TECO' and coalesce(f->>'cost', '') <> ''")
    tags = _tags_of(conn, [o["id"] for o in done])
    costs = defaultdict(list)
    for o in done:
        t = (tags.get(o["id"]) or {}).get("id")
        typ = o["f"].get("order_type")
        for key in (("tag", t, typ), ("class", classes.get(t), typ), ("asset", None, typ)):
            if key[0] == "asset" or key[1]:
                costs[key].append(_num(o["f"]["cost"]))
    return {"failures": failures, "years": years, "since": since, "lost": lost, "crit": crit,
            "classes": classes, "costs": costs, "window": f"{actors['since']} to {actors['until']}"}


def _tag_inputs(ctx: dict, tag_id: str | None, sources: dict) -> dict:
    """Criticality, failures per year and oil at risk for the tag an item concerns."""
    if not tag_id:
        return {}
    out = {"criticality": ctx["crit"].get(tag_id) or None,
           "failures_per_year": ctx["failures"].get(tag_id, 0) / ctx["years"],
           "oil_sm3d": ctx["lost"].get(tag_id, 0) / 365.0}
    if out["criticality"]:
        sources["criticality"] = "SAP functional location ABC indicator"
    sources["failures_per_year"] = f"SAP notifications {ctx['window']}"
    sources["oil_sm3d"] = f"EC downtime caused by the tag since {ctx['since']}, per day"
    return out


def _cost(ctx: dict, tag_id: str | None, order_type: str | None) -> tuple[float | None, str | None]:
    """Average completed order cost: on the same tag, else the same class, else the asset (same type)."""
    for scope, key in (("this tag", ("tag", tag_id, order_type)),
                       ("this ISO 14224 class", ("class", ctx["classes"].get(tag_id), order_type)),
                       ("the asset", ("asset", None, order_type))):
        if (scope == "the asset" or key[1]) and ctx["costs"].get(key):
            vals = ctx["costs"][key]
            return sum(vals) / len(vals), f"average of {len(vals)} completed {order_type} orders on {scope}"
    return None, None


def _incidents(conn, ctx: dict, today: str, horizon: int) -> list[Item]:
    """Incidents that ended within the lookback. An open one puts its own loss at stake; a closed one the
    loss of a repeat, times the repeats expected in the horizon (failures per year of the unit), due when
    the unit's MTBF says the next one comes."""
    since = (date.fromisoformat(today) - timedelta(days=ctx["cfg"]["incident_lookback_days"])).isoformat()
    rows = _rows(conn, "incident", "f->>'end' >= %(s)s", {"s": since})
    about = {r["src"]: r["dst"] for r in conn.execute(
        "select src, dst from edge where rel = 'about' and src = any(%s)", ([r["id"] for r in rows],))}
    out = []
    for r in rows:
        f, tid = r["f"], about.get(r["id"])
        src = {}
        t = _tag_inputs(ctx, tid, src)
        days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days + 1
        lost = _num(f.get("lost_oil_sm3"))
        ongoing = f["end"] >= (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        fpy = t.get("failures_per_year")
        notes = []
        if ongoing:
            ttc, chance = 0.0, 1.0
            src["time_to_consequence_days"] = "incident still open"
        elif fpy is None:
            ttc = chance = None
        else:
            chance = min(1.0, fpy * horizon / 365.25)
            since_end = (date.fromisoformat(today) - date.fromisoformat(f["end"])).days
            ttc = max(0.0, 365.25 / fpy - since_end) if fpy else None
            src["chance"] = f"failures per year of the unit over {horizon} d"
            notes.append(f"closed {f['end']}: scored as the cost of a repeat")
            if ttc is not None:
                src["time_to_consequence_days"] = "MTBF of the unit (SAP notifications) since the incident"
        src["oil_sm3d"] = "EC downtime in the incident"
        out.append(Item("incident", r["id"], r["label"] or f.get("unit"), f.get("description"), f.get("unit"), tid,
                        oil_sm3d=lost / days, duration_days=days, chance=chance,
                        criticality=t.get("criticality"), failures_per_year=fpy, time_to_consequence_days=ttc,
                        sources=src, notes=notes))
    return out


def _work_orders(conn, ctx: dict, today: str) -> list[Item]:
    orders = _rows(conn, "work_order", "f->>'status' = any(%(o)s)", {"o": list(operations.OPEN)})
    tags = _tags_of(conn, [o["id"] for o in orders])
    pdays = ctx["cfg"]["urgency"]["priority_days"]
    out = []
    for o in orders:
        f, t = o["f"], tags.get(o["id"]) or {}
        src = {}
        ti = _tag_inputs(ctx, t.get("id"), src)
        due = f.get("basic_finish")
        overdue = (date.fromisoformat(today) - date.fromisoformat(due)).days if due and due < today else 0
        ttc = pdays.get(str(f.get("priority"))) if f.get("priority") else None
        if ttc is not None:
            src["time_to_consequence_days"] = f"SAP priority {f['priority']}"
        ttc = None if ttc is None else max(0, ttc - overdue)
        cost = _num(f["cost"]) if f.get("cost") else None
        basis = "SAP order cost" if cost else None
        if cost is None:
            cost, basis = _cost(ctx, t.get("id"), f.get("order_type"))
        if cost is not None:
            src["effort_usd"] = "SAP work orders"
        notes = [f"{overdue} d past basic finish {due}"] if overdue else []
        out.append(Item("work_order", o["id"], f"{o['label']} {t.get('label') or ''}".strip(), f.get("text"),
                        t.get("label"), t.get("id"),
                        oil_sm3d=ti.get("oil_sm3d"), criticality=ti.get("criticality"),
                        failures_per_year=ti.get("failures_per_year"), time_to_consequence_days=ttc,
                        effort_usd=cost, effort_basis=basis, sources=src, notes=notes))
    return out


def _plans(conn, ctx: dict, today: str, horizon: int) -> list[Item]:
    comp = operations.maintenance_compliance(conn, today, horizon, limit=100000)
    out = []
    for p in comp["overdue"] + comp["due_soon"]:
        src = {"overdue_days": "SAP maintenance plan next due date"}
        ti = _tag_inputs(ctx, p["tag_id"], src)
        ttc = (date.fromisoformat(p["next_due"]) - date.fromisoformat(today)).days
        src["time_to_consequence_days"] = "SAP maintenance plan next due date"
        cost, basis = _cost(ctx, p["tag_id"], "PM02")
        if cost is not None:
            src["effort_usd"] = "SAP work orders"
        out.append(Item("maintenance_plan", p["id"], f"{p['plan']} {p['tag'] or ''}".strip(), p["task"],
                        p["tag"], p["tag_id"],
                        oil_sm3d=ti.get("oil_sm3d"), criticality=ti.get("criticality"),
                        failures_per_year=ti.get("failures_per_year"), overdue_days=p["overdue_days"],
                        compliance_kind=p["kind"], time_to_consequence_days=ttc, effort_usd=cost,
                        effort_basis=basis, sources=src,
                        notes=["needs a shutdown"] if p["needs_shutdown"] else []))
    return out


def _opportunities(conn, ctx: dict) -> list[Item]:
    """Open opportunities from the opportunity scan (#102), when it has run; else gas lift moves: on each
    header, lift gas from the well that responds least to the one that responds most (from well tests).
    Back-pressure options (#107) either way."""
    scanned = conn.execute("select to_regclass('opportunity') is not null as x").fetchone()["x"] and \
        conn.execute("select exists (select 1 from opportunity) as x").fetchone()["x"]
    if scanned:
        return [Item("opportunity", f"scan:{o['key']}", o["title"], o["detail"].get("basis"), None, o["target"],
                     oil_sm3d=o["gain_sm3d"], risk_applies=False, time_to_consequence_days=0.0,
                     sources={"oil_sm3d": f"opportunity scan: {o['detail'].get('basis')}",
                              "time_to_consequence_days": "gain starts when it is done"},
                     notes=[f"confidence {o['detail'].get('confidence')}", f"confirm by {o['detail'].get('confirm_by')}"])
                for o in conn.execute("select * from opportunity where status = 'open'")] + _debottleneck(conn)
    return _gas_lift_moves(conn, ctx) + _debottleneck(conn)


def _gas_lift_moves(conn, ctx: dict) -> list[Item]:
    net = operations.gas_lift_network(conn, limit=100000)
    if "error" in net:
        return []
    share = ctx["cfg"]["opportunities"]["gas_lift_move_share"]
    by_header = defaultdict(list)
    for w in net["most_response"]:
        by_header[w["header"]].append(w)
    out = []
    for header, ws in by_header.items():
        if len(ws) < 2:
            continue
        hi, lo = ws[0], ws[-1]
        gain = hi["response_oil_per_extra_gas"] - lo["response_oil_per_extra_gas"]
        if gain <= 0:
            continue
        move = lo["lift_gas_ksm3d"] * share
        out.append(Item(
            "opportunity", f"gas_lift:{lo['id']}->{hi['id']}", f"Lift gas {lo['well']} -> {hi['well']}",
            f"Move {move:.0f} kSm3/d lift gas on {header} from {lo['well']} "
            f"({lo['response_oil_per_extra_gas']:g} Sm3 oil per kSm3) to {hi['well']} "
            f"({hi['response_oil_per_extra_gas']:g})", hi["well"], hi["id"],
            oil_sm3d=move * gain, risk_applies=False, time_to_consequence_days=0.0,
            sources={"oil_sm3d": f"EC well tests {net['since']} to {net['until']}: lift gas response",
                     "time_to_consequence_days": "gain starts when the move is made"},
            notes=[f"moves {share:.0%} of {lo['well']}'s lift gas"]))
    return out


def _debottleneck(conn) -> list[Item]:
    out = []
    exists = conn.execute("select to_regclass('debottleneck_option') is not null as x").fetchone()["x"]
    for o in conn.execute("select * from debottleneck_option where gain_sm3d > 0 order by rank") if exists else []:
        d = o["detail"]
        out.append(Item(
            "opportunity", f"debottleneck:{o['kind']}:{o['target']}", o["action"], d.get("basis"),
            None, o["target"] if (o["target"] or "").startswith("fz-tag") else None, oil_sm3d=o["gain_sm3d"],
            risk_applies=False, time_to_consequence_days=0.0,
            sources={"oil_sm3d": f"back-pressure model (#107), scanned {o['as_of']}", "time_to_consequence_days":
                     "gain starts when the change is made"},
            notes=[f"confidence {d.get('confidence')}", f"next constraint: {d.get('next_constraint')}",
                   f"confirm by {d.get('confirm_by')}"]))
    return out


def items(conn, pack, kinds: tuple = KINDS, as_of_date: str | None = None) -> list[Item]:
    """Items for the asset: incidents ended within the horizon, open work orders, plans overdue or due
    within the horizon, and gas lift opportunities."""
    cfg = config(pack)
    today = as_of_date or operations.as_of(conn)
    ctx = {**_context(conn, today), "cfg": cfg}
    horizon = cfg["horizon_days"]
    out = []
    if "incident" in kinds:
        out += _incidents(conn, ctx, today, horizon)
    if "work_order" in kinds:
        out += _work_orders(conn, ctx, today)
    if "maintenance_plan" in kinds:
        out += _plans(conn, ctx, today, horizon)
    if "opportunity" in kinds:
        out += _opportunities(conn, ctx)
    return out


def ranked(conn, pack, kinds: tuple = KINDS, as_of_date: str | None = None, by: str = "value",
           ref_id: str | None = None, limit: int = 25) -> dict:
    """Scored items, highest value first (or highest value per effort, by='per_effort')."""
    cfg = config(pack)
    today = as_of_date or operations.as_of(conn)
    scored = [score(i, cfg) for i in items(conn, pack, kinds, today)]
    if ref_id:
        scored = [s for s in scored if ref_id in (s["id"], s["tag_id"])]
    key = (lambda s: -(s["value_per_effort"] or 0)) if by == "per_effort" else (lambda s: -s["value_usd"])
    scored.sort(key=lambda s: (key(s), s["label"] or ""))
    return {"as_of": today, "by": by, "currency": cfg["currency"], "horizon_days": cfg["horizon_days"],
            "config": cfg["source"], "count": len(scored),
            "by_kind": {k: sum(1 for s in scored if s["kind"] == k) for k in kinds},
            "source": "SAP work orders, notifications, plans and ABC indicator; EC downtime and well tests; "
                      f"prices and risk matrix from {cfg['source']}",
            "items": scored[:limit]}


# --- calibration -------------------------------------------------------------------------------------

def agreement(ours: list[str], theirs: list[str]) -> dict:
    """How well our ranking matches an engineer's ordering of the same sample: Kendall's tau over the
    items both rank (1 same order, -1 reversed), and the overlap of the top three."""
    pos = {x: i for i, x in enumerate(ours)}
    common = [x for x in theirs if x in pos]
    n = len(common)
    if n < 2:
        return {"items": n, "kendall_tau": None, "top3_overlap": None, "note": "fewer than two items in common"}
    agree = sum(1 if pos[common[i]] < pos[common[j]] else -1 for i in range(n) for j in range(i + 1, n))
    mine = sorted(common, key=pos.get)
    top = min(3, n)
    disagreements = sorted(({"id": x, "engineer": i + 1, "fuzer": mine.index(x) + 1} for i, x in enumerate(common)),
                           key=lambda d: -abs(d["engineer"] - d["fuzer"]))
    return {"items": n, "kendall_tau": round(agree / (n * (n - 1) / 2), 3),
            "top3_overlap": round(len(set(mine[:top]) & set(common[:top])) / top, 2),
            "biggest_disagreements": [d for d in disagreements if d["engineer"] != d["fuzer"]][:5]}


def calibrate(conn, pack, as_of_date: str | None = None) -> dict:
    """Every engineer ordering saved in value_ranking, compared with the score's ranking today."""
    rows = conn.execute("select id, by_user, items, created_at from value_ranking order by id").fetchall()
    if not rows:
        return {"rankings": 0, "note": "no engineer orderings yet: POST /value/rankings with a sample in order"}
    cfg = config(pack)
    scored = sorted((score(i, cfg) for i in items(conn, pack, KINDS, as_of_date)), key=lambda s: -s["value_usd"])
    ours = [s["id"] for s in scored]
    each = [{"id": r["id"], "by": r["by_user"], "created_at": r["created_at"], **agreement(ours, r["items"])}
            for r in rows]
    taus = [e["kendall_tau"] for e in each if e["kendall_tau"] is not None]
    return {"rankings": len(each), "mean_kendall_tau": round(sum(taus) / len(taus), 3) if taus else None,
            "config": cfg["source"], "each": each}
