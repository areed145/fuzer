"""Data quality of each source (#225): a profile saved with every load, checks with their results per run,
cross-source coverage and match statistics after each rebuild, freshness, and one health summary per dataset.

- Profile: per column its type, fill %, distinct count, top values and samples; per table the key's
  uniqueness and churn between runs. Each load keeps one (source_profile), compared with the one before it:
  fill dropping, new values in a coded column, a type change are flagged as profile drift.
- Checks (source_check): key_unique, not_null, range, accepted, row_count run on each load before it is
  applied; a failing blocking check holds the dataset (its records stay as they were) with a `check` review
  item. freshness and coverage are judged by `health` and after rebuilds. Duplicate keys always hold a load.
- Coverage and matching (source_match_stat, after each rebuild): for each source, the share of its links that
  found what they point at, how its records matched (auto, review, new), how many join an entity another
  source also has, the golden fields it wins and its open reviews.
"""

import datetime as dt
import re
from collections import Counter

from psycopg.types.json import Jsonb

from ..core.jsonsafe import plain

PROFILE_ROWS = 200_000  # profile at most this many rows of a load (the rest are counted, not looked at)
DISTINCT_CAP = 10_000
TOP = 5
SAMPLES = 3
CHECK_KINDS = {
    "key_unique": "no two records share a key",
    "not_null": "the column is filled (params.max_pct: the share allowed empty, default 0)",
    "range": "numbers in the column within params.min / params.max",
    "accepted": "the column holds only params.values",
    "row_count": "the row count within params.pct % of the last load",
    "freshness": "a new delivery within params.hours (default: the dataset's expected_hours)",
    "coverage": "at least params.min_pct % of the source's links resolve (params.rel: one link type)",
}
LOAD_CHECKS = ("key_unique", "not_null", "range", "accepted", "row_count")
SIGNIFICANT = {"fill dropped", "type changed", "column gone"}  # profile drift worth telling someone about
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?")
_NUM = re.compile(r"^-?\d+([.,]\d+)?([eE][-+]?\d+)?$")


class CheckError(ValueError):
    pass


# ---- profiles ------------------------------------------------------------------------------------

def _kind(v) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, (dict, list)):
        return "object"
    s = str(v).strip()
    if _NUM.match(s):
        return "number"
    if _DATE.match(s):
        return "date"
    return "text"


class Profiler:
    """A column profile built while the rows stream into the stage table, so a load reads nothing twice."""

    def __init__(self):
        self.rows = 0
        self.cols: dict[str, dict] = {}

    def add(self, row: dict) -> None:
        self.rows += 1
        if self.rows > PROFILE_ROWS:
            return
        for k, v in row.items():
            c = self.cols.get(k)
            if c is None:
                c = self.cols[k] = {"filled": 0, "kinds": Counter(), "values": Counter(), "capped": False, "samples": []}
            if v in (None, ""):
                continue
            c["filled"] += 1
            c["kinds"][_kind(v)] += 1
            if isinstance(v, (dict, list)):
                continue
            s = str(v)
            if s in c["values"] or len(c["values"]) < DISTINCT_CAP:
                c["values"][s] += 1
            else:
                c["capped"] = True
            if len(c["samples"]) < SAMPLES and s not in c["samples"]:
                c["samples"].append(s[:80])

    def result(self) -> dict:
        seen = min(self.rows, PROFILE_ROWS)
        cols = {}
        for k, c in self.cols.items():
            kind = c["kinds"].most_common(1)[0][0] if c["kinds"] else None
            distinct = len(c["values"])
            cols[k] = {"type": kind, "fill_pct": round(100 * c["filled"] / seen, 1) if seen else 0.0,
                       "distinct": f">{DISTINCT_CAP}" if c["capped"] else distinct,
                       "top": [[v, n] for v, n in c["values"].most_common(TOP)] if distinct <= seen // 2 or distinct < 50
                       else [], "samples": c["samples"], "coded": not c["capped"] and 0 < distinct <= 50 and seen >= 20,
                       "values": sorted(c["values"]) if not c["capped"] and 0 < distinct <= 50 else None}
        return {"rows": self.rows, "sampled": self.rows > PROFILE_ROWS, "columns": cols}


def profile_drift(old: dict | None, new: dict) -> list[dict]:
    """What changed between two profiles of a dataset worth a look: fill dropping by 20 points or more, a
    column's type changing, new values in a coded column, columns gone or new."""
    if not old:
        return []
    out = []
    o, n = old.get("columns") or {}, new.get("columns") or {}
    for k in n.keys() - o.keys():
        out.append({"column": k, "change": "new column"})
    for k in o.keys() - n.keys():
        out.append({"column": k, "change": "column gone"})
    for k in n.keys() & o.keys():
        a, b = o[k], n[k]
        if a.get("fill_pct", 0) - b.get("fill_pct", 0) >= 20:
            out.append({"column": k, "change": "fill dropped", "was": a["fill_pct"], "now": b["fill_pct"]})
        if a.get("type") and b.get("type") and a["type"] != b["type"]:
            out.append({"column": k, "change": "type changed", "was": a["type"], "now": b["type"]})
        if a.get("coded") and b.get("values") and a.get("values"):
            new_vals = sorted(set(b["values"]) - set(a["values"]))
            if new_vals:
                out.append({"column": k, "change": "new values", "values": new_vals[:10]})
    return out


def key_stats(conn, datasets: list[str]) -> dict[str, dict]:
    """Per staged dataset: duplicate and empty keys (with a few examples)."""
    out = {d: {"duplicates": 0, "empty": 0, "examples": []} for d in datasets}
    for r in conn.execute("""
        select dataset, count(*) filter (where n > 1) as dup, sum(n) filter (where k = '') as empty,
               (array_agg(k order by n desc) filter (where n > 1))[1:3] as examples
        from (select dataset, source_key as k, count(*) as n from source_record_stage group by 1, 2) x group by dataset
    """):
        out[r["dataset"]] = {"duplicates": r["dup"] or 0, "empty": r["empty"] or 0, "examples": r["examples"] or []}
    return out


def save_profile(conn, d: dict, run_id: int | None, prof: dict, key: dict | None) -> dict:
    prev = conn.execute("select columns, rows from source_profile where dataset_id = %s order by id desc limit 1",
                        (d["id"],)).fetchone()
    drift = profile_drift({"columns": prev["columns"]} if prev else None, prof)
    row = conn.execute(
        "insert into source_profile (dataset_id, run_id, rows, sampled, columns, key, drift) values (%s, %s, %s, %s, %s, %s, %s) "
        "returning id", (d["id"], run_id, prof["rows"], prof["sampled"], Jsonb(plain(prof["columns"])), Jsonb(plain(key)),
                         Jsonb(drift))).fetchone()
    return {"id": row["id"], "drift": drift}


def profile_now(conn, name: str, data_dir, rows: int = PROFILE_ROWS) -> dict:
    """Profile a dataset on demand (not loading it) and keep the profile."""
    from . import catalog, ingest

    d = catalog.dataset(conn, name)
    if not d:
        raise CheckError(f"no dataset {name!r}")
    c = ingest.dataset(data_dir, name, conn)
    if c is None:
        raise CheckError(f"{name} isn't there")
    p, keys = Profiler(), Counter()
    for i, (_, _, key, payload) in enumerate(ingest._records(name, c, d)):
        if i >= rows:
            break
        p.add(payload)
        keys[key] += 1
    prof = p.result()
    key = {"duplicates": sum(1 for n in keys.values() if n > 1), "empty": keys.get("", 0),
           "examples": [k for k, n in keys.most_common(3) if n > 1]}
    return {**save_profile(conn, d, None, prof, key), **prof, "key": key}


def profiles(conn, name: str, limit: int = 10) -> list[dict]:
    return [plain(dict(r)) for r in conn.execute(
        "select p.* from source_profile p join source_dataset d on d.id = p.dataset_id where d.name = %s "
        "order by p.id desc limit %s", (name, limit))]


# ---- checks --------------------------------------------------------------------------------------

def add_check(conn, dataset: str, kind: str, by: str, column: str | None = None, params: dict | None = None,
              blocking: bool = False, note: str | None = None) -> dict:
    from . import catalog

    d = catalog.dataset(conn, dataset)
    if not d:
        raise CheckError(f"no dataset {dataset!r}")
    if kind not in CHECK_KINDS:
        raise CheckError(f"unknown check {kind!r}; one of {sorted(CHECK_KINDS)}")
    params = params or {}
    if kind in ("not_null", "range", "accepted") and not column:
        raise CheckError(f"{kind} needs a column")
    if kind == "range" and "min" not in params and "max" not in params:
        raise CheckError("range needs params.min or params.max")
    if kind == "accepted" and not params.get("values"):
        raise CheckError("accepted needs params.values")
    if kind == "row_count" and "pct" not in params:
        raise CheckError("row_count needs params.pct")
    row = conn.execute("insert into source_check (dataset_id, kind, column_name, params, blocking, note, created_by) "
                       "values (%s, %s, %s, %s, %s, %s, %s) returning *",
                       (d["id"], kind, column, Jsonb(params), blocking, note, by)).fetchone()
    catalog._audit(conn, by, "check", {"id": row["id"], "name": f"{dataset}:{kind}"}, "create", new=plain(dict(row)))
    return dict(row)


def remove_check(conn, check_id: int, by: str) -> None:
    from . import catalog

    row = conn.execute("delete from source_check where id = %s returning *", (check_id,)).fetchone()
    if not row:
        raise CheckError(f"no check {check_id}")
    catalog._audit(conn, by, "check", {"id": check_id, "name": row["kind"]}, "delete", old=plain(dict(row)))


def checks(conn, dataset: str | None = None) -> list[dict]:
    """The checks, each with its latest result."""
    return [plain(dict(r)) for r in conn.execute(
        """
        select c.*, d.name as dataset, r.ok as last_ok, r.value as last_value, r.detail as last_detail, r.at as last_at
        from source_check c join source_dataset d on d.id = c.dataset_id
        left join lateral (select * from source_check_result x where x.check_id = c.id order by id desc limit 1) r on true
        where %(d)s::text is null or d.name = %(d)s order by d.name, c.id
        """, {"d": dataset})]


def _stage_check(conn, c: dict, d: dict, rows_read: int, key: dict) -> tuple[bool, float | None, dict]:
    col, p, name = c["column_name"], c["params"] or {}, d["name"]
    if c["kind"] == "key_unique":
        return key["duplicates"] == 0, float(key["duplicates"]), {"examples": key["examples"]}
    if c["kind"] == "row_count":
        last = d.get("rows")
        if not last:
            return True, None, {"note": "no earlier load to compare with"}
        pct = 100 * abs(rows_read - last) / last
        return pct <= float(p["pct"]), round(pct, 1), {"rows": rows_read, "last": last}
    if c["kind"] == "not_null":
        r = conn.execute("select count(*) as n, count(*) filter (where coalesce(payload->>%s, '') = '') as e "
                         "from source_record_stage where dataset = %s", (col, name)).fetchone()
        pct = 100 * r["e"] / r["n"] if r["n"] else 0.0
        return pct <= float(p.get("max_pct", 0)), round(pct, 2), {"empty": r["e"], "rows": r["n"]}
    if c["kind"] == "range":
        lo, hi = p.get("min"), p.get("max")
        r = conn.execute(
            """
            select count(*) filter (where v < %(lo)s or v > %(hi)s) as bad, (array_agg(v) filter (where v < %(lo)s or v > %(hi)s))[1:3] as ex
            from (select case when payload->>%(c)s ~ '^-?[0-9]+(\\.[0-9]+)?$' then (payload->>%(c)s)::float end as v
                  from source_record_stage where dataset = %(d)s) x
            """, {"lo": float(lo) if lo is not None else float("-inf"), "hi": float(hi) if hi is not None else float("inf"),
                  "c": col, "d": name}).fetchone()
        return r["bad"] == 0, float(r["bad"]), {"examples": r["ex"] or []}
    if c["kind"] == "accepted":
        r = conn.execute("select count(*) as bad, (array_agg(distinct payload->>%s))[1:5] as ex from source_record_stage "
                         "where dataset = %s and coalesce(payload->>%s, '') <> '' and not (payload->>%s = any(%s))",
                         (col, name, col, col, [str(v) for v in p["values"]])).fetchone()
        return r["bad"] == 0, float(r["bad"]), {"examples": r["ex"] or []}
    raise CheckError(f"{c['kind']} isn't judged on a load")


def run_load_checks(conn, d: dict, rows_read: int, key: dict) -> list[dict]:
    """The dataset's load checks against its staged rows: [{check_id, kind, ok, value, detail, blocking}]."""
    out = []
    for c in conn.execute("select * from source_check where dataset_id = %s and enabled and kind = any(%s) order by id",
                          (d["id"], list(LOAD_CHECKS))):
        ok, value, detail = _stage_check(conn, dict(c), d, rows_read, key)
        out.append({"check_id": c["id"], "kind": c["kind"], "column": c["column_name"], "ok": ok, "value": value,
                    "detail": detail, "blocking": c["blocking"]})
    return out


def store_results(conn, results: list[dict], run_id: int | None) -> None:
    for r in results:
        conn.execute("insert into source_check_result (check_id, run_id, ok, value, detail) values (%s, %s, %s, %s, %s)",
                     (r["check_id"], run_id, r["ok"], r["value"], Jsonb(plain(r["detail"]))))


def hold_for_checks(conn, name: str, failed: list[dict]) -> None:
    """A review item for a load held by failing blocking checks (or duplicate keys)."""
    conn.execute(
        """
        insert into review_item (kind, subject, payload) values ('check', %s, %s)
        on conflict (kind, subject) do update set payload = excluded.payload, status = 'open'
        """, (name, Jsonb(plain({"dataset": name, "failed": failed}))))


def clear_check_hold(conn, name: str) -> None:
    conn.execute("update review_item set status = 'stale' where kind = 'check' and subject = %s and status = 'open'",
                 (name,))


# ---- after a rebuild: coverage and matching ------------------------------------------------------

def after_rebuild(conn, coverage: dict[str, dict[str, list[int]]]) -> dict:
    """Per source: link coverage (resolved / total by link type, from the graph build), how its records matched,
    how many share an entity with another source, golden fields won and open reviews. Kept per rebuild for 90
    days (source_match_stat); coverage checks are judged here."""
    per: dict[str, dict] = {}
    for r in conn.execute(
        """
        with x as (select c.source, c.fuzer_id, c.method from crosswalk c),
             multi as (select fuzer_id from crosswalk group by fuzer_id having count(distinct source) > 1)
        select x.source, count(*) as records, count(distinct x.fuzer_id) as entities,
               count(*) filter (where x.fuzer_id in (select fuzer_id from multi)) as shared
        from x group by x.source
        """):
        per[r["source"]] = {"records": r["records"], "entities": r["entities"], "shared": r["shared"],
                            "shared_pct": round(100 * r["shared"] / r["records"], 1) if r["records"] else 0.0}
    for r in conn.execute("select source, method, count(*) as n from crosswalk group by 1, 2"):
        per.setdefault(r["source"], {}).setdefault("by_method", {})[r["method"]] = r["n"]
    for r in conn.execute("select source, count(*) as n from golden group by 1"):
        per.setdefault(r["source"], {})["golden_wins"] = r["n"]
    for r in conn.execute(
        """
        select source, kind, count(*) as n from (
            select distinct i.id, i.kind, split_part(r, ':', 1) as source        -- match: "left ref|right ref"
            from review_item i, unnest(string_to_array(i.subject, '|')) r where i.status = 'open' and i.kind = 'match'
            union
            select distinct i.id, i.kind, v->>'source'                           -- conflict: the values compared
            from review_item i, jsonb_array_elements(i.payload->'values') v where i.status = 'open' and i.kind = 'conflict'
        ) x group by 1, 2
        """):
        per.setdefault(r["source"], {}).setdefault("open_reviews", {})[r["kind"]] = r["n"]
    for source, rels in coverage.items():
        res, tot = sum(v[0] for v in rels.values()), sum(v[1] for v in rels.values())
        per.setdefault(source, {})["coverage"] = {"pct": round(100 * res / tot, 1) if tot else None, "resolved": res,
                                                  "total": tot, "by_rel": rels}
    conn.execute("delete from source_match_stat where built_at < now() - interval '90 days'")
    with conn.cursor() as cur:
        cur.executemany("insert into source_match_stat (source, stats) values (%s, %s)",
                        [(s, Jsonb(plain(v))) for s, v in per.items()])
    _coverage_checks(conn, per)
    return per


def _coverage_checks(conn, per: dict) -> None:
    for c in conn.execute("select c.*, s.name as source from source_check c join source_dataset d on d.id = c.dataset_id "
                          "join source_system s on s.id = d.system_id where c.kind = 'coverage' and c.enabled"):
        cov = (per.get(c["source"]) or {}).get("coverage") or {}
        p = c["params"] or {}
        if p.get("rel"):
            res, tot = (cov.get("by_rel") or {}).get(p["rel"], [0, 0])
            pct = 100 * res / tot if tot else None
        else:
            pct = cov.get("pct")
        ok = pct is None or pct >= float(p.get("min_pct", 0))
        conn.execute("insert into source_check_result (check_id, ok, value, detail) values (%s, %s, %s, %s)",
                     (c["id"], ok, pct, Jsonb({"rel": p.get("rel"), "min_pct": p.get("min_pct")})))


def matching(conn, source: str | None = None, limit: int = 30) -> list[dict]:
    """Match statistics per rebuild (newest first), of one source or all."""
    return [plain(dict(r)) for r in conn.execute(
        "select * from source_match_stat where %(s)s::text is null or source = %(s)s order by id desc limit %(l)s",
        {"s": source, "l": limit})]


# ---- health --------------------------------------------------------------------------------------

def health(conn, name: str | None = None) -> list[dict]:
    """Per dataset: status, last success, freshness (stale when a delivery is overdue), last run, row trend,
    failing checks, profile drift, coverage and match rate of its source, open reviews. What an agent should
    know before an answer rests on this data."""
    from . import catalog

    now = dt.datetime.now(dt.timezone.utc)
    out = []
    stats = {r["source"]: r["stats"] for r in conn.execute(
        "select distinct on (source) source, stats from source_match_stat order by source, id desc")}
    held = {(r["kind"], r["subject"]) for r in conn.execute(
        "select kind, subject from review_item where status = 'open' and kind in ('schema', 'check')")}
    for n, d in catalog.datasets(conn).items():
        if name and n != name:
            continue
        runs = conn.execute("select status, rows_read, finished_at, error from source_run where dataset_id = %s "
                            "order by id desc limit 5", (d["id"],)).fetchall()
        failing = [{"kind": c["kind"], "column": c["column_name"], "value": c["last_value"], "blocking": c["blocking"]}
                   for c in checks(conn, n) if c["last_ok"] is False]
        prof = conn.execute("select drift, taken_at from source_profile where dataset_id = %s order by id desc limit 1",
                            (d["id"],)).fetchone()
        hours = d["expected_hours"]
        for c in conn.execute("select params from source_check where dataset_id = %s and kind = 'freshness' and enabled",
                              (d["id"],)):
            hours = (c["params"] or {}).get("hours") or hours
        age = (now - d["loaded_at"]).total_seconds() / 3600 if d["loaded_at"] else None
        stale = bool(hours) and d["status"] == "active" and (age is None or age > float(hours))
        s = stats.get(d["source"]) or {}
        problems = [p for p, on in (("stale", stale), ("held", (("schema", n) in held) or (("check", n) in held)),
                                    ("failed", bool(runs) and runs[0]["status"] == "failed"),
                                    ("checks failing", bool(failing)),
                                    ("profile drift", any(x["change"] in SIGNIFICANT for x in (prof or {}).get("drift") or [])))
                    if on]
        out.append(plain({
            "dataset": n, "system": d["source"], "status": d["status"], "owner": d["owner"],
            "state": problems[0] if problems else "ok" if d["loaded_at"] else "not loaded", "problems": problems,
            "last_loaded": d["loaded_at"], "last_checked": d["checked_at"], "age_hours": round(age, 1) if age is not None else None,
            "expected_hours": hours, "rows": d["rows"], "row_trend": [r["rows_read"] for r in runs if r["rows_read"] is not None][::-1],
            "last_run": dict(runs[0]) if runs else None, "failing_checks": failing,
            "profile_drift": prof["drift"] if prof else [], "coverage_pct": (s.get("coverage") or {}).get("pct"),
            "shared_pct": s.get("shared_pct"), "open_reviews": s.get("open_reviews") or {},
        }))
    return out


# ---- suggestions ---------------------------------------------------------------------------------

_PCT = re.compile(r"(^|_)(pct|percent|percentage)(_|$)|%", re.IGNORECASE)
_REQUIRED = re.compile(r"(^|_)(id|key|number|no|tag)$", re.IGNORECASE)
_CODED = re.compile(r"(^|_)(status|state|type|class|category|priority|phase|kind|flag|uom|unit|grade|criticality)$",
                    re.IGNORECASE)


def suggest_checks(conn, name: str) -> list[dict]:
    """Checks worth adding to a dataset, read off its latest profile (and not there yet): each {kind, column,
    params, blocking, why}. Duplicate keys always hold a load, so key_unique isn't suggested."""
    from . import catalog

    d = catalog.dataset(conn, name)
    if not d:
        return []
    p = conn.execute("select columns, rows from source_profile where dataset_id = %s order by id desc limit 1",
                     (d["id"],)).fetchone()
    if not p:
        return []
    have = {(c["kind"], c["column_name"]) for c in conn.execute(
        "select kind, column_name from source_check where dataset_id = %s", (d["id"],))}
    out = []

    def add(kind, column, params, why, blocking=False):
        if (kind, column) not in have:
            out.append({"kind": kind, "column": column, "params": params, "blocking": blocking, "why": why})

    for col, c in (p["columns"] or {}).items():
        if c.get("fill_pct") == 100.0 and (col in (d["key"] or []) or _REQUIRED.search(col)):
            add("not_null", col, {}, f"{col} is always filled now; a gap would mean a broken export", col in (d["key"] or []))
        if c.get("type") == "number" and _PCT.search(col):
            add("range", col, {"min": 0, "max": 100}, f"{col} looks like a percentage")
        if c.get("coded") and c.get("values") and len(c["values"]) <= 10 and c.get("type") == "text" and _CODED.search(col):
            add("accepted", col, {"values": c["values"]}, f"{col} holds a few codes: {', '.join(c['values'][:6])}")
    if (p["rows"] or 0) >= 20 and d["load_mode"] == "snapshot":
        add("row_count", None, {"pct": 50}, "a snapshot that halves or doubles is usually a broken export")
    if d["schedule"] and not d["expected_hours"]:
        add("freshness", None, {"hours": 48}, "it has a schedule, so a missing delivery should be noticed")
    return out


def next_load(cron: str | None, tz: str | None = None) -> str | None:
    """When a dataset's own schedule next loads it (ISO, project timezone); None without one."""
    import os
    import time

    if not cron:
        return None
    try:
        from dagster._utils.schedules import cron_string_iterator

        return next(cron_string_iterator(time.time(), cron, tz or os.environ.get("FUZER_TIMEZONE") or "UTC")).isoformat(
            timespec="minutes")
    except Exception:  # noqa: BLE001  (a cron Dagster can't read: shown as not scheduled)
        return None


def rebuild_status(conn) -> dict:
    """Whether loads, mappings or rules changed since the register was last built (a rebuild applies them)."""
    built = conn.execute("select updated_at from register_summary where name = 'ontology'").fetchone()
    built = built["updated_at"] if built else None
    changes = []
    for what, sql in (
            ("loads", "select max(finished_at) as t, count(*) as n from source_run where status = 'done' "
                      "and coalesce(inserted, 0) + coalesce(updated, 0) + coalesce(removed, 0) > 0 and finished_at > %(b)s"),
            ("mappings", "select max(approved_at) as t, count(*) as n from mapping_version where status = 'approved' "
                         "and approved_at > %(b)s"),
            ("rules", "select max(updated_at) as t, count(*) as n from source_rule where updated_by <> 'seed' "
                      "and updated_at > %(b)s")):
        r = conn.execute(sql, {"b": built or "-infinity"}).fetchone()
        if r["n"]:
            changes.append({"what": what, "count": r["n"], "last": r["t"]})
    return plain({"built_at": built, "needed": bool(changes), "changes": changes})
