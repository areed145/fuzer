"""The Architecture page (#134): the components as docs/architecture.md shows them, with live status: row
counts of the tables each writes, entities by kind, when each scheduled job last did something, and the
sources' state. Reads only."""

import os

from fastapi import APIRouter, Depends

from .deps import get_conn

router = APIRouter()

# A scheduled job's latest trace in the tables it writes (the scheduler's own run log may be elsewhere).
LAST = {
    "refresh_all": ("loaded_at", "dataset_state"),
    "claude_reading": ("created_at", "llm_extraction"),
    "work_tick": ("at", "work_event"),
    "sre_scan": ("finished_at", "sre_run"),
    "studies_job": ("finished_at", "study"),
    "day_close": ("created_at", "daily_ops_report"),
    "opportunities": ("scanned_at", "opportunity"),
}
BIG = 200_000  # rows: above this a table's count is the planner's estimate


@router.get("/architecture")
def architecture(conn=Depends(get_conn)):
    from .. import components
    from ..sources import preview

    comps = components.components()
    tables = sorted({t for c in comps for t in c["writes"]})
    rows = {}
    for r in conn.execute("select relname as t, reltuples as n from pg_class where relkind = 'r' and relname = any(%s)",
                          (sorted({"entity", "edge", *tables}),)).fetchall():
        rows[r["t"]] = int(r["n"]) if r["n"] > BIG else \
            conn.execute(f"select count(*) as n from {r['t']}").fetchone()["n"]  # noqa: S608 (names from pg_class)
    jobs = {}
    for job, (col, table) in LAST.items():
        v = conn.execute(f"select max({col}) as t from {table}").fetchone()["t"] if table in rows else None  # noqa: S608
        jobs[job] = v.isoformat(timespec="minutes") if v else None
    kinds = [dict(r) for r in conn.execute(
        "select kind, count(*) as n from entity where merged_into is null and retired_at is null group by kind order by n desc")]
    try:
        src = preview.listing(conn, os.environ.get("FUZER_DATA_DIR") or "data")
    except Exception:  # noqa: BLE001  no data folder
        conn.rollback()
        src = []
    state: dict[str, int] = {}
    for s in src:
        state[s["state"]] = state.get(s["state"], 0) + 1
    return {"components": comps, "rows": rows, "jobs": jobs, "entities": kinds, "sources": state}
