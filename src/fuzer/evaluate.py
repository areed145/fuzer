"""Score the loaded register against answer keys: knowledge search, document links, section failure
modes and operator-log reading.

Answer keys live in a folder of JSON files. The fake generator writes them for its data; on a real
asset an engineer curates the same files (a few dozen good questions go a long way):

  search.json        [{question, ref, expected: {document, heading_contains}}]
  linking.json       [{document, applies_to: [entity labels]}]
  section_modes.json [{document, heading_contains, failure_modes: [ISO 14224 codes]}]
  logs.json          [{entry_id, mentions: [entity labels], event_types: [..]}]
  matches.json       [{source, key, same_as: {source, key}}]  (record pairs that are the same item)
"""

import json
from pathlib import Path

from . import knowledge, queries
from .pack import Pack


def _pr(tp: int, fp: int, fn: int) -> dict:
    return {"precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None}


def search(conn, pack: Pack, cases: list[dict]) -> dict:
    ranks, misses = [], []
    for c in cases:
        fid = queries.resolve_ref(conn, pack, c["ref"]) if c.get("ref") else None
        hits = knowledge.search(conn, c["question"], fid, limit=10)
        exp = c["expected"]
        rank = next((i + 1 for i, h in enumerate(hits) if h["document_label"] == exp["document"]
                     and exp["heading_contains"].lower() in (h["heading"] or "").lower()), None)
        ranks.append(rank)
        if rank != 1 and len(misses) < 10:
            misses.append({"question": c["question"], "ref": c.get("ref"), "rank": rank,
                           "got": f"{hits[0]['document_label'] or hits[0]['title']} / {hits[0]['heading'] or ''}"
                           if hits else None})
    n = len(ranks) or 1
    return {"questions": len(ranks), "hit_at_1": round(sum(r == 1 for r in ranks) / n, 3),
            "hit_at_3": round(sum(bool(r) and r <= 3 for r in ranks) / n, 3),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 3), "misses": misses}


def linking(conn, pack: Pack, cases: list[dict]) -> dict:
    tp = fp = fn = 0
    problems = []
    for c in cases:
        doc = queries.resolve_ref(conn, pack, c["document"])
        got = {r["label"] for r in conn.execute(
            "select l.label from edge e join entity_label l on l.fuzer_id = e.dst "
            "where e.src = %s and e.rel = 'applies_to'", (doc,))} if doc else set()
        exp = set(c["applies_to"])
        tp, fp, fn = tp + len(got & exp), fp + len(got - exp), fn + len(exp - got)
        if got != exp and len(problems) < 10:
            problems.append({"document": c["document"], "missing": sorted(exp - got), "extra": sorted(got - exp)})
    return {"documents": len(cases), **_pr(tp, fp, fn), "problems": problems}


def section_modes(conn, pack: Pack, cases: list[dict]) -> dict:
    tp = fp = fn = 0
    for c in cases:
        doc = queries.resolve_ref(conn, pack, c["document"])
        row = conn.execute(
            """
            select coalesce(m.value, '[]') as modes from edge p
            join golden h on h.fuzer_id = p.src and h.field = 'heading' and h.value #>> '{}' ilike %s
            left join golden m on m.fuzer_id = p.src and m.field = 'failure_modes'
            where p.dst = %s and p.rel = 'part_of' limit 1
            """,
            (f"%{c['heading_contains']}%", doc),
        ).fetchone()
        got, exp = set(row["modes"] if row else []), set(c["failure_modes"])
        tp, fp, fn = tp + len(got & exp), fp + len(got - exp), fn + len(exp - got)
    return {"sections": len(cases), **_pr(tp, fp, fn)}


def logs(conn, pack: Pack, cases: list[dict]) -> dict:
    by_entry = {c["entry_id"]: c for c in cases}
    linked: dict[str, set] = {}
    events: dict[str, set] = {}
    for r in conn.execute(
        """
        select c.source_key, e.rel, l.label, et.value #>> '{}' as event_type
        from crosswalk c
        join edge e on e.src = c.fuzer_id and e.rel in ('mentions', 'reports')
        left join entity_label l on l.fuzer_id = e.dst
        left join golden et on et.fuzer_id = e.dst and et.field = 'event_type'
        where c.source = 'oplog' and c.record_type = 'entry' and c.source_key = any(%s)
        """,
        (list(by_entry),),
    ):
        if r["rel"] == "mentions":
            linked.setdefault(r["source_key"], set()).add(r["label"])
        elif r["event_type"]:
            events.setdefault(r["source_key"], set()).add(r["event_type"])
    tp = fp = fn = etp = efp = efn = 0
    for key, c in by_entry.items():
        got, exp = linked.get(key, set()), set(c["mentions"])
        tp, fp, fn = tp + len(got & exp), fp + len(got - exp), fn + len(exp - got)
        if events:
            g, x = events.get(key, set()), set(c["event_types"])
            etp, efp, efn = etp + len(g & x), efp + len(g - x), efn + len(x - g)
    return {"entries": len(cases), "mentions": _pr(tp, fp, fn),
            "events": _pr(etp, efp, efn) if events else "no events yet (run fuzer llm-link --source logs)"}


def matches(conn, pack: Pack, cases: list[dict]) -> dict:
    """Entity resolution: is each record merged with the record it really is (and only that)?
    Records still waiting in the review queue count as not merged."""
    owner = {(r["source"], r["source_key"]): r["fuzer_id"] for r in conn.execute(
        "select source, source_key, fuzer_id from crosswalk where record_type in ('equipment', 'asset')")}
    tp = fp = fn = pending = 0
    for c in cases:
        a, b = owner.get((c["source"], c["key"])), owner.get((c["same_as"]["source"], c["same_as"]["key"]))
        if a and a == b:
            tp += 1
            continue
        fn += 1
        merged_with_other = a and any(f == a and s == c["same_as"]["source"] for (s, _), f in owner.items())
        if merged_with_other:
            fp += 1
        else:
            pending += 1
    return {"pairs": len(cases), **_pr(tp, fp, fn), "wrong_merges": fp, "not_merged": pending}


def schema(conn, pack: Pack, cases: list[dict], data_dir: Path) -> dict:
    """Suggested mappings against known column meanings: key column, every column, and the columns
    that refer to other things (what links depend on). Rules alone, and rules plus Claude where a
    cached Claude reading exists. Built-in tables are scored as if unmapped."""
    from . import connectors, mappings, suggest

    tabs = mappings.tables()
    runs = {"rules": [], "rules_and_claude": []}
    used_claude = False
    for case in cases:
        c = connectors.find(data_dir / case["dir"], case["dataset"])
        if c is None:
            continue
        m = tabs.get(case["dataset"]) if case["dir"] == "." else None
        s = suggest.suggest_table(conn, pack, case["dataset"], c,
                                  exclude={(m.source, m.record_type)} if m else frozenset())
        runs["rules"].append((case, s["suggestion"]))
        payload = suggest.claude_input(case["dataset"], s["profile"], s["suggestion"])
        if (hit := suggest.cached_claude(conn, case["dataset"], payload)) is not None:
            used_claude = True
            runs["rules_and_claude"].append((case, suggest.apply_claude(s["suggestion"], hit)))
        else:
            runs["rules_and_claude"].append((case, s["suggestion"]))

    def score(pairs):
        keys = cols = refs = n_keys = n_cols = n_refs = 0
        misses = []
        for case, sug in pairs:
            if case["key"] is not None:
                n_keys += 1
                keys += sug["key"] == case["key"]
                if sug["key"] != case["key"]:
                    misses.append({"table": case["dataset"], "column": "(key)", "got": sug["key"], "want": case["key"]})
            for col, want in case["columns"].items():
                want = want if isinstance(want, list) else [want]
                got = sug["columns"].get(col, {}).get("meaning")
                ok = got in want
                n_cols += 1
                cols += ok
                if set(want) & suggest.REFERENCES:
                    n_refs += 1
                    refs += ok
                if not ok:
                    misses.append({"table": case["dataset"], "column": col, "got": got, "want": want})
        return {"tables": len(pairs), "key": round(keys / max(n_keys, 1), 3), "columns": round(cols / max(n_cols, 1), 3),
                "references": round(refs / max(n_refs, 1), 3), "misses": misses}

    out = {"rules": score(runs["rules"])}
    if used_claude:
        out["rules_and_claude"] = score(runs["rules_and_claude"])
    return out


def run(conn, pack: Pack, keys_dir) -> dict:
    keys_dir = Path(keys_dir)
    load = lambda name: json.loads((keys_dir / name).read_text()) if (keys_dir / name).exists() else None
    out = {}
    for name, fn in [("search.json", search), ("linking.json", linking), ("section_modes.json", section_modes),
                     ("logs.json", logs), ("matches.json", matches)]:
        if (cases := load(name)) is not None:
            out[name.removesuffix(".json")] = fn(conn, pack, cases)
    if (cases := load("schema.json")) is not None:
        out["schema"] = schema(conn, pack, cases, keys_dir.parent)
    return out
