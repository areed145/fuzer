"""Scoped search across documents and knowledge.

knowledge_index holds document sections and incidents with a full-text vector and the IDs each is
linked to. A search "for" an entity first works out its scope (the entity, the tag / equipment it's
installed at or hosts, its model, its classes up the hierarchy, its parent tags and area) and keeps
items linked to anything in it. Ranking = text match x how close the link is x how authoritative the
source is. Questions match on any of their words, so full sentences still find passages.
"""

from . import db

AUTHORITY = {"oem_manual": 1.0, "procedure": 1.0, "troubleshooting_guide": 0.9, "lesson": 0.85, "incident": 0.7}
COLS = ["fuzer_id", "kind", "document", "title", "doc_type", "status", "heading", "page", "body", "scope_ids",
        "failure_modes"]
MODE_BOOST = 1.5  # a passage about the failure mode you're dealing with


def build(conn) -> dict:
    """Refresh knowledge_index from the graph (sections of documents, incidents)."""
    rows = conn.execute(
        """
        with sec as (
            select s.fuzer_id, p.dst as doc
            from entity s join edge p on p.src = s.fuzer_id and p.rel = 'part_of'
            where s.kind = 'document_section' and s.retired_at is null
        )
        select sec.fuzer_id, 'document_section' as kind, sec.doc as document,
               gt.value #>> '{}' as title, gd.value #>> '{}' as doc_type, gs.value #>> '{}' as status,
               gh.value #>> '{}' as heading, (gp.value #>> '{}')::int as page, coalesce(gx.value #>> '{}', '') as body,
               array(select distinct e.dst from edge e
                     where (e.src = sec.doc and e.rel = 'applies_to')
                        or (e.src = sec.fuzer_id and e.rel in ('mentions', 'applies_to')))
                   as scope_ids,
               array(select jsonb_array_elements_text(coalesce(gm.value, '[]'))) as failure_modes
        from sec
        left join golden gt on gt.fuzer_id = sec.doc and gt.field = 'title'
        left join golden gd on gd.fuzer_id = sec.doc and gd.field = 'doc_type'
        left join golden gs on gs.fuzer_id = sec.doc and gs.field = 'status'
        left join golden gh on gh.fuzer_id = sec.fuzer_id and gh.field = 'heading'
        left join golden gp on gp.fuzer_id = sec.fuzer_id and gp.field = 'page'
        left join golden gx on gx.fuzer_id = sec.fuzer_id and gx.field = 'text'
        left join golden gm on gm.fuzer_id = sec.fuzer_id and gm.field = 'failure_modes'
        union all
        select i.fuzer_id, 'incident', null, gd.value #>> '{}', 'incident', 'current', null, null,
               concat_ws(' ', gd.value #>> '{}', gm.value::text, ge.value::text),
               array(select a.dst from edge a where a.src = i.fuzer_id and a.rel = 'about'),
               array(select jsonb_array_elements_text(coalesce(gm.value, '[]')))
        from entity i
        left join golden gd on gd.fuzer_id = i.fuzer_id and gd.field = 'description'
        left join golden gm on gm.fuzer_id = i.fuzer_id and gm.field = 'failure_modes'
        left join golden ge on ge.fuzer_id = i.fuzer_id and ge.field = 'event_types'
        where i.kind = 'incident' and i.retired_at is null
        """
    ).fetchall()
    from . import lessons
    items = [tuple(r[c] for c in COLS) for r in rows] + lessons.knowledge_rows(conn)
    return db.sync_table(conn, "knowledge_index", ["fuzer_id"], COLS[1:], items)


def scope(conn, fuzer_id: str) -> dict[str, tuple[float, str]]:
    """Entities whose knowledge applies to this one, with a weight and how they relate."""
    weights: dict[str, tuple[float, str]] = {fuzer_id: (1.5, "this")}
    for r in conn.execute("select dst as id from edge where rel = 'installed_at' and src = %(id)s "
                          "union select src from edge where rel = 'installed_at' and dst = %(id)s", {"id": fuzer_id}):
        weights.setdefault(r["id"], (1.5, "installed"))
    units = list(weights)
    for r in conn.execute(
        """
        with recursive up(id, d) as (
            select unnest(%s::text[]), 0
            union select e.dst, up.d + 1 from edge e join up on e.src = up.id where e.rel = 'part_of' and up.d < 6
        )
        select id, min(d) as d from up where d > 0 group by id
        """,
        (units,),
    ):
        weights.setdefault(r["id"], (1.0 if r["d"] <= 2 else 0.8, "parent"))
    models = [r["dst"] for r in conn.execute("select dst from edge where rel = 'instance_of' and src = any(%s)", (units,))]
    for m in models:
        weights.setdefault(m, (1.3, "model"))
    for r in conn.execute(
        """
        with recursive c(id, d) as (
            select dst, 0 from edge where rel in ('classified_as', 'is_a') and src = any(%s)
            union select e.dst, c.d + 1 from edge e join c on e.src = c.id where e.rel = 'is_a' and c.d < 4
        )
        select id, min(d) as d from c group by id
        """,
        (units + models,),
    ):
        weights.setdefault(r["id"], (1.1 if r["d"] == 0 else 0.9, "class"))
    return weights


def search(conn, question: str, fuzer_id: str | None = None, limit: int = 10,
           include_superseded: bool = False, failure_modes: list[str] | None = None) -> list[dict]:
    """failure_modes (ISO 14224 codes) boosts passages about those modes."""
    sc = scope(conn, fuzer_id) if fuzer_id else {}
    wanted = set(failure_modes or [])
    rows = conn.execute(
        """
        with q as (
            select to_tsquery('english', string_agg(lexeme, ' | ')) as query
            from unnest(to_tsvector('english', %(q)s))
        )
        select k.fuzer_id, k.kind, k.document, k.title, k.doc_type, k.status, k.heading, k.page, k.scope_ids,
               k.failure_modes,
               ts_rank_cd(k.tsv, q.query) as rank,
               ts_headline('english', k.body, q.query, 'StartSel=[[, StopSel=]], MaxFragments=2, MaxWords=35, MinWords=12')
                   as snippet
        from knowledge_index k, q
        where q.query is not null and k.tsv @@ q.query
          and (%(sup)s or coalesce(k.status, 'current') <> 'superseded')
          and (%(scope)s::text[] is null or k.scope_ids && %(scope)s::text[])
        order by rank desc
        limit 200
        """,
        {"q": question, "sup": include_superseded, "scope": list(sc) or None},
    ).fetchall()
    labels = {}
    ids = {i for r in rows for i in r["scope_ids"] if i in sc} | {r["document"] for r in rows if r["document"]}
    if ids:
        labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
            "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (list(ids),))}
    out = []
    for r in rows:
        best = max((sc[i] for i in r["scope_ids"] if i in sc), default=(1.0, None))
        hit = next((i for i in r["scope_ids"] if i in sc and sc[i] == best), None)
        score = float(r["rank"]) * best[0] * AUTHORITY.get(r["doc_type"] or "", 0.8)
        if wanted & set(r["failure_modes"]):
            score *= MODE_BOOST
        out.append({
            "id": r["fuzer_id"], "kind": r["kind"], "score": round(score, 4),
            "document": r["document"], "document_label": labels.get(r["document"]), "title": r["title"],
            "doc_type": r["doc_type"], "status": r["status"], "heading": r["heading"], "page": r["page"],
            "snippet": r["snippet"], "failure_modes": r["failure_modes"],
            **({"why": f"{best[1]}: {labels.get(hit, hit)}"} if hit else {}),
        })
    return sorted(out, key=lambda x: -x["score"])[:limit]


def documents_for(conn, fuzer_id: str) -> list[dict]:
    """Current documents that apply to this entity's scope, closest first."""
    sc = scope(conn, fuzer_id)
    rows = conn.execute(
        """
        select d.src as id, d.dst as via, l.label, gt.value #>> '{}' as title, gd.value #>> '{}' as doc_type,
               gr.value #>> '{}' as revision, gs.value #>> '{}' as status
        from edge d
        join entity e on e.fuzer_id = d.src and e.kind = 'document' and e.retired_at is null
        left join entity_label l on l.fuzer_id = d.src
        left join golden gt on gt.fuzer_id = d.src and gt.field = 'title'
        left join golden gd on gd.fuzer_id = d.src and gd.field = 'doc_type'
        left join golden gr on gr.fuzer_id = d.src and gr.field = 'revision'
        left join golden gs on gs.fuzer_id = d.src and gs.field = 'status'
        where d.rel = 'applies_to' and d.dst = any(%s) and coalesce(gs.value #>> '{}', 'current') <> 'superseded'
        """,
        (list(sc),),
    ).fetchall()
    best: dict[str, dict] = {}
    for r in rows:
        w, how = sc[r["via"]]
        if r["id"] not in best or w > best[r["id"]]["_w"]:
            best[r["id"]] = {**{k: r[k] for k in ("id", "label", "title", "doc_type", "revision")}, "why": how, "_w": w}
    return [{k: v for k, v in d.items() if k != "_w"} for d in sorted(best.values(), key=lambda d: (-d["_w"], d["label"]))]



def for_incident(conn, incident_id: str, limit: int = 5) -> list[dict]:
    """Suggested reading for an incident: sections in scope of its unit that cover its failure modes."""
    row = conn.execute(
        """
        select a.dst as unit, coalesce(fm.value, '[]') as modes, coalesce(et.value, '[]') as types
        from edge a
        left join golden fm on fm.fuzer_id = a.src and fm.field = 'failure_modes'
        left join golden et on et.fuzer_id = a.src and et.field = 'event_types'
        where a.src = %s and a.rel = 'about'
        """,
        (incident_id,),
    ).fetchone()
    if not row or not row["modes"]:
        return []
    sc = scope(conn, row["unit"])
    rows = conn.execute(
        """
        select k.fuzer_id, k.document, k.title, k.doc_type, k.heading, k.page, k.failure_modes, k.scope_ids,
               l.label as document_label
        from knowledge_index k left join entity_label l on l.fuzer_id = k.document
        where k.kind = 'document_section' and coalesce(k.status, 'current') <> 'superseded'
          and k.failure_modes && %(modes)s::text[] and k.scope_ids && %(scope)s::text[]
        """,
        {"modes": list(row["modes"]), "scope": list(sc)},
    ).fetchall()
    out = []
    for r in rows:
        closeness = max(sc[i][0] for i in r["scope_ids"] if i in sc)
        overlap = len(set(r["failure_modes"]) & set(row["modes"]))
        out.append({"id": r["fuzer_id"], "document_label": r["document_label"], "title": r["title"],
                    "heading": r["heading"], "page": r["page"], "failure_modes": r["failure_modes"],
                    "score": round(closeness * overlap * AUTHORITY.get(r["doc_type"] or "", 0.8), 3)})
    return sorted(out, key=lambda x: -x["score"])[:limit]
