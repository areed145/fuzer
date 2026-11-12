"""Correlation studies over many points (#106): what moves with what, and what moved first.

- `points(conn, scope)`: pick points by unit, area, plant path (flows_to, lift_gas_to, part_of), the
  whole facility, or an explicit list.
- `matrix(...)`: their values on one time grid as a numpy matrix (time x point), read through the
  time-series provider in parallel and cached as Parquet by (points, window, step), so repeat studies and
  follow-up questions don't read PI again.
- `clean(...)`: drop points that don't move or are mostly missing, rows where the plant was down (unless
  asked to keep them), and optionally detrend or difference.
- `correlate(...)`: one target against many candidates, ranked by the best correlation at any lag
  within `max_lag`, with whether each leads or lags the target and whether the plant model connects
  them.
- `correlation_matrix(...)`: all pairs, ordered so related groups sit together (average-linkage on
  1 - |r|), with the strongest pairs as numbers.

Results follow the calcs convention: numbers with their basis and sources. Clustering and stored studies
come later (#106b).
"""

import hashlib
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

from ..core import projects, settings
from .calcs import CalcError

# Defaults; the values in effect are settings (#131): studies.max_points, .max_values, .workers
MAX_POINTS = 2000  # an interactive study reads at most this many points
MAX_VALUES = 15_000_000  # and at most this many values (points x steps); bigger studies run in the background
WORKERS = 8


# ---- picking points -------------------------------------------------------------------------------

def points(conn, scope: str | list[str], path: str | None = None, depth: int = 3, limit: int | None = None) -> list[dict]:
    """PI points for a scope: a list of timeseries ids; "facility" (every point); an area (every point on
    its tags and wells); any other entity, its points and its instruments' points, plus with `path`
    ("flows_to" or "lift_gas_to", up or down as "flows_to:up") what that path reaches within `depth`."""
    from ..sources import timeseries

    limit = settings.get(conn, "studies.max_points") if limit is None else limit
    if isinstance(scope, list):
        return [p for p in (timeseries._point(conn, i) for i in scope) if p][:limit]
    if scope == "facility":
        ids = [r["fuzer_id"] for r in conn.execute("select fuzer_id from entity where kind = 'timeseries' order by 1")]
        return [p for p in (timeseries._point(conn, i) for i in ids) if p][:limit]
    kind = conn.execute("select kind from entity where fuzer_id = %s", (scope,)).fetchone()
    if kind is None:
        raise CalcError("unknown scope")
    units = [scope]
    if kind["kind"] == "area":
        units = [r["src"] for r in conn.execute(
            "select src from edge where rel in ('part_of', 'located_at') and dst = %s", (scope,))]
    if path:
        from ..graph import edges

        rel, _, way = path.partition(":")
        units += [r["fuzer_id"] for r in edges.trace(conn, scope, rel, way or "up", None, depth)]
    out, seen = [], set()
    for u in dict.fromkeys(units):
        for p in timeseries._unit_points(conn, u, limit):
            if p["id"] not in seen:
                seen.add(p["id"])
                out.append(p)
    return out[:limit]


# ---- the matrix -----------------------------------------------------------------------------------

def _cache_dir() -> Path:
    d = Path(projects.field("cache") or ".fuzer/cache") / "studies"
    d.mkdir(parents=True, exist_ok=True)
    return d


def matrix(pts: list[dict], start: datetime, end: datetime, step: int = 10, src=None,
           cache: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """(times, values): every point on one grid from start to end, a column per point (nan where it
    had no value). Cached as Parquet by points, window, step and provider."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from ..sources import timeseries

    src = src or timeseries.provider()
    times = np.arange(np.datetime64(start, "m"), np.datetime64(end, "m") + np.timedelta64(1, "m"),
                      np.timedelta64(step, "m"))
    key = hashlib.sha1("|".join([getattr(src, "name", "?"), start.isoformat(), end.isoformat(), str(step),
                                 *sorted(p["id"] for p in pts)]).encode()).hexdigest()[:20]
    path = _cache_dir() / f"{key}.parquet"
    if cache and path.is_file():
        t = pq.read_table(path)
        return times, np.column_stack([t.column(p["id"]).to_numpy() for p in pts]) if pts else np.empty((len(times), 0))

    def one(p):
        try:
            vals = src.series({**p, "upsets": []}, start, end, step)
        except Exception:  # one unreadable point is a column of gaps, not a failed study
            return np.full(len(times), np.nan)
        if len(vals) == len(times):  # the provider's grid is ours
            return np.array([np.nan if v is None else v for _, v in vals], dtype=float)
        col = np.full(len(times), np.nan)
        at = {np.datetime64(t, "m"): v for t, v in vals}
        for i, t in enumerate(times):
            if t in at and at[t] is not None:
                col[i] = at[t]
        return col

    with ThreadPoolExecutor(settings.get(None, "studies.workers")) as pool:
        cols = list(pool.map(one, pts))
    X = np.column_stack(cols) if cols else np.empty((len(times), 0))
    if cache:
        pq.write_table(pa.table({p["id"]: X[:, i] for i, p in enumerate(pts)}), path)
    return times, X


def clean(X: np.ndarray, keep: np.ndarray | None = None, max_missing: float = 0.5, detrend: bool = False,
          difference: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(X, columns kept, rows kept): rows outside `keep` (e.g. plant down) dropped; columns mostly
    missing or not moving dropped; short gaps filled from the last value; then optionally detrended or
    differenced."""
    rows = np.ones(len(X), bool) if keep is None else np.asarray(keep, bool)
    X = X[rows]
    missing = np.isnan(X).mean(0) if len(X) else np.ones(X.shape[1])
    with warnings.catch_warnings():  # an all-missing column has no spread: it's dropped below
        warnings.simplefilter("ignore", RuntimeWarning)
        sd = np.nanstd(X, 0) if len(X) else np.zeros(X.shape[1])
    cols = (missing <= max_missing) & (sd > 1e-9)
    X = X[:, cols]
    for j in range(X.shape[1]):  # fill gaps: last value, the first from the next
        c = X[:, j]
        bad = np.isnan(c)
        if bad.any():
            idx = np.where(~bad, np.arange(len(c)), 0)
            np.maximum.accumulate(idx, out=idx)
            c = c[idx]
            if np.isnan(c[0]):
                c[np.isnan(c)] = c[~np.isnan(c)][0]
            X[:, j] = c
    if detrend and len(X) > 2:
        t = np.arange(len(X), dtype=float)
        A = np.column_stack([t, np.ones_like(t)])
        X = X - A @ np.linalg.lstsq(A, X, rcond=None)[0]
    if difference:
        X = np.diff(X, axis=0)
        rows = rows.copy()
        rows[np.flatnonzero(rows)[0]] = False
    return X, cols, rows


def plant_running(conn, pack, times: np.ndarray, step: int, src=None, share: float = 0.3) -> np.ndarray:
    """Rows where the plant was up: at least `share` of the units with their own run signal running."""
    from . import limits

    sig = {u for u, s in limits.run_signals(conn, pack, set(limits.units(conn).values())).items() if "point" in s}
    if not sig:
        return np.ones(len(times), bool)
    a, b = times[0].astype(datetime), times[-1].astype(datetime)
    mode = limits.modes(conn, pack, sig, a, b, step, src)
    run = np.mean([(m == "running") | (m == "startup") for m in mode.values()], axis=0)
    return run[:len(times)] >= share


# ---- the maths ------------------------------------------------------------------------------------

def pearson(a: np.ndarray, b: np.ndarray, min_n: int = 5) -> float | None:
    """Pearson r of two equal-length series; None if either is flat or shorter than `min_n`."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < min_n:
        return None
    da, db = a - a.mean(), b - b.mean()
    den = np.sqrt((da * da).sum() * (db * db).sum())
    return float((da * db).sum() / den) if den else None


def ranks(X: np.ndarray) -> np.ndarray:
    """Column ranks (for Spearman), ties averaged."""
    from scipy.stats import rankdata

    return rankdata(X, axis=0)


def lagged(X: np.ndarray, y: np.ndarray, lags: range) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each column of X, the Pearson r with y at lag 0, and the lag in `lags` (steps; positive: the
    column moves first) where |r| is largest, with that r. Each lag's r is on the overlapping part only."""
    n, m = X.shape
    r0 = np.zeros(m)
    best_r = np.zeros(m)
    best_lag = np.zeros(m, int)
    for lag in lags:
        if abs(lag) >= n - 2:
            continue
        a = X[:n - lag] if lag >= 0 else X[-lag:]
        b = y[lag:] if lag >= 0 else y[:n + lag]
        da, db = a - a.mean(0), b - b.mean()
        den = np.sqrt((da * da).sum(0) * (db * db).sum())
        with np.errstate(invalid="ignore", divide="ignore"):
            r = np.where(den > 0, (da * db[:, None]).sum(0) / den, 0.0)
        if lag == 0:
            r0 = r
        better = np.abs(r) > np.abs(best_r)
        best_r = np.where(better, r, best_r)
        best_lag = np.where(better, lag, best_lag)
    return r0, best_lag, best_r


def order(R: np.ndarray) -> list[int]:
    """Leaf order of an average-linkage tree on 1 - |r|: related points sit together."""
    if len(R) < 3:
        return list(range(len(R)))
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform

    D = np.clip(1 - np.abs(np.nan_to_num(R)), 0, 2)
    np.fill_diagonal(D, 0)
    return [int(i) for i in leaves_list(linkage(squareform(D, checks=False), "average"))]


# ---- studies --------------------------------------------------------------------------------------

def _window(start: str, end: str, n_points: int = 0, step: int = 10) -> tuple[datetime, datetime]:
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if b <= a:
        raise CalcError("the window ends before it starts")
    steps = int((b - a).total_seconds() // 60 // step) + 1
    if n_points * steps > settings.get(None, "studies.max_values"):
        raise CalcError(f"{n_points} points x {steps} steps is too big to answer now: narrow the scope, shorten the "
                        "window or use a longer step")
    return a, b


def _connected(conn, target_unit: str | None, units: dict[str, str]) -> dict[str, bool]:
    from ..core import settings
    from ..graph import topology

    adj, cache, hops = topology.topology(conn), {}, settings.get(conn, "sre.hops")
    return {pid: topology._near(adj, target_unit, u, cache, hops) for pid, u in units.items()} if target_unit else {}


def correlate(conn, pack, target: str, scope: str | list[str], start: str, end: str, step: int = 10,
              max_lag_minutes: int = 360, method: str = "pearson", keep_down: bool = False, top: int = 20,
              src=None, path: str | None = None) -> dict:
    """Rank every point in `scope` against the target PI point by the strongest correlation at any lag
    up to `max_lag_minutes` either way. `leads_by_minutes` > 0: the candidate moved first (a lead to the
    cause); < 0: it followed. `connected`: the plant model links their units (a strong correlation
    without a path is a shared cause, a mismatched tag or a mapping error to check). `leaders`: the
    strongly correlated candidates (|r| at least 0.8 of the best) that moved first, earliest first;
    points moving with the target at r = 1 are often its siblings (other wells on the same header), and
    the cause is usually among what moved before them."""
    from . import limits

    pts = points(conn, scope, path)
    a, b = _window(start, end, len(pts) + 1, step)
    tgt = next((p for p in pts if p["id"] == target), None) or (points(conn, [target]) or [None])[0]
    if tgt is None:
        raise CalcError("target must be a PI point")
    cands = [p for p in pts if p["id"] != target]
    times, X = matrix([tgt, *cands], a, b, step, src)
    keep = None if keep_down else plant_running(conn, pack, times, step, src)
    Xc, cols, rows = clean(X, keep)
    if not cols[0]:
        raise CalcError("the target doesn't move in that window (or the plant was down)")
    kept = [p for p, c in zip([tgt, *cands], cols) if c]
    y, C = Xc[:, 0], Xc[:, 1:]
    if method == "spearman":
        y, C = ranks(y[:, None])[:, 0], ranks(C)
    L = max(0, max_lag_minutes // step)
    r0, lag, r = lagged(C, y, range(-L, L + 1))
    units = limits.units(conn, [p["id"] for p in kept])
    near = _connected(conn, units.get(tgt["id"]), {p["id"]: units.get(p["id"]) for p in kept[1:]})
    rank = sorted(range(len(kept) - 1), key=lambda i: -abs(r[i]))
    strong = 0.8 * (abs(r[rank[0]]) if rank else 0)
    first = sorted((i for i in rank if abs(r[i]) >= strong and lag[i] > 0), key=lambda i: (-lag[i], -abs(r[i])))

    def row(i: int) -> dict:
        p = kept[i + 1]
        return {"point": p["point"], "point_id": p["id"], "description": p["description"], "r": round(float(r[i]), 3),
                "r_at_zero_lag": round(float(r0[i]), 3), "leads_by_minutes": int(lag[i]) * step,
                "moves": "first" if lag[i] > 0 else "after" if lag[i] < 0 else "together", "connected": near.get(p["id"])}

    out = [row(i) for i in rank[:top]]
    leaders = [row(i) for i in first[:top]]
    return {"target": tgt["point"], "target_id": tgt["id"], "window": [start, end], "step_minutes": step,
            "method": method, "max_lag_minutes": L * step, "candidates": len(cands), "used": len(kept) - 1,
            "rows_used": int(rows.sum()), "ranking": out, "leaders": leaders,
            "basis": f"{method} r at the best lag within ±{L * step} min on {int(rows.sum())} steps of {step} min"
                     + ("" if keep_down else " while the plant ran"),
            "sources": [tgt["id"], *dict.fromkeys(o["point_id"] for o in leaders + out)]}


def correlation_matrix(conn, pack, scope: str | list[str], start: str, end: str, step: int = 10, method: str = "pearson",
                       keep_down: bool = False, top: int = 20, src=None, path: str | None = None) -> dict:
    """All pairs in `scope`: r for each, ordered so related points sit together, with the `top` strongest
    pairs and whether the plant model connects each pair."""
    from ..core import settings
    from ..graph import topology
    from . import limits

    pts = points(conn, scope, path)
    a, b = _window(start, end, len(pts), step)
    times, X = matrix(pts, a, b, step, src)
    keep = None if keep_down else plant_running(conn, pack, times, step, src)
    Xc, cols, rows = clean(X, keep)
    kept = [p for p, c in zip(pts, cols) if c]
    if len(kept) < 2:
        raise CalcError("fewer than two points that move in that window")
    R = np.corrcoef(ranks(Xc) if method == "spearman" else Xc, rowvar=False)
    idx = order(R)
    units = limits.units(conn, [p["id"] for p in kept])
    adj, cache, hops = topology.topology(conn), {}, settings.get(conn, "sre.hops")
    iu = np.triu_indices(len(kept), 1)
    strongest = np.argsort(-np.abs(R[iu]))[:top]
    pairs = [{"a": kept[iu[0][k]]["point"], "b": kept[iu[1][k]]["point"], "r": round(float(R[iu][k]), 3),
              "connected": topology._near(adj, units.get(kept[iu[0][k]]["id"]), units.get(kept[iu[1][k]]["id"]), cache, hops)}
             for k in strongest]
    return {"window": [start, end], "step_minutes": step, "method": method, "points": len(pts), "used": len(kept),
            "rows_used": int(rows.sum()), "order": [kept[i]["point"] for i in idx], "ids": [kept[i]["id"] for i in idx],
            "matrix": np.round(R[np.ix_(idx, idx)], 3), "strongest_pairs": pairs,
            "basis": f"{method} r on {int(rows.sum())} steps of {step} min" + ("" if keep_down else " while the plant ran")
                     + "; ordered by average linkage on 1 - |r|",
            "sources": [p["id"] for p in kept]}
