"""Events, maintenance history and spare parts for a unit."""

from collections import defaultdict


def events(conn, fuzer_id: str | None = None, since: str | None = None, until: str | None = None,
           event_type: str | None = None, limit: int = 100) -> list[dict]:
    """Events read from the operator logs, newest first: facility-wide, or those involving one
    entity (or reported by one log entry). since / until are ISO dates or timestamps."""
    rows = conn.execute(
        """
        select * from (
            select en.fuzer_id as id, jsonb_object_agg(g.field, g.value) as fields
            from entity en join golden g using (fuzer_id)
            where en.kind = 'event' and en.merged_into is null and en.retired_at is null
              and (%(id)s::text is null or en.fuzer_id in (
                    select src from edge where rel = 'involves' and dst = %(id)s
                    union select dst from edge where rel = 'reports' and src = %(id)s))
            group by en.fuzer_id
        ) x
        where (%(type)s::text is null or fields->>'event_type' = %(type)s)
          and (%(since)s::text is null or fields->>'timestamp' >= %(since)s)
          and (%(until)s::text is null or fields->>'timestamp' < %(until)s)
        order by fields->>'timestamp' desc nulls last, id
        limit %(limit)s
        """,
        {"id": fuzer_id, "type": event_type, "since": since, "until": until, "limit": limit},
    ).fetchall()
    ids = [r["id"] for r in rows]
    links = conn.execute(
        """
        select e.rel, e.src, e.dst, e.props->>'role' as role,
               o.fuzer_id as other, o.kind, l.label, g.value #>> '{}' as text
        from edge e
        join entity o on o.fuzer_id = case when e.rel = 'involves' then e.dst else e.src end
        left join entity_label l on l.fuzer_id = o.fuzer_id
        left join golden g on g.fuzer_id = o.fuzer_id and g.field = 'text'
        where (e.rel = 'involves' and e.src = any(%(ids)s)) or (e.rel = 'reports' and e.dst = any(%(ids)s))
        """,
        {"ids": ids},
    ).fetchall()
    involves, logs = defaultdict(list), {}
    for link in links:
        if link["rel"] == "involves":
            involves[link["src"]].append({"id": link["other"], "label": link["label"], "kind": link["kind"],
                                          "role": link["role"]})
        else:
            logs[link["dst"]] = {"id": link["other"], "label": link["label"], "text": link["text"]}
    out = []
    for r in rows:
        f = dict(r["fields"])
        out.append({
            "id": r["id"], "type": f.pop("event_type", None), "summary": f.pop("summary", None),
            "timestamp": f.pop("timestamp", None), "details": {k: v for k, v in f.items() if k != "description"},
            "involves": involves[r["id"]], "log": logs.get(r["id"]),
        })
    return out


def maintenance(conn, fuzer_id: str, since: str | None = None, limit: int = 50) -> dict:
    """Notifications (with their work orders) for a tag, a piece of equipment, every instance of a
    model, or every member of a class including subtypes; plus counts by ISO 14224 failure mode."""
    params = {"id": fuzer_id, "since": since, "limit": limit}
    scope = """
        with recursive classes(id) as (
            select %(id)s::text
            union
            select e.src from edge e join classes c on e.dst = c.id
            where e.rel = 'is_a' and e.src in (select fuzer_id from entity where kind = 'equipment_class')
        ), targets as (
            select %(id)s::text as id
            union select src from edge where rel = 'instance_of' and dst = %(id)s
            union select src from edge where rel = 'classified_as' and dst in (select id from classes)
        ), notifs as (
            select distinct e.src as id
            from edge e join entity en on en.fuzer_id = e.src and en.kind = 'notification' and en.retired_at is null
            where e.rel = 'concerns' and e.dst in (select id from targets)
        ), n as (
            select notifs.id, jsonb_object_agg(g.field, g.value) as f
            from notifs join golden g on g.fuzer_id = notifs.id
            group by notifs.id
        )
    """
    since_clause = "where (%(since)s::text is null or n.f->>'created' >= %(since)s)"
    modes = conn.execute(scope + f"""
        select n.f->>'failure_mode' as code, n.f->>'failure_mode_name' as name, count(*) as count
        from n {since_clause}
        group by 1, 2 order by count desc, code
    """, params).fetchall()
    recent = conn.execute(scope + f"""
        select n.id, n.f from n {since_clause}
        order by n.f->>'created' desc nulls last, n.id
        limit %(limit)s
    """, params).fetchall()
    ids = [r["id"] for r in recent]
    orders = defaultdict(list)
    for r in conn.execute(
        """
        select e.dst as notification, w.fuzer_id as id, l.label, jsonb_object_agg(g.field, g.value) as f
        from edge e
        join entity w on w.fuzer_id = e.src
        left join entity_label l on l.fuzer_id = w.fuzer_id
        join golden g on g.fuzer_id = w.fuzer_id
        where e.rel = 'for_notification' and e.dst = any(%s)
        group by e.dst, w.fuzer_id, l.label
        """,
        (ids,),
    ):
        f = r["f"]
        orders[r["notification"]].append({"id": r["id"], "label": r["label"], "order_type": f.get("order_type"),
                                          "activity": f.get("activity_name"), "start": f.get("start"),
                                          "finish": f.get("finish"), "cost": f.get("cost"), "text": f.get("text")})
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (ids,))}
    return {
        "total": sum(m["count"] for m in modes),
        "failure_modes": modes,
        "recent": [{"id": r["id"], "label": labels.get(r["id"]), "created": r["f"].get("created"),
                    "type": r["f"].get("notif_type"), "priority": r["f"].get("priority"),
                    "failure_mode": r["f"].get("failure_mode"), "failure_mode_name": r["f"].get("failure_mode_name"),
                    "failure_mechanism_name": r["f"].get("failure_mechanism_name"),
                    "text": r["f"].get("short_text"), "work_orders": orders[r["id"]]} for r in recent],
    }


def spares(conn, fuzer_id: str) -> list[dict]:
    """Spare parts for a tag (via what's installed), a piece of equipment or every instance of a model:
    each material with stock, storage bin, how many units use it and the manual sections that name it."""
    return conn.execute(
        """
        with units as (
            select %(id)s::text as id
            union select src from edge where rel = 'installed_at' and dst = %(id)s and valid_to is null
            union select src from edge where rel = 'instance_of' and dst = %(id)s
        ), parts as (
            select b.dst as material, count(distinct b.src) as used_by
            from edge b where b.rel = 'uses_part' and b.src in (select id from units)
            group by b.dst
        )
        select p.material as fuzer_id, l.label, p.used_by,
               gd.value #>> '{}' as description, gm.value #>> '{}' as mpn,
               (gs.value #>> '{}')::numeric as stock, gb.value #>> '{}' as bin,
               (select coalesce(jsonb_agg(jsonb_build_object('id', s.fuzer_id, 'label', sl.label)), '[]')
                from edge m join entity s on s.fuzer_id = m.src and s.kind = 'document_section'
                left join entity_label sl on sl.fuzer_id = s.fuzer_id
                where m.dst = p.material and m.rel = 'mentions') as documents
        from parts p
        left join entity_label l on l.fuzer_id = p.material
        left join golden gd on gd.fuzer_id = p.material and gd.field = 'description'
        left join golden gm on gm.fuzer_id = p.material and gm.field = 'mpn'
        left join golden gs on gs.fuzer_id = p.material and gs.field = 'stock'
        left join golden gb on gb.fuzer_id = p.material and gb.field = 'bin'
        order by l.label
        """,
        {"id": fuzer_id},
    ).fetchall()
