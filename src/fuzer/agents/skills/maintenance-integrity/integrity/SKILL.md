---
name: integrity
description: Asset integrity and inspection evidence - wall loss and remaining life at CMLs, overdue statutory and internal inspections, integrity operating window excursions, and risk ranking of what to review first.
---

# Integrity and inspection

Each finding gives the item, the survey or plan date and the source. Say what was measured and what was not.

1. **Wall loss.** `plot_wall_thickness()` ranks the items losing wall fastest; `plot_wall_thickness(ref)` shows each
   CML over time. `measurements(ref, "wall_thickness")` gives the readings (mm) and the fitted `per_year`.
   `calc("wall_loss", ref, min_required_mm=...)` gives remaining life. Without a stated minimum thickness there is no
   retirement date: say so rather than assume one.
2. **Inspection compliance.** `maintenance_compliance(as_of)` gives on-time share, overdue and due-soon plans by kind
   (PM / INSP) and marks tasks that need a shutdown. `plot_compliance(as_of)` charts it. Overdue means past the plan's
   next due date in SAP; it does not state the equipment's condition. Never infer an inspection result from a date.
3. **Integrity operating windows.** `limits(ref)` gives IOWs and excursions while the unit runs, and
   `plot_operating_window(ref, since, until)` shows minutes outside. Repeated breaches can explain faster wall loss;
   say so as a lead, not a finding.
4. **Rank.** `value_score(ref)` gives the shared risk score with its inputs and unknowns. Use its numbers when saying
   one item matters more than another.

**Coverage.** Report items with no recent survey, and CMLs with too few readings for a rate.

Fitness for service, integrity acceptance, inspection deferral and any shutdown are accountable human decisions.
Present evidence for them; do not make them.
