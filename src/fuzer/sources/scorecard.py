"""The source quality scorecard (P2.6): four measures per dataset, kept with every rebuild so each has a trend, and a
drop in any of them is one routed case for the source's owner, not one per record.

    completeness   the mean fill of its columns, from the latest profile (sources.quality)
    validity       the share of its checks passing, counting its key (no duplicates, none empty) as one
    timeliness     100 while a delivery is on time; past its expected interval, the interval over the age
    agreement      of its source's records that join an item another source also has, the share with no open
                   disagreement (conflict questions) about them

Each is 0 to 100, or None where it can't be said (no profile, no expected interval, nothing shared). A measure that
falls `DROP` points or more below its best of the last `WINDOW_DAYS` raises a `source_quality_drop` case keyed by the
dataset, assigned to the system's owner (else the dataset's, else the data steward), saying which measure fell and
from what; it clears when every measure is back within `DROP` of its best.

    measure(conn, d, stats)   one dataset's four measures now
    record(conn)              keep every dataset's measures (after a rebuild); returns {name: measures}
    trend(conn, name, n)      a dataset's last n scores, oldest first
    sync(conn)                record, then raise or clear the drop cases
"""

import datetime as dt

from ..core.exceptions import clear_exception, exception_type, open_keys, raise_exception
from ..core.findings import EXTERNAL, Finding, fact, page

MEASURES = ("completeness", "validity", "timeliness", "agreement")
LABELS = {"completeness": "Completeness", "validity": "Validity", "timeliness": "Timeliness", "agreement": "Agreement"}
DROP = 15.0
WINDOW_DAYS = 30
KEEP_DAYS = 90
BY = "source-quality"

TYPE = exception_type("source_quality_drop", "Source quality dropped", role="role:data_steward", joins=False,
                      description="A dataset's completeness, validity, timeliness or agreement with other sources "
                                  "fell well below where it was; its owner looks at the source.")


def _completeness(conn, d: dict) -> float | None:
    p = conn.execute("select columns from source_profile where dataset_id = %s order by id desc limit 1",
                     (d["id"],)).fetchone()
    fills = [c.get("fill_pct") for c in ((p or {}).get("columns") or {}).values() if c.get("fill_pct") is not None]
    return round(sum(fills) / len(fills), 1) if fills else None


def _validity(conn, d: dict) -> float | None:
    from . import quality

    results = [c["last_ok"] for c in quality.checks(conn, d["name"]) if c["last_ok"] is not None]
    p = conn.execute("select rows, key from source_profile where dataset_id = %s order by id desc limit 1",
                     (d["id"],)).fetchone()
    if p and p["key"] is not None:
        k = p["key"] or {}
        results.append(not (k.get("duplicates") or k.get("empty")))
    return round(100 * sum(results) / len(results), 1) if results else None


def _timeliness(d: dict, hours: float | None, now: dt.datetime) -> float | None:
    if not hours or d["status"] != "active":
        return None
    if not d["loaded_at"]:
        return 0.0
    age = (now - d["loaded_at"]).total_seconds() / 3600
    return 100.0 if age <= hours else round(100 * hours / age, 1)


def _agreement(stats: dict | None) -> float | None:
    s = stats or {}
    shared = s.get("shared") or 0
    if not shared:
        return None
    conflicts = (s.get("open_reviews") or {}).get("conflict", 0)
    return round(100 * max(0.0, 1 - conflicts / shared), 1)


def measure(conn, d: dict, stats: dict | None, now: dt.datetime | None = None) -> dict:
    """One dataset's four measures now (each 0-100 or None)."""
    hours = d["expected_hours"]
    for c in conn.execute("select params from source_check where dataset_id = %s and kind = 'freshness' and enabled",
                          (d["id"],)):
        hours = (c["params"] or {}).get("hours") or hours
    return {"completeness": _completeness(conn, d), "validity": _validity(conn, d),
            "timeliness": _timeliness(d, float(hours) if hours else None, now or dt.datetime.now(dt.UTC)),
            "agreement": _agreement(stats)}


def record(conn) -> dict[str, dict]:
    """Keep every dataset's measures now, for its trend."""
    from . import catalog

    stats = {r["source"]: r["stats"] for r in conn.execute(
        "select distinct on (source) source, stats from source_match_stat order by source, id desc")}
    out = {}
    for name, d in catalog.datasets(conn).items():
        m = measure(conn, d, stats.get(d["source"]))
        if all(v is None for v in m.values()):
            continue
        conn.execute("insert into source_score (dataset_id, completeness, validity, timeliness, agreement) "
                     "values (%s, %s, %s, %s, %s)", (d["id"], *[m[k] for k in MEASURES]))
        out[name] = m
    conn.execute("delete from source_score where at < now() - make_interval(days => %s)", (KEEP_DAYS,))
    return out


def trend(conn, name: str, n: int = 12) -> list[dict]:
    """The dataset's last `n` scores, oldest first."""
    rows = conn.execute(
        "select s.at, s.completeness, s.validity, s.timeliness, s.agreement from source_score s "
        "join source_dataset d on d.id = s.dataset_id where d.name = %s order by s.id desc limit %s", (name, n)).fetchall()
    return [dict(r) for r in reversed(rows)]


def _drops(conn, dataset_id: int, now: dict) -> list[dict]:
    """The measures `now` holds that are DROP points or more below their best of the window before it."""
    best = conn.execute(
        "select max(completeness) as completeness, max(validity) as validity, max(timeliness) as timeliness, "
        "max(agreement) as agreement from source_score where dataset_id = %s "
        "and at >= now() - make_interval(days => %s) "
        "and id < (select max(id) from source_score where dataset_id = %s)",
        (dataset_id, WINDOW_DAYS, dataset_id)).fetchone()
    return [{"measure": k, "was": float(best[k]), "now": float(now[k])} for k in MEASURES
            if now.get(k) is not None and best and best[k] is not None and float(best[k]) - float(now[k]) >= DROP]


def title(name: str, drops: list[dict]) -> str:
    d = drops[0]
    more = f" (and {len(drops) - 1} more)" if len(drops) > 1 else ""
    return f"{name}: {LABELS[d['measure']].lower()} fell from {d['was']:.0f}% to {d['now']:.0f}%{more}"


def finding(d: dict, drops: list[dict]) -> Finding:
    return Finding("sources.scorecard", EXTERNAL, method=TYPE.description, items=(
        fact("Dataset", d["name"]),
        fact("System", d["source"].upper()),
        *(fact(LABELS[x["measure"]], f"{x['was']:.0f}% → {x['now']:.0f}%") for x in drops),
        page("Scorecard", f"#/sources?view=dataset&name={d['name']}", "See the dataset's scorecard"),
    ), data={"dataset": d["name"], "drops": drops})


def sync(conn, by: str = BY) -> dict:
    """Record the scorecard, then one open case per dataset whose quality dropped; cleared once it recovers."""
    from . import catalog

    now = record(conn)
    datasets = catalog.datasets(conn)
    out = {"scored": len(now), "raised": 0, "refreshed": 0, "cleared": 0}
    live = set()
    for name, m in now.items():
        d = datasets[name]
        drops = _drops(conn, d["id"], m)
        if not drops:
            continue
        live.add(name)
        owner = d.get("system_owner") or d.get("owner")
        row = raise_exception(conn, TYPE.name, name, title(name, drops), by, source=d["source"],
                              finding=finding(d, drops), **({"assignee": owner} if owner else {}))
        out["raised" if row.get("raised") else "refreshed"] += 1
    for key in open_keys(conn, TYPE.name) - live:
        clear_exception(conn, TYPE.name, key, by, reason="its quality is back where it was")
        out["cleared"] += 1
    return out


def card(conn, name: str, n: int = 12) -> dict:
    """The dataset page's scorecard: each measure now, its best of the window, whether it dropped, and its trend."""
    t = trend(conn, name, n)
    best = conn.execute(
        "select max(s.completeness) as completeness, max(s.validity) as validity, max(s.timeliness) as timeliness, "
        "max(s.agreement) as agreement from source_score s join source_dataset d on d.id = s.dataset_id "
        "where d.name = %s and s.at >= now() - make_interval(days => %s)", (name, WINDOW_DAYS)).fetchone()
    last = t[-1] if t else {}
    measures = []
    for k in MEASURES:
        now, top = last.get(k), (best or {}).get(k)
        measures.append({"key": k, "label": LABELS[k], "now": now, "best": top,
                         "dropped": now is not None and top is not None and float(top) - float(now) >= DROP,
                         "trend": [r[k] for r in t]})
    return {"measures": measures, "drop": DROP, "window_days": WINDOW_DAYS, "at": last.get("at")}
