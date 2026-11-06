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
3. **Materials.** `spare_parts(ref)` gives materials with stock on hand; 0 means none in stock. `turnaround_scope`
   lists out-of-stock spares for scoped units. Stock on hand is a count, not a reservation for this job.
4. **Priority.** `value_score(kinds=["work_order"])` ranks work by value with its inputs; `bad_actors()` shows
   repeat failures among the units.

**Report each job as:** recorded as ready, missing, and unknown.

**Unknown here.** Fuzer has no crew calendars, resource loading, purchase orders, lead times, vendor status,
transport or site-access records. So it cannot detect schedule conflicts beyond two orders on a unit in the same
dates, and it cannot confirm that anything will arrive. Say so; don't estimate.

Planners, schedulers and supply chain own priorities, schedules, reservations and logistics. Offer options and
evidence for them to approve; never state that work is scheduled, committed or ordered.
