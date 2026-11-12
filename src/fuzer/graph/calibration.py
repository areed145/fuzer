"""Calibrated match confidence (P2.2, from the #94 experiment): what a match score is worth, measured per source.

The matcher keeps every pair it scored on the last rebuild in `match_score`. Calibration labels those pairs and
measures them:

- **Labels** come from an answer key (`matches.json`: each record and the record it really is, as the fake plant
  writes it and as an engineer curates it on a real asset) and from the stewards' own match decisions
  (`pinned_link`), which win where both speak. A pair is the same item when both sides resolve to the same true
  item; a record the key doesn't list is nobody else, so a pair with it is not the same item.
- **Per source.** A pair belongs to the register being matched in: the less trusted of its two sources by rank,
  an unranked source counting as less trusted than any ranked one (both, when they rank the same). So a source's thresholds are measured on its own records: a
  contractor register with no serials scores differently from a migrated ERP, and the system of record they
  match into isn't held to either.
- **Ambiguous pairs** (a record that fits more than one item) always go to review whatever they score, so they
  are counted but not measured: straight-through never takes them.
- **Calibrated probability.** Pool-adjacent-violators over (score, same item) gives a monotone step function: the
  share of pairs at or near that score that were the same item.
- **Thresholds.** Straight-through (`auto_accept`) is the lowest score at which every pair at or above it is at
  least 99% correct; review (`review_min`) is the lowest score of any true pair, so nothing true is rejected
  unseen. Each comes with its measured precision, the lower bound of its 95% interval, recall and how many
  labelled pairs it rests on. The cautious direction is adopted on any evidence (a higher straight-through
  threshold, a lower review floor: both send more to a person); the other only when at least `MIN_PAIRS`
  labelled pairs stand behind it, since 100% of ten pairs is not 99%.

`measure` writes `match_calibration`; `adopt` sets each measured source's match rule (`equipment/<source>`), which
the next rebuild uses, audited like any rule change.
"""

import json
from collections import defaultdict
from pathlib import Path

from psycopg.types.json import Jsonb

from ..core.jsonsafe import plain

TARGET = 0.99  # straight-through precision the roadmap sets (P2.2)
MIN_PAIRS = 30  # labelled pairs at or above a threshold before one looser than the kind's is adopted


def truth_from_key(cases: list[dict], record_types=("equipment", "asset")) -> dict[tuple[str, str], str]:
    """(source, key) -> the true item, from an answer key's [{source, key, same_as: {source, key}}]."""
    item: dict[tuple[str, str], str] = {}
    for c in cases:
        target = (c["same_as"]["source"], str(c["same_as"]["key"]))
        root = item.get(target, f"{target[0]}:{target[1]}")
        item[target] = root
        item[(c["source"], str(c["key"]))] = root
    return item


def _split(ref: str) -> tuple[str, str]:
    source, _, rest = ref.partition(":")
    return source, rest.partition(":")[2]


def wilson_lower(right: int, n: int, z: float = 1.96) -> float | None:
    """The lower bound of the 95% Wilson interval for a measured share."""
    if not n:
        return None
    p = right / n
    return round((p + z * z / (2 * n) - z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5) / (1 + z * z / n), 4)


def labelled(conn, truth: dict | None, kind: str = "equipment") -> list[dict]:
    """The scored pairs with a label: {left, right, sources, score, outcome, same, by}."""
    pins = {frozenset((p["left_ref"], p["right_ref"])): p["rule"] == "must"
            for p in conn.execute("select left_ref, right_ref, rule from pinned_link")}
    out = []
    for r in conn.execute("select * from match_score where kind = %s", (kind,)):
        pair = frozenset((r["left_ref"], r["right_ref"]))
        if pair in pins:
            same, by = pins[pair], "steward"
        elif truth is not None:
            a, b = (truth.get(_split(x)) for x in (r["left_ref"], r["right_ref"]))
            same, by = (a is not None and a == b), "answer key"
        else:
            continue
        out.append({"left": r["left_ref"], "right": r["right_ref"], "sources": list(r["sources"]),
                    "score": float(r["score"]), "outcome": r["outcome"], "same": same, "by": by})
    return out


def isotonic(points: list[tuple[float, bool]]) -> list[dict]:
    """Pool adjacent violators: [{upto, p, n}] in ascending score, p the calibrated probability up to that score."""
    blocks: list[list[float]] = []  # [sum of labels, count, highest score]
    for score, same in sorted(points):
        blocks.append([float(same), 1, score])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            s, n, hi = blocks.pop()
            blocks[-1] = [blocks[-1][0] + s, blocks[-1][1] + n, hi]
    return [{"upto": round(hi, 3), "p": round(s / n, 3), "n": int(n)} for s, n, hi in blocks]


def probability(curve: list[dict], score: float) -> float | None:
    """The calibrated probability of a score on a measured curve."""
    for step in curve:
        if score <= step["upto"]:
            return step["p"]
    return curve[-1]["p"] if curve else None


def confidence(conn, kind: str = "equipment"):
    """How sure an automatic match is, for the human gate (P5.10): `fn(sources, score)`. Where the pair's source has
    been measured (its least trusted side, as `measure` attributes pairs), it is the precision measured at that score
    or above: of the labelled matches scored this high, the share that were the same item. A source not yet measured
    has only its score to go on, so the score is the confidence."""
    rank = {r["name"]: r["rank"] for r in conn.execute("select name, rank from source_system")}
    curves = {r["source"]: r["result"].get("curve") or [] for r in conn.execute(
        "select source, result from match_calibration where kind = %s", (kind,))}

    def fn(sources, score: float) -> tuple[float, str]:
        ranks = {s: rank.get(s) if rank.get(s) is not None else float("inf") for s in sources}
        measured = [s for s, r in ranks.items() if r == max(ranks.values()) and curves.get(s)]
        for s in measured:
            at = [c for c in curves[s] if c["threshold"] <= score]
            if at:
                return float(at[-1]["precision"]), f"measured on {s}'s labelled pairs"
        return float(score), "its match score (not yet measured)"
    return fn


def _measure(every: list[dict]) -> dict:
    pairs = [p for p in every if p["outcome"] != "ambiguous"]
    positives = [p["score"] for p in pairs if p["same"]]
    scores = sorted({p["score"] for p in pairs})
    curve = []
    for t in scores:
        above = [p for p in pairs if p["score"] >= t]
        right = sum(p["same"] for p in above)
        curve.append({"threshold": t, "precision": round(right / len(above), 4), "pairs": len(above),
                      "lower": wilson_lower(right, len(above)),
                      "recall": round(right / len(positives), 4) if positives else None})
    ok = [c for c in curve if all(d["precision"] >= TARGET for d in curve if d["threshold"] >= c["threshold"])]
    auto = ok[0] if ok else None
    out = {"pairs": len(pairs), "same": len(positives), "ambiguous": len(every) - len(pairs), "curve": curve,
           "calibrated": isotonic([(p["score"], p["same"]) for p in pairs]), "target": TARGET}
    if auto:
        out["auto_accept"] = {k: auto[k] for k in ("threshold", "precision", "lower", "pairs", "recall")}
    if positives:
        floor = min(positives)
        out["review_min"] = {"threshold": min(floor, auto["threshold"]) if auto else floor,
                             "rejected_true": 0}
    return out


def measure(conn, truth: dict | None, by: str, kind: str = "equipment", labels: str = "answer key") -> dict:
    """Measure each source's scored pairs against the labels and store the result. Returns {source: result}."""
    pairs = labelled(conn, truth, kind)
    rank = {r["name"]: r["rank"] for r in conn.execute("select name, rank from source_system")}
    by_source = defaultdict(list)
    for p in pairs:
        ranks = {s: rank.get(s) if rank.get(s) is not None else float("inf") for s in p["sources"]}  # unranked: least
        for s in [s for s, r in ranks.items() if r == max(ranks.values())]:
            by_source[s].append(p)
    out = {s: _measure(ps) for s, ps in sorted(by_source.items())}
    conn.execute("delete from match_calibration where kind = %s", (kind,))
    for s, result in out.items():
        conn.execute("insert into match_calibration (kind, source, result, labels, measured_by) values (%s, %s, %s, %s, %s)",
                     (kind, s, Jsonb(plain(result)), labels, by))
    return out


def measure_from_keys(conn, keys_dir, by: str, kind: str = "equipment") -> dict:
    """Measure against `matches.json` in an answer-key folder, plus the stewards' decisions."""
    path = Path(keys_dir) / "matches.json"
    truth = truth_from_key(json.loads(path.read_text())) if path.exists() else None
    return measure(conn, truth, by, kind, labels="answer key and steward decisions" if truth else "steward decisions")


def recommended(result: dict, base: dict) -> dict | None:
    """The thresholds a measurement recommends for its source against the kind's (`base`): None when nothing it
    measured reaches the target. Looser than the kind's only on `MIN_PAIRS` labelled pairs."""
    if "auto_accept" not in result:
        return None
    a = result["auto_accept"]
    auto = a["threshold"] if a["threshold"] >= base["auto_accept"] or a["pairs"] >= MIN_PAIRS else base["auto_accept"]
    review = result.get("review_min", {}).get("threshold", auto)
    if review > base["review_min"] and result.get("same", 0) < MIN_PAIRS:
        review = base["review_min"]  # a higher floor rejects pairs unseen: only on enough true pairs
    return {"auto_accept": round(auto, 3), "review_min": round(min(review, auto), 3)}


def _base(conn, kind: str) -> dict:
    from ..core.pack import current_pack
    from ..sources import rules

    return rules.apply(conn, current_pack(conn)).matching[kind]


def adopt(conn, by: str, kind: str = "equipment", reason: str | None = None) -> dict:
    """Set each measured source's match rule from its calibration. Returns {source: thresholds or None}."""
    from ..sources import rules

    base, out = _base(conn, kind), {}
    for r in conn.execute("select source, result from match_calibration where kind = %s order by source", (kind,)):
        want = recommended(r["result"], base)
        out[r["source"]] = want
        if want:
            rules.set_rule(conn, "match", f"{kind}/{r['source']}", want, by,
                           reason or f"calibrated: {r['result']['auto_accept']['precision']:.0%} measured precision")
    return out


def listing(conn, matching: dict | None = None) -> list[dict]:
    """Each calibrated source with the thresholds in use and what was measured at them (for the Rules page)."""
    from .resolve import thresholds

    rows = []
    for r in conn.execute("select * from match_calibration order by kind, source"):
        res, kind = r["result"], r["kind"]
        used = thresholds(matching, kind, [r["source"]]) if matching and kind in matching else None
        at = None
        if used:
            above = [c for c in res["curve"] if c["threshold"] >= used["auto_accept"]]
            at = above[0] if above else {"threshold": used["auto_accept"], "precision": None, "lower": None,
                                         "pairs": 0, "recall": 0}
        base = matching[kind] if matching and kind in matching else None
        rows.append(plain({"kind": kind, "source": r["source"], "labels": r["labels"], "measured_at": r["measured_at"],
                           "measured_by": r["measured_by"], "pairs": res["pairs"], "same": res["same"],
                           "ambiguous": res.get("ambiguous", 0), "target": res["target"],
                           "recommended": recommended(res, base) if base else None,
                           "in_use": used, "at_auto_accept": at, "calibrated": res["calibrated"]}))
    return rows
