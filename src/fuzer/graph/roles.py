"""Measurement roles on the register (P1.4): each historian point's role on the asset it measures, proposed with every
rebuild from its description, instrument letters, units and the equipment it hangs on (`core.roles.propose`).

`measurement_role` holds one row per point: the role, the asset it is a role of (the equipment or well, not the
instrument), the confidence and the evidence. Detectors read `on(conn, asset, role)` instead of matching tag names
or descriptions.

A proposal below `core.roles.CONFIDENT` becomes a Review item (kind `role`, P1.4b). The steward's answer is pinned in
`measurement_role_pin` (`confirm`) and wins over the proposal on every rebuild.
"""

import json

from ..core import db
from ..core import roles as vocabulary
from .resolve import replace_open_reviews

COLS = ["point_id", "role", "asset", "confidence", "basis"]


def _points(conn) -> list[dict]:
    """Every historian point with its description, units and what it hangs on (with each one's class)."""
    return conn.execute(
        """
        select t.dst as id, l.label as point, gd.value #>> '{}' as description, gu.value #>> '{}' as uom,
               array_agg(json_build_object('id', t.src, 'kind', se.kind, 'label', sl.label,
                                           'class', (select cl.label from edge c join entity_label cl on cl.fuzer_id = c.dst
                                                     where c.src = t.src and c.rel = 'classified_as' order by 1 limit 1))
                         order by t.src) as on_
        from edge t join entity e on e.fuzer_id = t.dst and e.kind = 'timeseries'
        join entity se on se.fuzer_id = t.src
        left join entity_label l on l.fuzer_id = t.dst
        left join entity_label sl on sl.fuzer_id = t.src
        left join golden gd on gd.fuzer_id = t.dst and gd.field = 'description'
        left join golden gu on gu.fuzer_id = t.dst and gu.field = 'uom'
        where t.rel = 'has_timeseries'
        group by t.dst, l.label, gd.value, gu.value
        """).fetchall()


def _code(cls: str | None) -> str | None:
    """ISO14224:CO.CE -> CO."""
    return cls.split(":", 1)[-1].split(".", 1)[0] if cls else None


def evidence(conn, pack) -> list[dict]:
    """What each point says about itself: its description, letters, units, the asset it measures and that asset's
    class codes."""
    grammar = getattr(pack, "tags", None)
    out = []
    for p in _points(conn):
        on = p["on_"]
        instruments = [o for o in on if _code(o["class"]) == "IP"]
        assets = [o for o in on if o not in instruments]
        asset = (assets or instruments or [None])[0]
        letters = next((x for o in instruments if (x := vocabulary.letters_in(o["label"], grammar))), None) \
            or vocabulary.letters_in(p["point"], grammar)
        classes = [c for o in assets if (c := ("WELL" if o["kind"] == "well" else _code(o["class"])))]
        out.append({"id": p["id"], "point": p["point"], "description": p["description"], "uom": p["uom"],
                    "letters": letters, "asset": asset["id"] if asset else None, "classes": classes})
    return out


def _choices(quantity: str | None) -> list[dict]:
    """Every role, those of the proposal's quantity first: what the steward picks from."""
    return [{"key": r.key, "label": r.label} for r in sorted(vocabulary.ROLES.values(),
                                                           key=lambda r: r.quantity != quantity)]


def build(conn, pack) -> dict:
    """Propose a role for every point, keep the ones a steward pinned, land them in measurement_role, and ask a
    steward about the rest of the uncertain ones."""
    pins = {r["point_id"]: r for r in conn.execute("select * from measurement_role_pin")}
    rows, ask = [], []
    for e in evidence(conn, pack):
        if pin := pins.get(e["id"]):
            rows.append((e["id"], pin["role"], e["asset"], 1.0, json.dumps([f"confirmed by {pin['decided_by']}"])))
            continue
        r = vocabulary.propose(e["description"], e["letters"], e["uom"], e["classes"])
        if r["role"] is not None:
            rows.append((e["id"], r["role"], e["asset"], r["confidence"], json.dumps(r["basis"])))
        if r["role"] is None or r["confidence"] < vocabulary.CONFIDENT:
            role = vocabulary.ROLES.get(r["role"])
            ask.append((e["id"], {
                "point_id": e["id"], "point": e["point"], "description": e["description"], "uom": e["uom"],
                "letters": e["letters"], "asset": e["asset"],
                "proposed": r["role"], "proposed_label": role.label if role else None,
                "confidence": r["confidence"], "basis": r["basis"],
                "choices": _choices(role.quantity if role else None)}))
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", ([q["asset"] for _, q in ask],))}
    for _, q in ask:
        q["asset_label"] = labels.get(q["asset"])
    sync = db.sync_table(conn, "measurement_role", COLS[:1], COLS[1:], rows)
    replace_open_reviews(conn, "role", ask)
    return {"roles": len(rows), "roles_unsure": len(ask), "roles_confirmed": len(pins),
            "role_changes": {k: v for k, v in sync.items() if k != "rows"}}


def confirm(conn, point_id: str, role: str, by: str) -> None:
    """A steward's role for a point: pinned for every rebuild, and live now."""
    if role not in vocabulary.ROLES:
        raise ValueError(f"{role!r} isn't a measurement role")
    conn.execute(
        """
        insert into measurement_role_pin (point_id, role, decided_by) values (%s, %s, %s)
        on conflict (point_id) do update set role = excluded.role, decided_by = excluded.decided_by, decided_at = now()
        """, (point_id, role, by))
    conn.execute("update measurement_role set role = %s, confidence = 1.0, basis = %s where point_id = %s",
                 (role, json.dumps([f"confirmed by {by}"]), point_id))


def on(conn, asset: str, role: str) -> list[str]:
    """The points that are `role` on `asset` (best proposal first)."""
    return [r["point_id"] for r in conn.execute(
        "select point_id from measurement_role where asset = %s and role = %s order by confidence desc, point_id",
        (asset, role))]


def of(conn, point_ids) -> dict[str, str]:
    """Each point's role."""
    return {r["point_id"]: r["role"] for r in conn.execute(
        "select point_id, role from measurement_role where point_id = any(%s)", (list(point_ids),))}
