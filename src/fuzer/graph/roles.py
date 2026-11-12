"""Measurement roles on the register (P1.4): each historian point's role on the asset it measures, proposed with every
rebuild from its description, instrument letters, units and the equipment it hangs on (`core.roles.propose`).

`measurement_role` holds one row per point: the role, the asset it is a role of (the equipment or well, not the
instrument), the confidence and the evidence. Detectors read `on(conn, asset, role)` instead of matching tag names
or descriptions.
"""

import json

from ..core import db
from ..core import roles as vocabulary

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


def build(conn, pack) -> dict:
    """Propose a role for every point and land them in measurement_role."""
    rows, unsure = [], 0
    for e in evidence(conn, pack):
        r = vocabulary.propose(e["description"], e["letters"], e["uom"], e["classes"])
        if r["role"] is None:
            unsure += 1
            continue
        unsure += r["confidence"] < vocabulary.CONFIDENT
        rows.append((e["id"], r["role"], e["asset"], r["confidence"], json.dumps(r["basis"])))
    sync = db.sync_table(conn, "measurement_role", COLS[:1], COLS[1:], rows)
    return {"roles": len(rows), "roles_unsure": unsure,
            "role_changes": {k: v for k, v in sync.items() if k != "rows"}}


def on(conn, asset: str, role: str) -> list[str]:
    """The points that are `role` on `asset` (best proposal first)."""
    return [r["point_id"] for r in conn.execute(
        "select point_id from measurement_role where asset = %s and role = %s order by confidence desc, point_id",
        (asset, role))]


def of(conn, point_ids) -> dict[str, str]:
    """Each point's role."""
    return {r["point_id"]: r["role"] for r in conn.execute(
        "select point_id, role from measurement_role where point_id = any(%s)", (list(point_ids),))}
