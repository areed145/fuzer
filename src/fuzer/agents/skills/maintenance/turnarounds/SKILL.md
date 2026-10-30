---
name: turnarounds
description: Turnaround (TAR / shutdown) scope - work planned for the revision, shutdown-only inspections and PM due before or around it, bad actors that should be added, long-lead spares to order, and scope gaps.
---

# Turnaround scope

A turnaround (TAR) is a planned shutdown; work that needs a unit down waits for it. In SAP the work
is tagged with a revision (e.g. OK-TAR-2026).

1. `turnaround_scope(revision)` returns: the scoped work orders and planned start; shutdown-only plans
   (statutory internal inspections etc.) overdue or due before the next window, split into in scope and
   **not** in scope; the worst bad actors not in scope; and out-of-stock spares for scoped units.
2. **Scope gaps** are the main finding: shutdown-only inspections that would otherwise wait another
   cycle, and bad actors whose permanent fix needs the unit down (check `maintenance_history` for
   repeated temporary fixes such as cleaning a fouled exchanger every year).
3. **Long-lead spares:** anything with zero stock for scoped work must be ordered now; name the
   material, the unit and the order it supports.
4. **Production case:** for each suggested addition, the deferment it has caused
   (`deferment_pareto(by="tag")`) is the cost of leaving it out.

Report: planned start and scoped work (unit, order, what), recommended additions ranked with evidence,
spares to order now, and risks (overdue statutory inspections, work with no order yet).
