---
name: integrity
description: Asset integrity and inspection evidence - wall loss and remaining life at CMLs, overdue statutory and internal inspections, integrity operating window excursions, and risk ranking of what to review first.
---

# Integrity and inspection

Each finding gives the item, the survey or plan date and the source. Say what was measured and what was not.

**Start here.** For "which line is worst", "how long has it got" or "when do we inspect", run
`calc("corrosion_circuit", pipeline)` first: the pipeline's corrosion circuit (the in-service lines in the same
service) with each line's rate, remaining life and next inspection date. Read it in this order:
- **Rate.** Per CML, the greater of the long-term rate (first to last reading) and the short-term rate (the last
  two), as API 570 asks. A short-term rate well above the long-term one means the condition changed recently:
  find out what (water breakthrough, a new well on the line, chemical injection stopped) before trusting the
  long-term figure. Quote both.
- **Remaining life and retirement.** The retirement thickness is the Barlow pressure design thickness at the line's
  MOP and size on an assumed grade (API 5L X52, design factor 0.72) unless one is passed in: `retirement_assumed`
  says so. Say the remaining life is an estimate on that assumption and ask for the line's design thickness;
  with it, rerun with `min_required_mm`.
- **Next inspection.** Half the remaining life, at most 10 years, after the last survey: the interval the rate
  supports, not a decision. `inspection_overdue` means that date has passed.
- **Flags.** `retire_soon` (under 5 years) and `inspection_overdue` go to the integrity engineer now.
  `localised` (one line over twice the circuit's median) is that line's conditions, not the circuit's: compare it
  with what flows through it.
**Escalate** when a line has under two years left or its next inspection is overdue, and say what would change
the answer (the real design thickness, a follow-up survey). **Don't trust** a rate from two readings under a month
apart, a CML whose newest reading is thicker than the one before (a measurement problem, not growth), or a
vessel without a retirement thickness (it gets no remaining life, and the result says so).

1. **Wall loss.** `plot_wall_thickness()` ranks the items losing wall fastest; `plot_wall_thickness(ref)` shows each
   CML over time. `measurements(ref, "wall_thickness")` gives the readings (mm) and the fitted `per_year`.
   `calc("wall_loss", ref, min_required_mm=...)` gives remaining life. Without a stated minimum thickness there is no
   retirement date: say so rather than assume one. With one, `next_inspection_years` is the API 570 / 510 interval
   from the rate (half the remaining life, at most 10 years): give it as the interval the rate supports, not a
   decision. Fewer than three readings on a CML is not a rate; a single fast CML (`localised`) points at local
   corrosion (dead legs, injection points, under deposits), so name the CML.
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
