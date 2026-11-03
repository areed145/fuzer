"""Stored studies (#106): a correlation or clustering run kept with its parameters, its top results, a
short summary and a chart, so people can re-run it, an agent can cite it (`study:<id>`) and a work item
can attach it. A small one runs straight away; a big one (`studies.MAX_VALUES` and up, or a facility
scope) queues for the `studies` background job.
"""

from datetime import datetime

from psycopg.types.json import Jsonb

KINDS = ("correlate", "correlation_matrix", "modes", "entities", "events", "spe")


def _call(conn, pack, kind: str, p: dict) -> tuple[dict, object | None]:
    """Run one study: (its result with the bulky parts left out, a figure or None)."""
    from ..analysis import clusters, studies
    from ..plots import troubleshooting

    if kind == "correlate":
        out = studies.correlate(conn, pack, p["target"], p.get("scope", "facility"), p["since"], p["until"],
                                p.get("step", 10), p.get("max_lag_minutes", 360), p.get("method", "pearson"), top=50)
        return out, None
    if kind == "correlation_matrix":
        out = studies.correlation_matrix(conn, pack, p["scope"], p["since"], p["until"], p.get("step", 10), top=50)
        return {k: v for k, v in out.items() if k not in ("matrix", "ids")} | {"order": out["order"][:200]}, None
    if kind == "modes":
        out = clusters.modes(conn, p["scope"], p["since"], p["until"], p.get("k"), pack=pack)
        fig = troubleshooting.modes_chart(conn, p["scope"], p["since"], p["until"], p.get("k"), pack=pack)
        return {k: v for k, v in out.items() if k not in ("times", "labels")}, fig
    if kind == "entities":
        return clusters.entities(conn, p.get("entity", "wells"), p.get("scope"), p.get("since"), p.get("until")), None
    if kind == "events":
        return clusters.events(conn, p.get("since"), p.get("until")), None
    if kind == "spe":
        out = clusters.spe(conn, p["scope"], (p["train_since"], p["train_until"]), (p["since"], p["until"]))
        fig = troubleshooting.spe_chart(conn, p["scope"], p["train_since"], p["train_until"], p["since"], p["until"])
        return out, fig
    raise ValueError(f"unknown study kind {kind!r}; one of {', '.join(KINDS)}")


def _summary(kind: str, r: dict) -> str:
    if kind == "correlate":
        lead = r["leaders"][:3] or r["ranking"][:3]
        return f"{r['target']}: {len(r['ranking'])} ranked; moved first: " + ", ".join(
            f"{x['point']} (r {x['r']}, {x['leads_by_minutes']} min)" for x in lead)
    if kind == "correlation_matrix":
        return f"{r['used']} points; strongest: " + ", ".join(f"{x['a']} ~ {x['b']} ({x['r']})" for x in r["strongest_pairs"][:3])
    if kind == "modes":
        return f"{r['value']} modes on {', '.join(r['regime_points'])}: " + ", ".join(f"{m['name']} {m['share']:.0%}"
                                                                                        for m in r["modes"])
    if kind == "spe":
        return f"{r['value']} anomalous stretches (limit {r['limit']}, {r['components']} components)"
    return f"{r['value']} {r.get('unit', 'groups')}"


def big(kind: str, p: dict) -> bool:
    """Whether to queue it: a facility scope, or a window long enough to read many values."""
    from ..analysis import studies

    if kind == "events":
        return False
    if p.get("scope") == "facility":
        return True
    try:
        days = (datetime.fromisoformat(p["until"]) - datetime.fromisoformat(p["since"])).days
    except (KeyError, TypeError, ValueError):
        return False
    return days * 144 * 50 > studies.MAX_VALUES  # about 50 points at 10-minute steps


def start(conn, pack, kind: str, params: dict, by: str, now: bool | None = None) -> dict:
    """Keep a study; run it now when it is small (or `now`), else queue it for the background job."""
    if kind not in KINDS:
        raise ValueError(f"unknown study kind {kind!r}; one of {', '.join(KINDS)}")
    sid = conn.execute("insert into study (kind, params, created_by) values (%s, %s, %s) returning id",
                       (kind, Jsonb(params), by)).fetchone()["id"]
    conn.commit()
    if now or (now is None and not big(kind, params)):
        return run(conn, pack, sid)
    return get(conn, sid)


def run(conn, pack, sid: int) -> dict:
    from .. import plots
    from ..analysis.calcs import CalcError
    from ..core.jsonsafe import plain as _plain

    s = conn.execute("update study set status = 'running' where id = %s returning *", (sid,)).fetchone()
    conn.commit()
    try:
        out, fig = _call(conn, pack, s["kind"], s["params"])
        figure = plots.store(conn, fig, {"chart": fig.kind, "study": sid})["figure"] if fig else None
        conn.execute("update study set status = 'done', result = %s, summary = %s, figure_id = %s, finished_at = now() "
                     "where id = %s", (Jsonb(_plain(out)), _summary(s["kind"], out), figure, sid))
    except (CalcError, ValueError, KeyError) as e:
        conn.rollback()
        conn.execute("update study set status = 'failed', error = %s, finished_at = now() where id = %s", (str(e), sid))
    conn.commit()
    return get(conn, sid)


def run_queued(conn, pack, limit: int = 5) -> dict:
    """The background job: run the oldest queued studies."""
    done = []
    for r in conn.execute("select id from study where status = 'queued' order by id limit %s", (limit,)).fetchall():
        done.append(run(conn, pack, r["id"])["status"])
    return {"ran": len(done), "done": done.count("done"), "failed": done.count("failed")}


def rerun(conn, pack, sid: int, by: str) -> dict:
    s = get(conn, sid)
    if s is None:
        raise ValueError(f"no study {sid}")
    return start(conn, pack, s["kind"], s["params"], by)


def get(conn, sid: int) -> dict | None:
    from ..core.jsonsafe import plain as _plain

    r = conn.execute("select * from study where id = %s", (sid,)).fetchone()
    return _plain(dict(r)) if r else None


def listing(conn, limit: int = 50) -> list[dict]:
    from ..core.jsonsafe import plain as _plain

    return [_plain(dict(r)) for r in conn.execute(
        "select id, kind, params, status, summary, figure_id, error, created_by, created_at, finished_at from study "
        "order by id desc limit %s", (limit,))]
