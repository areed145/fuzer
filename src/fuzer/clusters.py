"""Clustering and multivariate anomaly (#106b): operating modes, behaviour groups, recurring upset types.

- `modes(scope, since, until, k)`: operating modes of a unit or train: stopped (its run signals), and its
  running time clustered on the points that carry its regimes. It gives a mode for each time, each mode's
  profile in engineering units, the time in each, how the unit moves between modes, and the mode before
  each stop.
- `entities("wells")`: wells grouped by decline, water cut and lift gas response, as peer groups for #105.
  `entities("points", scope)`: points grouped by correlation distance, which finds redundant points,
  mislabelled tags and natural sub-units.
- `events(since)`: SRE upsets grouped by signature (which kinds of unit raised which findings, by Jaccard
  distance), so recurring upset types show up.
- `spe(scope, train, test)`: a PCA model of normal running fitted on a training window, and the squared
  prediction error of a test window against it. Stretches over the training's 99.9th percentile are
  multivariate anomalies, with the points contributing most. This is the SRE's `multivariate` detector.
"""

from datetime import datetime, timedelta

import numpy as np

from .calcs import CalcError, _label, result

MAX_K = 6


def _matrix(conn, scope, since: str, until: str, step: int, src=None, keep_down: bool = True):
    from . import studies

    pts = studies.points(conn, scope)
    if len(pts) < 2:
        raise CalcError("fewer than two PI points in that scope")
    a, b = datetime.fromisoformat(since), datetime.fromisoformat(until)
    studies._window(since, until, len(pts), step)
    times, X = studies.matrix(pts, a, b, step, src)
    keep = None if keep_down else studies.plant_running(conn, None, times, step, src)
    Xc, cols, rows = studies.clean(X, keep)
    return [p for p, c in zip(pts, cols) if c], times[rows], Xc


def _scale(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    return (X - mu) / sd, mu, sd


def modes(conn, scope, since: str, until: str, k: int | None = None, step: int = 360, src=None, pack=None) -> dict:
    """Two stages: the times the unit is stopped (its run signals, #101) are one mode; the running time is
    clustered with k-means on the points that carry its regimes: day-long medians of the points whose
    two-week medians hold at least 80% as much of their variance as the slowest point's. Fast noise and the
    daily swing don't make modes; fouling, a changed setpoint or a new well do. k counts all modes,
    stopped included; by silhouette when not given."""
    from scipy.ndimage import median_filter
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    from . import limits

    pts, times, X = _matrix(conn, scope, since, until, step, src)
    day, fortnight = max(1, 1440 // step), max(2, 14 * 1440 // step)
    units = set(limits.units(conn, [p["id"] for p in pts]).values())
    mode_of = limits.modes(conn, pack, units, datetime.fromisoformat(since), datetime.fromisoformat(until), step, src) if units else {}
    known = [m[:len(X)] for m in mode_of.values() if (m != "unknown").any()]
    stopped = np.zeros(len(X), bool)
    if known:
        stopped = np.mean([(m == "stopped") | (m == "transition") for m in known], axis=0)[:len(X)] > 0.5
    run = ~stopped
    if run.sum() < 10:
        raise CalcError("the unit hardly ran in that window")
    D = median_filter(X, size=(day, 1), mode="nearest")
    W = median_filter(X, size=(fortnight, 1), mode="nearest")
    slow = W[run].var(0) / (X[run].var(0) + 1e-12)
    sel = slow >= 0.8 * slow.max()
    Z, _, _ = _scale(D[run][:, sel])
    k_run = (k - int(stopped.any())) if k else None
    if not k_run:
        idx = np.random.default_rng(0).choice(len(Z), min(len(Z), 2000), replace=False)
        k_run = max(range(2, MAX_K), key=lambda kk: silhouette_score(Z[idx], KMeans(kk, n_init=5, random_state=0).fit_predict(Z[idx])))
    lab_run = KMeans(max(k_run, 1), n_init=10, random_state=0).fit_predict(Z) if k_run > 1 else np.zeros(len(Z), int)
    order = np.argsort(-np.bincount(lab_run))
    remap = {int(o): i for i, o in enumerate(order)}
    lab = np.full(len(X), -1)
    lab[run] = [remap[int(x)] for x in lab_run]
    n_modes = int(lab.max()) + 1
    if stopped.any():
        lab[stopped] = n_modes
    names = [f"running {i}" for i in range(n_modes)] + (["stopped"] if stopped.any() else [])
    mu, sd = X[run].mean(0), X[run].std(0)
    sd[sd == 0] = 1.0
    prof = []
    for m, name in enumerate(names):
        rows = X[lab == m]
        prof.append({"mode": m, "name": name, "share": round(float((lab == m).mean()), 3),
                     "profile": {p["point"]: round(float(v), 3) for p, v in zip(pts, rows.mean(0))},
                     "most_different": sorted(((p["point"], round(float((rows.mean(0)[j] - mu[j]) / sd[j]), 2))
                                               for j, p in enumerate(pts)), key=lambda x: -abs(x[1]))[:5]})
    trans = np.zeros((len(names), len(names)), int)
    for x, y in zip(lab, lab[1:]):
        if x != y:
            trans[x, y] += 1
    before = {}
    if stopped.any():
        for i in range(1, len(lab)):
            if lab[i] == n_modes and lab[i - 1] != n_modes:
                before[names[lab[i - 1]]] = before.get(names[lab[i - 1]], 0) + 1
    return {**result(len(names), None, None,
                     f"stopped from the units' run signals; running time by k-means on day-long medians of the "
                     f"{int(sel.sum())} points carrying its regimes ({', '.join(p['point'] for p, s_ in zip(pts, sel) if s_)}), "
                     f"{step}-minute steps", [p["id"] for p in pts][:20], unit="modes"),
            "modes": prof, "transitions": trans.tolist(), "mode_before_stops": before,
            "times": [str(x)[:16] for x in times], "labels": lab.tolist(), "points": [p["point"] for p in pts],
            "regime_points": [p["point"] for p, s_ in zip(pts, sel) if s_]}


def ari(a: list, b: list) -> float:
    from sklearn.metrics import adjusted_rand_score

    return float(adjusted_rand_score(a, b))


def entities(conn, kind: str = "wells", scope=None, since: str | None = None, until: str | None = None,
             k: int | None = None, src=None) -> dict:
    if kind == "points":
        return _point_groups(conn, scope, since, until, src)
    if kind != "wells":
        raise CalcError("kind is wells or points")
    from sklearn.cluster import KMeans

    from . import calcs, operations

    resp = {w["id"]: w["response_oil_per_extra_gas"] for w in operations.gas_lift_network(conn, limit=100000).get("most_response", [])}
    rows = []
    for r in conn.execute("select fuzer_id from entity where kind = 'well' order by 1"):
        try:
            d = calcs.decline(conn, r["fuzer_id"], until=until)
            tests = calcs.well_tests(conn, r["fuzer_id"], until)
        except CalcError:
            continue
        wc = [t.get("water_cut") for t in tests if t.get("water_cut") is not None]
        rows.append({"well": _label(conn, r["fuzer_id"]), "id": r["fuzer_id"], "decline_pct_per_year": d["effective_decline_pct_per_year"] or 0,
                     "water_cut": wc[-1] if wc else 0, "water_cut_rise": (wc[-1] - wc[0]) if len(wc) > 1 else 0,
                     "lift_response": resp.get(r["fuzer_id"], 0) or 0, "oil_sm3d": d["value"] or 0})
    if len(rows) < 6:
        raise CalcError("fewer than six wells with tests")
    keys = ["decline_pct_per_year", "water_cut", "water_cut_rise", "lift_response", "oil_sm3d"]
    Z, mu, sd = _scale(np.array([[r[x] for x in keys] for r in rows], float))
    k = k or 4
    lab = KMeans(k, n_init=10, random_state=0).fit_predict(Z)
    groups = []
    for m in range(k):
        members = [r for r, x in zip(rows, lab) if x == m]
        centre = {x: round(float(np.mean([r[x] for r in members])), 2) for x in keys}
        groups.append({"group": m, "wells": [r["well"] for r in members], "size": len(members), "centre": centre})
    groups.sort(key=lambda g: -g["size"])
    return {**result(k, None, None, "k-means on standardised decline, water cut, water cut rise, lift gas response and "
                     "oil rate from well tests", [r["id"] for r in rows][:20], unit="groups"), "groups": groups}


def _point_groups(conn, scope, since, until, src, cut: float = 0.3) -> dict:
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    if not (scope and since and until):
        raise CalcError("points need a scope and a window")
    pts, _, X = _matrix(conn, scope, since, until, 10, src, keep_down=False)
    R = np.corrcoef(X, rowvar=False)
    D = np.clip(1 - np.abs(np.nan_to_num(R)), 0, 2)
    np.fill_diagonal(D, 0)
    lab = fcluster(linkage(squareform(D, checks=False), "average"), cut, "distance")
    groups: dict[int, list[str]] = {}
    for p, g in zip(pts, lab):
        groups.setdefault(int(g), []).append(p["point"])
    out = sorted(groups.values(), key=lambda g: -len(g))
    return {**result(len(out), None, None, f"average linkage on 1 - |r|, cut at {cut}: points in a group move together",
                     [p["id"] for p in pts][:20], unit="groups"), "groups": [{"points": g, "size": len(g)} for g in out]}


def events(conn, since: str | None = None, until: str | None = None, cut: float = 0.5) -> dict:
    """SRE process upsets grouped by signature: the (kind of unit, finding) pairs in each."""
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    ups = conn.execute(
        """
        select u.id, u.start, array_agg(distinct coalesce(c.dst, d.unit_id) || '|' || d.detector || ':' ||
               split_part(d.key, ':', 1)) as sig
        from upset u join detection d on d.upset_id = u.id
        left join edge c on c.src = d.unit_id and c.rel = 'classified_as'
        where u.kind = 'process' and u.detections >= 3 and (%(s)s::timestamp is null or u.start >= %(s)s::timestamp)
          and (%(u)s::timestamp is null or u.start < %(u)s::timestamp)
        group by u.id, u.start order by u.start
        """, {"s": since, "u": until}).fetchall()
    if len(ups) < 2:
        raise CalcError("fewer than two upsets to compare")
    sigs = [set(u["sig"]) for u in ups]
    n = len(sigs)
    D = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            D[i, j] = D[j, i] = 1 - len(sigs[i] & sigs[j]) / len(sigs[i] | sigs[j])
    lab = fcluster(linkage(squareform(D, checks=False), "average"), cut, "distance")
    groups: dict[int, list] = {}
    for u, g in zip(ups, lab):
        groups.setdefault(int(g), []).append({"upset": u["id"], "start": u["start"].isoformat(timespec="minutes")})
    out = sorted(groups.values(), key=lambda g: -len(g))
    return {**result(len(out), None, None, f"average linkage on Jaccard distance between upset signatures, cut at {cut}",
                     [f"upset:{u['id']}" for u in ups][:20], unit="upset types"),
            "groups": [{"upsets": g, "size": len(g), "recurring": len(g) > 1} for g in out]}


def _fill(X: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    X = X.copy()
    for j in range(X.shape[1]):
        c = X[:, j]
        ok = ~np.isnan(c)
        if not ok.any():
            X[:, j] = fallback[j]
            continue
        idx = np.where(ok, np.arange(len(c)), 0)
        np.maximum.accumulate(idx, out=idx)
        c = c[idx]
        c[np.isnan(c)] = c[ok][0]
        X[:, j] = c
    return X


def spe(conn, scope, train: tuple[str, str], test: tuple[str, str], step: int = 10, variance: float = 0.9,
        src=None, quantile: float = 99.9, min_minutes: int = 60) -> dict:
    """PCA fitted on `train`; the test window's squared prediction error against it, the stretches over the
    training's `quantile`, and the points contributing most to each."""
    from sklearn.decomposition import PCA

    from . import studies

    pts = studies.points(conn, scope)
    a0, a1 = datetime.fromisoformat(train[0]), datetime.fromisoformat(train[1])
    b0, b1 = datetime.fromisoformat(test[0]), datetime.fromisoformat(test[1])
    _, Xa = studies.matrix(pts, a0, a1, step, src, cache=False)
    tb, Xb = studies.matrix(pts, b0, b1, step, src, cache=False)
    Ca, cols, _ = studies.clean(Xa)
    if Ca.shape[1] < 3:
        raise CalcError("fewer than three moving points to model")
    pts = [p for p, c in zip(pts, cols) if c]
    Cb = _fill(Xb[:, cols], np.nanmedian(Xa[:, cols], axis=0))  # the test keeps the model's columns, gaps filled
    Za, mu, sd = _scale(Ca)
    Zb = (Cb - mu) / sd
    pca = PCA(n_components=variance, svd_solver="full").fit(Za)
    resid = lambda Z: Z - pca.inverse_transform(pca.transform(Z))  # noqa: E731
    limit = float(np.percentile((resid(Za) ** 2).sum(1), quantile))
    rb = resid(Zb)
    s = (rb ** 2).sum(1)
    over = s > limit
    spans, i = [], 0
    while i < len(over):
        if over[i]:
            j = i
            while j < len(over) and over[j]:
                j += 1
            if (j - i) * step >= min_minutes:
                contrib = (rb[i:j] ** 2).mean(0)
                top = np.argsort(-contrib)[:3]
                spans.append({"start": str(tb[i])[:16], "end": str(tb[min(j, len(tb) - 1)])[:16],
                              "peak_spe": round(float(s[i:j].max()), 1),
                              "top_points": [{"point": pts[t]["point"], "point_id": pts[t]["id"],
                                              "share": round(float(contrib[t] / contrib.sum()), 3)} for t in top]})
            i = j
        else:
            i += 1
    return {**result(len(spans), 0, "anomaly" if spans else None,
                     f"PCA keeping {variance:.0%} of the variance of {len(pts)} standardised points over {train[0]} to "
                     f"{train[1]}; squared prediction error over its {quantile}th percentile for {min_minutes} min or more",
                     [p["id"] for p in pts][:20], unit="anomalous stretches"),
            "components": int(pca.n_components_), "limit": round(limit, 2), "anomalies": spans}
