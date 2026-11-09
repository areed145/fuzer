"""Turnarounds: one revision's scope, read against the integrated plan.

The scope itself (work orders on the revision, shutdown-only maintenance plans in or out of it, the worst bad actors
not in it, spares out of stock for its units) comes from the register (graph.operations.turnaround_scope). This
module adds what the plan knows:

    scope(conn, revision)              the scope plus its window on the plan, the plan items that could ride
                                       along with it, and shutdown-only items planned outside it
    bring_in(conn, item_id, revision, by)  move a plan item into the turnaround's window, saying so on it

so a turnaround planner sees in one place what is in, what should be, and what is missing, and pulls work in
without retyping it.
"""

from datetime import timedelta

from ..core.plan import PlanError
from ..graph.operations import turnaround_scope
from . import plan

ACTIVE = plan.ACTIVE


def _window(items: list[dict]) -> tuple | None:
    if not items:
        return None
    return min(i["start_at"] for i in items), max(i["end_at"] for i in items)


def scope(conn, revision: str | None = None) -> dict:
    """The revision's scope with its place on the plan: `window` (first start to last finish of its orders on the
    plan), `on_plan` (those plan items), `ride_along` (active items near the window in its areas that could be
    done in it) and `shutdown_outside` (active items that need the unit down but are planned outside it)."""
    out = turnaround_scope(conn, revision)
    order_ids = {w["id"] for w in out["scoped_work"]}
    rows = conn.execute(
        "select * from plan_item where status = any(%s) and subjects && %s order by start_at",
        (list(ACTIVE), list(order_ids))).fetchall() if order_ids else []
    window = _window(rows)
    out.update({"window": {"start": window[0], "end": window[1]} if window else None, "on_plan": rows,
                "ride_along": [], "shutdown_outside": []})
    if not window:
        return out
    areas = {r["area_id"] for r in rows if r["area_id"]}
    ids = {r["id"] for r in rows}
    margin = timedelta(days=plan.BUNDLE_DAYS)
    near = plan.items(conn, window[0] - margin, window[1] + margin)
    for i in near:
        if i["id"] in ids or i["status"] not in ACTIVE or i["kind"] in plan.SHUTDOWNS:
            continue
        inside = i["start_at"] >= window[0] and i["end_at"] <= window[1]
        if i["area_id"] in areas and not inside:
            out["ride_along"].append(i)
    for i in plan.items(conn, window[1] - timedelta(days=365), window[1] + timedelta(days=365)):
        if (i["needs_shutdown"] and i["id"] not in ids and i["status"] in ACTIVE and i["kind"] not in plan.SHUTDOWNS
                and not (i["start_at"] >= window[0] and i["end_at"] <= window[1])):
            out["shutdown_outside"].append(i)
    return out


def bring_in(conn, item_id: int, revision: str | None, by: str) -> dict:
    """Move a plan item into the revision's window (keeping its length, starting with the turnaround and never
    running past its end) and note the revision on it."""
    s = scope(conn, revision)
    if not s["window"]:
        raise PlanError(f"turnaround {s['revision'] or '(none)'} has no work on the plan to take a window from")
    row = conn.execute("select * from plan_item where id = %s", (item_id,)).fetchone()
    if row is None:
        raise PlanError(f"no plan item {item_id}")
    start, end = s["window"]["start"], s["window"]["end"]
    length = min(row["end_at"] - row["start_at"], end - start)
    note = f"in turnaround {s['revision']}"
    notes = row["notes"] if row["notes"] and note in row["notes"] else "; ".join(x for x in (row["notes"], note) if x)
    return plan.update(conn, item_id, by, start=start, end=start + length, notes=notes)
