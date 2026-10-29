"""Entity resolution: cluster candidates and give each cluster a stable Fuzer ID.

1. Reviewer must-links merge first; cannot-links block merges everywhere.
2. Same kind + same canonical key merges automatically.
3. Equipment at the same tag without a shared serial is scored; high scores
   merge, middle scores go to the review queue.
4. Each cluster keeps the ID most of its members already had.
"""

import uuid
from collections import Counter, defaultdict
from itertools import combinations

from psycopg.types.json import Jsonb
from rapidfuzz import fuzz, utils

from . import db
from .model import Candidate
from .pack import Pack


class Clusters:
    def __init__(self, refs, cannot: set[frozenset]):
        self.parent = {r: r for r in refs}
        self.members = {r: {r} for r in refs}
        self.cannot = cannot

    def find(self, r: str) -> str:
        while self.parent[r] != r:
            self.parent[r] = self.parent[self.parent[r]]
            r = self.parent[r]
        return r

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if any(frozenset((x, y)) in self.cannot for x in self.members[ra] for y in self.members[rb]):
            return False
        if len(self.members[ra]) < len(self.members[rb]):
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.members[ra] |= self.members.pop(rb)
        return True

    def groups(self) -> list[set[str]]:
        return list(self.members.values())


def _sim(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return fuzz.token_set_ratio(a, b, processor=utils.default_process) / 100


def score_equipment(a: Candidate, b: Candidate) -> tuple[float, str]:
    if a.key and b.key and a.key != b.key:
        return 0.0, "different serial numbers"
    mfr = _sim(a.fields.get("manufacturer"), b.fields.get("manufacturer"))
    desc = _sim(a.fields.get("description"), b.fields.get("description")) or 0.0
    mfr_score = 0.5 if mfr is None else mfr  # unknown manufacturer is neutral
    why = f"same tag {a.block}; manufacturer {'unknown' if mfr is None else f'{mfr:.2f}'}, description {desc:.2f}"
    return round(0.6 * mfr_score + 0.4 * desc, 3), why


def _summary(c: Candidate) -> dict:
    return {"ref": c.ref, "source": c.source, "key": c.key, "block": c.block, "fields": c.fields}


def resolve(conn, cands: list[Candidate], pack: Pack):
    cfg = pack.matching["equipment"]
    by_ref = {c.ref: c for c in cands}

    pins = conn.execute("select left_ref, right_ref, rule from pinned_link").fetchall()
    cannot = {frozenset((p["left_ref"], p["right_ref"])) for p in pins if p["rule"] == "cannot"}
    must = [(p["left_ref"], p["right_ref"]) for p in pins
            if p["rule"] == "must" and p["left_ref"] in by_ref and p["right_ref"] in by_ref]

    # Two items at the same tag with different serials are never the same item.
    blocks = defaultdict(list)
    for c in cands:
        if c.kind == "equipment" and c.block:
            blocks[c.block].append(c)
    for group in blocks.values():
        for a, b in combinations(group, 2):
            if a.key and b.key and a.key != b.key:
                cannot.add(frozenset((a.ref, b.ref)))

    cl = Clusters(by_ref, cannot)
    methods: dict[str, tuple[str, float]] = {}

    for a, b in must:
        if cl.union(a, b):
            methods[a] = methods[b] = ("pinned", 1.0)

    by_key = defaultdict(list)
    for c in cands:
        if c.key:
            by_key[(c.kind, c.key)].append(c.ref)
    for refs in by_key.values():
        for r in refs[1:]:
            cl.union(refs[0], r)
        if len(refs) > 1:
            for r in refs:
                methods.setdefault(r, ("key", 1.0))

    # Score equipment pairs at the same tag; keep the best pair per cluster pair.
    best = {}
    for group in blocks.values():
        for a, b in combinations(group, 2):
            ra, rb = cl.find(a.ref), cl.find(b.ref)
            if a.source == b.source or ra == rb or frozenset((a.ref, b.ref)) in cannot:
                continue
            s, why = score_equipment(a, b)
            k = frozenset((ra, rb))
            if s > best.get(k, (-1.0,))[0]:
                best[k] = (s, a, b, why)

    partners = defaultdict(set)
    for s, a, b, _ in best.values():
        if s >= cfg["auto_accept"]:
            partners[cl.find(a.ref)].add(cl.find(b.ref))
            partners[cl.find(b.ref)].add(cl.find(a.ref))

    reviews = []
    for s, a, b, why in best.values():
        ambiguous = len(partners[cl.find(a.ref)]) > 1 or len(partners[cl.find(b.ref)]) > 1
        if s >= cfg["auto_accept"] and not ambiguous and cl.union(a.ref, b.ref):
            for r in (a.ref, b.ref):
                methods.setdefault(r, ("score", s))
        elif s >= cfg["review_min"] or ambiguous:
            if ambiguous:
                why += "; more than one possible match"
            left, right = sorted((a, b), key=lambda c: c.ref)
            reviews.append((f"{left.ref}|{right.ref}",
                            {"left": _summary(left), "right": _summary(right), "score": s, "why": why}))
    replace_open_reviews(conn, "match", reviews)

    assigned = _assign_ids(conn, cl.groups(), by_ref)

    crosswalk = []
    for g in cl.groups():
        for r in g:
            c = by_ref[r]
            method, conf = methods.get(r, ("singleton", 1.0)) if len(g) > 1 else ("singleton", 1.0)
            crosswalk.append((c.source, c.record_type, c.source_key, assigned[r], method, conf))
    _write_crosswalk(conn, crosswalk)

    key_index = defaultdict(set)
    for c in cands:
        if c.key:
            key_index[(c.kind, c.key)].add(assigned[c.ref])
            if c.kind == "tag":
                key_index[("tag_noarea", c.key.split("-", 1)[1])].add(assigned[c.ref])
    conn.execute("delete from entity_key")
    db.copy_rows(conn, "entity_key", ["kind", "key", "fuzer_id"],
                 ((k, key, fid) for (k, key), fids in key_index.items() for fid in fids))

    stats = {
        "candidates": len(cands),
        "entities": len(set(assigned.values())),
        "merged_clusters": sum(1 for g in cl.groups() if len(g) > 1),
        "match_reviews": len(reviews),
    }
    return assigned, key_index, stats


def _assign_ids(conn, groups, by_ref) -> dict[str, str]:
    existing = {r["ref"]: r["fuzer_id"] for r in conn.execute(
        "select source || ':' || record_type || ':' || source_key as ref, fuzer_id from crosswalk")}
    assigned, taken, minted, events = {}, set(), [], []
    for g in sorted(groups, key=len, reverse=True):
        votes = Counter(existing[r] for r in g if r in existing)
        fid = next((f for f, _ in votes.most_common() if f not in taken), None)
        if fid is None:
            kind = by_ref[next(iter(g))].kind
            fid = f"fz-{kind}-{uuid.uuid4().hex[:12]}"
            minted.append((fid, kind))
            if votes:
                events.append(("split", fid, votes.most_common(1)[0][0]))
        taken.add(fid)
        for r in g:
            assigned[r] = fid
    db.copy_rows(conn, "entity", ["fuzer_id", "kind"], minted)

    conn.execute("update entity set merged_into = null where merged_into is not null and fuzer_id = any(%s)",
                 (list(taken),))
    refs_by_old = defaultdict(list)
    for r, f in existing.items():
        refs_by_old[f].append(r)
    merges = []
    for old in set(refs_by_old) - taken:
        winners = Counter(assigned[r] for r in refs_by_old[old] if r in assigned)
        if winners:
            merges.append((winners.most_common(1)[0][0], old))
            events.append(("merge", old, merges[-1][0]))
    with conn.cursor() as cur:
        cur.executemany("update entity set merged_into = %s where fuzer_id = %s", merges)
        cur.executemany("insert into id_event (event, fuzer_id, other_id) values (%s, %s, %s)", events)
    return assigned


def retire_unused(conn, live: set[str]) -> None:
    """Entities no record maps to any more are retired, not deleted, and revived if a record returns."""
    conn.execute("update entity set retired_at = null where retired_at is not null and fuzer_id = any(%s)",
                 (list(live),))
    conn.execute("update entity set retired_at = now() where retired_at is null and merged_into is null "
                 "and fuzer_id <> all(%s)", (list(live),))


def _write_crosswalk(conn, rows: list[tuple]) -> None:
    """Upsert through a staging table: one COPY instead of one INSERT per record."""
    cols = ["source", "record_type", "source_key", "fuzer_id", "method", "confidence"]
    conn.execute("create temp table if not exists crosswalk_new (like crosswalk including defaults) on commit drop")
    conn.execute("truncate crosswalk_new")
    db.copy_rows(conn, "crosswalk_new", cols, rows)
    conn.execute(
        """
        insert into crosswalk (source, record_type, source_key, fuzer_id, method, confidence)
        select source, record_type, source_key, fuzer_id, method, confidence from crosswalk_new
        on conflict (source, record_type, source_key) do update
        set fuzer_id = excluded.fuzer_id, method = excluded.method,
            confidence = excluded.confidence, updated_at = now()
        where (crosswalk.fuzer_id, crosswalk.method, crosswalk.confidence)
              is distinct from (excluded.fuzer_id, excluded.method, excluded.confidence)
        """
    )


def resolve_keyed(conn, cands: list[Candidate]) -> tuple[dict[str, str], dict]:
    """Stable IDs for derived records (classes, models) that are identified by key alone.
    Adds to entity_key; doesn't touch the matched records or the review queue."""
    by_ref = {c.ref: c for c in cands}
    groups: dict[tuple, set[str]] = defaultdict(set)
    for c in cands:
        groups[(c.kind, c.key) if c.key else ("ref", c.ref)].add(c.ref)
    assigned = _assign_ids(conn, list(groups.values()), by_ref)
    _write_crosswalk(conn, [(c.source, c.record_type, c.source_key, assigned[c.ref], "key", 1.0) for c in cands])
    key_index = defaultdict(set)
    for c in cands:
        if c.key:
            key_index[(c.kind, c.key)].add(assigned[c.ref])
    db.copy_rows(conn, "entity_key", ["kind", "key", "fuzer_id"],
                 ((k, key, fid) for (k, key), fids in key_index.items() for fid in fids))
    return assigned, key_index


def replace_open_reviews(conn, kind: str, items: list[tuple[str, dict]]) -> None:
    """Refresh the open review questions of one kind. Questions not raised again go stale;
    decided questions keep their decision."""
    conn.execute("update review_item set status = 'stale' where kind = %s and status = 'open'", (kind,))
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into review_item (kind, subject, payload) values (%s, %s, %s)
            on conflict (kind, subject) do update
            set payload = excluded.payload,
                status = case when review_item.status = 'stale' then 'open' else review_item.status end
            """,
            [(kind, subject, Jsonb(payload)) for subject, payload in items],
        )
