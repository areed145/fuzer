"""Where a new item sits, and which drawing and documents are about it (P2.1d): ranked proposals, each with a
confidence and the evidence behind it. Confident placements apply at every rebuild; the rest are suggestions on the
asset's context panel for a steward to accept or reject, and an answer is kept by every rebuild after it.

A parent (part_of an area) for a tag or well that is in none, from three kinds of evidence:

    grammar      the area code its tag names, by the pack's tag grammar
    neighbours   the areas of what it is piped, wired (controls) or shaft-coupled (drives) to; allocation flow and
                 gas lift cross areas, so they don't say where it is
    documents    the area most of what a document or drawing naming it names is in (a plant-wide one says nothing)

A drawing for an asset on none: the P&ID sheets that show the things it is piped or wired to. Documents for an asset
with none: the guides that apply to its equipment class, and the documents that name its neighbours.

Each kind of evidence for one answer adds to its score (1 - the product of what each leaves unsaid); the confidence is
that score less what the strongest other answer leaves room for (score x (1 - its score)). A parent applies at `APPLY` or
above when two kinds of evidence agree, or when its tag's name and its piping agree and nothing physical points to
another area; drawings and documents are only ever suggested, since a sheet or a guide
naming the neighbours is a place to look, not proof.

    parents(conn, pack, fids=None)     {fid: [proposal, ...]} best first, for items in no area
    documents(conn, fids)              {fid: [proposal, ...]} best first, for assets with no drawing or no document
    apply(conn, pack)                  write the confident and the accepted parents as edges (at rebuild)
    decide(conn, fid, rel, target, accept, by, reason)   a steward's answer (rel part_of, shown_on or applies_to),
                                                         kept by later rebuilds
"""

from collections import defaultdict

from psycopg.types.json import Jsonb

SOURCE = "fuzer:placement"
DRAWN = "pid:steward"  # a steward placed it on a sheet: it counts as on a drawing
APPLY = 0.9
TOP = 5
WEIGHT = {"grammar": 0.6, "neighbour": 0.5, "document": 0.3, "sheet": 0.45, "class_guide": 0.4,
          "neighbour_document": 0.25}
NEAR = ("pipes_to", "flows_to", "controls", "drives", "lift_gas_to")
LOCAL = ("pipes_to", "controls", "drives")  # links inside one area; allocation flow and gas lift cross them
DOCUMENT_FOCUS = 0.5  # a document votes for an area only when at least this share of what it names is there
CONTAINED = ("part_of", "located_at")


def _score(weights: list[float]) -> float:
    left = 1.0
    for w in weights:
        left *= 1 - w
    return 1 - left


def _rank(evidence: dict[str, list[tuple[str, float, str]]], rel: str) -> list[dict]:
    """{target: [(kind, weight, why)]} -> proposals best first. Each answer's confidence is its score less what the
    strongest other answer leaves room for (score x (1 - the best other's score))."""
    scores = {t: _score([w for _, w, _ in ev]) for t, ev in evidence.items()}
    out = []
    for t, ev in evidence.items():
        rival = max((v for u, v in scores.items() if u != t), default=0.0)
        out.append({"target": t, "rel": rel, "confidence": round(scores[t] * (1 - rival), 3),
                    "kinds": sorted({k for k, _, _ in ev}), "basis": [why for _, _, why in ev]})
    return sorted(out, key=lambda p: (-p["confidence"], p["target"]))[:TOP]


def _labels(conn, ids) -> dict[str, str]:
    return {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (list(ids),))}


def _area_of(conn, ids) -> dict[str, str]:
    """Each item's area: its own, or its parent tag's (an instrument's)."""
    out = {}
    for r in conn.execute(
        """
        select m.fid, coalesce(a.fuzer_id, a2.fuzer_id) as area from unnest(%s::text[]) m(fid)
        left join lateral (select x.fuzer_id from edge p join entity x on x.fuzer_id = p.dst and x.kind = 'area'
                           where p.src = m.fid and p.rel = any(%s) and p.valid_to is null limit 1) a on true
        left join lateral (select x.fuzer_id from edge p join edge q on q.src = p.dst and q.rel = any(%s)
                           join entity x on x.fuzer_id = q.dst and x.kind = 'area'
                           where p.src = m.fid and p.rel = 'part_of' and p.valid_to is null limit 1) a2 on true
        """, (list(ids), list(CONTAINED), list(CONTAINED))):
        if r["area"]:
            out[r["fid"]] = r["area"]
    return out


def _decisions(conn) -> dict[tuple[str, str, str], bool]:
    return {(r["fuzer_id"], r["rel"], r["target"]): r["accepted"] for r in conn.execute(
        "select fuzer_id, rel, target, accepted from placement_decision")}


def unplaced(conn) -> list[str]:
    """Tags and wells in no area, and not part of another tag."""
    return [r["fuzer_id"] for r in conn.execute(
        f"""
        select e.fuzer_id from entity e where e.kind in ('tag', 'well') and e.merged_into is null
          and e.retired_at is null and not exists (
            select 1 from edge p where p.src = e.fuzer_id and p.rel = any(%s) and p.valid_to is null
              and not ('{SOURCE}' = any(p.sources) and cardinality(p.sources) = 1))
        order by e.fuzer_id
        """, (list(CONTAINED),))]


def parents(conn, pack, fids: list[str] | None = None) -> dict[str, list[dict]]:
    """Ranked areas for each item in none (or for `fids`)."""
    fids = unplaced(conn) if fids is None else fids
    if not fids:
        return {}
    labels = _labels(conn, fids)
    areas = {r["fuzer_id"]: (r["key"], r["label"]) for r in conn.execute(
        "select k.fuzer_id, k.key, l.label from entity_key k join entity e using (fuzer_id) "
        "left join entity_label l using (fuzer_id) where k.kind = 'area' and e.merged_into is null")}
    by_code: dict[str, str] = {}
    for a, (key, label) in sorted(areas.items()):
        for name in {key, label}:
            if name:
                by_code.setdefault(name.upper(), a)
                by_code.setdefault(name.upper().rsplit("-", 1)[-1], a)
    near: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in conn.execute("select src, rel, dst from edge where rel = any(%s) and valid_to is null "
                          "and (src = any(%s) or dst = any(%s))", (list(LOCAL), fids, fids)):
        if r["src"] in labels:
            near[r["src"]].append((r["dst"], r["rel"]))
        if r["dst"] in labels:
            near[r["dst"]].append((r["src"], r["rel"]))
    docs: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))  # fid -> document -> other items
    for r in conn.execute(
        """
        select m.dst as fid, m.src as doc, o.dst as other from edge m join entity d on d.fuzer_id = m.src
          and d.kind in ('document', 'document_section')
        join edge o on o.src = m.src and o.rel in ('mentions', 'applies_to') and o.dst <> m.dst
        join entity t on t.fuzer_id = o.dst and t.kind in ('tag', 'well')
        where m.rel in ('mentions', 'applies_to') and m.dst = any(%s)
        """, (fids,)):
        docs[r["fid"]][r["doc"]].add(r["other"])
    others = {x for v in near.values() for x, _ in v} | {x for v in docs.values() for xs in v.values() for x in xs}
    area_of = _area_of(conn, others)
    names = _labels(conn, {x for v in near.values() for x, _ in v})
    grammar = getattr(pack, "tags", None) if pack else None
    out = {}
    for fid in fids:
        ev: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        found = grammar.find_all(labels.get(fid) or "") if grammar else []
        code = found[0].area if found else None
        if code and (a := by_code.get(code.upper())):
            ev[a].append(("grammar", WEIGHT["grammar"], f"its tag names area {code}"))
        votes: dict[str, list[str]] = defaultdict(list)
        for other, rel in near.get(fid, []):
            if other in area_of:
                votes[area_of[other]].append(f"{rel.replace('_', ' ')} {names.get(other, other)}")
        for a, whys in votes.items():
            ev[a].append(("neighbour", _score([WEIGHT["neighbour"]] * len(whys)), "; ".join(sorted(whys)[:3])))
        dvotes: dict[str, list[float]] = defaultdict(list)  # a document votes for where most of what it names is
        for named in docs.get(fid, {}).values():
            where = [area_of[x] for x in named if x in area_of]
            for a in set(where):
                if where.count(a) / len(where) >= DOCUMENT_FOCUS:
                    dvotes[a].append(WEIGHT["document"] * where.count(a) / len(where))
        for a, ws in dvotes.items():
            ev[a].append(("document", _score(ws), f"{len(ws)} document{'s' if len(ws) != 1 else ''} naming it "
                                                  f"name{'s' if len(ws) == 1 else ''} other items there"))
        if ev:
            ranked = _rank(ev, "part_of")
            names = _labels(conn, [p["target"] for p in ranked])
            for p in ranked:
                p["target_label"] = names.get(p["target"], p["target"])
            out[fid] = ranked
        else:
            out[fid] = []
    return out


def documents(conn, fids: list[str], want: tuple[str, ...] = ("drawing", "documents")) -> dict[str, list[dict]]:
    """Ranked drawings (P&ID sheets showing its neighbours) and documents (its class's guides, documents naming its
    neighbours) for each asset in `fids`."""
    if not fids:
        return {}
    near: dict[str, set[str]] = defaultdict(set)
    for r in conn.execute("select src, dst from edge where rel = any(%s) and valid_to is null "
                          "and (src = any(%s) or dst = any(%s))", (list(NEAR), fids, fids)):
        if r["src"] in fids:
            near[r["src"]].add(r["dst"])
        if r["dst"] in fids:
            near[r["dst"]].add(r["src"])
    others = sorted({x for v in near.values() for x in v})
    sheets: dict[str, set[str]] = defaultdict(set)  # neighbour -> sheets
    writings: dict[str, set[str]] = defaultdict(set)  # neighbour -> documents
    for r in conn.execute(
        """
        select m.dst, m.src, exists (select 1 from unnest(m.sources) s where s like 'pid%%') as drawing
        from edge m join entity d on d.fuzer_id = m.src and d.kind = 'document'
        where m.rel in ('mentions', 'applies_to') and m.dst = any(%s)
        """, (others,)):
        (sheets if r["drawing"] else writings)[r["dst"]].add(r["src"])
    guides: dict[str, set[str]] = defaultdict(set)
    for r in conn.execute(
        """
        select c.src as fid, a.src as doc from edge c
        join edge a on a.dst = c.dst and a.rel = 'applies_to'
        join entity d on d.fuzer_id = a.src and d.kind = 'document'
        where c.rel = 'classified_as' and c.src = any(%s)
        """, (fids,)):
        guides[r["fid"]].add(r["doc"])
    out = {}
    for fid in fids:
        ev: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        kinds: dict[str, str] = {}
        if "drawing" in want:
            on: dict[str, list[str]] = defaultdict(list)
            for n in near.get(fid, ()):
                for s in sheets.get(n, ()):
                    on[s].append(n)
            for s, ns in on.items():
                ev[s].append(("sheet", _score([WEIGHT["sheet"]] * len(ns)), f"it shows {len(ns)} of the things "
                              f"it is piped or wired to"))
                kinds[s] = "drawing"
        if "documents" in want:
            for d in guides.get(fid, ()):
                ev[d].append(("class_guide", WEIGHT["class_guide"], "it applies to its equipment class"))
                kinds[d] = "document"
            named: dict[str, int] = defaultdict(int)
            for n in near.get(fid, ()):
                for d in writings.get(n, ()):
                    named[d] += 1
            for d, k in named.items():
                ev[d].append(("neighbour_document", _score([WEIGHT["neighbour_document"]] * k),
                              f"it names {k} of the things it is piped or wired to"))
                kinds[d] = "document"
        ranked = _rank(ev, "applies_to") if ev else []
        for p in ranked:
            p["rel"] = "shown_on" if kinds[p["target"]] == "drawing" else "applies_to"
        names = _labels(conn, [p["target"] for p in ranked])
        for p in ranked:
            p["target_label"] = names.get(p["target"], p["target"])
            p["for"] = kinds[p["target"]]
            p["confidence"] = min(p["confidence"], APPLY - 0.01)  # a place to look, never applied on its own
        out[fid] = ranked
    return out


def _confident(best: dict, ranked: list[dict]) -> bool:
    """Apply on its own: at `APPLY` or above with two kinds of evidence, or where its tag's name and its piping agree
    and nothing physical (a name or a link) points anywhere else; documents alone never overrule that."""
    if best["confidence"] >= APPLY and len(best["kinds"]) >= 2:
        return True
    physical = {"grammar", "neighbour"}
    return physical <= set(best["kinds"]) and not any(set(p["kinds"]) & physical for p in ranked if p is not best)


def _write(conn, fid: str, rel: str, target: str, conf: float, basis: str, drawing: bool = False) -> None:
    """A parent is the item's own link (item part_of area); a document or drawing link runs from the document."""
    src, dst = (fid, target) if rel == "part_of" else (target, fid)
    conn.execute(
        "insert into edge (src, rel, dst, sources, confidence, props) values (%s, %s, %s, %s, %s, %s) "
        "on conflict (src, rel, dst, valid_from) do update set sources = "
        "(select array(select distinct unnest(edge.sources || excluded.sources)))",
        (src, rel, dst, [DRAWN if drawing else SOURCE], conf, Jsonb({"basis": basis, "confidence": conf})))


def apply(conn, pack) -> dict:
    """At rebuild, after the edges: the links stewards accepted, and for each item in no area the parent at `APPLY`
    or above with two kinds of evidence agreeing (never one a steward rejected). Returns what applied and what
    waits for a steward."""
    decided = _decisions(conn)
    for (fid, rel, target), ok in sorted(decided.items()):
        if ok and conn.execute("select 1 from entity where fuzer_id = %s and merged_into is null", (target,)).fetchone():
            _write(conn, fid, "mentions" if rel == "shown_on" else rel, target, 1.0, "a steward accepted it",
                   drawing=rel == "shown_on")
    applied, waiting = 0, 0
    for fid, ranked in parents(conn, pack).items():
        if any(ok for (f, rel, _), ok in decided.items() if f == fid and rel == "part_of"):
            continue
        open_ = [p for p in ranked if decided.get((fid, "part_of", p["target"])) is not False]
        best = open_[0] if open_ else None
        if best and _confident(best, open_):
            _write(conn, fid, "part_of", best["target"], best["confidence"], "; ".join(best["basis"]))
            applied += 1
        elif open_:
            waiting += 1
    return {"applied": applied, "to_confirm": waiting}


def decide(conn, fid: str, rel: str, target: str, accept: bool, by: str, reason: str | None = None) -> dict:
    """Keep a steward's answer to a proposal; a parent accepted is written at once and by every later rebuild."""
    conn.execute(
        """
        insert into placement_decision (fuzer_id, rel, target, accepted, decided_by, reason) values (%s, %s, %s, %s, %s, %s)
        on conflict (fuzer_id, rel, target) do update set accepted = excluded.accepted, decided_by = excluded.decided_by,
            reason = excluded.reason, decided_at = now()
        """, (fid, rel, target, accept, by, reason))
    edge_rel = "mentions" if rel == "shown_on" else rel
    if accept:
        _write(conn, fid, edge_rel, target, 1.0, f"{by} accepted it", drawing=rel == "shown_on")
    else:
        src, dst = (fid, target) if rel == "part_of" else (target, fid)
        conn.execute("delete from edge where src = %s and rel = %s and dst = %s and sources <@ %s",
                     (src, edge_rel, dst, [SOURCE, DRAWN]))
    return {"fuzer_id": fid, "rel": rel, "target": target, "accepted": accept}
