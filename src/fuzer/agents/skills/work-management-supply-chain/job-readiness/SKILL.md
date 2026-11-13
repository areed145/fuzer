---
name: job-readiness
description: Job and turnaround readiness from recorded evidence - scope, dates, spares and stock per job, and what is missing or unknown about materials, resources, schedule conflicts and logistics.
---

# Job readiness

Readiness is a list of what the records show and what they don't. It is never a statement that a job can start.

1. **Which jobs.** `work_order_backlog(as_of)` for open orders, or `turnaround_scope(revision)` for a revision.
   `plot_backlog(as_of)` charts open orders by priority and age. Name the `as_of` date.
2. **Scope.** Each order's text, activity and dates. `maintenance_history(ref)` shows how similar work went on that
   unit, and `maintenance_compliance(as_of)` shows plans due on it that could be done in the same access.
3. **Materials.** `material_readiness(as_of, ref)` reads the parts reserved for open orders against stock and open
   purchase orders, earliest need first: in stock, on order in time, late (an order due after the need date, by how
   many days), or not on order (and whether the planned delivery time would still fit). Lead with the late lines and
   the ones too late to order. `spare_parts(ref)` gives materials with stock on hand; 0 means none in stock.
   `turnaround_scope` lists out-of-stock spares for scoped units. A delivery date is the order's own, not a promise.
4. **Priority.** `value_score(kinds=["work_order"])` ranks work by value with its inputs; `bad_actors()` shows
   repeat failures among the units.

**Report each job as:** recorded as ready, missing, and unknown.

**Unknown here.** Fuzer has no crew calendars, resource loading, vendor performance, transport or site-access
records. So it cannot detect schedule conflicts beyond two orders on a unit in the same dates, and it cannot confirm
that anything will arrive beyond an order's own delivery date. Say so; don't estimate.

Planners, schedulers and supply chain own priorities, schedules, reservations and logistics. Offer options and
evidence for them to approve; never state that work is scheduled, committed or ordered.
