"""Leak detection and repair (LDAR) survey evidence (#371): what each survey screened and found, the components that
leak again and again, and which equipment the surveys cover. Evidence only, with survey dates: a component that
wasn't found leaking was clean when screened, not since, and equipment no survey reached is unknown, never clean.

    surveys(conn, since, until)   per survey: components screened, leaks found, tags reached; the repeat leakers;
                                  and coverage: each in-scope tag's latest survey, or unknown

A tag is in scope when its class is one the surveys screen anywhere (a pump class surveyed on one platform puts every
pump in scope), so the programme's own reach says what it should cover.
"""

from collections import defaultdict
from datetime import date, timedelta

from ..graph import operations as ops

LEAK_PPM = 500  # a Method 21 screening reading at or above this is a leak
REPEAT_SURVEYS = 2  # a component leaking in this many surveys in the window is a repeat leaker
COVERAGE_DAYS = 365  # a tag screened within this long before the window's end counts as covered
QUANTITY = "leak_screening"


def surveys(conn, since: str | None = None, until: str | None = None, days: int = 730) -> dict:
    """Each survey in [since, until), repeat leakers, and coverage of the in-scope tags."""
    since, until = ops._window(conn, since, until, days)
    rows = conn.execute(
        "select m.fuzer_id, l.label, m.at, m.value, m.location, m.method, m.record from measurement m "
        "left join entity_label l using (fuzer_id) where m.quantity = %s and m.at >= %s and m.at < %s order by m.at",
        (QUANTITY, since, until)).fetchall()
    base = {"since": since, "until": until, "leak_ppm": LEAK_PPM, "repeat_surveys": REPEAT_SURVEYS,
            "coverage_days": COVERAGE_DAYS, "source": "LDAR survey readings (Method 21 screening, ppm)"}
    if not rows:
        return base | {"surveys": [], "repeat_leakers": [], "coverage": {"in_scope": 0, "covered": 0, "unknown": []},
                       "verdict": "no_surveys", "note": "No LDAR survey recorded in the window: leaks on every tag are "
                                                        "unknown, not absent."}
    by_day: dict[date, dict] = {}
    leaks: dict[tuple[str, str], list[dict]] = defaultdict(list)
    last_seen: dict[str, date] = {}
    name: dict[str, str] = {}
    for r in rows:
        day = r["at"].date()
        s = by_day.setdefault(day, {"date": day.isoformat(), "components": 0, "leaks": [], "tags": set(),
                                    "methods": set()})
        s["components"] += 1
        s["tags"].add(r["fuzer_id"])
        s["methods"].add(r["method"] or "")
        name[r["fuzer_id"]] = r["label"] or r["fuzer_id"]
        last_seen[r["fuzer_id"]] = max(day, last_seen.get(r["fuzer_id"], day))
        if float(r["value"]) >= LEAK_PPM:
            hit = {"tag": name[r["fuzer_id"]], "id": r["fuzer_id"], "component": r["location"], "ppm": float(r["value"]),
                   "date": day.isoformat(), "record": r["record"]}
            s["leaks"].append(hit)
            leaks[(r["fuzer_id"], r["location"] or "")].append(hit)
    first_seen: dict[str, date] = {}
    for d in sorted(by_day):
        for f in by_day[d]["tags"]:
            first_seen.setdefault(f, d)
    survey_rows = [{"date": s["date"], "components": s["components"], "tags": len(s["tags"]), "leaks": len(s["leaks"]),
                    "leak_rate_pct": round(100 * len(s["leaks"]) / s["components"], 2),
                    "methods": sorted(m for m in s["methods"] if m), "leaking": s["leaks"],
                    "missed": sorted(name[f] for f in first_seen if first_seen[f] < d and f not in s["tags"])}
                   for d, s in ((d, by_day[d]) for d in sorted(by_day))]
    repeat = sorted(({"tag": hits[0]["tag"], "id": fid, "component": comp, "surveys": [h["date"] for h in hits],
                      "max_ppm": max(h["ppm"] for h in hits), "latest": hits[-1]["date"],
                      "consecutive": _consecutive([h["date"] for h in hits], [s["date"] for s in survey_rows])}
                     for (fid, comp), hits in leaks.items() if len(hits) >= REPEAT_SURVEYS),
                    key=lambda x: (-len(x["surveys"]), x["tag"]))
    # Coverage: every live tag of a class the surveys screen.
    surveyed = list(last_seen)
    classes = {r["dst"] for r in conn.execute(
        "select distinct dst from edge where rel = 'classified_as' and src = any(%s)", (surveyed,))}
    scope = conn.execute(
        "select e.fuzer_id, l.label, c.dst as class, cl.label as class_label from entity e "
        "join edge c on c.src = e.fuzer_id and c.rel = 'classified_as' left join entity_label l on l.fuzer_id = e.fuzer_id "
        "left join entity_label cl on cl.fuzer_id = c.dst "
        "where e.kind = 'tag' and e.retired_at is null and e.merged_into is null and c.dst = any(%s)",
        (list(classes),)).fetchall()
    end = date.fromisoformat(until[:10])
    cutoff = end - timedelta(days=COVERAGE_DAYS)
    in_scope = {r["fuzer_id"]: r for r in scope} | {f: {"fuzer_id": f, "label": name[f], "class_label": None}
                                                     for f in surveyed}
    unknown, stale, covered = [], [], 0
    for fid, r in sorted(in_scope.items(), key=lambda kv: kv[1]["label"] or kv[0]):
        seen = last_seen.get(fid)
        if seen is None:
            unknown.append({"tag": r["label"] or fid, "id": fid, "class": r.get("class_label"), "last_survey": None})
        elif seen < cutoff:
            stale.append({"tag": r["label"] or fid, "id": fid, "class": r.get("class_label"),
                          "last_survey": seen.isoformat()})
        else:
            covered += 1
    verdict = "repeat_leakers" if repeat else "leaks_repaired" if any(s["leaks"] for s in survey_rows) else "no_leaks_found"
    return base | {
        "surveys": survey_rows, "repeat_leakers": repeat, "verdict": verdict,
        "coverage": {"in_scope": len(in_scope), "covered": covered, "covered_pct": round(100 * covered / len(in_scope), 1),
                     "unknown": unknown, "not_recent": stale},
        "summary": {"surveys": len(survey_rows), "components_screened": sum(s["components"] for s in survey_rows),
                    "leaks": sum(s["leaks"] for s in survey_rows), "repeat_leakers": len(repeat),
                    "unknown_tags": len(unknown)},
        "note": (f"A reading of {LEAK_PPM} ppm or more is a leak; a component leaking in {REPEAT_SURVEYS} or more "
                 "surveys is a repeat leaker. Equipment of a surveyed class that no survey reached is unknown, not "
                 "clean, and a clean reading holds only for its survey date. Evidence only, not an emissions "
                 "inventory.")}


def _consecutive(hit_dates: list[str], survey_dates: list[str]) -> int:
    """The longest run of surveys in a row that found the component leaking."""
    best = run = 0
    hits = set(hit_dates)
    for d in survey_dates:
        run = run + 1 if d in hits else 0
        best = max(best, run)
    return best
