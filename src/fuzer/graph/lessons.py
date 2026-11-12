"""Lessons learned: what has fixed a failure mode on an equipment model before.

Every incident with ISO 14224 failure modes and corrective work orders is evidence for its unit's
equipment model (or class, when the model is unknown), and so is every case Fuzer closed as a real
problem with a failure mode and the action taken (P6.2a: the structured close-out of P6.1). Per model
and failure mode, a draft lesson lists the corrective actions, how often each one held, how many
incidents and cases it rests on and how often the problem came back: within RECUR_DAYS for an
incident, by its re-check or a repeat for a case. Engineers approve, edit or reject drafts; approved
lessons join knowledge search as reviewed site experience. Rebuilds refresh drafts' evidence and
numbers only.
"""

from collections import Counter, defaultdict
from datetime import date

from psycopg.types.json import Jsonb

from ..core.taxonomy import load_standard

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


def _cases(conn, actions: dict) -> list[dict]:
    """Closed cases with a real problem's close-out, in the shape of an incident: the action taken stands for its work
    orders, and `recurred` is the re-check saying the fix did not hold, or a repeat reopening it."""
    if conn.execute("select to_regclass('case_disposition') is null or to_regclass('case_repeat') is null "
                    "as missing").fetchone()["missing"]:
        return []
    rows = conn.execute(
        """
        select 'case ' || w.id as id, w.id as item_id, w.title, s.unit, d.failure_mode, d.activity, d.at,
               (d.held is false or exists (select 1 from case_repeat r where r.first = w.id)) as recurred,
               coalesce((select m.dst from edge m where m.src = s.unit and m.rel = 'instance_of' limit 1),
                        (select m.dst from edge ins join edge m on m.src = ins.src and m.rel = 'instance_of'
                         where ins.dst = s.unit and ins.rel = 'installed_at' and ins.valid_to is null limit 1)) as model,
               (select c.dst from edge c where c.src = s.unit and c.rel = 'classified_as' limit 1) as class
        from case_disposition d join work_item w on w.id = d.item_id
        cross join lateral (select w.subjects[1] as unit) s
        where d.disposition = 'real' and d.failure_mode is not null and d.activity is not null and w.state = 'done'
          and s.unit is not null
        """).fetchall()
    return [{"id": r["id"], "case": r["item_id"], "label": f"{r['id']}: {r['title']}", "unit": r["unit"],
             "model": r["model"], "class": r["class"], "start": r["at"].date().isoformat(),
             "modes": [r["failure_mode"]], "orders": [actions.get(r["activity"], r["activity"])],
             "recurred": bool(r["recurred"])} for r in rows]


def suggest(conn, taxonomy: str = "iso14224") -> dict:
    std = load_standard(taxonomy)
    modes = std.get("failure_modes", {})
    rows = _incidents(conn)
    cases = _cases(conn, std.get("maintenance_activities", {}))
    keys = {r["fuzer_id"]: (r["kind"], r["key"]) for r in conn.execute(
        "select fuzer_id, kind, key from entity_key where kind in ('equipment_model', 'equipment_class') "
        "and fuzer_id = any(%s)", (list({x for r in [*rows, *cases] for x in (r["model"], r["class"]) if x}),))}
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)",
        (list({x for r in [*rows, *cases] for x in (r["model"], r["class"], r["unit"], r["id"]) if x}),))}
    labels |= {c["id"]: c["label"] for c in cases}
    by_unit = defaultdict(list)
    for r in rows:
        by_unit[r["unit"]].append(r)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for incs in by_unit.values():
        incs.sort(key=lambda r: r["f"]["start"])
        for i, r in enumerate(incs):
            nxt = incs[i + 1]["f"]["start"] if i + 1 < len(incs) else None
            recurred = bool(nxt and (date.fromisoformat(nxt) - date.fromisoformat(r["f"]["end"])).days <= RECUR_DAYS)
            scope = keys.get(r["model"]) or keys.get(r["class"])
            orders = [o for o in (r["orders"] or []) if o.strip("|")]
            if not scope or not orders:
                continue
            for mode in r["f"].get("failure_modes", []):
                groups[(scope, mode)].append({**r, "start": r["f"]["start"], "recurred": recurred, "orders": orders})
    for c in cases:
        if scope := keys.get(c["model"]) or keys.get(c["class"]):
            for mode in c["modes"]:
                groups[(scope, mode)].append(c)

    upserts = []
    for ((kind, key), mode), incs in groups.items():
        if len(incs) < MIN_INCIDENTS:
            continue
        actions = Counter(o.split("|", 1)[0] for r in incs for o in set(r["orders"]))
        held = Counter(o.split("|", 1)[0] for r in incs if not r["recurred"] for o in set(r["orders"]))
        fixed = [r for r in incs if not r["recurred"]]
        n_cases = sum(1 for r in incs if "case" in r)
        scope_label = key.replace("|", " ") if kind == "equipment_model" else key
        title = f"{scope_label}: {modes.get(mode, mode).lower()}"
        steps = [f"- {a} ({n} of {len(incs)}; held in {held[a]})" for a, n in actions.most_common(4)]
        source = (f" ({len(incs) - n_cases} incidents, {n_cases} closed cases)" if n_cases and n_cases < len(incs) else
                  " (closed cases)" if n_cases else "")
        draft = "\n".join([f"Seen {len(incs)} times{source}; it did not come back in {len(fixed)} of them "
                           f"(within {RECUR_DAYS} days of an incident, or by a case's re-check).",
                           "Corrective work that has been done:", *steps])
        evidence = [{"id": r["id"], "label": labels.get(r["id"]), "start": r["start"], "unit": labels.get(r["unit"]),
                     "recurred": r["recurred"], **({"case": r["case"]} if "case" in r else {})}
                    for r in sorted(incs, key=lambda r: str(r["start"]), reverse=True)[:20]]
        stats = {"incidents": len(incs) - n_cases, "cases": n_cases, "fixed": len(fixed),
                 "fix_rate": round(len(fixed) / len(incs), 2)}
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
        order by coalesce((stats->>'incidents')::int, 0) + coalesce((stats->>'cases')::int, 0) desc, title limit %s
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


def approved_for(conn, fids: list[str], limit: int = 5) -> list[dict]:
    """The approved lessons for these assets' equipment model or class (P6.2b), most incidents and cases first: what a
    brief on them hands the specialist. A suggested or rejected lesson is never among them."""
    from .knowledge import scope

    scoped = list({s for f in dict.fromkeys(fids) if f for s in scope(conn, f)})
    if not scoped:
        return []
    return conn.execute(
        """
        select l.id, l.title, coalesce(l.text, l.draft) as text, l.failure_mode, l.scope_kind, l.scope_key,
               l.decided_by, l.decided_at
        from lesson l
        where l.status = 'approved' and exists (select 1 from entity_key k where k.kind = l.scope_kind
                                                and k.key = l.scope_key and k.fuzer_id = any(%s))
        order by coalesce((l.stats->>'incidents')::int, 0) + coalesce((l.stats->>'cases')::int, 0) desc, l.id
        limit %s
        """, (scoped, limit)).fetchall()
