"""Outages on the plan and the customers they hit (P5.22, #1253): each planned outage over a horizon with the plant
it takes down, the customers' contracts it hits there, and what they lose in volume and dollars. An estimate for
planners, not a nomination or a commitment.

    contracts(conn)              the customer and contract register (the contracts source): each contract, its
                                 customer, its delivery point, its daily volume and rate, and the dates it runs
    outages(conn, start, days)   the outages in [start, start + days), each with what it takes down and the contracts
                                 it hits, the volume and dollars each loses, the crews and people on it, the
                                 overloads it is in and the planner's rules its jobs break; and the same outages by
                                 asset (the areas its jobs are in and the units it takes down), by person (each crew
                                 and person booked) and by customer, a customer hit by more than one outage flagged

Read from the records, never from the asset type:
    an outage            a schedule whose activities need the plant down is one outage, from its first start to its
                         last finish; any other plan item that needs a shutdown (or is one) is one on its own
    what it takes down   the flow units its jobs take down, as Production at risk reads them (at_risk._units)
    a contract it hits   one whose delivery point is a unit it takes down, or loses every feed because of it, while
                         the contract runs
    what it loses        the contract's daily volume for the hours it is hit, at the contract's rate; where two
                         outages hit one contract at once, by customer counts the hours once
    hit more than once   a customer hit at two or more separate times (outages that overlap are one time for it)
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from . import at_risk, plan_rules, schedule
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


def _union(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[list[datetime]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def by_customer(outs: list[dict], book: list[dict]) -> list[dict]:
    """Each customer an outage hits: the outages, how many separate times it is hit, and what it loses over the
    horizon, each contract's hours counted once however many outages hit it at once."""
    terms = {c["key"]: c for c in book}
    spans: dict[str, list] = defaultdict(list)
    who: dict[str, dict] = {}
    for o in outs:
        for h in o["hits"]:
            spans[h["contract"]].append((h["start"], h["end"]))
            w = who.setdefault(h["customer"] or h["contract"], {"outages": [], "contracts": set()})
            if o["key"] not in w["outages"]:
                w["outages"].append(o["key"])
            w["contracts"].add(h["contract"])
    out = []
    for name, w in who.items():
        cs = sorted(w["contracts"])
        losses = [_loss(terms[k], sum(_hours(a, b) for a, b in _union(spans[k]))) for k in cs]
        times = len(_union([x for k in cs for x in spans[k]]))
        out.append({"customer": name, "customer_type": terms[cs[0]]["customer_type"], "contracts": cs,
                    "outages": w["outages"], "times": times, "flagged": times > 1,
                    "volume_bbl": _sum(v for v, _ in losses), "usd": _sum(u for _, u in losses)})
    return sorted(out, key=lambda x: (not x["flagged"], -(x["usd"] or 0), x["customer"]))


def _crews(conn, ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    for r in conn.execute("select x.item_id, r.code, r.name from plan_demand_all x join plan_resource r on r.code = "
                          "x.resource where x.item_id = any(%s) and r.kind = 'crew' order by r.code", (ids,)):
        out[r["item_id"]].append({"code": r["code"], "name": r["name"]})
    return out


def outages(conn, start=None, days: int = HORIZON_DAYS) -> dict:
    """The outages in [start, start + days), each with the plant it takes down and the contracts it hits, what each
    loses (volume in bbl and dollars, for the hours it is hit), the crews and people on it, the overloads its jobs
    are in and the planner's rules they break; and the same outages by asset, by person and by customer."""
    t0 = _when(start) if start else datetime.now(UTC)
    t1 = t0 + timedelta(days=days)
    book = contracts(conn)
    groups = _group(conn, _rows(conn, t0, t1))
    ids = [i for g in groups for i in g["item_ids"]]
    crews = _crews(conn, ids)
    over = schedule.over_capacity(conn, t0, t1) if ids else {}
    broken = plan_rules.breaks(conn) if ids else {}
    labels = at_risk._labels(conn, {u for g in groups for u in g["units"] | g["areas"]})
    named = lambda xs: sorted(({"id": u, "label": labels.get(u) or u} for u in xs), key=lambda x: x["label"])
    out = []
    for g in groups:
        hits = _hits(g, (g["start"], g["end"]), book, t0, t1)
        team = {c["code"]: c for i in g["item_ids"] for c in crews.get(i, [])}
        loads = {o["key"]: o for i in g["item_ids"] for o in over.get(i, [])}
        out.append({"key": g["key"], "title": g["title"], "project": g["project"], "item_ids": g["item_ids"],
                    "start": g["start"], "end": g["end"], "hours": round(_hours(g["start"], g["end"]), 1),
                    "units": named(g["units"]), "areas": named(g["areas"]),
                    "crews": sorted(team.values(), key=lambda c: c["code"]), "people": sorted(g["people"]),
                    "over_capacity": sorted(loads.values(), key=lambda o: (o["from"], o["resource"])),
                    "breaks": [b for i in g["item_ids"] for b in broken.get(i, [])],
                    "hits": hits, "volume_bbl": _sum(h["volume_bbl"] for h in hits), "usd": _sum(h["usd"] for h in hits)})
    assets: dict[str, dict] = {}
    kinds = {r["fuzer_id"]: r["kind"] for r in conn.execute("select fuzer_id, kind from entity where fuzer_id = any(%s)",
                                                            (list(labels),))} if labels else {}
    for o in out:
        for x in o["areas"] + o["units"]:
            assets.setdefault(x["id"], {**x, "kind": kinds.get(x["id"]), "outages": []})["outages"].append(o["key"])
    people: dict[str, dict] = {}
    for o in out:
        for c in o["crews"]:
            p = people.setdefault(c["code"], {"who": c["code"], "name": c["name"], "crew": True, "outages": [],
                                              "over_capacity": []})
            p["outages"].append(o["key"])
            p["over_capacity"] += [x for x in o["over_capacity"] if x["resource"] == c["code"]]
        for who in o["people"]:
            people.setdefault(who, {"who": who, "name": who, "crew": False, "outages": [], "over_capacity": []}
                              )["outages"].append(o["key"])
    return {"start": t0, "end": t1, "days": days, "contracts": len(book), "outages": out,
            "by_asset": sorted(assets.values(), key=lambda a: (a["kind"] != "area", a["label"])),
            "by_person": sorted(people.values(), key=lambda p: (not p["crew"], p["name"])),
            "by_customer": by_customer(out, book), "basis": BASIS}
