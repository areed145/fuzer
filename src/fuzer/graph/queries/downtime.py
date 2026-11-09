"""Downtime (EC deferment) and incidents: lists, rankings, reason checks, timelines and similar incidents."""

from collections import defaultdict

_DOWNTIME = """
    with recursive classes(id) as (
        select %(id)s::text
        union
        select e.src from edge e join classes c on e.dst = c.id
        where e.rel = 'is_a' and e.src in (select fuzer_id from entity where kind = 'equipment_class')
    ), targets as (
        select %(id)s::text as id
        union select src from edge where rel = 'instance_of' and dst = %(id)s
        union select src from edge where rel = 'classified_as' and dst in (select id from classes)
    ), scoped as (
        select en.fuzer_id as id from entity en
        where en.kind = 'downtime' and en.retired_at is null
          and (%(id)s::text is null or exists (
                select 1 from edge e where e.src = en.fuzer_id and e.rel in ('affects', 'caused_by')
                  and e.dst in (select id from targets)))
    ), d as (
        select s.id, jsonb_object_agg(g.field, g.value) as f
        from scoped s join golden g on g.fuzer_id = s.id
        group by s.id
    ), dd as (
        select * from d
        where (%(since)s::text is null or f->>'start' >= %(since)s)
          and (%(until)s::text is null or f->>'start' < %(until)s)
    )
"""


def downtime(conn, fuzer_id: str | None = None, since: str | None = None, until: str | None = None,
             limit: int = 50) -> dict:
    """Downtime affecting a well or the facility, or caused by a tag / equipment / model / class."""
    params = {"id": fuzer_id, "since": since, "until": until, "limit": limit}
    totals = conn.execute(_DOWNTIME + """
        select count(*) as events, coalesce(sum((f->>'lost_oil_sm3')::numeric), 0) as lost_oil_sm3,
               coalesce(sum((f->>'duration_h')::numeric), 0) as hours
        from dd
    """, params).fetchone()
    rows = conn.execute(_DOWNTIME + """
        select dd.id, dd.f, l.label from dd left join entity_label l on l.fuzer_id = dd.id
        order by dd.f->>'start' desc, dd.id limit %(limit)s
    """, params).fetchall()
    ids = [r["id"] for r in rows]
    links = defaultdict(lambda: {"affects": [], "caused_by": []})
    for r in conn.execute(
        """
        select e.src, e.rel, e.dst, l.label from edge e left join entity_label l on l.fuzer_id = e.dst
        where e.src = any(%s) and e.rel in ('affects', 'caused_by')
        """,
        (ids,),
    ):
        links[r["src"]][r["rel"]].append({"id": r["dst"], "label": r["label"]})
    return {
        "events": totals["events"], "lost_oil_sm3": float(totals["lost_oil_sm3"]), "hours": float(totals["hours"]),
        "recent": [{"id": r["id"], "label": r["label"], **{k: r["f"].get(k) for k in (
            "start", "end", "duration_h", "reason", "reason_category", "lost_oil_sm3", "comment", "reason_check")},
                    **links[r["id"]]} for r in rows],
    }


def downtime_ranking(conn, by: str = "tag", since: str | None = None, until: str | None = None,
                     limit: int = 20) -> list[dict]:
    """Lost oil ranked by causing tag, by the class of the causing tag, or by reason. Reasons with no loss are left
    out; equipment and class rankings end with an `unattributed` row for the loss that names no equipment (or no
    classified equipment), so each ranking accounts for the whole total (#403)."""
    params = {"id": None, "since": since, "until": until, "limit": limit}
    if by == "reason":
        sql = """
            select coalesce(f->>'reason', 'Unknown') as label, null::text as description, null::text as fuzer_id,
                   count(*) as events, sum((f->>'lost_oil_sm3')::numeric) as lost_oil_sm3,
                   sum((f->>'duration_h')::numeric) as hours
            from dd group by 1 having coalesce(sum((f->>'lost_oil_sm3')::numeric), 0) > 0
            order by lost_oil_sm3 desc nulls last limit %(limit)s
        """
    elif by in ("tag", "class"):
        via = ("join edge k on k.src = c.dst and k.rel = 'classified_as'" if by == "class" else "")
        target = "k.dst" if by == "class" else "c.dst"
        sql = f"""
            select l.label, gd.value #>> '{{}}' as description, {target} as fuzer_id, count(distinct dd.id) as events,
                   sum((dd.f->>'lost_oil_sm3')::numeric) as lost_oil_sm3, sum((dd.f->>'duration_h')::numeric) as hours
            from dd
            join edge c on c.src = dd.id and c.rel = 'caused_by'
            {via}
            left join entity_label l on l.fuzer_id = {target}
            left join golden gd on gd.fuzer_id = {target} and gd.field = 'description'
            group by l.label, gd.value, {target} order by lost_oil_sm3 desc nulls last limit %(limit)s
        """
    else:
        raise ValueError("by must be tag, class or reason")
    rows = [{**r, "lost_oil_sm3": float(r["lost_oil_sm3"] or 0), "hours": float(r["hours"] or 0)}
            for r in conn.execute(_DOWNTIME + sql, params)]
    if by in ("tag", "class"):
        named = ("exists (select 1 from edge c join edge k on k.src = c.dst and k.rel = 'classified_as' "
                 "where c.src = dd.id and c.rel = 'caused_by')" if by == "class" else
                 "exists (select 1 from edge c where c.src = dd.id and c.rel = 'caused_by')")
        rest = conn.execute(_DOWNTIME + f"""
            select count(*) as events, coalesce(sum((f->>'lost_oil_sm3')::numeric), 0) as lost_oil_sm3,
                   coalesce(sum((f->>'duration_h')::numeric), 0) as hours
            from dd where not {named}
        """, params).fetchone()
        if rest["lost_oil_sm3"] > 0:
            rows.append({"label": "No equipment named" if by == "tag" else "No classified equipment named",
                         "description": "Downtime whose record names no " + ("equipment" if by == "tag" else
                                                                               "equipment with a class"),
                         "fuzer_id": None, "events": rest["events"], "lost_oil_sm3": float(rest["lost_oil_sm3"]),
                         "hours": float(rest["hours"]), "unattributed": True})
    return rows


def downtime_reason_checks(conn, limit: int = 100) -> list[dict]:
    """Downtime whose comment names failed equipment but whose reason code says otherwise."""
    return conn.execute(
        """
        select g.fuzer_id, l.label, g.value #>> '{}' as issue, s.value #>> '{}' as start
        from golden g
        left join entity_label l using (fuzer_id)
        left join golden s on s.fuzer_id = g.fuzer_id and s.field = 'start'
        where g.field = 'reason_check'
        order by start desc limit %s
        """,
        (limit,),
    ).fetchall()


def incidents(conn, fuzer_id: str | None = None, since: str | None = None, until: str | None = None,
              limit: int = 50) -> dict:
    """Incidents facility-wide, or about a tag (an instrument counts toward its equipment), a piece of
    equipment (via where it's installed), every instance of a model, or a class including subtypes."""
    params = {"id": fuzer_id, "since": since, "until": until, "limit": limit}
    rows = conn.execute(
        """
        with recursive classes(id) as (
            select %(id)s::text
            union
            select e.src from edge e join classes c on e.dst = c.id
            where e.rel = 'is_a' and e.src in (select fuzer_id from entity where kind = 'equipment_class')
        ), base as (
            select %(id)s::text as id
            union select src from edge where rel = 'instance_of' and dst = %(id)s
            union select src from edge where rel = 'classified_as' and dst in (select id from classes)
        ), targets as (
            select id from base
            union select dst from edge where rel in ('installed_at', 'part_of') and src in (select id from base)
        ), inc as (
            select en.fuzer_id as id, jsonb_object_agg(g.field, g.value) as f
            from entity en join golden g using (fuzer_id)
            where en.kind = 'incident' and en.retired_at is null
              and (%(id)s::text is null or en.fuzer_id in (
                    select src from edge where rel = 'about' and dst in (select id from targets)))
            group by en.fuzer_id
        )
        select inc.id, inc.f, count(*) over () as total from inc
        where (%(since)s::text is null or inc.f->>'start' >= %(since)s)
          and (%(until)s::text is null or inc.f->>'start' < %(until)s)
        order by inc.f->>'start' desc, inc.id
        limit %(limit)s
        """,
        params,
    ).fetchall()
    return {"total": rows[0]["total"] if rows else 0,
            "items": [{"id": r["id"], **{k: v for k, v in r["f"].items() if k != "timestamp"}} for r in rows]}


def incident_timeline(conn, fuzer_id: str) -> list[dict]:
    """The signals in one incident, in time order."""
    rows = conn.execute(
        """
        select s.fuzer_id as id, s.kind, l.label, jsonb_object_agg(g.field, g.value) as f
        from edge e
        join entity s on s.fuzer_id = e.dst
        left join entity_label l on l.fuzer_id = s.fuzer_id
        join golden g on g.fuzer_id = s.fuzer_id
        where e.src = %s and e.rel = 'includes'
        group by s.fuzer_id, s.kind, l.label
        """,
        (fuzer_id,),
    ).fetchall()
    out = []
    for r in rows:
        f = r["f"]
        out.append({"id": r["id"], "kind": r["kind"], "label": r["label"],
                    "at": f.get("timestamp") or f.get("start") or f.get("created"),
                    "text": f.get("summary") or f.get("comment") or f.get("short_text") or f.get("description"),
                    "detail": f.get("event_type") or f.get("reason") or f.get("failure_mode_name"),
                    "lost_oil_sm3": f.get("lost_oil_sm3")})
    return sorted(out, key=lambda x: (x["at"] or "", x["kind"]))


def similar_incidents(conn, fuzer_id: str, limit: int = 10) -> list[dict]:
    """Past incidents most like this one: same unit, same equipment class, shared failure modes and
    event types. Candidates come only from incidents on units of the same class."""
    me = conn.execute(
        """
        select a.dst as unit, c.dst as class, jsonb_object_agg(g.field, g.value) as f
        from edge a
        left join edge c on c.src = a.dst and c.rel = 'classified_as'
        join golden g on g.fuzer_id = a.src
        where a.src = %s and a.rel = 'about'
        group by a.dst, c.dst
        """,
        (fuzer_id,),
    ).fetchone()
    if not me:
        return []
    rows = conn.execute(
        """
        select a.src as id, a.dst as unit, jsonb_object_agg(g.field, g.value) as f
        from edge a
        join golden g on g.fuzer_id = a.src
        where a.rel = 'about' and a.src <> %(me)s
          and (a.dst = %(unit)s or a.dst in (select src from edge where rel = 'classified_as' and dst = %(class)s))
        group by a.src, a.dst
        """,
        {"me": fuzer_id, "unit": me["unit"], "class": me["class"]},
    ).fetchall()

    def jaccard(a, b):
        a, b = set(a or []), set(b or [])
        return len(a & b) / len(a | b) if a | b else 0.0

    scored = []
    for r in rows:
        score = (1.0 if r["unit"] == me["unit"] else 0.0) + 0.5 \
            + jaccard(me["f"].get("failure_modes"), r["f"].get("failure_modes")) \
            + jaccard(me["f"].get("event_types"), r["f"].get("event_types"))
        scored.append({"id": r["id"], "score": round(score, 2), "same_unit": r["unit"] == me["unit"],
                       **{k: r["f"].get(k) for k in ("description", "start", "end", "lost_oil_sm3", "failure_modes",
                                                    "event_types")}})
    return sorted(scored, key=lambda x: (-x["score"], x["start"] or ""), reverse=False)[:limit]
