"""The work board (#99): all outstanding work in one list, ranked by value (#100), for people and agents.

Cards come from what already exists, mirrored, not copied as truth:
- the #100 items: incidents (open, or recent ones as the cost of a repeat), open SAP work orders, PM and
  inspection plans due or overdue, and opportunities;
- SRE upsets that were handed off with a priority (#96);
- open work items in the workflow (#97), such as IOW breaches and upsets;
- Fuzer's own reviews (open match and conflict questions), one card per queue;
- projects created on the board (`board_project`).

One card per real piece of work: records joined by an explicit link are one card, its value the
highest of them. The links are an incident listing an upset, an upset's work item, and a work order
raised for a notification that an incident includes. People can pin a card's rank or value with a
reason; pins live in `board_pin` by record key, so they survive refreshes and rebuilds. Each refresh keeps
the rank history and says why a card moved.
"""

from collections import defaultdict
from datetime import datetime

from psycopg.types.json import Jsonb

from ..core.jsonsafe import plain as _plain

PAGE = 50


# ---- cards from each source -----------------------------------------------------------------------

def _value_cards(conn, pack) -> list[dict]:
    from ..analysis import value

    cfg = value.config(pack)
    out = []
    for item in value.items(conn, pack):
        s = value.score(item, cfg)
        out.append({"key": f"{s['kind']}:{s['id']}", "kind": s["kind"], "title": s["label"] or s["id"], "text": s["text"],
                    "unit_id": s["tag_id"], "value_usd": s["value_usd"], "reason": s["reason"],
                    "breakdown": {k: s[k] for k in ("components", "urgency", "effort", "unknown", "value_per_effort")}})
    return out


def _upset_cards(conn) -> list[dict]:
    rows = conn.execute(
        """
        select u.id, u.start, u."end", u.first_unit, u.priority_usd, u.detections, d.key, p.label as point
        from upset u left join detection d on d.id = u.first_detection left join entity_label p on p.fuzer_id = d.point_id
        where u.kind = 'process' and u.status in ('detected', 'confirmed') and u.priority_usd is not null
        """).fetchall()
    return [{"key": f"upset:{r['id']}", "kind": "upset", "title": f"Upset: {r['key']} on {r['point']}",
             "text": f"{r['detections']} detections from {r['start']:%Y-%m-%d %H:%M}" + (", still going" if r["end"] is None else ""),
             "unit_id": r["first_unit"], "value_usd": r["priority_usd"], "reason": "SRE upset priority (#100)",
             "breakdown": {}} for r in rows]


def _work_item_cards(conn, pack) -> list[dict]:
    """Open workflow items. Their priority is a #100 value for upsets; IOW breaches are valued as
    compliance (#100 compliance rate for IOWs for each day outside past the allowed time)."""
    from ..analysis import value

    cfg = value.config(pack)
    rate = cfg["compliance"]["usd_per_day_overdue"].get("IOW", 0)
    out = []
    for r in conn.execute("select id, type, title, subjects, priority, payload from work_item where state = 'open'"):
        v = r["priority"]
        if r["type"] == "iow_breach":
            hours = (r["payload"] or {}).get("hours_outside") or 0
            v = round(rate * max(1.0, hours / 24) * (r["priority"] or 1))
        out.append({"key": f"work_item:{r['id']}", "kind": r["type"], "title": r["title"], "text": None,
                    "unit_id": next((s for s in r["subjects"] if not s.startswith("fz-timeseries")), None),
                    "value_usd": v, "reason": f"work item {r['id']} ({r['type']})", "breakdown": {}, "item_id": r["id"]})
    return out


def _review_cards(conn) -> list[dict]:
    return [{"key": f"review:{r['kind']}", "kind": "review", "title": f"{r['n']} open {r['kind']} reviews",
             "text": "Questions about the register for a person to decide", "unit_id": None, "value_usd": None,
             "reason": "not valued: data quality work", "breakdown": {}}
            for r in conn.execute("select kind, count(*) as n from review_item where status = 'open' group by kind")]


def _project_cards(conn) -> list[dict]:
    return [{"key": f"project:{r['id']}", "kind": "project", "title": r["title"], "text": r["scope"], "unit_id": r["unit_id"],
             "value_usd": r["benefit_usd"], "reason": f"expected benefit {r['benefit_usd']:,.0f}" if r["benefit_usd"] else
             "no expected benefit given", "breakdown": {"owner": r["owner"], "due": r["due"], "effort_usd": r["effort_usd"]}}
            for r in conn.execute("select * from board_project where status = 'open'")]


def add_project(conn, title: str, by: str, unit_id: str | None = None, owner: str | None = None, due: str | None = None,
                benefit_usd: float | None = None, effort_usd: float | None = None, scope: str | None = None) -> dict:
    row = conn.execute(
        "insert into board_project (title, unit_id, owner, due, benefit_usd, effort_usd, scope, created_by) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s) returning *", (title, unit_id, owner, due, benefit_usd, effort_usd, scope, by)
    ).fetchone()
    conn.commit()
    return dict(row)


# ---- one card per piece of work ---------------------------------------------------------------------

def _links(conn, keys: set[str]) -> list[tuple[str, str]]:
    out = []
    for r in conn.execute("select e.fuzer_id, e.value from golden e where e.field = 'upsets'"):
        for u in r["value"] or []:
            out.append((f"incident:{r['fuzer_id']}", f"upset:{u}"))
    for r in conn.execute("select id, work_item_id from upset where work_item_id is not null"):
        out.append((f"upset:{r['id']}", f"work_item:{r['work_item_id']}"))
    for r in conn.execute(
        """
        select wo.src as wo, inc.src as inc from edge wo join edge inc on inc.dst = wo.dst and inc.rel = 'includes'
        where wo.rel = 'for_notification'
        """):
        out.append((f"work_order:{r['wo']}", f"incident:{r['inc']}"))
    for r in conn.execute("select id, payload ->> 'opportunity' as o from work_item where type = 'opportunity' and state = 'open'"):
        out.append((f"work_item:{r['id']}", f"opportunity:scan:{r['o']}"))
    return [(a, b) for a, b in out if a in keys and b in keys]


def merge(cards: list[dict], links: list[tuple[str, str]]) -> list[dict]:
    """Cards joined by links become one: the highest-valued leads, the rest are its records."""
    parent = {c["key"]: c["key"] for c in cards}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for a, b in links:
        parent[find(a)] = find(b)
    groups = defaultdict(list)
    for c in cards:
        groups[find(c["key"])].append(c)
    out = []
    for members in groups.values():
        members.sort(key=lambda c: -(c["value_usd"] or 0))
        lead = dict(members[0])
        lead["records"] = [c["key"] for c in members]
        lead["unit_id"] = lead["unit_id"] or next((c["unit_id"] for c in members if c["unit_id"]), None)
        lead["item_id"] = next((c.get("item_id") for c in members if c.get("item_id")), None)
        out.append(lead)
    return out


# ---- ranking and refresh ----------------------------------------------------------------------------

def rank(cards: list[dict], pins: dict[str, dict]) -> list[dict]:
    """Highest value first (a pinned value replaces the computed one; unvalued cards last), then cards
    with a pinned rank moved to that place."""
    for c in cards:
        pin = next((pins[k] for k in c["records"] if k in pins), None)
        c["pin"] = pin
        c["effective_usd"] = pin["value_usd"] if pin and pin["value_usd"] is not None else c["value_usd"]
    order = sorted(cards, key=lambda c: (c["effective_usd"] is None, -(c["effective_usd"] or 0), c["key"]))
    fixed = sorted((c for c in order if c["pin"] and c["pin"]["rank"]), key=lambda c: c["pin"]["rank"])
    rest = [c for c in order if c not in fixed]
    for c in fixed:
        rest.insert(min(c["pin"]["rank"] - 1, len(rest)), c)
    for i, c in enumerate(rest, 1):
        c["rank"] = i
    return rest


def refresh(conn, pack) -> dict:
    """Rebuild the board from its sources, keep each card's rank history and say why it moved."""
    cards = _value_cards(conn, pack) + _upset_cards(conn) + _work_item_cards(conn, pack) + _review_cards(conn) \
        + _project_cards(conn)
    cards = merge(cards, _links(conn, {c["key"] for c in cards}))
    pins = {r["key"]: dict(r) for r in conn.execute("select * from board_pin")}
    cards = rank(cards, pins)
    before = {r["key"]: r for r in conn.execute("select key, rank, value_usd, first_seen from board_card")}
    areas = {r["src"]: r["dst"] for r in conn.execute(
        "select e.src, e.dst from edge e join entity a on a.fuzer_id = e.dst and a.kind = 'area' where e.rel = 'part_of'")}
    now = datetime.now()
    conn.execute("delete from board_card")
    moved = 0
    for c in cards:
        b = before.get(c["key"])
        why = _why(c, b)
        moved += bool(why)
        conn.execute(
            """
            insert into board_card (key, kind, title, text, unit_id, area_id, records, value_usd, effective_usd, reason,
                                    breakdown, rank, prev_rank, moved, pinned, item_id, first_seen, updated_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (c["key"], c["kind"], c["title"], c["text"], c["unit_id"], areas.get(c["unit_id"]), c["records"],
                  c["value_usd"], c["effective_usd"], c["reason"], Jsonb(_plain(c["breakdown"])), c["rank"],
                  b["rank"] if b else None, why, bool(c["pin"]), c["item_id"], b["first_seen"] if b else now, now))
        if not b or b["rank"] != c["rank"]:
            conn.execute("insert into board_history (key, rank, value_usd) values (%s, %s, %s)",
                         (c["key"], c["rank"], c["effective_usd"]))
    conn.commit()
    return {"cards": len(cards), "new": sum(1 for c in cards if c["key"] not in before),
            "gone": len(set(before) - {c["key"] for c in cards}), "moved": moved,
            "by_kind": dict(sorted(_count(c["kind"] for c in cards).items()))}


def _why(c: dict, b) -> str | None:
    if b is None:
        return "new"
    if b["rank"] == c["rank"]:
        return None
    d = b["rank"] - c["rank"]
    v0, v1 = b["value_usd"], c["effective_usd"]
    because = f": value {v0:,.0f} -> {v1:,.0f}" if v0 is not None and v1 is not None and v0 != v1 else \
        ": pinned" if c["pin"] else ": others moved"
    return f"{'up' if d > 0 else 'down'} {abs(d)} place{'s' if abs(d) > 1 else ''}{because}"


def _count(xs) -> dict:
    out: dict = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def pin(conn, key: str, by: str, reason: str, rank: int | None = None, value_usd: float | None = None) -> dict:
    """Pin a card (by any of its record keys) to a rank or a value, with a reason; None for both unpins."""
    if not reason and (rank or value_usd is not None):
        raise ValueError("a pin needs a reason")
    if rank is None and value_usd is None:
        conn.execute("delete from board_pin where key = %s", (key,))
    else:
        conn.execute(
            "insert into board_pin (key, rank, value_usd, reason, pinned_by) values (%s, %s, %s, %s, %s) "
            "on conflict (key) do update set rank = excluded.rank, value_usd = excluded.value_usd, "
            "reason = excluded.reason, pinned_by = excluded.pinned_by, pinned_at = now()",
            (key, rank, value_usd, reason, by))
    conn.commit()
    return {"key": key, "rank": rank, "value_usd": value_usd, "reason": reason}


# ---- reading it -----------------------------------------------------------------------------------

OWNER = "coalesce(w.assignee, w.role, 'unassigned')"


def cards(conn, kind: str | None = None, area: str | None = None, unit: str | None = None, text: str | None = None,
          since: str | None = None, page: int = 1, per_page: int = PAGE, owner: str | None = None,
          facets: bool = False) -> dict:
    """One page of the board in rank order, filtered by kind, area, unit, owner (the person or role its
    work item is with), words in the title, or new since a time. `facets`: counts by kind, area and owner
    under the same filters, for the views."""
    where = ["(%(kind)s::text is null or c.kind = %(kind)s)", "(%(area)s::text is null or c.area_id = %(area)s)",
             "(%(unit)s::text is null or c.unit_id = %(unit)s)", "(%(text)s::text is null or c.title ilike %(like)s)",
             "(%(since)s::timestamp is null or c.first_seen >= %(since)s::timestamp)",
             f"(%(owner)s::text is null or {OWNER} = %(owner)s)"]
    params = {"kind": kind, "area": area, "unit": unit, "text": text, "like": f"%{text}%", "since": since, "owner": owner,
              "limit": per_page, "offset": (page - 1) * per_page}
    base = f"from board_card c left join work_item w on w.id = c.item_id where {' and '.join(where)}"
    total = conn.execute(f"select count(*) as n {base}", params).fetchone()["n"]
    rows = conn.execute(
        f"""
        select c.*, l.label as unit, a.label as area, {OWNER} as owner, p.reason as pin_reason, p.pinned_by
        from board_card c left join work_item w on w.id = c.item_id
        left join entity_label l on l.fuzer_id = c.unit_id left join entity_label a on a.fuzer_id = c.area_id
        left join lateral (select reason, pinned_by from board_pin where key = any(c.records) limit 1) p on true
        where {' and '.join(where)} order by c.rank limit %(limit)s offset %(offset)s
        """, params).fetchall()
    out = {"total": total, "page": page, "per_page": per_page, "cards": [_plain(dict(r)) for r in rows]}
    if facets:
        out["facets"] = {
            "kind": {r["k"]: r["n"] for r in conn.execute(f"select c.kind as k, count(*) as n {base} group by 1 order by 2 desc", params)},
            "owner": {r["k"]: r["n"] for r in conn.execute(f"select {OWNER} as k, count(*) as n {base} group by 1 order by 2 desc", params)},
            "area": [dict(r) for r in conn.execute(
                f"select c.area_id as id, (select label from entity_label where fuzer_id = c.area_id) as label, count(*) as n "
                f"{base} group by 1 order by 3 desc", params)]}
    return out


def history(conn, key: str) -> list[dict]:
    return [_plain(dict(r)) for r in conn.execute(
        "select rank, value_usd, at from board_history where key = %s order by at", (key,))]


def next_work(conn, role: str, limit: int = 5) -> list[dict]:
    """The steps an agent role can take now, in board order (their work item's rank)."""
    from ..workflow import engine

    steps = engine.agent_work(conn, role, limit=500)
    ranks = {r["item_id"]: r["rank"] for r in conn.execute("select item_id, rank from board_card where item_id is not null")}
    steps.sort(key=lambda t: ranks.get(t.get("item_id"), 10 ** 9))
    return [_plain({**t, "board_rank": ranks.get(t.get("item_id"))}) for t in steps[:limit]]
