"""Outages on the plan and the customers they hit (P5.22, #1253): each planned outage over a horizon with the plant
it takes down, the customers' contracts it hits there, and what they lose in volume and dollars. An estimate for
planners, not a nomination or a commitment.

    contracts(conn)              the customer and contract register (the contracts source): each contract, its
                                 customer, its delivery point, its daily volume and rate, and the dates it runs
    outages(conn, start, days)   the outages in [start, start + days), each with what it takes down and the contracts
                                 it hits, the volume and dollars each loses

Read from the records, never from the asset type:
    an outage            a schedule whose activities need the plant down is one outage, from its first start to its
                         last finish; any other plan item that needs a shutdown (or is one) is one on its own
    what it takes down   the flow units its jobs take down, as Production at risk reads them (at_risk._units)
    a contract it hits   one whose delivery point is a unit it takes down, or loses every feed because of it, while
                         the contract runs
    what it loses        the contract's daily volume for the hours it is hit, at the contract's rate
"""

from datetime import UTC, datetime, timedelta

from . import at_risk
from .plan import ACTIVE, SHUTDOWNS, _when

HORIZON_DAYS = at_risk.HORIZON_DAYS
BASIS = ("Each contract whose delivery point an outage takes down, or leaves with no feed on the flow network, at its "
         "daily volume and rate for the hours it is hit")


def contracts(conn) -> list[dict]:
    """Every contract on the register: its customer, what it buys, its delivery point, its daily volume (bbl/d), its
    rate ($/bbl) and the dates it runs (either may be open)."""
    rows = conn.execute(
        """
        select e.fuzer_id as id,
               (select min(k.key) from entity_key k where k.fuzer_id = e.fuzer_id and k.kind = 'contract') as key,
               (select jsonb_object_agg(g.field, g.value) from golden g where g.fuzer_id = e.fuzer_id) as f,
               (select min(x.dst) from edge x where x.src = e.fuzer_id and x.rel = 'delivers_at'
                and (x.valid_to is null or x.valid_to > now())) as point
        from entity e where e.kind = 'contract' and e.retired_at is null and e.merged_into is null order by 2
        """).fetchall()
    labels = at_risk._labels(conn, {r["point"] for r in rows if r["point"]})
    number = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    day = lambda v: _when(str(v)[:10]) if v else None
    out = []
    for r in rows:
        f = r["f"] or {}
        out.append({"id": r["id"], "key": r["key"] or r["id"], "customer": str(f.get("customer") or "").strip() or None,
                    "customer_type": f.get("customer_type"), "service": f.get("service"), "point": r["point"],
                    "point_label": labels.get(r["point"]) or r["point"], "volume_bbl_d": number(f.get("volume_bbl_d")),
                    "rate_usd_bbl": number(f.get("rate_usd_bbl")), "start": day(f.get("start")), "end": day(f.get("end"))})
    return out


def _hours(a: datetime, b: datetime) -> float:
    return max(0.0, (b - a).total_seconds() / 3600)


def _clip(span: tuple[datetime, datetime], c: dict, t0: datetime, t1: datetime) -> tuple[datetime, datetime] | None:
    """The part of an outage's span the contract runs in, within the horizon."""
    a = max(span[0], t0, c["start"] or t0)
    b = min(span[1], t1, c["end"] + timedelta(days=1) if c["end"] else t1)
    return (a, b) if b > a else None


def _loss(c: dict, hours: float) -> tuple[float | None, float | None]:
    vol = c["volume_bbl_d"] * hours / 24 if c["volume_bbl_d"] is not None else None
    return vol, (vol * c["rate_usd_bbl"] if vol is not None and c["rate_usd_bbl"] is not None else None)


def _rows(conn, t0: datetime, t1: datetime) -> list[dict]:
    return conn.execute(
        """
        select i.id, i.title, i.kind, i.source, i.project, i.subjects, i.area_id, i.start_at, i.end_at, i.crew,
               p.title as project_title
        from plan_item i left join plan_project p on p.code = i.project
        where i.status = any(%(active)s) and (i.needs_shutdown or i.kind = any(%(sd)s))
          and i.start_at < %(t1)s and i.end_at > %(t0)s
          and not exists (select 1 from plan_item c where c.parent_id = i.id)
        order by i.start_at, i.id
        """, {"active": list(ACTIVE), "sd": list(SHUTDOWNS), "t0": t0, "t1": t1}).fetchall()


def _group(conn, rows: list[dict]) -> list[dict]:
    """The outages: a schedule's activities that need the plant down as one, every other item on its own, each with
    the flow units its jobs take down and everything that loses its feed because of them."""
    feeds, nodes = at_risk._flow(conn)
    groups: dict[str, dict] = {}
    for r in rows:
        key = r["project"] or f"item:{r['id']}"
        g = groups.setdefault(key, {"key": key, "title": r["project_title"] or r["title"], "project": r["project"],
                                    "item_ids": [], "start": r["start_at"], "end": r["end_at"], "units": set(),
                                    "areas": set(), "people": set()})
        g["item_ids"].append(r["id"])
        g["start"], g["end"] = min(g["start"], r["start_at"]), max(g["end"], r["end_at"])
        g["units"] |= set(at_risk._units(conn, list(r["subjects"] or []), nodes))
        if r["area_id"]:
            g["areas"].add(r["area_id"])
        if r["crew"]:
            g["people"].add(r["crew"])
    for g in groups.values():
        g["reach"] = g["units"] | at_risk._starved(g["units"], feeds)
    return sorted(groups.values(), key=lambda g: (g["start"], g["key"]))


def _hits(g: dict, span: tuple[datetime, datetime], book: list[dict], t0: datetime, t1: datetime) -> list[dict]:
    out = []
    for c in book:
        if c["point"] not in g["reach"] or not (cut := _clip(span, c, t0, t1)):
            continue
        hours = _hours(*cut)
        vol, usd = _loss(c, hours)
        vol, usd = (round(x) if x is not None else None for x in (vol, usd))
        out.append({"contract": c["key"], "customer": c["customer"], "service": c["service"], "point": c["point_label"],
                    "start": cut[0], "end": cut[1], "hours": round(hours, 1), "volume_bbl": vol, "usd": usd})
    return out


def _sum(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs), 0) if xs else None


def outages(conn, start=None, days: int = HORIZON_DAYS) -> dict:
    """The outages in [start, start + days), each with the plant it takes down and the contracts it hits, and what
    each loses: volume (bbl) and dollars, for the hours it is hit."""
    t0 = _when(start) if start else datetime.now(UTC)
    t1 = t0 + timedelta(days=days)
    book = contracts(conn)
    groups = _group(conn, _rows(conn, t0, t1))
    labels = at_risk._labels(conn, {u for g in groups for u in g["units"] | g["areas"]})
    out = []
    for g in groups:
        hits = _hits(g, (g["start"], g["end"]), book, t0, t1)
        out.append({"key": g["key"], "title": g["title"], "project": g["project"], "item_ids": g["item_ids"],
                    "start": g["start"], "end": g["end"], "hours": round(_hours(g["start"], g["end"]), 1),
                    "units": sorted(({"id": u, "label": labels.get(u) or u} for u in g["units"]), key=lambda x: x["label"]),
                    "areas": sorted(({"id": a, "label": labels.get(a) or a} for a in g["areas"]), key=lambda x: x["label"]),
                    "hits": hits, "volume_bbl": _sum(h["volume_bbl"] for h in hits), "usd": _sum(h["usd"] for h in hits)})
    return {"start": t0, "end": t1, "days": days, "contracts": len(book), "outages": out, "basis": BASIS}
