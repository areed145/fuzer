"""Context completeness per asset (P2.5): what Fuzer knows about each asset, what is missing, and what each gap
prevents.

An asset is what the work is about: a tag that is not part of another (a pump, a vessel, a valve), or a well. Its
context is gathered from it, the tags under it and the equipment installed at them:

    class        it is classified (a well is one by kind)
    model        the installed equipment names its model
    maintenance  a work order, notification or plan concerns it
    measured     a historian point measures it
    roles        one of those points has a measurement role on it (graph.roles)
    limits       one of those points has an alarm limit, an IOW or a learned normal range
    drawing      it is on a P&ID
    documents    a datasheet, manual or guide mentions it or its model
    criticality  its consequence is rated

What an asset is expected to have comes from its class, as the register's other gaps do: a check is expected of a
class when at least half its assets pass it (`EXPECT`), so valves are not asked for maintenance plans because pumps
have them. Each asset's score is the share of its expected checks it passes.

A gap prevents the detectors that need what is missing and apply to the asset's class (read from the detector
registry, so a new detector's needs count at once). Gaps that prevent something are steward work: the assets whose
gaps prevent the most, weighted by criticality, are open `context_gap` cases (`TOP`), refreshed at every rebuild,
and a case clears when its gaps are filled or it drops out of the top.

    assets(conn)             every asset, with its class, area, what it has and lacks, score and value
    of_asset(conn, fid)      one asset's panel (the asset itself, or the asset a tag or equipment belongs to), with
                             ranked suggestions for what its gaps lack (graph.placement)
    summary(conn)            completeness by area and by class, and the top gaps
    sync(conn)               the top gaps as open cases
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

from ..core import detectors
from ..core.exceptions import clear_exception, exception_type, open_keys, raise_exception
from ..core.findings import EXTERNAL, Finding, asset, fact, page

EXPECT = 0.5    # a check is expected of a class when at least this share of its assets pass it
MIN_CLASS = 3   # a class this small expects nothing: too few to say what is usual
TOP = 25        # the gaps raised as cases, most valuable first
BY = "completeness"
CRITICALITY = {"A": 3, "B": 2, "C": 1}


@dataclass(frozen=True)
class Check:
    key: str
    label: str        # what it is, in a list of what is known
    missing: str      # what is missing, in a sentence
    prevents: str     # what else the gap holds back, beyond detectors
    needs: tuple = ()  # words in a detector's `needs` names that this check supplies


CHECKS = (
    Check("class", "Equipment class", "no equipment class", "class-specific checks and settings",
          needs=("rotating machine",)),
    Check("model", "Make and model", "no make or model", "curves, datasheets and like-for-like comparisons",
          needs=("curve",)),
    Check("maintenance", "Maintenance record", "no maintenance record", "repeat-failure and history on its cases"),
    Check("measured", "Historian points", "no historian points", "trends on its cases", needs=("historian points",)),
    Check("roles", "Measurement roles", "no measurement roles on its points", "role-based trends on its cases"),
    Check("limits", "Limits", "no alarm limits, IOWs or normal ranges", "excursion and IOW cases",
          needs=("limits",)),
    Check("drawing", "Drawing position", "not on a drawing", "tracing what is up and downstream in an upset"),
    Check("documents", "Documents", "no datasheet, manual or guide", "references on its troubleshooting brief"),
    Check("criticality", "Criticality", "no criticality", "ranking its work by consequence"),
)
BY_KEY = {c.key: c for c in CHECKS}

TYPE = exception_type("context_gap", "Asset context missing", role="role:data_steward", joins=False,
                      description="An asset lacks context its class usually has, and a check can't run on it "
                                  "until it is filled.")

_ASSETS = """
with recursive asset as (
    select e.fuzer_id, e.kind from entity e
    where e.merged_into is null and e.retired_at is null
      and (e.kind = 'well' or (e.kind = 'tag' and not exists (
            select 1 from edge p join entity a on a.fuzer_id = p.dst and a.kind in ('tag', 'well')
            where p.src = e.fuzer_id and p.rel = 'part_of')))
), member (asset, fuzer_id, depth) as (
    select fuzer_id, fuzer_id, 0 from asset
    union
    select m.asset, p.src, m.depth + 1 from member m
    join edge p on p.dst = m.fuzer_id and p.rel = 'part_of'
    join entity t on t.fuzer_id = p.src and t.kind = 'tag' and t.merged_into is null
    where m.depth < 3
)
select m.asset, m.fuzer_id, 'tag' as role from member m
union
select m.asset, i.src, 'equipment' from member m
join edge i on i.dst = m.fuzer_id and i.rel = 'installed_at' and i.valid_to is null
join entity q on q.fuzer_id = i.src and q.merged_into is null
"""


def _sets(conn, ids: list[str], equipment: list[str]) -> dict[str, set[str]]:
    """For each check, the members (tags, wells and equipment) that pass it; for `measured`, also the points."""
    def ids_of(sql: str, args=()) -> set[str]:
        return {r["id"] for r in conn.execute(sql, args)}

    allids = ids + equipment
    has = {
        "class": ids_of("select src as id from edge where rel = 'classified_as' and src = any(%s) union "
                        "select fuzer_id from entity where kind = 'well' and fuzer_id = any(%s)", (allids, ids)),
        "model": ids_of("select src as id from edge where rel = 'instance_of' and src = any(%s) union "
                        "select fuzer_id from golden where field = 'model' and fuzer_id = any(%s)",
                        (equipment, equipment)),
        "maintenance": ids_of("select c.dst as id from edge c join entity s on s.fuzer_id = c.src "
                              "and s.kind in ('work_order', 'notification', 'maintenance_plan') "
                              "where c.dst = any(%s)", (allids,)),
        "criticality": ids_of("select fuzer_id as id from golden where field = 'criticality' and fuzer_id = any(%s)",
                              (allids,)),
        "drawing": ids_of("select src as id from edge where src = any(%(i)s) and exists "
                          "(select 1 from unnest(sources) s where s like 'pid%%') union "
                          "select dst from edge where dst = any(%(i)s) and exists "
                          "(select 1 from unnest(sources) s where s like 'pid%%')", {"i": ids}),
    }
    # Documents name the item or the model of what is installed there; a drawing's own mentions don't count.
    models = {r["src"]: r["dst"] for r in conn.execute(
        "select src, dst from edge where rel = 'instance_of' and src = any(%s)", (equipment,))}
    named = ids_of("select m.dst as id from edge m join entity d on d.fuzer_id = m.src "
                   "and d.kind in ('document', 'document_section') where m.rel in ('mentions', 'applies_to') "
                   "and m.dst = any(%s) and exists (select 1 from unnest(m.sources) s where s not like 'pid%%')", (allids + list(set(models.values())),))
    has["documents"] = {i for i in allids if i in named or models.get(i) in named}
    points = defaultdict(set)
    for r in conn.execute("select src, dst from edge where rel = 'has_timeseries' and src = any(%s)", (ids,)):
        points[r["src"]].add(r["dst"])
    has["measured"] = set(points)
    limited: set[str] = set()
    wanted = sorted({p for ps in points.values() for p in ps})
    for table in ("point_limit", "point_normal"):
        if wanted and conn.execute("select to_regclass(%s) as t", (table,)).fetchone()["t"]:
            limited |= ids_of(f"select distinct point_id as id from {table} where point_id = any(%s)", (wanted,))
    has["limits"] = {m for m, ps in points.items() if ps & limited}
    has["roles"] = ids_of("select distinct asset as id from measurement_role where asset = any(%s)", (allids,))
    return has


def _gather(conn, only: list[str] | None = None) -> list[dict]:
    members: dict[str, dict[str, set]] = defaultdict(lambda: {"tag": set(), "equipment": set()})
    for r in conn.execute(_ASSETS):
        if only is None or r["asset"] in only:
            members[r["asset"]][r["role"]].add(r["fuzer_id"])
    if not members:
        return []
    tags = sorted({t for m in members.values() for t in m["tag"]})
    equipment = sorted({q for m in members.values() for q in m["equipment"]})
    has = _sets(conn, tags, equipment)
    fids = sorted(members)
    classes = detectors.classes_of(conn, fids)
    names = {c["code"]: c["name"] for c in detectors.asset_classes(conn)}
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (fids,))}
    describe = {r["fuzer_id"]: r["v"] for r in conn.execute(
        "select fuzer_id, value #>> '{}' as v from golden where field = 'description' and fuzer_id = any(%s)",
        (fids,))}
    crit = {r["fuzer_id"]: r["v"] for r in conn.execute(
        "select fuzer_id, value #>> '{}' as v from golden where field = 'criticality' and fuzer_id = any(%s)",
        (fids,))}
    areas = {r["fid"]: r["area"] for r in conn.execute(
        """
        select distinct on (m.fid) m.fid, coalesce(g.value #>> '{}', l.label) as area
        from unnest(%s::text[]) m(fid)
        join edge p on p.src = m.fid and p.rel in ('part_of', 'located_at')
        join entity a on a.fuzer_id = p.dst and a.kind = 'area'
        left join entity_label l on l.fuzer_id = a.fuzer_id
        left join golden g on g.fuzer_id = a.fuzer_id and g.field = 'description'
        order by m.fid, p.rel desc
        """, (fids,))}
    out = []
    for fid in fids:
        m = members[fid]
        every = m["tag"] | m["equipment"]
        code = classes.get(fid) or "unclassified"
        out.append({"fuzer_id": fid, "label": labels.get(fid, fid), "description": describe.get(fid),
                    "class": code, "class_name": names.get(code, code), "area": areas.get(fid),
                    "criticality": crit.get(fid),
                    "has": {c.key: bool(every & has[c.key]) for c in CHECKS}})
    return out


def _blocks(d: detectors.Detector, check: Check, class_name: str, code: str) -> bool:
    if d.classes and not any(c in class_name.lower() or c == code for c in d.classes):
        return False
    if check.key == "class":
        return bool(d.classes)
    if check.key == "roles":
        return bool(d.roles)
    return any(word in need for need in d.needs for word in check.needs)


def _score(rows: list[dict]) -> list[dict]:
    """Expectations by class, each asset's score, what each gap prevents and the value of filling them."""
    by_class: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_class[r["class"]].append(r)
    registry = list(detectors.registry().values())
    for code, members in by_class.items():
        n = len(members)
        share = {c.key: sum(r["has"][c.key] for r in members) / n for c in CHECKS}
        expected = [c for c in CHECKS if n >= MIN_CLASS and share[c.key] >= EXPECT]
        name = members[0]["class_name"]
        prevented = {c.key: [{"name": d.name, "title": d.title} for d in registry if _blocks(d, c, name, code)]
                     for c in CHECKS}
        for r in members:
            r["expected"] = [c.key for c in expected]
            r["share"] = {k: round(v, 3) for k, v in share.items()}
            r["missing"] = [c.key for c in expected if not r["has"][c.key]]
            r["score"] = round(1 - len(r["missing"]) / len(expected), 3) if expected else None
            r["gaps"] = [{"check": k, "label": BY_KEY[k].label, "missing": BY_KEY[k].missing,
                          "detectors": prevented[k], "prevents": BY_KEY[k].prevents,
                          "usual": f"{round(share[k] * 100)}% of {name.lower()} have it"} for k in r["missing"]]
            blocked = {d["name"] for g in r["gaps"] for d in g["detectors"]}
            r["blocked"] = sorted(blocked)
            r["value"] = CRITICALITY.get((r["criticality"] or "").upper(), 1) * len(blocked)
    return rows


def assets(conn) -> list[dict]:
    """Every asset with what it has and lacks, most valuable gaps first."""
    rows = _score(_gather(conn))
    rows.sort(key=lambda r: (-r["value"], r["score"] if r["score"] is not None else 2, r["label"]))
    return rows


def asset_of(conn, fuzer_id: str) -> str | None:
    """The asset an entity belongs to: itself, the asset a tag is under, or where equipment is installed."""
    for r in conn.execute(_ASSETS + " order by 1"):
        if r["fuzer_id"] == fuzer_id:
            return r["asset"]
    return None


def of_asset(conn, fuzer_id: str, pack=None) -> dict | None:
    """One asset's panel: what is known, what is missing and what each gap prevents. The asset's class is compared
    with every asset of that class, so the expectation is the same one the summary uses."""
    fid = asset_of(conn, fuzer_id)
    if not fid:
        return None
    row = next((r for r in assets(conn) if r["fuzer_id"] == fid), None)
    if row is None:
        return None
    known = [{"check": c.key, "label": c.label, "has": row["has"][c.key], "expected": c.key in row["expected"]}
             for c in CHECKS]
    return {**row, "known": known, "of": fuzer_id if fuzer_id != fid else None,
            "suggestions": suggestions(conn, fid, row, pack)}


def suggestions(conn, fid: str, row: dict, pack=None) -> list[dict]:
    """Ranked proposals for what its gaps lack (P2.1d): an area when it is in none, the sheets that show what it is
    piped to when it is on no drawing, its class's guides and its neighbours' documents when no document names it.
    A proposal a steward already answered is left out."""
    from ..graph import placement

    answered = {(r["rel"], r["target"]) for r in conn.execute(
        "select rel, target from placement_decision where fuzer_id = %s", (fid,))}
    out = []
    if not row["area"] and pack is not None:
        out += placement.parents(conn, pack, [fid]).get(fid, [])
    want = tuple(k for k in ("drawing", "documents") if not row["has"][k])
    if want:
        out += placement.documents(conn, [fid], want).get(fid, [])
    return [p for p in out if (p["rel"], p["target"]) not in answered]


def summary(conn, top: int = TOP) -> dict:
    """Completeness by area and by class (the share of expected checks passed), and the top gaps."""
    rows = assets(conn)
    def roll(key: str) -> list[dict]:
        acc: dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            k = r[key] or "No area"
            acc[k]["assets"] += 1
            acc[k]["expected"] += len(r["expected"])
            acc[k]["missing"] += len(r["missing"])
            acc[k]["blocked"] += bool(r["blocked"])
            for m in r["missing"]:
                acc[k][f"gap:{m}"] += 1
        out = []
        for k, c in acc.items():
            gaps = sorted(((g.removeprefix("gap:"), n) for g, n in c.items() if g.startswith("gap:")), key=lambda x: -x[1])
            out.append({"name": k, "assets": c["assets"], "blocked": c["blocked"],
                        "score": round(1 - c["missing"] / c["expected"], 3) if c["expected"] else None,
                        "gaps": [{"check": g, "label": BY_KEY[g].label, "assets": n} for g, n in gaps[:3]]})
        return sorted(out, key=lambda x: (x["score"] if x["score"] is not None else 2, -x["assets"]))
    return {"areas": roll("area"), "classes": roll("class_name"),
            "top": [_brief(r) for r in rows if r["value"] > 0][:top],
            "checks": [{"check": c.key, "label": c.label} for c in CHECKS]}


def _brief(r: dict) -> dict:
    return {k: r[k] for k in ("fuzer_id", "label", "description", "class_name", "area", "criticality", "score",
                              "missing", "blocked", "value")}


def title(r: dict) -> str:
    """What is missing, in words a steward reads in a list."""
    what = [BY_KEY[k].missing for k in r["missing"]]
    lacks = what[0] if len(what) == 1 else ", ".join(what[:-1]) + " and " + what[-1]
    n = len(r["blocked"])
    return f"{r['label']} ({r['class_name'].lower()}): {lacks}; {n} check{'s' if n != 1 else ''} can't run"


def finding(r: dict) -> Finding:
    """The case's evidence: the asset, what is missing, what it prevents and where to see it all."""
    titles = {d["name"]: d["title"] for g in r["gaps"] for d in g["detectors"]}
    return Finding("completeness.context_gap", EXTERNAL, method=TYPE.description, items=(
        asset("Asset", r["fuzer_id"]),
        fact("Missing", ", ".join(BY_KEY[k].label.lower() for k in r["missing"])),
        fact("Checks that can't run", "; ".join(titles[n] for n in r["blocked"])),
        fact("Criticality", r["criticality"]),
        fact("Completeness", round(r["score"] * 100) if r["score"] is not None else None, "%"),
        page("Context", f"#/e/{r['fuzer_id']}", "See what is known on the asset page"),
    ), data={"fuzer_id": r["fuzer_id"], "missing": r["missing"], "blocked": r["blocked"], "value": r["value"]})


def sync(conn, top: int = TOP, by: str = BY) -> dict:
    """The `top` assets whose gaps prevent the most are open cases; the rest are cleared. {raised, refreshed,
    cleared}."""
    out = {"raised": 0, "refreshed": 0, "cleared": 0}
    live = set()
    for r in [r for r in assets(conn) if r["value"] > 0][:top]:
        live.add(r["fuzer_id"])
        row = raise_exception(conn, TYPE.name, r["fuzer_id"], title(r)[:300], by, refs=[r["fuzer_id"]],
                              source=BY, finding=finding(r))
        out["raised" if row.get("raised") else "refreshed"] += 1
    for key in open_keys(conn, TYPE.name) - live:
        clear_exception(conn, TYPE.name, key, by, reason="its context is filled, or other gaps now prevent more")
        out["cleared"] += 1
    return out
