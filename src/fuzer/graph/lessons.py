"""Lessons learned: what has fixed a failure mode on an equipment model before.

Every incident with ISO 14224 failure modes and corrective work orders is evidence for its unit's
equipment model (or class, when the model is unknown). Per model and failure mode, a draft lesson
lists the usual corrective actions, how many incidents it rests on and how often the problem came
back within RECUR_DAYS. Engineers approve, edit or reject drafts; approved lessons join knowledge
search as reviewed site experience. Rebuilds refresh drafts' evidence and numbers only.
"""

from collections import Counter, defaultdict
from datetime import date

from psycopg.types.json import Jsonb

from .taxonomy import load_standard

RECUR_DAYS = 30
MIN_INCIDENTS = 2


def _incidents(conn) -> list[dict]:
    return conn.execute(
        """
        select i.fuzer_id as id, a.dst as unit, f.value as f,
               (select array_agg(distinct coalesce(ws.value #>> '{}', '') || '|' || coalesce(wa.value #>> '{}', ''))
                from edge inc
                join edge wo on wo.dst = inc.dst and wo.rel = 'for_notification'
                left join golden ws on ws.fuzer_id = wo.src and ws.field = 'text'
                left join golden wa on wa.fuzer_id = wo.src and wa.field = 'activity_name'
                where inc.src = i.fuzer_id and inc.rel = 'includes') as orders,
               (select m.dst from edge ins
                join edge m on m.src = ins.src and m.rel = 'instance_of'
                where ins.dst = a.dst and ins.rel = 'installed_at'
                  and (ins.valid_from is null or ins.valid_from <= (f.value->>'start')::date)
                  and (ins.valid_to is null or ins.valid_to > (f.value->>'start')::date)
                limit 1) as model,
               (select c.dst from edge c where c.src = a.dst and c.rel = 'classified_as' limit 1) as class
        from entity i
        join edge a on a.src = i.fuzer_id and a.rel = 'about'
        join lateral (select jsonb_object_agg(g.field, g.value) as value from golden g where g.fuzer_id = i.fuzer_id) f on true
        where i.kind = 'incident' and i.retired_at is null
        """
    ).fetchall()


def suggest(conn, taxonomy: str = "iso14224") -> dict:
    modes = load_standard(taxonomy).get("failure_modes", {})
    rows = _incidents(conn)
    keys = {r["fuzer_id"]: (r["kind"], r["key"]) for r in conn.execute(
        "select fuzer_id, kind, key from entity_key where kind in ('equipment_model', 'equipment_class') "
        "and fuzer_id = any(%s)", (list({x for r in rows for x in (r["model"], r["class"]) if x}),))}
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)",
        (list({x for r in rows for x in (r["model"], r["class"], r["unit"], r["id"]) if x}),))}
    by_unit = defaultdict(list)
    for r in rows:
        by_unit[r["unit"]].append(r)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for unit, incs in by_unit.items():
        incs.sort(key=lambda r: r["f"]["start"])
        for i, r in enumerate(incs):
            nxt = incs[i + 1]["f"]["start"] if i + 1 < len(incs) else None
            recurred = bool(nxt and (date.fromisoformat(nxt) - date.fromisoformat(r["f"]["end"])).days <= RECUR_DAYS)
            scope = keys.get(r["model"]) or keys.get(r["class"])
            orders = [o for o in (r["orders"] or []) if o.strip("|")]
            if not scope or not orders:
                continue
            for mode in r["f"].get("failure_modes", []):
                groups[(scope, mode)].append({**r, "recurred": recurred, "orders": orders})

    upserts = []
    for ((kind, key), mode), incs in groups.items():
        if len(incs) < MIN_INCIDENTS:
            continue
        actions = Counter(o.split("|", 1)[0] for r in incs for o in set(r["orders"]))
        fixed = [r for r in incs if not r["recurred"]]
        scope_label = key.replace("|", " ") if kind == "equipment_model" else key
        title = f"{scope_label}: {modes.get(mode, mode).lower()}"
        steps = [f"- {a} ({n} of {len(incs)} incidents)" for a, n in actions.most_common(4)]
        draft = "\n".join([f"Seen {len(incs)} times; fixed for at least {RECUR_DAYS} days in {len(fixed)} of them.",
                           "Corrective work that has been done:", *steps])
        evidence = [{"id": r["id"], "label": labels.get(r["id"]), "start": r["f"]["start"], "unit": labels.get(r["unit"]),
                     "recurred": r["recurred"]} for r in sorted(incs, key=lambda r: r["f"]["start"], reverse=True)[:20]]
        stats = {"incidents": len(incs), "fixed": len(fixed), "fix_rate": round(len(fixed) / len(incs), 2)}
        upserts.append((kind, key, mode, title, draft, Jsonb(evidence), Jsonb(stats)))
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into lesson (scope_kind, scope_key, failure_mode, title, draft, evidence, stats)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (scope_kind, scope_key, failure_mode) do update
            set title = excluded.title, draft = excluded.draft, evidence = excluded.evidence,
                stats = excluded.stats, updated_at = now()
            where (lesson.draft, lesson.evidence, lesson.stats) is distinct from (excluded.draft, excluded.evidence, excluded.stats)
            """,
            upserts,
        )
    return {"lesson_drafts": len(upserts)}


def listing(conn, status: str | None = "suggested", limit: int = 100) -> list[dict]:
    return conn.execute(
        """
        select id, scope_kind, scope_key, failure_mode, title, coalesce(text, draft) as text, draft, evidence, stats,
               status, decided_by, decided_at
        from lesson where (%s::text is null or status = %s)
        order by (stats->>'incidents')::int desc, title limit %s
        """,
        (status, status, limit),
    ).fetchall()


def decide(conn, lesson_id: int, decision: str, by: str, text: str | None = None) -> dict | None:
    status = {"approve": "approved", "reject": "rejected", "edit": None}[decision]
    row = conn.execute(
        """
        update lesson set text = coalesce(%(text)s, text), status = coalesce(%(status)s, status),
               decided_by = %(by)s, decided_at = now()
        where id = %(id)s returning id, status
        """,
        {"text": text, "status": status, "by": by, "id": lesson_id},
    ).fetchone()
    conn.commit()
    return row


def knowledge_rows(conn) -> list[tuple]:
    """Approved lessons for knowledge_index: scoped to the model / class they're about."""
    rows = conn.execute(
        """
        select l.id, l.title, coalesce(l.text, l.draft) as body, l.failure_mode, k.fuzer_id as scope
        from lesson l join entity_key k on k.kind = l.scope_kind and k.key = l.scope_key
        where l.status = 'approved'
        """
    ).fetchall()
    return [(f"lesson:{r['id']}", "lesson", None, r["title"], "lesson", "current", None, None, r["body"],
             [r["scope"]], [r["failure_mode"]]) for r in rows]
